"""Read-only evidence gate for a local model that actually accepts raw audio.

This module never generates approvals. It checks current files, provenance
fields, coverage and review records; it cannot establish that a model's prose
is truthful or that an observation is perceptually correct. Codex must inspect
the saved raw responses and resolve contradictions before recording PASS.
ASR output and signal measurements alone are not perceptual review evidence.
"""
from __future__ import annotations

import math
import hashlib
import io
import re
import subprocess
import unicodedata
import wave
from pathlib import Path, PureWindowsPath

from episode_core import load_json, safe_child, sha256_file

TARGETS = {"original_pcm", "final_mp4_audio"}
CHECKS = ("pronunciation", "omissions", "intonation", "pacing", "pauses",
          "level_and_distortion", "ending", "sync")
TOLERANCE = 0.05
_HEX = re.compile(r"[0-9a-f]{64}")
_BARE_VERDICTS = {
    "pass", "ok", "okay", "good", "allgood", "none", "n/a", "test", "fixture",
    "clearandnatural", "thespeechisclearandnatural", "theaudiosoundsgood",
    "noissues", "noproblems", "좋아요", "정상", "정상입니다", "문제없음",
    "문제없어요", "문제없습니다", "통과", "잘들려요", "자연스러워요",
}


def _specific_text(value, minimum):
    # Reject absent/bare/repeated boilerplate, without claiming that length
    # proves specificity. Semantic adequacy remains the reviewer's decision.
    if not isinstance(value, str):
        return False
    compact = re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()
    return len(compact) >= minimum and compact not in _BARE_VERDICTS


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _artifact_filename(value):
    """Accept a portable artifact basename, without probing model directories."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if value in {".", ".."} or value.endswith("."):
        return False
    if any(ord(char) < 32 or ord(char) == 127 or char in '<>:"/\\|?*' for char in value):
        return False
    stem = value.split(".", 1)[0].rstrip(" ").upper()
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    reserved.update(prefix + suffix for prefix in ("COM", "LPT") for suffix in "123456789¹²³")
    return stem not in reserved


def _artifact_sha256(value):
    return isinstance(value, str) and bool(_HEX.fullmatch(value.lower()))


def _native_model_provenance(model, fail):
    """Check recorded identities only; the runner verifies actual source files.

    A native integrated model has no separately fabricated projector hash.
    The runner must verify the complete shard list, source revision, config,
    processor files and their actual bytes before recording this provenance.
    This schema check cannot infer unlisted shards or establish file existence.
    """
    seen = set()
    for field, require_bytes in (("weight_shards", True), ("processor_artifacts", False)):
        entries = model.get(field)
        if not isinstance(entries, list) or not entries:
            fail("model " + field + " must be a nonempty artifact list")
            continue
        for index, entry in enumerate(entries):
            label = f"model {field}[{index}]"
            if not isinstance(entry, dict):
                fail(label + " must be an artifact object")
                continue
            filename = entry.get("filename")
            if not _artifact_filename(filename):
                fail(label + " filename must be a safe artifact basename")
            else:
                identity = unicodedata.normalize("NFC", filename).casefold()
                if identity in seen:
                    fail(label + " has a duplicate artifact filename: " + filename)
                seen.add(identity)
            if not _artifact_sha256(entry.get("sha256")):
                fail(label + " sha256 must contain 64 hexadecimal characters")
            if require_bytes or "bytes" in entry:
                size = entry.get("bytes")
                if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                    fail(label + " bytes must be a positive integer")
    if not _artifact_sha256(model.get("config_sha256")):
        fail("model config_sha256 must contain 64 hexadecimal characters")


def _relative_file(base, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a nonempty relative file path is required")
    portable = PureWindowsPath(value)
    if Path(value).is_absolute() or portable.is_absolute() or portable.drive or portable.root:
        raise ValueError("absolute or drive-relative paths are forbidden")
    if ".." in portable.parts or ".." in Path(value).parts or ":" in value:
        raise ValueError("parent path traversal is forbidden")
    path = safe_child(base, value)
    if not path.is_file():
        raise ValueError("evidence/input file is missing: " + value)
    return path


def media_duration(path):
    """Measure present media, rather than accepting the evidence's duration."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                return audio.getnframes() / audio.getframerate()
        except (wave.Error, EOFError):
            pass
    import json
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=30,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _covered(intervals, duration):
    cursor = 0.0
    for start, end in sorted(intervals):
        if start > cursor + TOLERANCE:
            return False
        cursor = max(cursor, end)
    return cursor >= duration - TOLERANCE


