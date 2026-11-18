"""Setup-profile structure/evidence regressions, never production approvals."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import unicodedata

from PIL import Image

from asset_tools import image_target
from check_episode import check_images, check_text, validate_image_reviews, validate_video_specs
from episode_core import IMAGE_NAMES, atomic_json, image_names, narration_groups, sha256_file, speech_segments


def setup_config():
    cfg = {
        "schema_version": 2, "production_profile": "setup_story_v3", "winner": "none",
        "thumb": "thumb.png", "hook": "분신 앞에서 활이 통할까요?",
        "reason": "숨기기와 조준하기의 대결.", "question": "누가 먼저 빈틈을 찾을까요?",
        "setup": {"title": "창고와 길목", "image": "scene.png",
                  "lines": ["창고를 털고 달아난다고 해 보죠.", "길목의 궁수가 막을 수 있을까요?"]},
    }
    for who in ("a", "b"):
        cfg[who] = {
            "name": who, "tag": "가상 검증 인물", "image": f"{who}.png",
            "intro_images": [f"{who}_1.png", f"{who}_2.png"],
            "intro": ["소설 속 인물입니다.", "이 대결에 쓸 기술이 있죠."],
            "edge": {"title": "조건을 바꾸면", "image": f"{who}_atk.png",
                     "lines": ["조건에 따라 조준이 어렵습니다.", "소설 속 장면이 그 근거죠."]},
        }
    cfg["b"]["edge"]["lines"] = ["하지만 본체가 보이면 조준할 수 있습니다.", "표적 명중의 기록이 그 근거입니다."]
    return cfg


def errors_for(cfg):
    errors = []
    check_text(cfg, errors)
    return errors


class SetupProfileValidationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = setup_config()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def invalid(self, cfg, marker):
        errors = errors_for(cfg)
        self.assertTrue(any(marker in error for error in errors), errors)

    def test_valid_profile_core_sequence_default_groups_and_scene_target(self):
        self.assertEqual([], errors_for(self.cfg))
        segments = speech_segments(self.cfg)
        self.assertEqual(["hook", "a.intro.0", "a.intro.1", "b.intro.0", "b.intro.1", "setup.0", "setup.1",
                          "a.edge.0", "a.edge.1", "b.edge.0", "b.edge.1", "reason", "question", "cta"],
                         [item["id"] for item in segments])
        self.assertEqual(["scene.png", "scene.png"], [x["image"] for x in segments if x["id"].startswith("setup.")])
        self.assertEqual([x["id"] for x in segments], [key for group in narration_groups(self.cfg) for key in group["segment_ids"]])
        self.assertEqual(10, len(image_names(self.cfg)))
        self.assertEqual((1024, 1536), image_target("scene.png"))
        self.assertEqual((1024, 1536), image_target("a_react.png"))
        self.assertEqual((1024, 1024), image_target("a.png"))

    def test_unknown_profile_and_mixed_story_rejected(self):
        self.cfg["production_profile"] = "typo"
        self.invalid(self.cfg, "Unknown production_profile")
        self.cfg["production_profile"] = "setup_story_v3"
        self.cfg["story_beats"] = []
        self.invalid(self.cfg, "cannot be mixed")

    def test_exact_structure_and_metadata_required(self):
        for mutation, marker in (
            (lambda c: c["a"].update(intro=["한 문장입니다."]), "a.intro:"),
            (lambda c: c["b"]["edge"].update(lines=[]), "b.edge.lines:"),
            (lambda c: c["setup"].update(lines=[]), "setup.lines:"),
            (lambda c: c["setup"].update(image="a_1.png"), "setup.image:"),
            (lambda c: c["a"].update(intro_images=["a_2.png", "a_1.png"]), "a.intro_images:"),
            (lambda c: c["a"]["edge"].update(reaction_image="b_react.png"), "reaction_image:"),
            (lambda c: c.pop("winner"), "winner:"),
            (lambda c: c.update(stats=[]), "stats/cta"),
            (lambda c: c.update(cta="local"), "stats/cta"),
        ):
            with self.subTest(marker=marker):
                cfg = copy.deepcopy(self.cfg)
                mutation(cfg)
                self.invalid(cfg, marker)

    def test_limits_nfc_and_questions(self):
        self.cfg["a"]["intro"][0] = unicodedata.normalize("NFD", "가" * 44 + "입니다.")
        self.assertEqual([], errors_for(self.cfg))
        self.cfg["a"]["intro"][0] = "가" * 45 + "입니다."
        self.invalid(self.cfg, "a.intro.0: at most 48")
        for name in ("hook", "question"):
            cfg = setup_config()
            cfg[name] = "질문입니다."
            self.invalid(cfg, name + ": question must end")
        cfg = setup_config()
        cfg["setup"]["lines"][1] = "질문입니다."
        self.invalid(cfg, "setup.1: question must end")
        for where, limit in (("title", 22),):
            cfg = setup_config()
            cfg["setup"][where] = "가" * (limit + 1)
            self.invalid(cfg, "setup.title")

    def test_malformed_sentence_fails_without_crashing(self):
        for value in (None, [], "...", "한 문장. 두 문장.", "끝이 없으면"):
            with self.subTest(value=value):
                cfg = copy.deepcopy(self.cfg)
                cfg["a"]["intro"][0] = value
                self.invalid(cfg, "a.intro.0")

    def test_yo_ratio_counts_reason_and_excludes_shared_cta(self):
        # 13 authored sentences; six 요 endings pass, a seventh (reason) fails.
        self.cfg["a"]["intro"] = ["첫 기술이 있어요.", "다른 조건도 있나요?"]
        self.cfg["b"]["intro"][0] = "또 다른 인물이에요."
        self.assertEqual([], errors_for(self.cfg))
        self.cfg["reason"] = "조건이 중요해요."
        self.invalid(self.cfg, "요 ending ratio 7/13")
        cfg = setup_config()
        with patch("make_vs.CTA_SAY", "요. 요. 요. 요. 요. 요. 요. 요. 요. 요."):
            self.assertEqual([], errors_for(cfg))

    def test_same_ending_three_times_uses_playback_order_and_distinguishes_jo(self):
        self.cfg["a"]["intro"][1] = "기술을 사용합니다."
        self.invalid(self.cfg, "same ending repeated 3 times")
        self.cfg = setup_config()
        self.cfg["a"]["intro"] = ["기술이 있어요.", "다른 조건이 있죠."]
        self.cfg["b"]["intro"][0] = "또 다른 기술이에요."
        self.assertEqual([], errors_for(self.cfg))

    def test_explicit_groups_validate_continuity_ids_and_repetition(self):
        self.cfg["narration_groups"] = [{"id": "intro", "segment_ids": ["a.intro.0", "a.intro.1"]}]
        self.assertEqual([], errors_for(self.cfg))
        for members in (["a.intro.1", "a.intro.0"], ["a.intro.0", "b.intro.0"], ["unknown"], ["a.intro.0", "a.intro.0"]):
            with self.subTest(members=members):
                cfg = copy.deepcopy(self.cfg)
                cfg["narration_groups"][0]["segment_ids"] = members
                self.invalid(cfg, "narration group")
        self.cfg["narration_groups"][0]["id"] = "../bad"
        self.invalid(self.cfg, "safe narration group ID")

    def write_images(self):
        for index, name in enumerate(image_names(self.cfg)):
            Image.new("RGB", image_target(name), (index * 19, index * 13, index * 7)).save(self.base / name)

    def test_ten_images_pass_without_reactions_but_scene_is_mandatory(self):
        self.write_images()
        errors = []
        check_images(self.cfg, self.base, errors)
        self.assertEqual([], errors)
        (self.base / "scene.png").unlink()
        check_images(self.cfg, self.base, errors)
        self.assertTrue(any("scene.png" in e for e in errors), errors)
        self.assertFalse(any("react" in e for e in errors), errors)
        legacy_errors = []
        check_images({}, self.base, legacy_errors)
        self.assertTrue(any("a_react.png" in e for e in legacy_errors), legacy_errors)

    def test_exact_dimensions_and_duplicate_pixels_preserved(self):
        self.write_images()
        (self.base / "scene.png").write_bytes((self.base / "a_1.png").read_bytes())
        Image.new("RGB", (1000, 1500)).save(self.base / "b_atk.png")
        errors = []
        check_images(self.cfg, self.base, errors)
        self.assertTrue(any("동일 픽셀 복제" in e for e in errors), errors)
        self.assertTrue(any("필수 크기" in e for e in errors), errors)

    def review_fixture(self):
        for name in image_names(self.cfg):
            (self.base / name).write_bytes(name.encode())
        atomic_json(self.base / "character_bible.json", {"fixture": True})
        atomic_json(self.base / "storyboard.json", {"fixture": True})
        refs = {name: sha256_file(self.base / name) for name in image_names(self.cfg)}
        reviews = [{"file": name, "sha256": refs[name], "status": "PASS", "generation_version": 1,
                    "reviewed_at": "test-only", "full_observations": "fixture", "detail_observations": "fixture",
                    "issues": [], "reference_hashes": dict(refs),
                    "character_bible_sha256": sha256_file(self.base / "character_bible.json"),
                    "storyboard_sha256": sha256_file(self.base / "storyboard.json")}
                   for name in image_names(self.cfg)]
        atomic_json(self.base / "image_review.json", {"images": reviews})
        return reviews

    def test_scene_and_attack_require_both_current_portraits(self):
        reviews = self.review_fixture()
        errors = []
        validate_image_reviews(self.base, errors, self.cfg)
        self.assertEqual([], errors)
        for review in reviews:
            if review["file"] in ("scene.png", "a_atk.png", "b_atk.png"):
                del review["reference_hashes"]["b.png"]
        atomic_json(self.base / "image_review.json", {"images": reviews})
        validate_image_reviews(self.base, errors, self.cfg)
        for name in ("scene.png", "a_atk.png", "b_atk.png"):
            self.assertTrue(any(name + " reference b.png" in e for e in errors), errors)

    def test_video_duration_uses_profile_and_audio_requirements_remain(self):
        def probe(duration, audio=True):
            streams = [{"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "h264",
                        "pix_fmt": "yuv420p", "avg_frame_rate": "30/1"}]
            if audio:
                streams.append({"codec_type": "audio", "codec_name": "aac"})
            return SimpleNamespace(stdout=json.dumps({"streams": streams, "format": {"duration": str(duration)}}))
        for duration, expected in ((29.99, False), (30, True), (59.9, True), (60, True), (60.01, False)):
            with self.subTest(duration=duration), patch("check_episode.subprocess.run", return_value=probe(duration)):
                errors = []
                validate_video_specs(self.base / "video.mp4", "voice", errors, self.cfg)
                self.assertEqual(expected, not errors, errors)
        with patch("check_episode.subprocess.run", return_value=probe(50)):
            errors = []
            validate_video_specs(self.base / "video.mp4", "voice", errors)
            self.assertTrue(any("20~45" in e for e in errors), errors)
        with patch("check_episode.subprocess.run", return_value=probe(40, audio=False)):
            errors = []
            validate_video_specs(self.base / "video.mp4", "voice", errors, self.cfg)
            self.assertTrue(any("requires AAC" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
