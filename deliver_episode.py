"""Verified staging delivery. It never uploads videos or posts comments."""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from episode_core import atomic_json, load_json, safe_child, sha256_file, is_longform, object_hash
from check_episode import DELIVERY_FILES, DELIVERY_OPTIONAL_SOURCES, DELIVERY_SOURCES, delivery_source, validate_completion, validate_quality, linked_short_comment_document
from project_state import ProjectState, file_lock, now


def sanitize_title(title, max_length=120):
    # Preserve Korean and ordinary punctuation; only filesystem-invalid characters change.
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).rstrip(" .")
    if not title or title in {".", ".."}:
        title = "제목 없음"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", title):
        title = "_" + title
    return title[:max(1, max_length)].rstrip(" .") or "제목"


def project_root_for(base):
    for path in [base, *base.parents]:
        if (path / "plan_matchups.py").is_file() and (path / "AGENTS.md").is_file():
            return path
    raise ValueError("Cannot identify the existing project root")


def destination_project(root, supplied=None):
    if supplied:
        candidate = Path(supplied).resolve()
        if candidate.is_dir() and (candidate / "AGENTS.md").is_file() and (candidate / "make_vs.py").is_file():
            return candidate, None
        return root, "Requested destination is not an accessible confirmed project; preserved in current project"
    return root, "Desktop project was not confirmed; preserved in current project"


def configured_delivery_root(root):
    """Read portable project storage settings without changing legacy projects."""
    config_path = root / "storage_config.json"
    if not config_path.is_file():
        return None, False
    config = load_json(config_path)
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("Unsupported storage_config.json schema")
    relative = config.get("delivery_root")
    if (not isinstance(relative, str) or not relative.strip()
            or Path(relative).drive or Path(relative).is_absolute()
            or ".." in Path(relative).parts):
        raise ValueError("delivery_root must be a relative directory inside the project")
    target = safe_child(root, relative)
    if target == root.resolve():
        raise ValueError("delivery_root must be a subdirectory of the project")
    required = config.get("require_delivery_root", False)
    if not isinstance(required, bool):
        raise ValueError("require_delivery_root must be a boolean")
    return target, required


def select_target(parent, title, job_id, media_format="shorts"):
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    # Work identity takes precedence over renamed titles; resumes do not make (2) copies.
    for item in parent.iterdir():
        if item.is_dir() and not item.name.startswith(".") and (item / "manifest.json").is_file():
            try:
                record = load_json(item / "manifest.json")
                if record.get("job_id") == job_id and record.get("format", "shorts") == media_format:
                    return item
            except (OSError, ValueError):
                continue
    # Reserve room for review filenames and Windows compatibility without long-path support.
    budget = min(120, 230 - len(str(parent)) - 40)
    if budget < 10:
        raise ValueError("Destination path is too long; choose a shorter existing project path")
    stem = sanitize_title(title, budget)
    target = parent / stem
    suffix = 2
    while target.exists():
        ending = f" ({suffix})"
        target = parent / (stem[:budget - len(ending)].rstrip(" .") + ending)
        suffix += 1
    return target


def verify_bundle(target, manifest):
    errors = []
    for name, expected in manifest.get("files", {}).items():
        try:
            actual = sha256_file(safe_child(target, name))
            if actual != expected:
                errors.append(f"Delivery hash mismatch: {name}")
        except (OSError, ValueError) as exc:
            errors.append(f"Delivery file missing/unsafe {name}: {exc}")
    return errors


