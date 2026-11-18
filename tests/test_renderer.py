"""Renderer regressions; synthetic fixtures are never production assets."""
import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageChops

from episode_core import atomic_json, load_json, script_hash, sha256_file, speech_segments
from make_vs import (COL_A, CTA_SAY, Episode, extract_reviews, layout_text, photo_transition_frames, review_samples,
                     render_episode, settings_for, technical_checks, validate_timeline, wrap_pixels)


def fixture_cfg():
    cfg = {
        "schema_version": 2, "hook": "거리가 달라지면 누가 유리할까요?", "thumb": "thumb.png",
        "winner": "none", "clash_text": "둘이 붙는다면?", "clash_say": "둘이 맞붙으면 어떨까요?",
        "reason": "거리마다 강점이 달라져요.", "question": "어느 거리에서 맞붙게 할까요?", "bgm": "",
    }
    for who, other in (("a", "b"), ("b", "a")):
        cfg[who] = {"name": "시험인물" + who, "tag": "검증용 예시",
                    "image": who + ".png", "intro_images": [who + "_1.png", who + "_2.png"],
                    "intro": ["첫 번째 소개 문장이에요.", "두 번째 소개 문장이에요.", "세 번째 소개 문장이에요."],
                    "edge": {"title": "가정한 거리 조건", "image": who + "_atk.png",
                             "reaction_image": other + "_react.png",
                             "lines": ["공격하는 가상 장면이에요.", "대응하는 가상 장면이에요."]}}
    return cfg


