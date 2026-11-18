import copy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from episode_core import atomic_json, sha256_file
from subtitle_checks import parse_srt, validate_subtitles


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base / "out/review_fixture").mkdir(parents=True)
        self.timeline = {"segments": [
            {"id": "hook", "start": 0.0, "end": 2.0, "speech_start": .08,
             "display_text": "누가 유리할까요?"},
            {"id": "reason", "start": 2.0, "end": 4.0, "speech_start": 2.0,
             "display_text": "조건마다 달라져요."}]}
        atomic_json(self.base / "timeline.json", self.timeline)
        self.srt = "1\n00:00:00,080 --> 00:00:02,000\n누가 유리할까요?\n\n2\n00:00:02,000 --> 00:00:04,000\n조건마다 달라져요.\n"
        (self.base / "out/captions.srt").write_text(self.srt, encoding="utf-8")
        # All evidence is fixture data; no real visual PASS is claimed by tests.
        (self.base / "out/video.mp4").write_bytes(b"synthetic fixture")
        frames = []
        cue_reviews = []
        for index, segment in enumerate(self.timeline["segments"], 1):
            relative = f"review_fixture/frame_{index:04d}.png"
            path = self.base / "out" / relative
            Image.new("RGB", (1080, 1920), "black").save(path)
            frames.append({"file": relative, "sha256": sha256_file(path), "time": index * 2 - 1})
            cue_reviews.append({"id": segment["id"], "frame_file": relative,
                                "frame_sha256": sha256_file(path), "observations": "Fixture-only observation",
                                "text_matches": True, "readable": True, "not_clipped": True})
        atomic_json(self.base / "out/review_frames.json", {"video_sha256": sha256_file(self.base / "out/video.mp4"), "frames": frames})
        self.review = {"schema_version": 1, "status": "PASS", "burned_in": True,
                       "reviewed_at": "fixture", "observations": "Fixture-only caption review", "cues": cue_reviews,
                       "video_sha256": sha256_file(self.base / "out/video.mp4"),
                       "timeline_sha256": sha256_file(self.base / "timeline.json"),
                       "captions_sha256": sha256_file(self.base / "out/captions.srt"),
                       "review_frames_sha256": sha256_file(self.base / "out/review_frames.json")}
        atomic_json(self.base / "subtitle_review.json", self.review)

    def tearDown(self):
        self.temp.cleanup()

    def errors(self, **kwargs):
        errors = []
        validate_subtitles(self.base, self.timeline, errors, **kwargs)
        return errors

    def test_current_srt_and_caption_observations_match(self):
        self.assertEqual([], self.errors())

    def test_missing_srt_never_passes_from_video_review_alone(self):
        (self.base / "out/captions.srt").unlink()
        self.assertTrue(any("missing/invalid SRT" in e for e in self.errors()))

    def test_text_and_time_changes_are_detected_independently_of_hash(self):
        changed = self.srt.replace("조건마다 달라져요.", "한 명이 확실히 이겨요.").replace("00:00:00,080", "00:00:00,000")
        (self.base / "out/captions.srt").write_text(changed, encoding="utf-8")
        self.review["captions_sha256"] = sha256_file(self.base / "out/captions.srt")
        atomic_json(self.base / "subtitle_review.json", self.review)
        errors = self.errors()
        self.assertEqual(2, sum("text/timing" in e for e in errors))

    def test_visual_unreviewed_and_wrong_frame_block_completion(self):
        self.review["status"] = "UNREVIEWED"
        self.review["cues"][0]["frame_file"] = self.review["cues"][1]["frame_file"]
        atomic_json(self.base / "subtitle_review.json", self.review)
        errors = self.errors()
        self.assertTrue(any("has not passed" in e for e in errors))
        self.assertTrue(any("outside its caption interval" in e for e in errors))

    def test_stale_video_or_missing_cue_cannot_reuse_caption_pass(self):
        (self.base / "out/video.mp4").write_bytes(b"changed video")
        self.review["cues"].pop()
        atomic_json(self.base / "subtitle_review.json", self.review)
        errors = self.errors()
        self.assertTrue(any("stale visual-review hash" in e for e in errors))
        self.assertTrue(any("every current cue" in e for e in errors))

    def test_silent_reading_interval_requires_no_invented_speech(self):
        timeline = copy.deepcopy(self.timeline)
        timeline["segments"][0]["speech_start"] = None
        self.timeline = timeline
        (self.base / "out/captions.srt").write_text(self.srt.replace("00:00:00,080", "00:00:00,000"), encoding="utf-8")
        self.assertEqual([], self.errors(require_visual=False))

    def test_parser_rejects_overlap_and_missing_cue_index(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            parse_srt(self.srt.replace("00:00:02,000 --> 00:00:04,000", "00:00:01,900 --> 00:00:04,000"))
        with self.assertRaisesRegex(ValueError, "indices"):
            parse_srt(self.srt.replace("\n\n2\n", "\n\n3\n"))


if __name__ == "__main__":
    unittest.main()