def deliver(episode_path, destination=None, update_state=True, *, desktop_root=None, output_root=None):
    if sum(value is not None for value in (destination, desktop_root, output_root)) > 1:
        raise ValueError("Use only one destination option")
    episode_path = Path(episode_path).resolve()
    base = episode_path.parent
    cfg = load_json(episode_path)
    media_format = "longform" if is_longform(cfg) else "shorts"
    root = project_root_for(base)
    manifest_path = base / "manifest.json"
    manifest = load_json(manifest_path)
    job_id = manifest.get("job_id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
        raise ValueError("A stable, safe job_id is required before delivery")
    if media_format == "longform":
        if cfg.get("job_id") != job_id:
            raise ValueError("Longform config and working manifest job identities differ")
        ledger_path = root / "state/project_state_longform.json"
        if update_state or ledger_path.is_file():
            ledger = load_json(ledger_path) if ledger_path.is_file() else {}
            matches = [job for job in ledger.get("jobs", []) if job.get("job_id") == job_id]
            if (len(matches) != 1 or matches[0].get("story_id") != cfg.get("story_id")
                    or matches[0].get("format") != "longform"
                    or (root / matches[0].get("episode_path", "")).resolve() != episode_path):
                raise ValueError("Longform delivery requires the matching ledger story/job/episode identity")
    title = (base / "title.txt").read_text(encoding="utf-8-sig").strip()
    if not title:
        raise ValueError("title.txt must contain the real title")
    configured_root, require_root = configured_delivery_root(root)
    explicit_root = output_root if output_root is not None else desktop_root
    if require_root and (destination is not None or (
            explicit_root is not None and Path(explicit_root).resolve() != configured_root)):
        raise ValueError("Delivery destination conflicts with required storage_config.json delivery_root")
    if explicit_root is not None:
        dest_root, limitation = Path(explicit_root).resolve(), None
        layout = "title_root" if output_root is not None else "desktop_title"
        if not dest_root.is_dir() or sanitize_title(title) != title:
            raise ValueError('Output folder must exist and title must be an exact valid folder name')
    elif configured_root is not None and destination is None:
        dest_root, limitation, layout = configured_root, None, "title_root"
        if sanitize_title(title) != title:
            raise ValueError('Title must be an exact valid folder name')
        dest_root.mkdir(parents=True, exist_ok=True)
    else:
        dest_root, limitation = destination_project(root, destination)
        layout = "project_category"
    from release_checks import release_errors
    quality_errors = validate_quality(episode_path)
    errors = release_errors(episode_path, quality_errors)
    if limitation:
        errors.append(limitation)
    if media_format == "longform":
        blocking = [error for error in errors if not str(error).startswith(("LONGFORM_LISTENING_PENDING:", "LONGFORM_LISTENING_STALE:"))]
        if blocking:
            raise ValueError("Longform review delivery requires technical and visual checks first: " + "; ".join(blocking))
    classification = "NEEDS_REVIEW" if errors else "COMPLETE"
    category = "검수대기" if errors else "완성본"
    # Exact-title layouts apply to reviewed and pending artifacts alike.
    # Keep the review classification in the manifest rather than changing the title path.
    exact_title_layout = layout in {"desktop_title", "title_root"}
    parent = dest_root if exact_title_layout else dest_root / category
    parent.mkdir(parents=True, exist_ok=True)
    with file_lock(dest_root / ".delivery.lock"):
        target = select_target(parent, title, job_id, media_format)
        if layout == "title_root" and target.name != title:
            raise ValueError("Exact title folder is unavailable or the destination path is too long")
        if media_format == "longform" and target.exists():
            prior = load_json(target / "manifest.json")
            prior_episode = prior.get("working_episode")
            if (prior.get("story_id") != cfg.get("story_id") or not isinstance(prior_episode, str)
                    or not Path(prior_episode).is_absolute() or Path(prior_episode).resolve() != episode_path):
                raise ValueError("Refusing to replace another longform story or working episode")
        staging = parent / (".staging-" + job_id + "-" + uuid.uuid4().hex)
        staging.mkdir()
        if media_format == "longform" and classification == "COMPLETE":
            comment = linked_short_comment_document(cfg)
            if comment:
                from project_state import atomic_text
                atomic_text(base / "linked_short_comment.txt", comment)
        sources = {name: base / delivery_source(base, name) for name in DELIVERY_SOURCES}
        sources.update({name: base / relative for name, relative in DELIVERY_OPTIONAL_SOURCES.items()
                        if (base / relative).is_file()})
        frame_index = load_json(base / "out/review_frames.json") if (base / "out/review_frames.json").is_file() else {}
        if media_format == "longform" and classification == "COMPLETE" and cfg.get("planning", {}).get("related_short") and (base / "linked_short_comment.txt").is_file():
            sources["linked_short_comment.txt"] = base / "linked_short_comment.txt"
        full_frame_names = []
        for frame in frame_index.get("frames", []):
            source = safe_child(base / "out", frame["file"])
            name = "review/" + Path(frame["file"]).as_posix()
            if source.is_file() and sha256_file(source) != frame.get("sha256"):
                raise RuntimeError("Extracted review frame changed before delivery: " + frame["file"])
            sources[name] = source
            full_frame_names.append(name)
        if (base / "out/_review.png").is_file():
            sources["review/_review.png"] = base / "out/_review.png"
        # Include the exact extracted-frame index for an independently checkable handoff.
        if (base / "out/review_frames.json").is_file():
            sources["review/review_frames.json"] = base / "out/review_frames.json"
        hashes, missing = {}, []
        for name, source in sources.items():
            if not source.is_file():
                missing.append(name)
                continue
            dest = safe_child(staging, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            before = sha256_file(source)
            shutil.copy2(source, dest)
            if sha256_file(dest) != before or sha256_file(source) != before:
                raise RuntimeError("Source changed or copy was corrupted: " + name)
            hashes[name] = before
        if classification == "COMPLETE" and (missing or not full_frame_names or any(x not in hashes for x in full_frame_names)):
            raise RuntimeError("A COMPLETE delivery lacks required files/review frames")
        delivered = {"schema_version": 2, "job_id": job_id, "format": media_format, "story_id": cfg.get("story_id"), "status": classification,
                     "completion_basis": "USER_ACCEPTANCE_AND_REVISION_CHECKS" if quality_errors and not errors else "QUALITY_GATES",
                     "accepted_quality_limits": quality_errors if not errors else [],
                     "title": title, "working_episode": str(episode_path), "delivery_path": str(target),
                     "created_at": now(), "files": hashes, "missing_files": missing,
                     "review_issues": errors, "upload_performed": False}
        if media_format == "longform":
            audio_review = load_json(base / "audio_review.json") if (base / "audio_review.json").is_file() else {}
            delivered["audio_acceptance"] = {"technical_status": audio_review.get("technical", {}).get("status", "NOT_RUN"),
                "user_approval_sha256": object_hash(audio_review.get("user_approval", {})),
                "listening_pending": any("LONGFORM_LISTENING_" in str(error) for error in errors),
                "video_sha256": hashes.get("video.mp4"),
                "final_audio_sha256": audio_review.get("technical", {}).get("final_audio_sha256"),
                "audio_timing_hash": audio_review.get("technical", {}).get("audio_timing_hash")}
        atomic_json(staging / "manifest.json", delivered)
        copy_errors = verify_bundle(staging, delivered)
        if copy_errors:
            raise RuntimeError("; ".join(copy_errors))
        # Renames stay within the explicitly resolved destination project, never an inferred root.
        if not staging.resolve().is_relative_to(dest_root) or not target.resolve().is_relative_to(dest_root):
            raise ValueError("Unsafe delivery target")
        previous = None
        if target.exists():
            prior = load_json(target / "manifest.json")
            if prior.get("job_id") != job_id or prior.get("format", "shorts") != media_format:
                raise RuntimeError("Refusing to replace another episode's delivery")
            revisions = dest_root / ".delivery_revisions" / job_id
            revisions.mkdir(parents=True, exist_ok=True)
            previous = revisions / uuid.uuid4().hex
            if not previous.resolve().is_relative_to(dest_root):
                raise ValueError("Unsafe delivery revision path")
            target.rename(previous)
        try:
            staging.rename(target)
        except BaseException:
            if previous is not None and previous.exists() and not target.exists():
                previous.rename(target)
            raise
        manifest["status"] = classification
        manifest["delivery"] = {"path": str(target), "classification": classification,
                                "layout": layout,
                                "desktop_root": str(dest_root) if layout == "desktop_title" else None,
                                "output_root": str(dest_root) if layout == "title_root" else None,
                                "verified": True, "manifest_sha256": sha256_file(target / "manifest.json"),
                                "job_id": job_id, "verified_at": now(), "limitations": errors}
        atomic_json(manifest_path, manifest)
        if classification == "COMPLETE":
            completion_errors = validate_completion(episode_path)
            if completion_errors:
                # Do not leave the newly failed candidate displayed as a completed artifact.
                if not exact_title_layout:
                    hold_parent = dest_root / "검수대기"
                    hold = select_target(hold_parent, title, job_id, media_format)
                    if hold.exists():
                        revisions = dest_root / ".delivery_revisions" / job_id
                        revisions.mkdir(parents=True, exist_ok=True)
                        hold.rename(revisions / uuid.uuid4().hex)
                    target.rename(hold)
                    target = hold
                classification = "NEEDS_REVIEW"
                errors.extend(completion_errors)
                delivered.update(status=classification, delivery_path=str(target), review_issues=errors)
                atomic_json(target / "manifest.json", delivered)
                manifest["status"] = classification
                manifest["delivery"].update(path=str(target), classification=classification, manifest_sha256=sha256_file(target / "manifest.json"), limitations=errors)
                atomic_json(manifest_path, manifest)
    if update_state:
        from plan_matchups import IdentityRegistry
        pool_name = "matchup_ideas_longform.json" if media_format == "longform" else "matchup_ideas.json"
        pool = load_json(root / pool_name) if (root / pool_name).is_file() else {"aliases": {}, "candidates": []}
        registry = IdentityRegistry(pool)
        with ProjectState(root, registry.line_key, media_format=media_format) as state:
            state.set_status(job_id, classification, "; ".join(errors) if errors else "Verified delivery and all quality gates passed")
            job = state.get_job(job_id)
            if job.get("run_id"):
                run = state.data["runs"][job["run_id"]]
                if not state.remaining(job["run_id"]) and all(state.get_job(j)["status"] != "IN_PROGRESS" for j in run["attempts"]):
                    state.finish_run(job["run_id"])
    return {"status": classification, "delivery_path": str(target), "issues": errors, "missing_files": missing, "job_id": job_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode")
    parser.add_argument("--destination-project")
    parser.add_argument("--desktop-root", help="OS-confirmed Desktop path; write directly under the exact video title")
    parser.add_argument("--output-root", help="Existing output directory; write directly under the exact video title")
    args = parser.parse_args()
    if sum(value is not None for value in (args.desktop_root, args.output_root, args.destination_project)) > 1:
        parser.error('Use only one destination option')
    print(json.dumps(deliver(args.episode, args.destination_project, desktop_root=args.desktop_root,
                             output_root=args.output_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