def fixture_timeline(cfg, total_frames=None):
    segments = speech_segments(cfg)
    total_frames = total_frames or len(segments) * 40
    cursor = 0
    for index, segment in enumerate(segments):
        end = total_frames * (index + 1) // len(segments)
        segment.update(start_frame=cursor, end_frame=end, start=cursor / 30,
                       end=end / 30, speech_start=None, speech_end=None)
        cursor = end
    return {"schema_version": 2, "fps": 30, "audio_mode": "silent", "script_hash": script_hash(cfg),
            "segments": segments, "total_frames": cursor, "duration": cursor / 30}


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.cfg = fixture_cfg()
        self.timeline = fixture_timeline(self.cfg)
        names = {s["image"] for s in speech_segments(self.cfg) if s["image"]} | {"a.png", "b.png"}
        for i, name in enumerate(sorted(names)):
            size = (128, 128) if name in ("a.png", "b.png") else (128, 192)
            Image.new("RGB", size, (20 + i * 15, 40, 90)).save(self.base / name)

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_timeline_image_mapping_and_silent_mode(self):
        self.assertEqual([], validate_timeline(self.cfg, self.timeline, self.base, silent=True))
        errors = validate_timeline(self.cfg, self.timeline, self.base)
        self.assertTrue(any("--silent" in error for error in errors))
        self.assertTrue(any("PCM" in error for error in errors))
        intro = self.timeline["segments"][1:4]
        self.assertEqual(["a_1.png", "a_1.png", "a_2.png"], [s["image"] for s in intro])
        self.assertEqual("b_react.png", next(s for s in self.timeline["segments"] if s["id"] == "a.edge.1")["image"])

    def test_stale_script_and_missing_reaction_are_rejected(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["a"]["intro"][0] = "수정된 첫 문장이에요."
        errors = validate_timeline(cfg, self.timeline, self.base, silent=True)
        self.assertTrue(any("대본이 바뀌었어요" in error for error in errors))
        (self.base / "b_react.png").unlink()
        self.assertTrue(any("b_react.png" in error for error in
                            validate_timeline(self.cfg, self.timeline, self.base, silent=True)))

    def test_overlap_and_wrong_image_do_not_pass(self):
        invalid = copy.deepcopy(self.timeline)
        invalid["segments"][2]["start_frame"] -= 1
        invalid["segments"][3]["image"] = "a.png"
        errors = validate_timeline(self.cfg, invalid, self.base, silent=True)
        self.assertTrue(any("간격" in error for error in errors))
        self.assertTrue(any("연결" in error for error in errors))

    def test_no_space_text_fits_measured_stroke_bounds(self):
        canvas = Image.new("RGB", (1080, 1920))
        draw = ImageDraw.Draw(canvas)
        box = (72, 1555, 948, 1700)
        layout = layout_text(draw, "가나다라마바사아자차카타파하가나다라마바사아자차카타파하", box)
        self.assertGreater(len(layout), 1)
        for line in layout:
            left, top, right, bottom = line["bounds"]
            self.assertLessEqual(box[0], left)
            self.assertLessEqual(box[1], top)
            self.assertGreaterEqual(box[2], right)
            self.assertGreaterEqual(box[3], bottom)

    def test_korean_caption_keeps_words_and_shrinks_before_exceeding_two_lines(self):
        draw = ImageDraw.Draw(Image.new("RGB", (1080, 1920)))
        box = (72, 1555, 948, 1700)
        for text in ("분신들은 서로 구별하기 어려워요.",
                     "표적이 보이면 활을 겨누기 좋아요.",
                     "숨으면 도술, 보이면 조준이 빛나요."):
            with self.subTest(text=text):
                layout = layout_text(draw, text, box, max_lines=2)
                self.assertLessEqual(len(layout), 2)
                self.assertEqual(text.split(), [word for line in layout for word in line["text"].split()])
                self.assertFalse(any(line["text"] == "요." for line in layout))
                for line in layout:
                    left, top, right, bottom = line["bounds"]
                    self.assertTrue(box[0] <= left < right <= box[2])
                    self.assertTrue(box[1] <= top < bottom <= box[3])
        text = "하나둘셋 넷다섯 여섯일곱 여덟아홉"
        self.assertGreater(len(wrap_pixels(draw, text, 61, 430)), 2)
        smaller = layout_text(draw, text, (0, 0, 430, 400), 61, 30, max_lines=2)
        self.assertLessEqual(len(smaller), 2)
        self.assertLess(smaller[0]["size"], 61)

    def test_reaction_uses_attacker_condition_color_and_preserves_art(self):
        ep = Episode(self.cfg, self.base, settings_for())
        segment = next(s for s in self.timeline["segments"] if s["id"] == "a.edge.1")
        frame = ep.frame(segment, segment["end_frame"] - 1)
        self.assertEqual(COL_A, frame.getpixel((18, 500)))
        art, (x, y) = ep.contained_art("b_react.png", 1.0, True, 6)
        left, top, right, bottom = ep.art_box
        self.assertTrue(left <= x and top <= y and x + art.width <= right and y + art.height <= bottom)

    def test_every_sentence_and_transition_gets_decoded_review_sample(self):
        samples = {sample["frame"]: sample for sample in review_samples(self.timeline)}
        for segment in self.timeline["segments"]:
            self.assertIn(segment["start_frame"], samples)
            self.assertIn(max(0, segment["start_frame"] - 1), samples)
            self.assertIn(segment["end_frame"] - 1, samples)
            self.assertTrue(any(segment["id"] + " 자막" in sample["labels"] for sample in samples.values()))

    def test_photo_is_static_even_across_same_photo_caption_change(self):
        ep = Episode(self.cfg, self.base)
        first, second = self.timeline["segments"][1:3]
        self.assertEqual(first["image"], second["image"])
        before = ep.frame(first, first["start_frame"] + 10).crop(ep.art_box)
        for segment, frame, previous in [(first, first["end_frame"] - 1, None),
                                          (second, second["start_frame"], first),
                                          (second, second["end_frame"] - 1, first)]:
            with self.subTest(segment=segment["id"], frame=frame):
                after = ep.frame(segment, frame, previous).crop(ep.art_box)
                self.assertIsNone(ImageChops.difference(before, after).getbbox())
        self.assertEqual(0, photo_transition_frames(first, second, ep.settings))

    def test_crossfade_stays_inside_photo_and_finishes_in_six_frames(self):
        ep = Episode(self.cfg, self.base)
        previous, current = self.timeline["segments"][2:4]
        self.assertNotEqual(previous["image"], current["image"])
        self.assertEqual(6, photo_transition_frames(previous, current, ep.settings))
        start = current["start_frame"]
        steady = ep.frame(current, start + 8, previous)
        frames = [ep.frame(current, start + n, previous) for n in range(6)]
        self.assertEqual(ep.photo_layer(previous["image"]).tobytes(), frames[0].crop(ep.art_box).tobytes())
        self.assertEqual(ep.photo_layer(current["image"]).tobytes(), frames[-1].crop(ep.art_box).tobytes())
        self.assertNotEqual(frames[0].crop(ep.art_box).tobytes(), frames[3].crop(ep.art_box).tobytes())
        for frame in frames:
            # Header and subtitle pixels remain identical throughout the dissolve.
            self.assertEqual(steady.crop((0, 0, 1080, 300)).tobytes(), frame.crop((0, 0, 1080, 300)).tobytes())
            self.assertEqual(steady.crop((0, 1520, 1080, 1920)).tobytes(), frame.crop((0, 1520, 1080, 1920)).tobytes())

    def test_attack_reaction_is_hard_cut_for_edge_and_story_ids(self):
        ep = Episode(self.cfg, self.base)
        attack, reaction = [s for s in self.timeline["segments"] if s["id"].startswith("a.edge.")]
        for kind in ("edge", "story"):
            a, b = dict(attack, kind=kind), dict(reaction, kind=kind)
            self.assertEqual(0, photo_transition_frames(a, b, ep.settings))
            opening = ep.frame(b, b["start_frame"], a).crop(ep.art_box)
            ending = ep.frame(b, b["end_frame"] - 1, a).crop(ep.art_box)
            self.assertEqual(opening.tobytes(), ending.tobytes())

    def test_story_header_and_arbitrary_intro_id_omit_annotation_line(self):
        beat = dict(self.timeline["segments"][1], id="rival_arrives", kind="story", header="연막 속의 추격")
        cfg = dict(self.cfg, fictional_matchup=True, story_beats=[beat])
        ep = Episode(cfg, self.base)
        segment = {**beat, **speech_segments(cfg)[0]}
        title, sub, _ = ep.header_content(segment)
        self.assertEqual("연막 속의 추격", title)
        self.assertEqual("", sub)
        self.assertEqual((1080, 1920), ep.frame(segment, segment["start_frame"]).size)
        segment.update(kind="intro")
        self.assertEqual("연막 속의 추격", ep.header_content(segment)[0])
        segment.pop("header")
        self.assertEqual("시험인물a vs 시험인물b", ep.header_content(segment)[0])
        self.assertEqual("", ep.header_content(self.timeline["segments"][1])[1])

    def test_transition_review_samples_match_actual_photo_changes(self):
        samples = review_samples(self.timeline)
        changes = [row for row in samples if any("사진 교차전환" in label for label in row["labels"])]
        current = self.timeline["segments"][3]
        self.assertEqual({current["start_frame"] + n for n in (0, 3, 5)},
                         {row["frame"] for row in changes if any(current["id"] + " 사진" in label for label in row["labels"])})
        repeated = self.timeline["segments"][2]["id"]
        reaction = "a.edge.1"
        self.assertFalse(any(label.startswith((repeated + " 사진", reaction + " 사진"))
                             for row in changes for label in row["labels"]))
        self.assertTrue(all(row["time"] == row["frame"] / 30 for row in samples))

    def test_static_style_rejects_old_motion_and_uses_fixed_cta(self):
        self.assertEqual("여러분의 생각은 어떠신지 댓글로 남겨 주세요. 좋아요와 구독도 부탁드려요.", CTA_SAY)
        path = self.base / "settings.json"
        for values in ({"zoom_amount": .025}, {"shake_pixels": 6}, {"image_transition_ms": 400}):
            atomic_json(path, values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                settings_for(path)

    def test_transition_quantization_stays_in_duration_band(self):
        previous, current = self.timeline["segments"][2:4]
        for duration in (180, 200, 220):
            settings = dict(settings_for(), image_transition_ms=duration)
            self.assertEqual(6, photo_transition_frames(previous, current, settings))
        short = dict(current, end_frame=current["start_frame"] + 3)
        self.assertEqual(0, photo_transition_frames(previous, short, settings_for()))

    def test_story_edge_header_does_not_require_legacy_edge_fields(self):
        beat = dict(self.timeline["segments"][8], id="chase.split", header="분신이 흩어져요", kind="edge", condition="a")
        cfg = copy.deepcopy(self.cfg)
        cfg.update(story_beats=[beat], fictional_matchup=True)
        for side in ("a", "b"):
            cfg[side].pop("edge")
            cfg[side].pop("tag")
        ep = Episode(cfg, self.base)
        self.assertEqual("분신이 흩어져요", ep.header_content(beat)[0])
        self.assertEqual((1080, 1920), ep.frame(beat, beat["start_frame"]).size)

    def setup_profile_fixture(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["production_profile"] = "setup_story_v3"
        cfg["setup"] = {"title": "창고 앞의 가상 대결", "image": "scene.png",
                        "lines": ["창고 앞에서 서로 마주쳤어요.", "이 만남은 가상의 상황이에요."]}
        for who in ("a", "b"):
            cfg[who]["intro"] = cfg[who]["intro"][:2]
        Image.new("RGB", (128, 192), (12, 34, 56)).save(self.base / "scene.png")
        return cfg

    def test_setup_profile_duration_range_is_explicit_and_isolated(self):
        cfg = self.setup_profile_fixture()
        for seconds in (29, 30, 50, 60, 61):
            timeline = fixture_timeline(cfg, total_frames=seconds * 30)
            errors = validate_timeline(cfg, timeline, self.base, silent=True)
            with self.subTest(seconds=seconds):
                if 30 <= seconds <= 60:
                    self.assertEqual([], errors)
                else:
                    self.assertTrue(any("30~60" in error for error in errors))
        for seconds in (20, 45, 50):
            timeline = fixture_timeline(self.cfg, total_frames=seconds * 30)
            errors = validate_timeline(self.cfg, timeline, self.base, silent=True)
            with self.subTest(legacy_seconds=seconds):
                self.assertEqual(seconds > 45, any("20~45" in error for error in errors))

    def test_setup_profile_loads_ten_assets_and_keeps_one_edge_photo(self):
        cfg = self.setup_profile_fixture()
        timeline = fixture_timeline(cfg, total_frames=1500)
        self.assertEqual(14, len(timeline["segments"]))
        ep = Episode(cfg, self.base)
        self.assertEqual(10, len(ep.images))
        self.assertIn("scene.png", ep.images)
        self.assertNotIn("a_react.png", ep.images)
        self.assertNotIn("b_react.png", ep.images)
        setup = [segment for segment in timeline["segments"] if segment["id"].startswith("setup.")]
        self.assertEqual(["scene.png", "scene.png"], [segment["image"] for segment in setup])
        self.assertEqual("창고 앞의 가상 대결", ep.header_content(setup[0])[0])
        self.assertEqual(0, photo_transition_frames(setup[0], setup[1], ep.settings))
        for who in ("a", "b"):
            edges = [segment for segment in timeline["segments"] if segment["id"].startswith(who + ".edge.")]
            self.assertEqual([who + "_atk.png"] * 2, [segment["image"] for segment in edges])
            self.assertEqual(0, photo_transition_frames(edges[0], edges[1], ep.settings))
        self.assertEqual((1080, 1920), ep.frame(setup[0], setup[0]["end_frame"] - 1).size)

    def test_identical_failed_renders_cannot_bypass_retry_limit(self):
        from make_voice import build_plan, input_hash, silent_timeline
        path = self.base / "episode.json"
        atomic_json(path, self.cfg)
        plan = build_plan(self.cfg, self.base, silent=True)
        atomic_json(self.base / "voice_plan.json", plan)
        timeline = silent_timeline(self.cfg, plan)
        atomic_json(self.base / "timeline.json", timeline)
        atomic_json(self.base / "voice_build.json", {"status": "COMPLETE", "input_hash": input_hash(plan),
                                                    "timeline_sha256": sha256_file(self.base / "timeline.json")})
        with patch("make_vs.subprocess.Popen", side_effect=RuntimeError("synthetic encoder failure")) as launch:
            for _ in range(3):
                with self.assertRaisesRegex(RuntimeError, "synthetic encoder failure"):
                    render_episode(path, silent=True)
            with self.assertRaisesRegex(RuntimeError, "실행 한도"):
                render_episode(path, silent=True)
            self.assertEqual(3, launch.call_count)
        history = load_json(self.base / "out/render_history.json")
        self.assertEqual(1, len(history["attempts"]))
        self.assertEqual(3, history["attempts"][0]["executions"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg needed")
    def test_real_silent_voice_timeline_renders_without_inventing_speech(self):
        # A pure silent-mode fixture uses the real voice timeline contract. It is
        # isolated in a temporary directory and never replaces a production run.
        from make_voice import build_plan, input_hash, silent_timeline
        path = self.base / "episode.json"
        atomic_json(path, self.cfg)
        plan = build_plan(self.cfg, self.base, silent=True)
        atomic_json(self.base / "voice_plan.json", plan)
        timeline = silent_timeline(self.cfg, plan)
        atomic_json(self.base / "timeline.json", timeline)
        atomic_json(self.base / "voice_build.json", {"status": "COMPLETE", "input_hash": input_hash(plan),
                                                    "timeline_sha256": sha256_file(self.base / "timeline.json")})
        self.assertTrue(all(segment["speech_start"] is None for segment in timeline["segments"]))
        video = render_episode(path, silent=True)
        self.assertTrue(video.is_file())
        qa = load_json(self.base / "out/technical_qa.json")
        self.assertTrue(qa["passed"])
        self.assertEqual(timeline["input_hash"], qa["voice_input_hash"])
        self.assertEqual("UNREVIEWED", qa["visual_review"])
        self.assertEqual("N/A", qa["audio_review"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg needed")
    def test_encoded_mp4_review_and_technical_audio_gate(self):
        # A 20-second solid-color codec fixture, not an episode or visual approval.
        video = self.base / "codec_fixture.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=navy:s=1080x1920:r=30", "-t", "20", "-an", "-c:v", "libx264",
                        "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", str(video)], check=True)
        timing = {"duration": 20.0, "total_frames": 600, "segments": [
            {"id": "fixture", "kind": "edge", "start_frame": 0, "end_frame": 600,
             "speech_start": 0, "speech_end": 20}]}
        self.assertTrue(technical_checks(video, timing, silent=True)["passed"])
        self.assertFalse(technical_checks(video, timing, silent=False)["checks"]["audio_mode"])
        frames = extract_reviews(video, timing, self.base, "fixturehash")
        self.assertTrue((self.base / "_review.png").is_file())
        self.assertGreaterEqual(len(frames), 4)
        self.assertTrue(all((self.base / item["file"]).is_file() for item in frames))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg needed")
    def test_fifty_second_codec_fixture_is_valid_only_for_setup_profile(self):
        # Encoder/decoder contract fixture only; no production episode is rendered.
        video = self.base / "setup_codec_fixture.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=navy:s=1080x1920:r=30", "-t", "50", "-an", "-c:v", "libx264",
                        "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", str(video)], check=True)
        timeline = {"duration": 50.0, "total_frames": 1500}
        result = technical_checks(video, timeline, silent=True, cfg={"production_profile": "setup_story_v3"})
        self.assertTrue(result["passed"])
        self.assertEqual([30, 60], result["duration_limits_seconds"])
        self.assertEqual("setup_story_v3", result["production_profile"])
        legacy = technical_checks(video, timeline, silent=True)
        self.assertFalse(legacy["checks"]["duration_range"])
        self.assertEqual([20, 45], legacy["duration_limits_seconds"])


if __name__ == "__main__":
    unittest.main()
