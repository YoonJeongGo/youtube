import copy
import json
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_episode
from audio_review_checks import CHECKS, media_duration, validate_audio_model_review
from episode_core import atomic_json, sha256_file


class AudioModelReviewTests(unittest.TestCase):
    """Synthetic fixtures exercise evidence validation, never real approval."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base / "out").mkdir()
        for name, rate in (("voice.wav", 8000), ("out/final_audio.wav", 12000)):
            with wave.open(str(self.base / name), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(rate)
                audio.writeframes(b"".join(struct.pack("<h", index - rate) for index in range(rate * 2)))
        (self.base / "out/video.mp4").write_bytes(b"test video fixture, not production media")
        self.timeline = {"audio_mode": "voice", "duration": 2.0, "pcm_path": "voice.wav",
                         "pcm_sha256": sha256_file(self.base / "voice.wav"), "input_hash": "fixture-input"}
        self.evidence = {
            "schema_version": 1, "status": "COMPLETED", "method": "raw_audio_model",
            "model": {"name": "fixture audio model", "revision": "test-revision",
                      "weights_sha256": "a" * 64, "projector_sha256": "b" * 64},
            "runtime": {"name": "fixture runtime", "version": "1.0"},
            "inputs": [
                {"kind": "original_pcm", "path": "voice.wav", "sha256": self.timeline["pcm_sha256"], "duration": 2.0},
                {"kind": "final_mp4_audio", "path": "out/final_audio.wav",
                 "sha256": sha256_file(self.base / "out/final_audio.wav"), "duration": 2.0,
                 "source_video_sha256": sha256_file(self.base / "out/video.mp4")},
            ],
            "runs": [],
            "review": {"checks": {}, "unresolved_issues": []},
        }
        for item in self.evidence["inputs"]:
            for part, (start, end) in enumerate(((0.0, 1.0), (1.0, 2.0))):
                run = {
                    "id": f"{item['kind']}-{part}", "target_kind": item["kind"],
                    "input_sha256": item["sha256"], "start": start, "end": end,
                    "prompt": "Describe this test audio's actual acoustic features and ending; no script is supplied.",
                    "response": f"Test-only response for {item['kind']} part {part}: the final vowel has a falling pitch; a pause follows the first phrase. This is a validator fixture, not a listening result.",
                    "audio_processed": True, "transcript_only": False,
                }
                with wave.open(str(self.base / item["path"]), "rb") as parent:
                    rate = parent.getframerate()
                    first, last = int(start * rate), int(end * rate)
                    parent.setpos(first)
                    content = parent.readframes(last - first)
                    clip_path = f"out/{item['kind']}_{part}.wav"
                    with wave.open(str(self.base / clip_path), "wb") as clip:
                        clip.setparams(parent.getparams())
                        clip.writeframes(content)
                run["processed_audio"] = {"path": clip_path, "sha256": sha256_file(self.base / clip_path),
                                          "sample_rate": rate, "frames": last - first,
                                          "start_sample": first, "end_sample": last}
                self.evidence["runs"].append(run)
        for name in CHECKS:
            self.evidence["review"]["checks"][name] = {
                "status": "PASS", "observations": f"Fixture-specific {name} observation linked to the first phrase and complete final vowel; not production evidence.",
                "run_ids": [r["id"] for r in self.evidence["runs"]],
            }
        self.review = {"reviewer_kind": "local_audio_model", "status": "PASS", "listened": False,
                       "human_listened": False, "codex_native_listened": False,
                       "audio_perception_verified": True, "observations": "Fixture review distinguishes model perception from actual human listening.",
                       "input_hash": self.timeline["input_hash"], "pcm_sha256": self.timeline["pcm_sha256"]}
        self.seal()

    def tearDown(self):
        self.temp.cleanup()

    def seal(self):
        path = self.base / "audio_understanding_local.json"
        atomic_json(path, self.evidence)
        self.review["perception_evidence"] = {"path": path.name, "sha256": sha256_file(path)}

    def validate(self):
        def probe(path):
            return 2.0 if Path(path).suffix == ".mp4" else media_duration(path)
        with patch("audio_review_checks.media_duration", side_effect=probe):
            return validate_audio_model_review(self.base, self.timeline, self.review)

    def assert_error(self, fragment):
        errors = self.validate()
        self.assertTrue(any(fragment in x for x in errors), errors)

    def use_native_model(self):
        self.evidence["model"] = {
            "name": "fixture native Thinker", "revision": "fixture-native-revision",
            "format": "safetensors",
            "weight_shards": [
                {"filename": "model-00001-of-00002.safetensors", "sha256": "a" * 64, "bytes": 512},
                {"filename": "model-00002-of-00002.safetensors", "sha256": "b" * 64, "bytes": 384},
            ],
            "config_sha256": "c" * 64,
            "processor_artifacts": [
                {"filename": "preprocessor_config.json", "sha256": "d" * 64},
                {"filename": "tokenizer.json", "sha256": "e" * 64},
            ],
        }

    def test_complete_current_evidence_has_separate_model_route(self):
        self.assertEqual([], self.validate())
        self.assertFalse(self.review["listened"])

    def test_native_safetensors_identity_does_not_fabricate_a_projector(self):
        self.use_native_model()
        self.seal()
        self.assertEqual([], self.validate())
        self.assertNotIn("projector_sha256", self.evidence["model"])
        self.assertNotIn("weights_sha256", self.evidence["model"])
        # Artifact existence and complete shard discovery belong to the runner,
        # not this metadata-only branch of the evidence gate.
        self.assertFalse((self.base / "model-00001-of-00002.safetensors").exists())

    def test_native_missing_weight_shard_records_are_rejected(self):
        for shards in (None, [], [{}]):
            with self.subTest(shards=shards):
                self.use_native_model()
                if shards is None:
                    self.evidence["model"].pop("weight_shards")
                else:
                    self.evidence["model"]["weight_shards"] = shards
                self.seal()
                self.assert_error("weight_shards")

    def test_native_duplicate_artifact_names_are_rejected_case_insensitively(self):
        for target, source in (("weight_shards", "weight_shards"),
                               ("processor_artifacts", "processor_artifacts"),
                               ("processor_artifacts", "weight_shards")):
            with self.subTest(target=target, source=source):
                self.use_native_model()
                model = self.evidence["model"]
                model[target][1]["filename"] = model[source][0]["filename"].upper()
                self.seal()
                self.assert_error("duplicate artifact filename")

    def test_native_artifact_hashes_require_64_hexadecimal_characters(self):
        for field in ("weight_shards", "processor_artifacts"):
            for value in (None, "", "a" * 63, "g" * 64, 123):
                with self.subTest(field=field, value=value):
                    self.use_native_model()
                    self.evidence["model"][field][0]["sha256"] = value
                    self.seal()
                    self.assert_error(field + "[0] sha256")

    def test_native_missing_or_invalid_config_hash_is_rejected(self):
        for value in (None, "invalid", "c" * 63):
            with self.subTest(value=value):
                self.use_native_model()
                if value is None:
                    self.evidence["model"].pop("config_sha256")
                else:
                    self.evidence["model"]["config_sha256"] = value
                self.seal()
                self.assert_error("config_sha256")

    def test_native_artifact_filenames_are_safe_basenames(self):
        for field in ("weight_shards", "processor_artifacts"):
            for value in ("", "..", "../model.safetensors", "folder/model.safetensors",
                          "folder\\model.safetensors", "C:model.safetensors", "C:\\model.safetensors",
                          "NUL.json", "COM1 .json", "CONOUT$", "bad?.json", "bad\nname", "name.", "name "):
                with self.subTest(field=field, value=value):
                    self.use_native_model()
                    self.evidence["model"][field][0]["filename"] = value
                    self.seal()
                    self.assert_error("filename must be a safe artifact basename")

    def test_native_weight_shard_bytes_must_be_positive_integers(self):
        for value in (None, 0, -1, True, 1.5, "512"):
            with self.subTest(value=value):
                self.use_native_model()
                if value is None:
                    self.evidence["model"]["weight_shards"][0].pop("bytes")
                else:
                    self.evidence["model"]["weight_shards"][0]["bytes"] = value
                self.seal()
                self.assert_error("bytes must be a positive integer")

    def test_native_processor_artifacts_cannot_be_absent(self):
        for value in (None, [], [None]):
            with self.subTest(value=value):
                self.use_native_model()
                self.evidence["model"]["processor_artifacts"] = value
                self.seal()
                self.assert_error("processor_artifacts")

    def test_native_optional_processor_size_is_checked_when_recorded(self):
        self.use_native_model()
        self.evidence["model"]["processor_artifacts"][0]["bytes"] = 0
        self.seal()
        self.assert_error("processor_artifacts[0] bytes must be a positive integer")

    def test_stale_evidence_hash_is_rejected(self):
        self.evidence["runs"][0]["response"] += " changed after signoff"
        atomic_json(self.base / "audio_understanding_local.json", self.evidence)
        self.assert_error("stale perception evidence hash")

    def test_current_original_and_final_audio_hashes_are_required(self):
        for name in ("voice.wav", "out/final_audio.wav"):
            with self.subTest(name=name):
                path = self.base / name
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                self.assert_error("stale input hash")
                path.write_bytes(original)

    def test_changed_video_invalidates_final_audio_provenance(self):
        (self.base / "out/video.mp4").write_bytes(b"changed video")
        self.assert_error("stale source video")

    def test_asr_only_or_absent_raw_processing_never_passes(self):
        for field, value in (("transcript_only", True), ("audio_processed", False)):
            with self.subTest(field=field):
                old = self.evidence["runs"][0][field]
                self.evidence["runs"][0][field] = value
                self.seal()
                self.assert_error("not ASR-only")
                self.evidence["runs"][0][field] = old

    def test_missing_or_generic_model_response_never_passes(self):
        for response in (None, "", "The speech is clear and natural."):
            with self.subTest(response=response):
                self.evidence["runs"][0]["response"] = response
                self.seal()
                self.assert_error("generic prompt/actual response")

    def test_missing_ending_and_interior_gap_block_full_coverage(self):
        for index, field, value in ((3, "end", 1.8), (1, "start", 1.2)):
            with self.subTest(case=field):
                original = copy.deepcopy(self.evidence)
                self.evidence["runs"][index][field] = value
                self.seal()
                self.assert_error("full input through its ending")
                self.evidence = original

    def test_ending_check_must_cite_ending_runs(self):
        self.evidence["review"]["checks"]["ending"]["run_ids"] = ["original_pcm-0", "final_mp4_audio-0"]
        self.seal()
        self.assert_error("ending check lacks an end-reaching run")

    def test_unresolved_issue_or_missing_check_cannot_pass(self):
        self.evidence["review"]["unresolved_issues"] = ["Final syllable uncertain"]
        self.seal()
        self.assert_error("unresolved_issues")
        self.evidence["review"]["unresolved_issues"] = []
        del self.evidence["review"]["checks"]["intonation"]
        self.seal()
        self.assert_error("observations required: intonation")

    def test_unknown_run_ids_and_repeated_generic_reviews_rejected(self):
        self.evidence["review"]["checks"]["pacing"]["run_ids"] = ["invented-run"]
        for item in self.evidence["review"]["checks"].values():
            item["observations"] = "The overall audio sounds clear and natural throughout the recording."
        self.seal()
        self.assert_error("unknown or duplicate run_ids")
        self.assert_error("identical generic observations")

    def test_identity_flags_must_not_claim_human_or_native_listening(self):
        for field in ("listened", "human_listened", "codex_native_listened"):
            with self.subTest(field=field):
                self.review[field] = True
                self.assert_error(field + " must explicitly")
                self.review[field] = False

    def test_duration_lie_and_wrong_original_path_rejected(self):
        self.evidence["inputs"][0]["duration"] = 1.0
        self.seal()
        self.assert_error("declared duration differs")
        self.evidence["inputs"][0]["duration"] = 2.0
        self.evidence["inputs"][0]["path"] = "out/final_audio.wav"
        self.evidence["inputs"][0]["sha256"] = sha256_file(self.base / "out/final_audio.wav")
        self.seal()
        self.assert_error("not the current timeline PCM")

    def test_changed_chunk_hash_is_rejected(self):
        clip = self.base / self.evidence["runs"][0]["processed_audio"]["path"]
        clip.write_bytes(clip.read_bytes() + b"changed after model inference")
        self.assert_error("stale processed audio hash")

    def test_chunk_interval_must_match_parent_sample_indices(self):
        self.evidence["runs"][0]["processed_audio"]["start_sample"] = 80
        self.seal()
        self.assert_error("sample range differs from run interval")

    def test_current_chunk_hash_with_wrong_parent_content_is_rejected(self):
        processed = self.evidence["runs"][0]["processed_audio"]
        path = self.base / processed["path"]
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        path.write_bytes(content)
        processed["sha256"] = sha256_file(path)  # hash alone is insufficient
        self.seal()
        self.assert_error("PCM content differs from the parent slice")

    def test_chunk_rate_and_frame_claims_must_match_actual_wav(self):
        for field, value in (("sample_rate", 16000), ("frames", 7999)):
            with self.subTest(field=field):
                processed = self.evidence["runs"][0]["processed_audio"]
                old = processed[field]
                processed[field] = value
                self.seal()
                self.assert_error("rate/frame metadata differs")
                processed[field] = old

    def test_partial_input_cannot_claim_full_parent_hash_without_chunk(self):
        self.evidence["runs"][0].pop("processed_audio")
        self.seal()
        self.assert_error("partial run requires processed_audio")

    def test_actual_whole_input_runs_remain_supported_without_chunks(self):
        self.evidence["runs"] = [self.evidence["runs"][1], self.evidence["runs"][3]]
        for run in self.evidence["runs"]:
            run.pop("processed_audio")
            run["start"] = 0.0
            run["end"] = 2.0
        for check in self.evidence["review"]["checks"].values():
            check["run_ids"] = [run["id"] for run in self.evidence["runs"]]
        self.seal()
        self.assertEqual([], self.validate())

    def test_safe_relative_evidence_and_input_paths_required(self):
        for name in ("../outside.json", str(self.base / "audio_understanding_local.json"), "C:outside.json"):
            with self.subTest(path=name):
                self.review["perception_evidence"]["path"] = name
                self.assertNotEqual([], self.validate())
        self.seal()
        self.evidence["inputs"][1]["path"] = "../final_audio.wav"
        self.seal()
        self.assert_error("path traversal")

    def test_missing_artifact_identity_and_malformed_targets_are_errors(self):
        self.evidence["model"]["projector_sha256"] = "unknown"
        self.evidence["inputs"][0]["kind"] = []
        self.evidence["runs"][0]["target_kind"] = []
        self.seal()
        self.assert_error("projector_sha256")
        self.assert_error("input target is invalid")

    def test_quality_dispatches_model_gate_without_requiring_listened_true(self):
        atomic_json(self.base / "episode.json", {})
        atomic_json(self.base / "timeline.json", self.timeline)
        atomic_json(self.base / "audio_review.json", self.review)
        with patch("check_episode.validate_episode", return_value=[]), patch("check_episode.validate_planning"), \
             patch("check_episode.validate_sources"), patch("check_episode.validate_image_reviews"), \
             patch("check_episode.validate_subtitles"), patch("check_episode.validate_video_specs"), \
             patch("check_episode.script_hash", return_value="fixture"), \
             patch("make_voice.validate_voice", return_value=[]), patch("make_vs.validate_timeline", return_value=[]), \
             patch("check_episode.validate_audio_model_review", return_value=["model-gate-sentinel"]) as gate:
            errors = check_episode.validate_quality(self.base / "episode.json")
        gate.assert_called_once()
        self.assertIn("model-gate-sentinel", errors)
        self.assertNotIn("Actual listening and specific audio observations are required", errors)

    def test_direct_listening_route_remains_unchanged(self):
        atomic_json(self.base / "episode.json", {})
        atomic_json(self.base / "timeline.json", self.timeline)
        self.review.pop("reviewer_kind")
        self.review["listened"] = True
        atomic_json(self.base / "audio_review.json", self.review)
        with patch("check_episode.validate_episode", return_value=[]), patch("check_episode.validate_planning"), \
             patch("check_episode.validate_sources"), patch("check_episode.validate_image_reviews"), \
             patch("check_episode.validate_subtitles"), patch("check_episode.validate_video_specs"), \
             patch("check_episode.script_hash", return_value="fixture"), \
             patch("make_voice.validate_voice", return_value=[]), patch("make_vs.validate_timeline", return_value=[]), \
             patch("check_episode.validate_audio_model_review") as gate:
            errors = check_episode.validate_quality(self.base / "episode.json")
        gate.assert_not_called()
        self.assertNotIn("Actual listening and specific audio observations are required", errors)


if __name__ == "__main__":
    unittest.main()
