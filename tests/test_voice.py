"""Offline regression tests; synthetic audio never becomes an episode deliverable."""
import asyncio
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import wave

from episode_core import atomic_json, load_json, sha256_file, speech_segments
import make_voice as voice


def fixture():
    return {
        "schema_version": 2, "hook": "거리가 달라지면 누가 유리할까요?", "thumb": "thumb.png",
        "clash_text": "둘이 붙는다면?", "clash_say": "둘이 맞붙으면 어떨까요?",
        "winner": "none", "reason": "거리에 따라 강점이 달라져요.", "question": "먼 거리라면 누구를 고르실까요?",
        **{who: {"name": name, "image": who + ".png", "tag": "테스트 인물",
                 "intro": ["옛 이야기 속 인물이에요.", "이야기에서 활을 사용해요.", "동료들과 함께 움직여요."],
                 "intro_images": [who + "_1.png", who + "_2.png"],
                 "edge": {"title": "조건", "image": who + "_atk.png", "reaction_image": other + "_react.png",
                          "lines": ["이 조건에서 강점을 살려요.", "이야기에는 이런 행동이 나와요."]}}
           for who, other, name in (("a", "b", "가"), ("b", "a", "나"))},
    }


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cfg = fixture()
        self.path = self.base / "episode.json"
        atomic_json(self.path, self.cfg)
        self.pcm = b"\x10\x00" * 96001  # Deliberately not divisible by one frame.
        self.engine = {"name": "test-only-engine", "version": "1"}
        self.patches = [patch.object(voice, "engine_info", return_value=self.engine),
                        patch.object(voice, "decoder_info", return_value={"name": "test-only-decoder", "version": "1"}),
                        patch.object(voice, "verify_voices", new_callable=AsyncMock),
                        patch.object(voice, "decode_pcm", side_effect=self.decode),
                        patch.object(voice, "fetch_tts", new_callable=AsyncMock, side_effect=self.fetch)]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    async def fetch(self, entry, path):
        Path(path).write_bytes(b"TEST ONLY:" + entry["spoken_text"].encode("utf-8"))
        return [{"type": "WordBoundary", "offset_ticks": 1000000, "duration_ticks": 1000000, "text": "test"}]

    def decode(self, path):
        if not Path(path).read_bytes().startswith(b"TEST ONLY:"):
            raise voice.VoiceError("Synthetic decoder rejected corrupt audio")
        return self.pcm

    def build(self):
        return voice.main(self.path)

    def test_pcm_sample_counts_match_frame_timeline_and_explicit_pauses(self):
        timeline = self.build()
        self.assertEqual([], voice.validate_voice(self.cfg, self.base))
        self.assertEqual(timeline["duration"], timeline["total_frames"] / 30)
        with wave.open(str(self.base / "voice.wav"), "rb") as output:
            self.assertEqual(output.getnframes(), timeline["total_frames"] * 1600)
        for segment in timeline["segments"]:
            self.assertEqual(segment["decoded_sample_count"], 96001)
            self.assertEqual(segment["speech_end_sample"] - segment["speech_start_sample"], 96001)
            self.assertLess(segment["alignment_tail_samples"], 1600)
            self.assertGreaterEqual(segment["speech_start"], segment["start"])
            self.assertLessEqual(segment["speech_end"], segment["end"])
            self.assertAlmostEqual(segment["boundaries"][0]["start"], segment["speech_start"] + .1)
        self.assertEqual("AUDIO_REVIEW_REQUIRED", load_json(self.base / "audio_review.json")["status"])

    def test_unchanged_inputs_reuse_audio_but_changed_text_rejects_old_timeline(self):
        self.build()
        calls = self.mocks[4].await_count
        self.build()
        self.assertEqual(calls, self.mocks[4].await_count)
        changed = copy.deepcopy(self.cfg)
        changed["hook"] = "가까이 다가서면 누가 유리할까요?"
        self.assertTrue(voice.validate_voice(changed, self.base))
        atomic_json(self.path, changed)
        self.build()
        self.assertEqual(calls + 1, self.mocks[4].await_count)
        state = load_json(self.base / "voice_state.json")
        self.assertEqual(2, state["sentences"]["hook"]["attempts"])
        self.assertEqual(1, state["sentences"]["a.intro.0"]["attempts"])

    def test_cta_engine_and_voice_settings_invalidate_dependencies(self):
        self.build()
        with patch("make_vs.CTA_SAY", "이유를 댓글로 알려 주세요."):
            self.assertTrue(voice.validate_voice(self.cfg, self.base))
        with patch.object(voice, "engine_info", return_value={"name": "test-only-engine", "version": "2"}):
            self.assertTrue(voice.validate_voice(self.cfg, self.base))
        plan = load_json(self.base / "voice_plan.json")
        plan["segments"][0]["pitch"] = "+2Hz"
        atomic_json(self.base / "voice_plan.json", plan)
        self.assertTrue(voice.validate_voice(self.cfg, self.base))

    def test_missing_and_corrupt_audio_never_pass_as_silence(self):
        self.build()
        (self.base / "v_hook.mp3").write_bytes(b"corrupt")
        self.assertTrue(voice.validate_voice(self.cfg, self.base))
        (self.base / "voice.wav").unlink()
        self.assertTrue(voice.validate_voice(self.cfg, self.base))
        self.assertEqual("voice", load_json(self.base / "timeline.json")["audio_mode"])

    def test_attempt_limit_survives_state_reload(self):
        plan = voice.build_plan(self.cfg, self.base)
        entry = plan["segments"][0]
        state = {"schema_version": 2, "sentences": {}}
        for _ in range(3):
            asyncio.run(voice.generate_sentence(self.base, entry, plan, state, force=True))
            state = load_json(self.base / "voice_state.json")
        with self.assertRaisesRegex(voice.VoiceError, "2회 한도"):
            asyncio.run(voice.generate_sentence(self.base, entry, plan, state, force=True))
        self.assertEqual(3, self.mocks[4].await_count)

    def test_failed_regeneration_blocks_old_success(self):
        self.build()
        with patch.object(voice, "fetch_tts", new_callable=AsyncMock, side_effect=RuntimeError("service unavailable")):
            with self.assertRaises(voice.VoiceError):
                voice.main(self.path, regenerate=["hook"])
        self.assertEqual("FAILED", load_json(self.base / "voice_build.json")["status"])
        self.assertTrue(voice.validate_voice(self.cfg, self.base))

    def test_explicit_silent_uses_no_engine_and_marks_audio_not_applicable(self):
        with patch.object(voice, "engine_info", side_effect=AssertionError("must not call engine")):
            timeline = voice.main(self.path, silent=True)
            self.assertEqual([], voice.validate_voice(self.cfg, self.base))
        self.assertEqual("silent", timeline["audio_mode"])
        self.assertIsNone(timeline["pcm_path"])
        self.assertFalse((self.base / "voice.wav").exists())
        self.assertEqual("N/A", load_json(self.base / "audio_review.json")["status"])
        self.mocks[4].assert_not_awaited()

    def test_pronunciation_override_is_cleared_when_source_changes(self):
        plan = voice.build_plan(self.cfg, self.base)
        atomic_json(self.base / "voice_plan.json", plan)
        plan["segments"][0]["spoken_text"] = "거리가 달라지면 어떨까요?"
        atomic_json(self.base / "voice_plan.json", plan)
        refreshed = voice.build_plan(self.cfg, self.base)
        self.assertEqual("거리가 달라지면 어떨까요?", refreshed["segments"][0]["spoken_text"])
        atomic_json(self.base / "voice_plan.json", refreshed)
        self.cfg["hook"] = "가까운 거리라면 누가 유리할까요?"
        changed = voice.build_plan(self.cfg, self.base)
        self.assertEqual(self.cfg["hook"], changed["segments"][0]["spoken_text"])

    def test_stage_directions_are_not_sent_to_tts(self):
        plan = voice.build_plan(self.cfg, self.base)
        plan["segments"][0]["spoken_override"] = "[놀라며] 누가 유리할까요?"
        atomic_json(self.base / "voice_plan.json", plan)
        with self.assertRaisesRegex(voice.VoiceError, "연출 지시"):
            voice.build_plan(self.cfg, self.base)

    def test_padding_trim_preserves_every_nonzero_sample_and_conservative_tails(self):
        spoken = b"\x01\x00" * 1000  # Even one least-significant bit is retained.
        raw = b"\0\0" * 9600 + spoken + b"\0\0" * 48000
        kept, record = voice.trim_digital_silence(raw)
        self.assertEqual(kept, b"\0\0" * 2400 + spoken + b"\0\0" * 4800)
        self.assertEqual(7200, record["leading_trim_samples"])
        self.assertEqual(43200, record["trailing_trim_samples"])
        self.assertEqual(4800, record["retained_tail_samples"])
        with self.assertRaises(voice.VoiceError):
            voice.trim_digital_silence(b"\0\0" * 48000)

    def test_processor_change_reuses_verified_raw_tts_without_consuming_retry(self):
        self.build()
        before = load_json(self.base / "voice_state.json")
        plan = voice.build_plan(self.cfg, self.base)
        plan["producer_sha256"] = "changed PCM implementation"
        with patch.object(voice, "build_plan", return_value=plan):
            self.build()
            self.assertEqual([], voice.validate_voice(self.cfg, self.base))
        self.assertEqual(before, load_json(self.base / "voice_state.json"))
        self.assertEqual(len(speech_segments(self.cfg)), self.mocks[4].await_count)
        self.assertTrue(load_json(self.base / "v_hook.json")["processing_history"])


if __name__ == "__main__":
    unittest.main()
