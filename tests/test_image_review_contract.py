"""Synthetic evidence fixtures only; these tests never grant a real visual PASS."""
from copy import deepcopy
from pathlib import Path
import contextlib
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

import asset_tools
import check_episode
from episode_core import atomic_json, load_json, sha256_file
from image_review_contract import (
    CONTRACT, CHECKS, PROMPT_GUIDANCE, contract_errors, generation_metadata,
    latest_generation, prepare_prompt, review_template,
)


class ImageContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "generated.png"
        Image.new("RGB", (100, 100), "navy").save(self.source)
        self.image = self.base / "episode" / "a.png"
        self.prompt = prepare_prompt("Fictional fixture: rear camera, a shield supported on the left forearm.")
        self.generation = asset_tools.normalize_asset(self.source, self.image, prompt=self.prompt)
        self.episode = self.image.parent
        for name in ("character_bible.json", "storyboard.json"):
            atomic_json(self.episode / name, {"fixture_only": True})
        atomic_json(self.episode / "episode.json", {"fixture_only": True})
        self.views = asset_tools.build_review_views(self.image, {"fixture detail": [20, 20, 80, 90]})
        self.manifest = Path(self.views["views"][0]["file"]).parent / "review_views.json"
        self.review = review_template(self.episode, self.image, self.manifest)

    def observed_fixture(self):
        review = deepcopy(self.review)
        review.update(status="PASS", reviewed_at="synthetic-test-only",
                      full_observations="Synthetic full-view record", detail_observations="Synthetic detail record")
        for item in review["visual_checks"].values():
            item.update(status="PASS", observations="Synthetic structural fixture; no real visual judgement.")
        for item in review["viewed_evidence"]:
            item.update(viewed=True, observations="Synthetic opened-view claim used only to test gate validation.")
        return review

    def errors(self, review, generation=None):
        return contract_errors(self.episode, "a.png", review, self.generation if generation is None else generation)

    def test_prompt_is_idempotent_and_keeps_scene_without_forced_faces(self):
        self.assertEqual(self.prompt, prepare_prompt(self.prompt))
        self.assertIn("rear camera", self.prompt)
        self.assertIn("Never impose a face-visible percentage", self.prompt)
        self.assertIn("Normal overlap", self.prompt)
        self.assertIn("wrist, elbow and shoulder", self.prompt)
        self.assertIn("centerline, thickness, position", self.prompt)
        self.assertIn("forearm support", self.prompt)

    def test_new_save_has_current_contract_and_never_grants_pass(self):
        self.assertEqual(CONTRACT, self.generation["review_contract"])
        self.assertEqual("UNREVIEWED", self.generation["review_status"])
        self.assertEqual(generation_metadata(self.prompt)["prompt_sha256"], self.generation["prompt_sha256"])
        self.assertTrue(self.generation["prompt_guidance_present"])
        self.assertEqual(self.prompt, self.generation["prompt"])

    def test_template_starts_unreviewed_and_preserves_history(self):
        before = (self.episode / "generation_records.json").read_bytes()
        self.assertEqual("UNREVIEWED", self.review["status"])
        self.assertTrue(all(x["status"] == "UNREVIEWED" for x in self.review["visual_checks"].values()))
        self.assertTrue(all(x["viewed"] is False for x in self.review["viewed_evidence"]))
        self.assertTrue(self.errors(self.review))
        self.assertEqual(before, (self.episode / "generation_records.json").read_bytes())
        self.assertFalse((self.episode / "image_review.json").exists())

    def test_only_complete_hash_bound_evidence_shape_passes(self):
        self.assertEqual([], self.errors(self.observed_fixture()))
        # This checks record integrity only, not whether fixture prose is visually true.
        for key in CHECKS:
            review = self.observed_fixture()
            review["visual_checks"][key]["observations"] = ""
            self.assertTrue(any(key in error for error in self.errors(review)))

    def test_normal_occlusion_or_not_applicable_requires_explanation(self):
        review = self.observed_fixture()
        review["visual_checks"]["weapon_continuity"].update(
            status="NOT_APPLICABLE", observations="Fixture portrait has no weapon; fingers may be hidden normally.")
        self.assertEqual([], self.errors(review))
        review["visual_checks"]["weapon_continuity"]["observations"] = ""
        self.assertTrue(self.errors(review))

    def test_unresolved_or_unviewed_never_passes(self):
        review = self.observed_fixture()
        review["issues"] = ["Fixture unresolved hand ownership"]
        self.assertTrue(any("unresolved" in x for x in self.errors(review)))
        review = self.observed_fixture()
        review["viewed_evidence"][0]["viewed"] = False
        self.assertTrue(any("actual viewing" in x for x in self.errors(review)))

    def test_hash_changed_or_fake_detail_is_rejected(self):
        review = self.observed_fixture()
        review["viewed_evidence"][-1]["image_sha256"] = "0" * 64
        self.assertTrue(any("different image" in x for x in self.errors(review)))
        review = self.observed_fixture()
        detail = review["viewed_evidence"][-1]
        Image.new("RGB", (60, 70), "yellow").save(self.episode / detail["file"])
        detail["sha256"] = sha256_file(self.episode / detail["file"])
        self.assertTrue(any("native crop" in x for x in self.errors(review)))

    def test_detail_or_original_omission_rejected(self):
        for kind in ("full", "detail", "original"):
            review = self.observed_fixture()
            review["viewed_evidence"] = [x for x in review["viewed_evidence"] if x["kind"] != kind]
            self.assertTrue(self.errors(review))

    def test_repair_requires_prior_hash_and_recheck_not_na(self):
        review = self.observed_fixture()
        generation = dict(self.generation, replaced_sha256="b" * 64)
        self.assertTrue(any("previous image" in x for x in self.errors(review, generation)))
        review["previous_sha256"] = generation["replaced_sha256"]
        review["visual_checks"]["revision_recheck"]["status"] = "NOT_APPLICABLE"
        self.assertTrue(any("reinspection" in x for x in self.errors(review, generation)))
        review["visual_checks"]["revision_recheck"]["status"] = "PASS"
        self.assertEqual([], self.errors(review, generation))

    def test_legacy_record_unchanged_but_reused_asset_can_opt_in(self):
        legacy = {"sha256": self.review["sha256"], "status": "PASS"}
        self.assertEqual([], self.errors(legacy, {}))
        legacy["review_contract"] = CONTRACT
        self.assertTrue(self.errors(legacy, {}))
        self.assertEqual([], self.errors(self.observed_fixture(), {}))

    def test_reinspection_keeps_known_current_issues_and_version_without_old_pass(self):
        known = self.observed_fixture()
        known["issues"] = ["Known unresolved shield support"]
        known["status"] = "NEEDS_REVIEW"
        atomic_json(self.episode / "image_review.json", {"images": [known]})
        before = (self.episode / "image_review.json").read_bytes()
        draft = review_template(self.episode, self.image, self.manifest)
        self.assertEqual(known["issues"], draft["issues"])
        self.assertEqual(known["generation_version"], draft["generation_version"])
        self.assertEqual("UNREVIEWED", draft["status"])
        self.assertEqual("", draft["full_observations"])
        self.assertEqual(before, (self.episode / "image_review.json").read_bytes())

    def test_declared_adjacent_reference_must_be_current(self):
        review = self.observed_fixture()
        adjacent = self.episode / "adjacent.png"
        Image.new("RGB", (50, 50), "red").save(adjacent)
        review["reference_hashes"]["adjacent.png"] = sha256_file(adjacent)
        self.assertEqual([], self.errors(review))
        Image.new("RGB", (50, 50), "blue").save(adjacent)
        self.assertTrue(any("reference evidence" in x for x in self.errors(review)))

    def test_prompt_claim_or_boolean_cannot_replace_actual_prompt(self):
        generation = dict(self.generation, prompt="Unprepared prompt", prompt_guidance_present=True)
        self.assertTrue(any("actual generation prompt" in x for x in self.errors(self.observed_fixture(), generation)))
        generation = dict(self.generation, prompt_sha256="f" * 64)
        self.assertTrue(any("prompt hash" in x for x in self.errors(self.observed_fixture(), generation)))

    def test_review_template_rejects_stale_views(self):
        manifest = load_json(self.manifest)
        manifest["image_sha256"] = "f" * 64
        atomic_json(self.manifest, manifest)
        with self.assertRaisesRegex(ValueError, "different image"):
            review_template(self.episode, self.image, self.manifest)

    def test_shared_checker_gate_applies_to_short_and_longform(self):
        for cfg in (None, {"format": "longform", "production_profile": "showdown_story_long_v1", "a": {"image": "a.png"}, "b": {"image": "b.png"}}):
            atomic_json(self.episode / "image_review.json", {"images": [self.observed_fixture()]})
            with patch("check_episode.image_names", return_value=["a.png"]):
                errors = []
                check_episode.validate_image_reviews(self.episode, errors, cfg)
                self.assertEqual([], errors)
                atomic_json(self.episode / "image_review.json", {"images": [self.review]})
                errors = []
                check_episode.validate_image_reviews(self.episode, errors, cfg)
                self.assertTrue(any("visual contract" in x for x in errors))

    def test_cli_outputs_real_newlines_and_never_overwrites(self):
        original = self.base / "prompt.txt"
        original.write_text("Test action", encoding="utf-8")
        prepared = self.base / "prepared.txt"
        args = ["asset_tools.py", "prepare-prompt", "--prompt-file", str(original), "--output", str(prepared)]
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            asset_tools.main()
        self.assertIn(PROMPT_GUIDANCE, prepared.read_text(encoding="utf-8"))
        before = prepared.read_bytes()
        with patch.object(sys, "argv", args), self.assertRaises(FileExistsError):
            asset_tools.main()
        self.assertEqual(before, prepared.read_bytes())
        output = self.base / "review.json"
        args = ["asset_tools.py", "review-template", str(self.image),
                "--episode", str(self.episode / "episode.json"), "--views-manifest", str(self.manifest), "--output", str(output)]
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            asset_tools.main()
        self.assertEqual("UNREVIEWED", load_json(output)["status"])
        self.assertTrue(output.read_bytes().endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
