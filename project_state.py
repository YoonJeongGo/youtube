"""Crash-recoverable production ledger. No media generation or upload is performed here."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATUSES = {"IN_PROGRESS", "NEEDS_REVIEW", "FAILED", "SKIPPED", "COMPLETE"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_text(path):
    return Path(path).read_text(encoding="utf-8-sig") if Path(path).exists() else ""


def load_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


@contextlib.contextmanager
def file_lock(path):
    """An OS lock is released on process exit; stale lock files are harmless."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another planning/state transaction is active") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def safe_component(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    if not value:
        value = "unnamed"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", value):
        value = "_" + value
    return value[:70].rstrip(" .")


class ProjectState:
    """Use as a context manager for every mutation, including completion."""

    def __init__(self, root, pair_key_resolver=None):
        self.root = Path(root).resolve()
        self.path = self.root / "state" / "project_state.json"
        self.pair_key_resolver = pair_key_resolver
        self.data = None
        self._lock = None

    def __enter__(self):
        self._lock = file_lock(self.root / "state" / "project.lock")
        self._lock.__enter__()
        try:
            self.data = load_json(self.path, {"schema_version": 1, "jobs": [], "runs": {}, "skipped": [], "pending_transaction": None})
            if self.data.get("schema_version") != 1:
                raise ValueError("Unsupported state schema")
            self.recover()
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        return self._lock.__exit__(*args)

    def save(self):
        if self.data is None:
            raise RuntimeError("Open ProjectState as a context manager")
        atomic_json(self.path, self.data)

    def recover(self):
        tx = self.data.get("pending_transaction")
        if not tx:
            return
        # Validate both destinations first; never overwrite an intervening edit.
        for name in ("queue.txt", "done.txt"):
            item = tx[name]
            current = read_text(self.root / name)
            if digest_text(current) not in {item["before_hash"], digest_text(item["after"])}:
                raise RuntimeError(f"Recovery conflict: {name} changed after interrupted transaction; preserve and reconcile it")
        for name in ("queue.txt", "done.txt"):
            atomic_text(self.root / name, tx[name]["after"])
        self.data["pending_transaction"] = None
        self.save()

    def start_run(self, count=1, run_id=None):
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("count must be a positive integer")
        run_id = run_id or uuid.uuid4().hex
        if run_id not in self.data["runs"]:
            self.data["runs"][run_id] = {"started_at": now(), "limit": min(count, 5), "attempts": [], "inspected": [], "finished_at": None}
            self.save()
        elif self.data["runs"][run_id]["limit"] != min(count, 5):
            raise ValueError("Cannot change the attempt cap of an existing run")
        return run_id

    def finish_run(self, run_id):
        self.data["runs"][run_id]["finished_at"] = now()
        self.save()

    def jobs_for(self, pair_key):
        return [job for job in self.data["jobs"] if job["pair_key"] == pair_key]

    def get_job(self, job_id):
        return next(job for job in self.data["jobs"] if job["job_id"] == job_id)

    def remaining(self, run_id):
        run = self.data["runs"][run_id]
        return max(0, run["limit"] - len(run["attempts"]))

    def note_inspected(self, run_id, marker):
        run = self.data["runs"][run_id]
        if marker in run["inspected"]:
            return False
        run["inspected"].append(marker)
        self.save()
        return True

    def skip(self, pair_key, original, reason, candidate_id=None, source="queue", evidence=None):
        record = {"pair_key": pair_key, "original": original, "reason": reason, "candidate_id": candidate_id, "source": source, "status": "SKIPPED", "at": now()}
        if evidence:
            record["evidence"] = evidence
        if not any(x["pair_key"] == pair_key and x["original"] == original and x["reason"] == reason for x in self.data["skipped"]):
            self.data["skipped"].append(record)
            self.save()
        return record

    def reserve(self, run_id, candidate, pair_key, source, original=None, remake=False, resume_job_id=None):
        run = self.data["runs"][run_id]
        if run.get("finished_at"):
            raise RuntimeError("Run already finished")
        if not self.remaining(run_id):
            raise RuntimeError("Production attempt cap reached")
        already = {self.get_job(jid)["pair_key"] for jid in run["attempts"]}
        if pair_key in already:
            raise RuntimeError("This pair has already been attempted in this run")
        existing = self.jobs_for(pair_key)
        resumable = [j for j in existing if j["status"] in {"FAILED", "NEEDS_REVIEW"}]
        active = [j for j in existing if j["status"] == "IN_PROGRESS"]
        completed = [j for j in existing if j["status"] == "COMPLETE"]
        if resume_job_id:
            job = self.get_job(resume_job_id)
            if job["pair_key"] != pair_key or job["status"] == "COMPLETE":
                raise ValueError("Only the matching unfinished job can be resumed")
            if job["status"] == "IN_PROGRESS" and not self.data["runs"].get(job.get("run_id"), {}).get("finished_at"):
                raise RuntimeError("Existing job is active; finish/interruption-record its prior run before resuming")
        elif active:
            previous = active[-1]
            prior_run = self.data["runs"].get(previous.get("run_id"), {})
            if prior_run.get("finished_at"):
                job = previous
            else:
                raise RuntimeError("Existing job is IN_PROGRESS; do not duplicate it")
        elif resumable:
            job = resumable[-1]
        else:
            if completed and not remake:
                raise RuntimeError("Pair already COMPLETE; an explicit remake request is required")
            episodes = self.root / "episodes"
            episodes.mkdir(exist_ok=True)
            numbers = [int(p.name.split("_", 1)[0]) for p in episodes.iterdir() if p.is_dir() and p.name.split("_", 1)[0].isdigit()]
            numbers += [j.get("number", 0) for j in self.data["jobs"]]
            number = max(numbers, default=0) + 1
            dirname = f"{number:03d}_{safe_component(candidate['a']['name'])}_vs_{safe_component(candidate['b']['name'])}"
            episode_dir = episodes / dirname
            episode_dir.mkdir()
            job = {"job_id": uuid.uuid4().hex, "number": number, "pair_key": pair_key, "candidate_id": candidate["id"], "names": [candidate["a"]["name"], candidate["b"]["name"]], "person_ids": [candidate["a"]["id"], candidate["b"]["id"]], "episode_path": str((episode_dir / "episode.json").relative_to(self.root)), "source": source, "original": original, "created_at": now(), "history": [], "remake_of": completed[-1]["job_id"] if completed else None}
            self.data["jobs"].append(job)
        job["status"] = "IN_PROGRESS"
        job["run_id"] = run_id
        job["updated_at"] = now()
        job["history"].append({"status": "IN_PROGRESS", "at": now(), "run_id": run_id, "action": "resume" if job.get("history") else "start"})
        job["attempt_count"] = job.get("attempt_count", 0) + 1
        run["attempts"].append(job["job_id"])
        self.save()
        return dict(job)

    def import_job(self, record):
        if any(j["job_id"] == record["job_id"] or j["episode_path"] == record["episode_path"] for j in self.data["jobs"]):
            return
        self.data["jobs"].append(record)
        self.save()

    def set_status(self, job_id, status, reason=""):
        if status not in STATUSES or status == "IN_PROGRESS":
            raise ValueError("Use reserve/resume to enter IN_PROGRESS")
        job = self.get_job(job_id)
        if job["status"] == "COMPLETE" and status != "COMPLETE":
            raise ValueError("A completed job is immutable; use explicit remake")
        if status == "COMPLETE":
            from check_episode import validate_completion
            episode_path = self.root / job["episode_path"]
            manifest = load_json(episode_path.parent / "manifest.json", {})
            if manifest.get("job_id") != job_id:
                raise ValueError("Completion gate rejected: working manifest job_id does not match the ledger job")
            errors = validate_completion(episode_path)
            if errors:
                raise ValueError("Completion gate rejected: " + "; ".join(errors))
        job["status"] = status
        job["updated_at"] = now()
        job["history"].append({"status": status, "at": now(), "reason": reason})
        if status != "COMPLETE":
            self.save()
            return
        queue_before = read_text(self.root / "queue.txt")
        done_before = read_text(self.root / "done.txt")
        def matches(line):
            if self.pair_key_resolver:
                try:
                    return self.pair_key_resolver(line) == job["pair_key"]
                except ValueError:
                    return False
            return line.strip() == (job.get("original") or " vs ".join(job["names"])).strip()
        kept = [line for line in queue_before.splitlines() if not matches(line)]
        done = done_before.splitlines()
        if not any(matches(line) for line in done):
            done.append(" vs ".join(job["names"]))
        queue_after = "\n".join(kept) + ("\n" if kept else "")
        done_after = "\n".join(done) + ("\n" if done else "")
        self.data["pending_transaction"] = {"job_id": job_id, "queue.txt": {"before_hash": digest_text(queue_before), "after": queue_after}, "done.txt": {"before_hash": digest_text(done_before), "after": done_after}}
        self.save()  # Durable COMPLETE + intended files before mutating either list.
        self.recover()
