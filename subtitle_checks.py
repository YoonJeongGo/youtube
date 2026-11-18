"""Read-only SRT/timeline consistency and actual burned-caption review gates.

Parsing an SRT does not prove that captions are visible in the MP4. Completion
also requires observations of current decoded frames for every displayed cue.
"""
from __future__ import annotations

import re
from pathlib import Path

from episode_core import load_json, nfc, safe_child, sha256_file


def _milliseconds(timestamp):
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})", timestamp)
    if not match:
        raise ValueError("Invalid SRT timestamp: " + timestamp)
    h, m, s, ms = map(int, match.groups())
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def parse_srt(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("SRT is empty")
    entries, previous_end = [], -1
    for expected_index, block in enumerate(re.split(r"\n[ \t]*\n", text), 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0].strip() != str(expected_index):
            raise ValueError("SRT cue indices must be consecutive and each cue needs text")
        timing = re.fullmatch(r"([^ ]+) --> ([^ ]+)", lines[1].strip())
        if not timing:
            raise ValueError("Invalid SRT cue timing")
        start, end = map(_milliseconds, timing.groups())
        if start < previous_end or end <= start:
            raise ValueError("SRT cues overlap or have nonpositive durations")
        previous_end = end
        entries.append({"index": expected_index, "start_ms": start, "end_ms": end,
                        "text": nfc("\n".join(lines[2:]))})
    return entries


def expected_cues(timeline):
    result = []
    for index, segment in enumerate(timeline.get("segments", []), 1):
        # Silent mode has a reading interval, not a fictitious speech boundary.
        start = segment["start"] if segment.get("speech_start") is None else segment["speech_start"]
        result.append({"id": segment["id"], "index": index,
                       "start_ms": round(start * 1000), "end_ms": round(segment["end"] * 1000),
                       "text": nfc(segment["display_text"])})
    return result


def validate_subtitles(base, timeline, errors, *, require_visual=True):
    base = Path(base)
    try:
        cues = parse_srt((base / "out/captions.srt").read_text(encoding="utf-8-sig"))
        expected = expected_cues(timeline)
        if not expected or len(cues) != len(expected):
            errors.append("subtitles: SRT cue count differs from the current timeline")
        for actual, wanted in zip(cues, expected):
            if any(actual.get(key) != wanted[key] for key in ("index", "start_ms", "end_ms", "text")):
                errors.append("subtitles: SRT text/timing differs from current timeline: " + wanted["id"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append("subtitles: missing/invalid SRT or timeline: " + str(exc))
        return
    if not require_visual:
        return
    try:
        review = load_json(base / "subtitle_review.json")
        frames = load_json(base / "out/review_frames.json")
        if not isinstance(review, dict) or not isinstance(frames, dict):
            raise ValueError("Subtitle review and frame index must be objects")
        if review.get("status") != "PASS" or review.get("burned_in") is not True:
            errors.append("subtitles: actual burned-in caption review has not passed")
        if not review.get("reviewed_at") or not review.get("observations"):
            errors.append("subtitles: review time and specific visual observations required")
        for file, key in (("out/video.mp4", "video_sha256"), ("timeline.json", "timeline_sha256"),
                          ("out/captions.srt", "captions_sha256"), ("out/review_frames.json", "review_frames_sha256")):
            if sha256_file(base / file) != review.get(key):
                errors.append("subtitles: stale visual-review hash: " + file)
        if frames.get("video_sha256") != review.get("video_sha256"):
            errors.append("subtitles: decoded frames belong to a different video")
        indexed_frames = {entry["file"]: entry for entry in frames.get("frames", [])}
        cue_reviews = review.get("cues", [])
        if not isinstance(cue_reviews, list):
            raise ValueError("subtitle_review.cues must be a list")
        indexed = {}
        for cue in cue_reviews:
            if not isinstance(cue, dict) or cue.get("id") in indexed:
                raise ValueError("Subtitle review contains an invalid or duplicate cue")
            indexed[cue.get("id")] = cue
        if set(indexed) != {entry["id"] for entry in expected}:
            errors.append("subtitles: every current cue needs exactly one actual frame observation")
        for entry in expected:
            observed = indexed.get(entry["id"], {})
            if not observed.get("observations") or any(observed.get(key) is not True for key in
                                                       ("text_matches", "readable", "not_clipped")):
                errors.append("subtitles: unconfirmed wording/readability/clipping: " + entry["id"])
            file = observed.get("frame_file")
            actual_frame = indexed_frames.get(file)
            if not actual_frame:
                errors.append("subtitles: cue is not linked to an extracted MP4 frame: " + entry["id"])
                continue
            frame_ms = round(actual_frame["time"] * 1000)
            if not entry["start_ms"] <= frame_ms < entry["end_ms"]:
                errors.append("subtitles: observed frame is outside its caption interval: " + entry["id"])
            frame_hash = sha256_file(safe_child(base / "out", file))
            if frame_hash != actual_frame.get("sha256") or frame_hash != observed.get("frame_sha256"):
                errors.append("subtitles: observed caption frame changed: " + entry["id"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append("subtitles: missing/invalid visual evidence: " + str(exc))