def _pcm_snapshot(path, expected_hash):
    """Bind decoded PCM bytes and WAV headers to the same hashed file bytes."""
    blob = Path(path).read_bytes()
    if hashlib.sha256(blob).hexdigest() != expected_hash:
        raise ValueError("stale processed audio hash: " + str(path))
    try:
        with wave.open(io.BytesIO(blob), "rb") as audio:
            if audio.getcomptype() != "NONE":
                raise ValueError("chunk provenance requires uncompressed PCM WAV")
            result = {"sample_rate": audio.getframerate(), "channels": audio.getnchannels(),
                      "sample_width": audio.getsampwidth(), "frames": audio.getnframes()}
            result["pcm"] = audio.readframes(result["frames"])
    except (wave.Error, EOFError) as exc:
        raise ValueError("chunk provenance requires valid PCM WAV: " + str(exc)) from exc
    if len(result["pcm"]) != result["frames"] * result["channels"] * result["sample_width"]:
        raise ValueError("truncated PCM WAV in processed audio provenance")
    return result


def _processed_audio(base, run, item, parent_cache):
    """Validate a full-input run or an exact, non-resampled parent PCM slice.

    processed_audio.start_sample/end_sample are frame indices in the parent
    (end exclusive). In multichannel WAV one frame includes every channel.
    Hashes alone cannot establish a chunk's parentage, so compare its PCM bytes.
    """
    processed = run.get("processed_audio")
    parent_path = _relative_file(base, item.get("path"))
    key = (str(parent_path), item.get("sha256"))
    if parent_path.suffix.lower() == ".wav" and key not in parent_cache:
        parent_cache[key] = _pcm_snapshot(parent_path, item.get("sha256"))
    parent = parent_cache.get(key)
    duration = item.get("measured_duration", item.get("duration"))
    tolerance = (0.5 / parent["sample_rate"]) if parent else 0.000001
    if processed is None:
        if abs(run["start"]) > tolerance or abs(run["end"] - duration) > tolerance:
            raise ValueError("partial run requires processed_audio chunk provenance")
        return
    if not isinstance(processed, dict) or parent is None:
        raise ValueError("processed_audio requires an object and a PCM WAV parent")
    for name in ("sample_rate", "frames", "start_sample", "end_sample"):
        if not isinstance(processed.get(name), int) or isinstance(processed[name], bool):
            raise ValueError("processed_audio integer field required: " + name)
    start, end = processed["start_sample"], processed["end_sample"]
    if not 0 <= start < end <= parent["frames"]:
        raise ValueError("processed_audio range is outside parent PCM samples")
    rate = parent["sample_rate"]
    if abs(run["start"] - start / rate) > tolerance or abs(run["end"] - end / rate) > tolerance:
        raise ValueError("processed_audio sample range differs from run interval")
    clip_path = _relative_file(base, processed.get("path"))
    clip = _pcm_snapshot(clip_path, processed.get("sha256"))
    if any(clip[name] != parent[name] for name in ("sample_rate", "channels", "sample_width")):
        raise ValueError("processed_audio format differs from parent PCM")
    if processed["sample_rate"] != rate or processed["frames"] != clip["frames"] or clip["frames"] != end - start:
        raise ValueError("processed_audio rate/frame metadata differs from actual PCM slice")
    frame_bytes = parent["channels"] * parent["sample_width"]
    if clip["pcm"] != parent["pcm"][start * frame_bytes:end * frame_bytes]:
        raise ValueError("processed_audio PCM content differs from the parent slice")


