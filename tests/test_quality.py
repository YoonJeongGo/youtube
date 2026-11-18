import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_episode
from check_episode import check_images, check_text, validate_completion, validate_image_reviews, validate_quality, validate_sources
from deliver_episode import deliver, sanitize_title, select_target
from episode_core import IMAGE_NAMES, atomic_json, script_hash, sha256_file, speech_segments


def config():
    result = {"schema_version": 2, "hook": "거리가 바뀌면 어떨까요?", "thumb": "thumb.png", "winner": "none", "reason": "거리에 따라 강점이 달라져요.", "question": "어떤 조건을 고르실까요?", "clash_say": "둘이 맞붙으면 어떨까요?"}
    for who, other in (("a", "b"), ("b", "a")):
        result[who] = {"name": who, "tag": "옛 이야기 인물", "image": f"{who}.png", "intro_images": [f"{who}_1.png", f"{who}_2.png"], "intro": ["소설 속 인물이에요.", "소설에서는 활을 쏘아요.", "소설에서는 칼을 써요."], "edge": {"title": "거리가 가까우면", "image": f"{who}_atk.png", "reaction_image": f"{other}_react.png", "lines": ["가까우면 강점을 살릴 수 있어요.", "소설에서는 칼을 썼어요."]}}
    return result


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / "episodes/001_test"
        self.base.mkdir(parents=True)
        self.cfg = config()
        self.path = self.base / "episode.json"
        atomic_json(self.path, self.cfg)

    def tearDown(self):
        self.temp.cleanup()

    def test_text_reaction_required_and_no_legacy_fallback(self):
        errors = []
        check_text(self.cfg, errors)
        self.assertEqual(errors, [])
        del self.cfg["a"]["edge"]["reaction_image"]
        self.cfg["schema_version"] = 1
        check_text(self.cfg, errors)
        self.assertTrue(any("reaction_image" in e for e in errors))
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_yo_question_and_explicit_winner_required(self):
        self.cfg["hook"] = "둘이 붙는다면?"
        self.cfg["question"] = "어떤 조건을 고르실까요."
        del self.cfg["winner"]
        errors = []
        check_text(self.cfg, errors)
        self.assertTrue(any("hook" in e for e in errors))
        self.assertTrue(any("question" in e for e in errors))
        self.assertTrue(any("winner" in e for e in errors))

    def test_nfc_character_count(self):
        import unicodedata
        self.cfg["hook"] = unicodedata.normalize("NFD", "거리가 바뀌면 어떨까요?")
        errors = []
        check_text(self.cfg, errors)
        self.assertEqual(errors, [])

    def test_pixel_duplicate_even_different_file_metadata(self):
        from PIL.PngImagePlugin import PngInfo
        image = Image.new("RGB", (1024, 1536), "red")
        image.save(self.base / "a_1.png")
        metadata = PngInfo()
        metadata.add_text("changed", "different metadata same pixels")
        image.save(self.base / "b_1.png", pnginfo=metadata)
        Image.new("RGB", (1000, 1000), "blue").save(self.base / "a.png")
        errors = []
        check_images(self.cfg, self.base, errors)
        self.assertTrue(any("동일 픽셀 복제" in e for e in errors))
        self.assertTrue(any("필수 크기" in e for e in errors))
        self.assertTrue(any("b_react.png" in e for e in errors))

    def source_fixture(self):
        records, lines = [], []
        for segment in speech_segments(self.cfg):
            if segment["id"] == "cta":
                continue
            record = {"id": segment["id"], "text": segment["spoken_text"], "kind": "hypothetical", "work": "Test fixture", "locator": "fixture", "url": "https://example.invalid/fixture", "checked_at": "2026-11-16", "summary": "Not production evidence"}
            records.append(record)
            lines.append(f"{record['id']} | {record['text']} | {record['url']}")
        atomic_json(self.base / "source_records.json", {"records": records})
        (self.base / "sources.md").write_text("\n".join(lines), encoding="utf-8")
        return {"script": {"status": "PASS", "observations": "Test-only observations", "script_hash": script_hash(self.cfg), "sources_sha256": sha256_file(self.base / "sources.md"), "source_records_sha256": sha256_file(self.base / "source_records.json")}}

    def test_source_change_invalidates_script_signoff(self):
        signoffs = self.source_fixture()
        errors = []
        validate_sources(self.cfg, self.base, signoffs, errors)
        self.assertEqual(errors, [])
        with (self.base / "sources.md").open("a", encoding="utf-8") as file:
            file.write("Changed evidence")
        validate_sources(self.cfg, self.base, signoffs, errors)
        self.assertTrue(any("stale" in e for e in errors))

    def test_changed_sentence_does_not_reuse_old_source_record(self):
        signoffs = self.source_fixture()
        self.cfg["a"]["intro"][1] = "소설에서는 창을 써요."
        errors = []
        validate_sources(self.cfg, self.base, signoffs, errors)
        self.assertTrue(any("source record missing/stale" in e for e in errors))

    def image_review_fixture(self):
        for name in IMAGE_NAMES:
            (self.base / name).write_bytes(name.encode())  # review-hash fixture, not production image
        atomic_json(self.base / "character_bible.json", {"test": True})
        atomic_json(self.base / "storyboard.json", {"test": True})
        refs = {name: sha256_file(self.base / name) for name in IMAGE_NAMES}
        records = [{"file": name, "sha256": refs[name], "status": "PASS", "generation_version": 1, "reviewed_at": "fixture", "full_observations": "Fixture body observation", "detail_observations": "Fixture hand observation", "issues": [], "reference_hashes": refs, "character_bible_sha256": sha256_file(self.base / "character_bible.json"), "storyboard_sha256": sha256_file(self.base / "storyboard.json")} for name in IMAGE_NAMES]
        atomic_json(self.base / "image_review.json", {"images": records})

    def test_portrait_change_invalidates_downstream_image_reviews(self):
        self.image_review_fixture()
        errors = []
        validate_image_reviews(self.base, errors)
        self.assertEqual(errors, [])
        (self.base / "a.png").write_bytes(b"new reference")
        validate_image_reviews(self.base, errors)
        self.assertTrue(any("a_1.png reference a.png" in e for e in errors))
        self.assertTrue(any("b_react.png reference a.png" in e for e in errors))

    def test_unseen_or_unresolved_image_cannot_pass(self):
        self.image_review_fixture()
        data = json.loads((self.base / "image_review.json").read_text(encoding="utf-8"))
        data["images"][0]["status"] = "UNREVIEWED"
        data["images"][1]["issues"] = ["Unresolved hand"]
        atomic_json(self.base / "image_review.json", data)
        errors = []
        validate_image_reviews(self.base, errors)
        self.assertTrue(any("UNREVIEWED" in e for e in errors))
        self.assertTrue(any("unresolved image" in e for e in errors))

    def test_audio_file_and_input_changes_block_quality(self):
        atomic_json(self.base / "timeline.json", {"audio_mode": "voice", "input_hash": "new-input", "pcm_path": "voice.wav"})
        (self.base / "voice.wav").write_bytes(b"new audio")
        atomic_json(self.base / "audio_review.json", {"status": "PASS", "listened": True, "observations": "Fixture", "input_hash": "old-input", "pcm_sha256": "old-audio"})
        with patch("check_episode.validate_episode", return_value=[]), patch("check_episode.validate_planning"), patch("check_episode.validate_sources"), patch("check_episode.validate_image_reviews"), patch("check_episode.validate_video_specs"), patch("make_voice.validate_voice", return_value=[]), patch("make_vs.validate_timeline", return_value=[]):
            errors = validate_quality(self.path)
        self.assertTrue(any("audio review: stale input hash" in e for e in errors))
        self.assertTrue(any("audio review PCM: stale" in e for e in errors))

    def test_bgm_change_invalidates_rendered_video(self):
        self.cfg["bgm"] = "licensed_music.wav"
        self.cfg["bgm_rights"] = {"verified": True, "source": "fixture", "license": "fixture"}
        atomic_json(self.path, self.cfg)
        (self.base / "licensed_music.wav").write_bytes(b"new background music")
        (self.base / "out").mkdir()
        atomic_json(self.base / "out/technical_qa.json", {"bgm_sha256": "old-background-music"})
        atomic_json(self.base / "timeline.json", {"audio_mode": "voice", "input_hash": "fixture", "pcm_path": "voice.wav"})
        with patch("check_episode.validate_episode", return_value=[]), patch("check_episode.validate_planning"), patch("check_episode.validate_sources"), patch("check_episode.validate_image_reviews"), patch("check_episode.validate_video_specs"), patch("make_voice.validate_voice", return_value=[]), patch("make_vs.validate_timeline", return_value=[]):
            errors = validate_quality(self.path)
        self.assertTrue(any("rendered BGM: stale" in e for e in errors))

    def test_no_delivery_prevents_complete_even_if_quality_passed(self):
        atomic_json(self.base / "manifest.json", {"job_id": "test-job", "status": "NEEDS_REVIEW"})
        with patch("check_episode.validate_quality", return_value=[]):
            errors = validate_completion(self.path)
        self.assertTrue(any("delivery" in e.lower() for e in errors))

    def test_safe_unicode_titles_reserved_names_and_collision(self):
        self.assertEqual(sanitize_title("여포: 척준경? "), "여포_ 척준경_")
        self.assertEqual(sanitize_title("CON"), "_CON")
        parent = self.root / "검수대기"
        first = select_target(parent, "한글 제목", "job-1")
        first.mkdir()
        atomic_json(first / "manifest.json", {"job_id": "job-1"})
        self.assertEqual(select_target(parent, "수정한 제목", "job-1"), first)
        self.assertEqual(select_target(parent, "한글 제목", "job-2").name, "한글 제목 (2)")

    def test_unreviewed_delivery_stays_pending_and_reuses_job(self):
        for name in ("AGENTS.md", "plan_matchups.py", "make_vs.py"):
            (self.root / name).write_text("fixture", encoding="utf-8")
        atomic_json(self.base / "manifest.json", {"job_id": "test-job"})
        for name in ("title.txt", "description.txt", "pinned_comment.txt", "sources.md", "qa_report.md"):
            (self.base / name).write_text("시험 제목" if name == "title.txt" else "fixture", encoding="utf-8")
        (self.base / "thumb.png").write_bytes(b"fixture thumbnail")
        (self.base / "out").mkdir()
        (self.base / "out/video.mp4").write_bytes(b"not a real MP4: fixture")
        with patch("deliver_episode.validate_quality", return_value=["AUDIO_REVIEW_REQUIRED"]):
            first = deliver(self.path, self.root, update_state=False)
            second = deliver(self.path, self.root, update_state=False)
        self.assertEqual(first["status"], "NEEDS_REVIEW")
        self.assertEqual(first["delivery_path"], second["delivery_path"])
        self.assertEqual(Path(first["delivery_path"]).parent.name, "검수대기")
        self.assertFalse((self.root / "완성본").exists())
        data = json.loads((self.base / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(data["delivery"]["verified"])
        self.assertEqual(data["status"], "NEEDS_REVIEW")

    def complete_delivery_fixture(self):
        for name in ("AGENTS.md", "plan_matchups.py", "make_vs.py"):
            (self.root / name).write_text("fixture", encoding="utf-8")
        atomic_json(self.base / "manifest.json", {"job_id": "test-job"})
        for name in ("title.txt", "description.txt", "pinned_comment.txt", "sources.md", "qa_report.md"):
            (self.base / name).write_text("시험 제목" if name == "title.txt" else "fixture", encoding="utf-8")
        (self.base / "thumb.png").write_bytes(b"fixture thumbnail")
        (self.base / "out/review_abc123").mkdir(parents=True)
        (self.base / "out/video.mp4").write_bytes(b"fixture video")
        frame = self.base / "out/review_abc123/frame_0001.png"
        Image.new("RGB", (1080, 1920), "red").save(frame)
        Image.new("RGB", (300, 600), "blue").save(self.base / "out/_review.png")
        atomic_json(self.base / "out/review_frames.json", {"frames": [{"file": "review_abc123/frame_0001.png", "sha256": sha256_file(frame)}]})
        # Isolated delivery fixture: semantic/visual checks are mocked by these
        # copy/rollback tests, so required evidence files are explicitly dummy data.
        for relative in check_episode.DELIVERY_SOURCES.values():
            target = self.base / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture evidence", encoding="utf-8")

    def test_desktop_pending_delivery_keeps_exact_title_and_pending_status(self):
        self.complete_delivery_fixture()
        with patch("deliver_episode.validate_quality", return_value=["AUDIO_REVIEW_REQUIRED"]):
            first = deliver(self.path, update_state=False, desktop_root=self.root)
            second = deliver(self.path, update_state=False, desktop_root=self.root)
        target = self.root / "시험 제목"
        self.assertEqual(Path(first["delivery_path"]), target)
        self.assertEqual(second["delivery_path"], first["delivery_path"])
        self.assertEqual(first["status"], "NEEDS_REVIEW")
        self.assertFalse((self.root / "검수대기").exists())
        self.assertEqual(json.loads((target / "manifest.json").read_text(encoding="utf-8"))["status"], "NEEDS_REVIEW")
        self.assertEqual(json.loads((self.base / "manifest.json").read_text(encoding="utf-8"))["delivery"]["layout"], "desktop_title")

    def test_desktop_failed_completion_keeps_title_without_promoting_status(self):
        self.complete_delivery_fixture()
        with patch("deliver_episode.validate_quality", return_value=[]), patch("deliver_episode.validate_completion", return_value=["review changed"]):
            result = deliver(self.path, update_state=False, desktop_root=self.root)
        target = self.root / "시험 제목"
        self.assertEqual(Path(result["delivery_path"]), target)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(json.loads((target / "manifest.json").read_text(encoding="utf-8"))["status"], "NEEDS_REVIEW")
        self.assertFalse((self.root / "검수대기").exists())

    def test_delivery_copies_indexed_native_frames_and_validates_current_sources(self):
        self.complete_delivery_fixture()
        with patch("deliver_episode.validate_quality", return_value=[]), patch("check_episode.validate_quality", return_value=[]):
            result = deliver(self.path, self.root, update_state=False)
            self.assertEqual(result["status"], "COMPLETE")
            target = Path(result["delivery_path"])
            self.assertTrue((target / "review/review_abc123/frame_0001.png").is_file())
            self.assertTrue((target / "captions.srt").is_file())
            self.assertTrue((target / "review/subtitle_review.json").is_file())
            self.assertTrue((target / "review/timeline.json").is_file())
            self.assertEqual(validate_completion(self.path), [])
            (self.base / "out/video.mp4").write_bytes(b"new video after delivery")
            errors = validate_completion(self.path)
            self.assertTrue(any("delivery current source video.mp4" in e for e in errors))

    def test_contact_sheet_alone_never_counts_as_full_review_delivery(self):
        self.complete_delivery_fixture()
        atomic_json(self.base / "out/review_frames.json", {"frames": []})
        with patch("deliver_episode.validate_quality", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "required files/review frames"):
                deliver(self.path, self.root, update_state=False)

    def test_delivery_rejects_changed_indexed_frame(self):
        self.complete_delivery_fixture()
        (self.base / "out/review_abc123/frame_0001.png").write_bytes(b"changed")
        with patch("deliver_episode.validate_quality", return_value=["changed frame"]):
            with self.assertRaisesRegex(RuntimeError, "frame changed"):
                deliver(self.path, self.root, update_state=False)

    def test_optional_audio_evidence_copies_only_present_files_and_checks_current_hash(self):
        self.complete_delivery_fixture()
        atomic_json(self.base / "audio_analysis.json", {"transcript": "fixture only", "listened": False})
        atomic_json(self.base / "audio_model_observation.json", {"status": "FAILED", "reason": "fixture"})
        with patch("deliver_episode.validate_quality", return_value=[]), patch("check_episode.validate_quality", return_value=[]):
            result = deliver(self.path, self.root, update_state=False)
            target = Path(result["delivery_path"])
            self.assertEqual("COMPLETE", result["status"])
            self.assertTrue((target / "review/audio_analysis.json").is_file())
            self.assertTrue((target / "review/audio_model_observation.json").is_file())
            self.assertFalse((target / "review/audio_analysis_medium.json").exists())
            self.assertFalse((target / "review/audio_capability_probe.json").exists())
            self.assertEqual([], result["missing_files"])
            self.assertEqual([], validate_completion(self.path))
            atomic_json(self.base / "audio_analysis.json", {"transcript": "changed analysis", "listened": False})
            self.assertTrue(any("delivery current optional review/audio_analysis.json" in e
                                for e in validate_completion(self.path)))

    def test_optional_audio_evidence_absence_never_blocks_completion(self):
        self.complete_delivery_fixture()
        with patch("deliver_episode.validate_quality", return_value=[]), patch("check_episode.validate_quality", return_value=[]):
            result = deliver(self.path, self.root, update_state=False)
            self.assertEqual("COMPLETE", result["status"])
            self.assertEqual([], validate_completion(self.path))

    def test_optional_delivered_audio_evidence_tampering_is_detected(self):
        self.complete_delivery_fixture()
        atomic_json(self.base / "audio_capability_probe.json", {"actual_listening_available": False})
        with patch("deliver_episode.validate_quality", return_value=[]), patch("check_episode.validate_quality", return_value=[]):
            result = deliver(self.path, self.root, update_state=False)
            target = Path(result["delivery_path"])
            atomic_json(target / "review/audio_capability_probe.json", {"tampered": True})
            self.assertTrue(any("delivered optional review/audio_capability_probe.json" in e
                                for e in validate_completion(self.path)))


if __name__ == "__main__":
    unittest.main()