def validate_audio_model_review(base, timeline, audio_review):
    """Validate schema-1 local perceptual evidence; return errors, never mutate."""
    errors = []
    prefix = "audio model review: "
    base = Path(base).resolve()

    def fail(message):
        errors.append(prefix + message)

    if not isinstance(timeline, dict):
        return [prefix + "timeline must be an object"]
    if not isinstance(audio_review, dict):
        return [prefix + "audio_review must be an object"]
    required = {"reviewer_kind": "local_audio_model", "status": "PASS", "listened": False,
                "human_listened": False, "codex_native_listened": False,
                "audio_perception_verified": True}
    for key, value in required.items():
        actual = audio_review.get(key)
        if actual != value or (isinstance(value, bool) and actual is not value):
            fail(f"{key} must explicitly be {value!r}")
    if not _specific_text(audio_review.get("observations"), 12):
        fail("specific reviewer observations are required")
    try:
        reference = audio_review.get("perception_evidence", {})
        if not isinstance(reference, dict):
            raise ValueError("perception_evidence must be an object")
        evidence_path = _relative_file(base, reference.get("path"))
        # The fixed filename also binds delivery's existing optional evidence
        # mapping; a locally approved alternative path must not disappear there.
        if evidence_path != (base / "audio_understanding_local.json").resolve():
            raise ValueError("perception_evidence.path must be audio_understanding_local.json")
        if sha256_file(evidence_path) != reference.get("sha256"):
            raise ValueError("stale perception evidence hash")
        evidence = load_json(evidence_path)
        if not isinstance(evidence, dict):
            raise ValueError("perception evidence must be an object")
    except (OSError, ValueError, TypeError) as exc:
        fail(str(exc))
        return errors
    for key, value in {"schema_version": 1, "status": "COMPLETED", "method": "raw_audio_model"}.items():
        if evidence.get(key) != value or (key == "schema_version" and isinstance(evidence.get(key), bool)):
            fail(f"evidence {key} must be {value!r}")
    model, runtime = evidence.get("model"), evidence.get("runtime")
    if not isinstance(model, dict):
        model = {}
    if not isinstance(runtime, dict):
        runtime = {}
    for field in ("name", "revision"):
        if not isinstance(model.get(field), str) or not model[field].strip():
            fail("model " + field + " is required")
    if model.get("format") == "safetensors":
        _native_model_provenance(model, fail)
    else:
        # Preserve the original GGUF evidence contract, including records that
        # predate an explicit format field. Native weights need their own list.
        for field in ("weights_sha256", "projector_sha256"):
            if not isinstance(model.get(field), str) or not _HEX.fullmatch(model[field]):
                fail("model " + field + " must identify the actual artifact")
    for field in ("name", "version"):
        if not isinstance(runtime.get(field), str) or not runtime[field].strip():
            fail("runtime " + field + " is required")

    inputs = evidence.get("inputs")
    if not isinstance(inputs, list):
        fail("inputs must contain original_pcm and final_mp4_audio")
        inputs = []
    indexed = {}
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str) or item.get("kind") not in TARGETS or item.get("kind") in indexed:
            fail("input target is invalid or duplicated")
            continue
        kind = item["kind"]
        indexed[kind] = item
        try:
            path = _relative_file(base, item.get("path"))
            if sha256_file(path) != item.get("sha256"):
                raise ValueError("stale input hash: " + kind)
            duration = media_duration(path)
            if not _number(duration) or duration <= 0:
                raise ValueError("input duration is not positive: " + kind)
            if not _number(item.get("duration")) or abs(item["duration"] - duration) > TOLERANCE:
                raise ValueError("declared duration differs from current input: " + kind)
            if not _number(timeline.get("duration")) or abs(duration - timeline["duration"]) > TOLERANCE:
                raise ValueError("input does not cover the current timeline duration: " + kind)
            item = dict(item, measured_duration=duration)
            indexed[kind] = item
            if kind == "original_pcm":
                expected = _relative_file(base, timeline.get("pcm_path", "voice.wav"))
                if path != expected or item["sha256"] != timeline.get("pcm_sha256"):
                    raise ValueError("original input is not the current timeline PCM")
            else:
                if item.get("source_video_sha256") != sha256_file(base / "out/video.mp4"):
                    raise ValueError("stale source video hash for final audio")
                # The extraction's content provenance and the model's response
                # truth must also be checked by the reviewer; matching hashes
                # do not prove that arbitrary bytes came from this MP4.
                final_duration = media_duration(base / "out/video.mp4")
                if not _number(final_duration) or abs(duration - final_duration) > TOLERANCE:
                    raise ValueError("final audio duration differs from current MP4")
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            fail(str(exc))
    if set(indexed) != TARGETS:
        fail("both original_pcm and final_mp4_audio are required")

    runs = evidence.get("runs")
    if not isinstance(runs, list) or not runs:
        fail("actual model runs are required")
        runs = []
    run_index = {}
    intervals = {kind: [] for kind in TARGETS}
    parent_cache = {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("id"), str) or not run["id"].strip():
            fail("each model run needs a nonempty id")
            continue
        run_id = run["id"]
        if run_id in run_index:
            fail("duplicate run id: " + run_id)
            continue
        run_index[run_id] = run
        kind = run.get("target_kind")
        item = indexed.get(kind) if isinstance(kind, str) else None
        if not item:
            fail("unknown run target: " + run_id)
            continue
        valid = True
        if run.get("audio_processed") is not True or run.get("transcript_only") is not False:
            fail("raw audio perception, not ASR-only output, is required: " + run_id)
            valid = False
        if run.get("input_sha256") != item.get("sha256"):
            fail("run uses a stale/unknown input: " + run_id)
            valid = False
        if not _specific_text(run.get("prompt"), 12) or not _specific_text(run.get("response"), 30):
            fail("missing or generic prompt/actual response: " + run_id)
            valid = False
        start, end = run.get("start"), run.get("end")
        duration = item.get("measured_duration", item.get("duration"))
        if not all(_number(x) for x in (start, end, duration)) or not 0 <= start < end <= duration + TOLERANCE:
            fail("invalid model run interval: " + run_id)
            valid = False
        if valid:
            try:
                _processed_audio(base, run, item, parent_cache)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                fail(str(exc) + ": " + run_id)
                valid = False
        if valid:
            intervals[kind].append((start, end))
    for kind, item in indexed.items():
        duration = item.get("measured_duration", item.get("duration"))
        if not _number(duration) or duration <= 0 or not _covered(intervals[kind], duration):
            fail("model runs do not cover the full input through its ending: " + kind)

    review = evidence.get("review")
    if not isinstance(review, dict):
        review = {}
    if review.get("unresolved_issues") != []:
        fail("unresolved_issues must explicitly be empty after manual review")
    checks = review.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    observations = []
    cited_targets = set()
    for name in CHECKS:
        check = checks.get(name)
        if not isinstance(check, dict):
            check = {}
        if check.get("status") != "PASS" or not _specific_text(check.get("observations"), 12):
            fail("specific reviewed PASS observations required: " + name)
        observations.append(check.get("observations"))
        ids = check.get("run_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids):
            fail("specific run_ids required: " + name)
            continue
        if len(set(ids)) != len(ids) or any(x not in run_index for x in ids):
            fail("unknown or duplicate run_ids: " + name)
        cited = [run_index[x] for x in ids if x in run_index]
        cited_targets.update(x.get("target_kind") for x in cited if isinstance(x.get("target_kind"), str))
        if name == "ending":
            for kind, item in indexed.items():
                duration = item.get("measured_duration", item.get("duration"))
                if not _number(duration) or not any(
                    r.get("target_kind") == kind and _number(r.get("end")) and
                    r["end"] >= duration - TOLERANCE for r in cited
                ):
                    fail("ending check lacks an end-reaching run: " + kind)
    specific = [x.strip() for x in observations if isinstance(x, str) and x.strip()]
    if len(specific) > 1 and len(set(specific)) == 1:
        fail("identical generic observations cannot replace separate perceptual checks")
    if not TARGETS <= cited_targets:
        fail("review checks must cite both original and final audio observations")
    return errors
