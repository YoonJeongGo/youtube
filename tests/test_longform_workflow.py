"""Synthetic workflow contracts only; no real jobs, media, source research or approvals."""
import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import check_episode as qa
import deliver_episode as delivery
from episode_core import LONGFORM_PROFILE, atomic_json, image_names, object_hash, sha256_file
from plan_matchups import (LONGFORM_CRITERIA, LONGFORM_PLAN_FIELDS, longform_candidate_errors,
                           narrative_signature, select_matchups)
from project_state import ProjectState, atomic_text, load_json
from tests.test_longform_profile import longform_fixture
from tests.test_planning import idea


def candidate(sid="fixture-story"):
    value = idea()
    value.update(format="longform", production_profile=LONGFORM_PROFILE, story_id=sid,
                 planned_duration_seconds=660,
                 story_plan={key: "Fixture narrative " + key for key in LONGFORM_PLAN_FIELDS},
                 criteria={key: {"pass": True, "reason": "Fixture review only"} for key in LONGFORM_CRITERIA})
    return value


class LongformWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / "qa_fixture"
        self.base.mkdir(parents=True)
        self.cfg = longform_fixture()

    def tearDown(self):
        self.temp.cleanup()

    def pool(self, *candidates):
        atomic_json(self.root / "matchup_ideas_longform.json", {"schema_version": 1, "candidates": list(candidates)})

    def test_separate_state_queue_and_same_pair_short_is_not_duplicate(self):
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        atomic_text(self.root / "done.txt", "인물가 vs 인물나\n")
        with ProjectState(self.root) as state:
            run = state.start_run()
            short = state.reserve(run, idea(), "person:a|person:b", "fixture")
            state.finish_run(run)
        protected = {name: (self.root / name).read_bytes() for name in ("queue.txt", "done.txt", "state/project_state.json")}
        self.pool(candidate())
        result = select_matchups(self.root, media_format="longform")
        self.assertEqual("SELECTED", result["status"])
        long = result["selections"][0]
        self.assertNotEqual(short["job_id"], long["job_id"])
        self.assertIn("longform", Path(long["episode_path"]).parts)
        for name, data in protected.items():
            self.assertEqual(data, (self.root / name).read_bytes())

    def test_same_story_resume_and_title_change_do_not_duplicate(self):
        item = candidate()
        self.pool(item)
        first = select_matchups(self.root, media_format="longform", story_id=item["story_id"])
        with ProjectState(self.root, media_format="longform") as state:
            state.finish_run(first["run_id"])
        item["title"] = "Different marketing title only"
        self.pool(item)
        second = select_matchups(self.root, media_format="longform", story_id=item["story_id"])
        self.assertEqual(first["selections"][0]["job_id"], second["selections"][0]["job_id"])
        self.assertEqual("resume", second["selections"][0]["action"])

    def test_new_same_pair_requires_real_narrative_distinction_record(self):
        first = candidate("first")
        with ProjectState(self.root, media_format="longform") as state:
            run = state.start_run()
            state.reserve(run, first, "person:a|person:b", "fixture")
            state.finish_run(run)
            second = candidate("second")
            run2 = state.start_run()
            with self.assertRaisesRegex(ValueError, "narrative differences"):
                state.reserve(run2, second, "person:a|person:b", "fixture")
            second["narrative_review"] = {"status": "PASS", "reviewed_at": "fixture", "differences": "Fixture comparison", "compared_story_ids": ["first"]}
            second["title"] = "Title is not story identity"
            with self.assertRaisesRegex(ValueError, "Same narrative"):
                state.reserve(run2, second, "person:a|person:b", "fixture")
            second["story_plan"]["central_event"] = "A distinct fixture event"
            job = state.reserve(run2, second, "person:a|person:b", "fixture")
            self.assertEqual("second", job["story_id"])
            self.assertEqual(2, len(state.data["jobs"]))

    def test_longform_candidate_never_uses_short_conditions_or_duration(self):
        value = candidate()
        del value["conditions"]
        self.assertEqual([], longform_candidate_errors(value))
        for duration in (539.9, 900.1, True):
            value["planned_duration_seconds"] = duration
            self.assertTrue(longform_candidate_errors(value))
        for duration in (540, 900):
            value["planned_duration_seconds"] = duration
            self.assertEqual([], longform_candidate_errors(value))
        value["production_profile"] = "showdown_detail_v5"
        self.assertTrue(longform_candidate_errors(value))

    def test_complete_gate_keeps_queue_when_listening_is_missing(self):
        with ProjectState(self.root, media_format="longform") as state:
            run = state.start_run()
            job = state.reserve(run, candidate(), "person:a|person:b", "fixture")
            path = self.root / job["episode_path"]
            atomic_json(path.parent / "manifest.json", {"job_id": job["job_id"]})
            atomic_text(self.root / "queue_longform.txt", "fixture-story\t인물가 vs 인물나\n")
            with patch("check_episode.validate_completion", return_value=["LONGFORM_LISTENING_PENDING"]):
                with self.assertRaisesRegex(ValueError, "Completion gate"):
                    state.set_status(job["job_id"], "COMPLETE")
            self.assertEqual("IN_PROGRESS", state.get_job(job["job_id"])["status"])
            self.assertFalse((self.root / "done_longform.txt").exists())
            self.assertIn("fixture-story", (self.root / "queue_longform.txt").read_text())

    def test_video_actual_boundaries_and_landscape(self):
        for duration, valid in ((539.99, False), (540, True), (900, True), (900.01, False)):
            probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                       "codec_name": "h264", "pix_fmt": "yuv420p", "avg_frame_rate": "30/1"},
                       {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": str(duration)}}
            errors = []
            with patch("check_episode.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(probe))):
                qa.validate_video_specs(self.base / "fixture.mp4", "voice", errors, self.cfg)
            self.assertEqual(valid, not errors, errors)
        probe["format"]["duration"] = "600"
        probe["streams"][0].update(width=1080, height=1920)
        with patch("check_episode.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(probe))):
            errors = []
            qa.validate_video_specs(self.base / "fixture.mp4", "voice", errors, self.cfg)
            self.assertTrue(any("1920x1080" in error for error in errors))

    def audio_fixture(self):
        out = self.base / "out"
        out.mkdir(exist_ok=True)
        (out / "video.mp4").write_bytes(b"FIXTURE NOT MEDIA")
        (self.base / "voice.wav").write_bytes(b"FIXTURE NOT AUDIO")
        timeline = {"audio_mode": "voice", "sample_rate": 48000, "duration": 600, "total_frames": 18000,
                    "segments": [{"id": "cue", "spoken_text": "Fixture text", "start": 0, "end": 600,
                                  "start_frame": 0, "end_frame": 18000, "scene_id": "scene_start", "cut_id": "cut_start"}]}
        atomic_json(self.base / "timeline.json", timeline)
        video_hash = sha256_file(out / "video.mp4")
        timing_hash = qa.audio_timing_hash(timeline)
        analysis = {"status": "ASR_ANALYSIS_COMPLETE_AUDIO_REVIEW_REQUIRED", "video_sha256": video_hash,
                    "timeline_sha256": sha256_file(self.base / "timeline.json"),
                    "full_transcript": "Fixture text", "full_segments": [{"text": "Fixture text"}],
                    "acoustic_diagnostics": {"fixture": True}, "sentence_comparisons": [
                    {"id": "cue", "expected_for_comparison_only": "Fixture text", "normalized_edit_distance": 0}]}
        atomic_json(self.base / "audio_analysis.json", analysis)
        evidence = {"file": "audio_analysis.json", "sha256": sha256_file(self.base / "audio_analysis.json")}
        review = {"technical": {"status": "PASS", "video_sha256": video_hash,
                  "timeline_sha256": sha256_file(self.base / "timeline.json"), "final_audio_sha256": "a" * 64,
                  "audio_timing_hash": timing_hash, "asr_sha256": evidence["sha256"],
                  "checks": {key: {"status": "PASS", "observations": "Synthetic fixture only", "issues": [],
                                      "evidence": [evidence]} for key in qa.LONGFORM_AUDIO_CHECKS}}}
        return timeline, review, {}

    def approve_fixture(self, timeline, review, signoffs):
        """Only used under mocked actual PCM; never writes a production approval."""
        approval = {"status": "APPROVED", "reviewer_kind": "user", "scope": "full_final_mp4", "full_listened": True,
                    "statement": "SYNTHETIC TEST APPROVAL, not a real user", "approved_at": "fixture",
                    "video_sha256": review["technical"]["video_sha256"], "final_audio_sha256": "a" * 64,
                    "audio_timing_hash": qa.audio_timing_hash(timeline)}
        review["user_approval"] = approval
        signoffs["audio"] = {"user_approval_sha256": object_hash(approval), "video_sha256": approval["video_sha256"],
                             "final_audio_sha256": "a" * 64, "audio_timing_hash": approval["audio_timing_hash"]}

    def audio_errors(self, timeline, review, signoffs):
        errors = []
        with patch("check_episode.final_audio_sha256", return_value="a" * 64):
            qa.validate_longform_audio(self.base, self.cfg, timeline, review, signoffs, errors)
        return errors

    def test_technical_pass_needs_full_user_listening_and_no_model_requirement(self):
        timeline, review, signoffs = self.audio_fixture()
        errors = self.audio_errors(timeline, review, signoffs)
        self.assertEqual(1, len(errors), errors)
        self.assertTrue(errors[0].startswith("LONGFORM_LISTENING_PENDING"))
        review.update(status="PASS", reviewer_kind="local_audio_model", listened=True)
        self.assertTrue(self.audio_errors(timeline, review, signoffs))
        self.approve_fixture(timeline, review, signoffs)
        self.assertEqual([], self.audio_errors(timeline, review, signoffs))
        for field, value in (("full_listened", False), ("scope", "script_only"), ("reviewer_kind", "codex")):
            changed = copy.deepcopy(review)
            changed["user_approval"][field] = value
            self.assertTrue(any("LISTENING_PENDING" in x for x in self.audio_errors(timeline, changed, signoffs)))

    def test_approval_cannot_waive_unrun_or_stale_technical_evidence(self):
        timeline, review, signoffs = self.audio_fixture()
        self.approve_fixture(timeline, review, signoffs)
        review["technical"]["checks"]["group_boundaries"]["status"] = "NOT_RUN"
        self.assertTrue(any("group_boundaries" in x for x in self.audio_errors(timeline, review, signoffs)))
        (self.base / "audio_analysis.json").write_text("{}")
        self.assertTrue(any("ASR" in x or "audio diagnostic" in x for x in self.audio_errors(timeline, review, signoffs)))

    def test_asr_missing_duplicate_reordered_or_changed_text_is_rejected(self):
        timeline, review, signoffs = self.audio_fixture()
        self.approve_fixture(timeline, review, signoffs)
        analysis = load_json(self.base / "audio_analysis.json")
        analysis["sentence_comparisons"] *= 2
        atomic_json(self.base / "audio_analysis.json", analysis)
        self.assertTrue(any("missing/duplicated/reordered" in x for x in self.audio_errors(timeline, review, signoffs)))

    def test_visual_only_change_retains_approval_but_audio_or_timing_change_does_not(self):
        timeline, review, signoffs = self.audio_fixture()
        self.approve_fixture(timeline, review, signoffs)
        (self.base / "approved.mp4").write_bytes((self.base / "out/video.mp4").read_bytes())
        (self.base / "out/video.mp4").write_bytes(b"VISUAL REVISION FIXTURE")
        current = sha256_file(self.base / "out/video.mp4")
        review["user_approval"]["approved_video_path"] = "approved.mp4"
        review["technical"]["video_sha256"] = current
        analysis = load_json(self.base / "audio_analysis.json")
        analysis["video_sha256"] = current
        atomic_json(self.base / "audio_analysis.json", analysis)
        digest = sha256_file(self.base / "audio_analysis.json")
        review["technical"]["asr_sha256"] = digest
        for item in review["technical"]["checks"].values():
            item["evidence"][0]["sha256"] = digest
        signoffs["audio"].update(video_sha256=current, user_approval_sha256=object_hash(review["user_approval"]))
        timeline["segments"][0]["image"] = "new_visual.png"
        self.assertEqual([], self.audio_errors(timeline, review, signoffs))
        review["user_approval"]["final_audio_sha256"] = "b" * 64
        self.assertTrue(any("LISTENING_STALE" in x for x in self.audio_errors(timeline, review, signoffs)))
        review["user_approval"]["final_audio_sha256"] = "a" * 64
        timeline["segments"][0]["speech_start"] = 1
        self.assertTrue(any("LISTENING_STALE" in x for x in self.audio_errors(timeline, review, signoffs)))

    def test_visual_only_revision_reuses_unchanged_asr_with_verified_predecessor(self):
        timeline, review, signoffs = self.audio_fixture()
        self.approve_fixture(timeline, review, signoffs)
        prior_video = review["technical"]["video_sha256"]
        (self.base / "approved.mp4").write_bytes((self.base / "out/video.mp4").read_bytes())
        (self.base / "approved_timeline.json").write_bytes((self.base / "timeline.json").read_bytes())
        old_asr = (self.base / "audio_analysis.json").read_bytes()
        (self.base / "out/video.mp4").write_bytes(b"VISUAL ONLY FIXTURE")
        current = sha256_file(self.base / "out/video.mp4")
        timeline["segments"][0]["display_text"] = "Changed screen text"
        atomic_json(self.base / "timeline.json", timeline)
        review["user_approval"]["approved_video_path"] = "approved.mp4"
        review["technical"].update(video_sha256=current, timeline_sha256=sha256_file(self.base / "timeline.json"),
            asr_reuse={"source_video_path": "approved.mp4", "source_video_sha256": prior_video,
                       "source_timeline_path": "approved_timeline.json", "final_audio_sha256": "a" * 64,
                       "audio_timing_hash": qa.audio_timing_hash(timeline)})
        signoffs["audio"].update(video_sha256=current, user_approval_sha256=object_hash(review["user_approval"]))
        self.assertEqual([], self.audio_errors(timeline, review, signoffs))
        self.assertEqual(old_asr, (self.base / "audio_analysis.json").read_bytes())
        with patch("check_episode.final_audio_sha256", side_effect=["a" * 64, "b" * 64, "b" * 64]):
            errors = []
            qa.validate_longform_audio(self.base, self.cfg, timeline, review, signoffs, errors)
        self.assertTrue(any("verified identical" in item for item in errors))

    def test_image_source_detail_and_current_hash_are_required(self):
        atomic_json(self.base / "character_bible.json", {"fixture": True})
        atomic_json(self.base / "storyboard.json", {"fixture": True})
        records = []
        for name in image_names(self.cfg):
            (self.base / name).write_bytes(("FIXTURE " + name).encode())
        refs = {who + ".png": sha256_file(self.base / (who + ".png")) for who in ("a", "b")}
        for name in image_names(self.cfg):
            records.append({"file": name, "sha256": sha256_file(self.base / name), "status": "PASS",
                "full_observations": "Fixture view", "detail_observations": "Fixture hand/weapon review",
                "small_size_observations": "Fixture small-list view",
                "reviewed_at": "fixture", "generation_version": 1, "issues": [], "reference_hashes": refs,
                "character_bible_sha256": sha256_file(self.base / "character_bible.json"),
                "storyboard_sha256": sha256_file(self.base / "storyboard.json")})
        # No circular reference requirement for reference portraits.
        for row in records:
            if row["file"] in refs:
                row["reference_hashes"] = {}
        atomic_json(self.base / "image_review.json", {"images": records})
        errors = []
        qa.validate_image_reviews(self.base, errors, self.cfg)
        self.assertEqual([], errors)
        (self.base / "first.png").write_bytes(b"CHANGED FIXTURE")
        qa.validate_image_reviews(self.base, errors, self.cfg)
        self.assertTrue(any("stale" in x for x in errors))
        records[0]["detail_observations"] = ""
        atomic_json(self.base / "image_review.json", {"images": records})
        errors = []
        qa.validate_image_reviews(self.base, errors, self.cfg)
        self.assertTrue(any("detail" in x for x in errors))

    def test_final_cut_frames_need_current_observations_and_transition_samples(self):
        timeline = {"segments": [{"cut_id": cut["id"], "start_frame": i * 100, "end_frame": (i + 1) * 100}
                                   for i, cut in enumerate(self.cfg["cuts"])]}
        frames = [{"frame": i * 100, "sha256": str(i), "labels": []} for i in range(3)]
        review = {"frame_observations": [{"frame": x["frame"], "sha256": x["sha256"], "status": "PASS",
                   "issues": [], "observations": "Fixture actual composition", "full_resolution_viewed": True} for x in frames]}
        errors = []
        qa.validate_longform_frames(self.cfg, self.base, timeline, review, {"frames": frames}, errors)
        self.assertEqual([], errors)
        frames.append({"frame": 103, "sha256": "x", "labels": ["사진 교차전환 중간"]})
        qa.validate_longform_frames(self.cfg, self.base, timeline, review, {"frames": frames}, errors)
        self.assertTrue(any("transition" in x for x in errors))
        review["frame_observations"][0]["issues"] = ["Unresolved hand defect"]
        errors = []
        qa.validate_longform_frames(self.cfg, self.base, timeline, review, {"frames": frames}, errors)
        self.assertTrue(any("unresolved" in x for x in errors))

    def delivery_fixture(self):
        for filename in ("plan_matchups.py", "AGENTS.md"):
            (self.root / filename).write_text("fixture")
        atomic_json(self.root / "storage_config.json", {"schema_version": 1, "delivery_root": "전달본", "require_delivery_root": True})
        atomic_json(self.base / "episode.json", self.cfg)
        atomic_json(self.base / "manifest.json", {"job_id": self.cfg["job_id"], "status": "NEEDS_REVIEW"})
        for name, source in qa.DELIVERY_SOURCES.items():
            target = self.base / source
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text("{}" if target.suffix == ".json" else "SYNTHETIC FILE, NOT MEDIA")
        (self.base / "title.txt").write_text("Fixture longform title", encoding="utf-8")
        return self.base / "episode.json"

    def test_review_delivery_precedes_user_approval_and_does_not_complete(self):
        path = self.delivery_fixture()
        with patch("deliver_episode.validate_quality", return_value=["LONGFORM_LISTENING_PENDING: fixture full listening absent"]):
            result = delivery.deliver(path, update_state=False)
        self.assertEqual("NEEDS_REVIEW", result["status"])
        manifest = load_json(Path(result["delivery_path"]) / "manifest.json")
        self.assertEqual("longform", manifest["format"])
        self.assertTrue(manifest["audio_acceptance"]["listening_pending"])
        self.assertFalse((self.root / "done_longform.txt").exists())

    def test_technical_or_visual_failure_blocks_longform_review_delivery(self):
        path = self.delivery_fixture()
        with patch("deliver_episode.validate_quality", return_value=["LONGFORM_LISTENING_PENDING: absent", "Unresolved hand"]):
            with self.assertRaisesRegex(ValueError, "technical and visual"):
                delivery.deliver(path, update_state=False)
        self.assertFalse((self.root / "전달본/Fixture longform title").exists())

    def test_custom_longform_thumbnail_is_delivered_without_first_frame_requirement(self):
        self.cfg["thumb"] = "art/custom.png"
        path = self.delivery_fixture()
        custom = self.base / self.cfg["thumb"]
        custom.parent.mkdir()
        custom.write_bytes(b"CUSTOM THUMB FIXTURE")
        with patch("deliver_episode.validate_quality", return_value=["LONGFORM_LISTENING_PENDING: absent"]):
            result = delivery.deliver(path, update_state=False)
        self.assertEqual(custom.read_bytes(), (Path(result["delivery_path"]) / "thumbnail.png").read_bytes())

    def test_other_format_or_job_delivery_is_not_overwritten(self):
        path = self.delivery_fixture()
        prior = self.root / "전달본/Fixture longform title"
        prior.mkdir(parents=True)
        atomic_json(prior / "manifest.json", {"job_id": self.cfg["job_id"], "format": "shorts"})
        protected = (prior / "manifest.json").read_bytes()
        with patch("deliver_episode.validate_quality", return_value=["LONGFORM_LISTENING_PENDING: absent"]):
            with self.assertRaisesRegex(ValueError, "Exact title"):
                delivery.deliver(path, update_state=False)
        self.assertEqual(protected, (prior / "manifest.json").read_bytes())

    def test_done_only_history_blocks_a_duplicate_after_ledger_loss(self):
        self.pool(candidate())
        atomic_text(self.root / "done_longform.txt", "fixture-story\t인물가 vs 인물나\n")
        result = select_matchups(self.root, media_format="longform")
        self.assertEqual([], result["selections"])
        self.assertTrue(any("done history" in row["reason"] for row in result["skipped"]))
        self.assertFalse((self.root / "episodes").exists())

    def test_existing_review_folder_is_imported_and_resumed_without_new_job(self):
        item = candidate()
        self.pool(item)
        folder = self.root / "episodes/longform/001_existing"
        folder.mkdir(parents=True)
        cfg = {**self.cfg, **item, "job_id": "existing-job", "planning": {"idea_id": item["id"]}}
        atomic_json(folder / "episode.json", cfg)
        atomic_json(folder / "manifest.json", {"job_id": "existing-job", "status": "NEEDS_REVIEW"})
        result = select_matchups(self.root, media_format="longform", story_id=item["story_id"])
        self.assertEqual("existing-job", result["selections"][0]["job_id"])
        self.assertEqual("resume", result["selections"][0]["action"])
        self.assertEqual(1, len(list((self.root / "episodes/longform").iterdir())))

    def test_misbound_manifest_cannot_replace_another_story_delivery(self):
        path = self.delivery_fixture()
        prior = self.root / "전달본/Fixture longform title"
        prior.mkdir(parents=True)
        atomic_json(prior / "manifest.json", {"job_id": self.cfg["job_id"], "format": "longform",
                    "story_id": "other-story", "working_episode": str(self.root / "other/episode.json")})
        before = (prior / "manifest.json").read_bytes()
        with patch("deliver_episode.validate_quality", return_value=["LONGFORM_LISTENING_PENDING: absent"]):
            with self.assertRaisesRegex(ValueError, "another longform story"):
                delivery.deliver(path, update_state=False)
        self.assertEqual(before, (prior / "manifest.json").read_bytes())

    def test_misbound_ledger_prevents_delivery_before_writes(self):
        path = self.delivery_fixture()
        atomic_json(self.root / "state/project_state_longform.json", {"jobs": [{"job_id": self.cfg["job_id"],
                    "format": "longform", "story_id": "other-story", "episode_path": "other/episode.json"}]})
        with self.assertRaisesRegex(ValueError, "matching ledger"):
            delivery.deliver(path, update_state=False)
        self.assertFalse((self.root / "전달본").exists())

    def test_actual_upload_thumbnail_requires_small_list_and_full_review(self):
        path = self.delivery_fixture()
        (self.base / "upload_thumbnail.png").write_bytes(b"replacement thumbnail fixture")
        record = {"status": "PASS", "sha256": sha256_file(self.base / "upload_thumbnail.png"),
                  "source_sha256": sha256_file(self.base / "thumb.png"), "observations": "fixture"}
        atomic_json(self.base / "upload_thumbnail_review.json", record)
        with self.assertRaisesRegex(ValueError, "full/detail/small-list"):
            qa.delivery_source(self.base, "thumbnail.png")
        record.update(full_observations="fixture", detail_observations="fixture", small_size_observations="fixture", issues=[])
        atomic_json(self.base / "upload_thumbnail_review.json", record)
        self.assertEqual("upload_thumbnail.png", qa.delivery_source(self.base, "thumbnail.png"))

    def test_longform_duplicate_pixels_still_require_actual_image_reviews(self):
        for name in image_names(self.cfg):
            (self.base / name).write_bytes(b"fixture copied asset")
        pixel = SimpleNamespace(size=(1920, 1080), tobytes=lambda: b"identical fixture pixels")
        image = SimpleNamespace(width=1920, height=1080, size=(1920, 1080), load=lambda: None,
                                convert=lambda _: pixel)
        from unittest.mock import MagicMock
        context = MagicMock()
        context.__enter__.return_value = image
        errors = []
        with patch("check_episode.Image.open", return_value=context):
            qa.check_images(self.cfg, self.base, errors)
        self.assertEqual([], errors)
        qa.validate_image_reviews(self.base, errors, self.cfg)
        self.assertTrue(any("UNREVIEWED" in error for error in errors))

    def test_removed_short_link_does_not_ship_an_old_comment(self):
        path = self.delivery_fixture()
        (self.base / "linked_short_comment.txt").write_text("Stale unrelated draft")
        (self.base / "out/frame.png").write_bytes(b"fixture frame")
        atomic_json(self.base / "out/review_frames.json", {"frames": [{"file": "frame.png", "sha256": sha256_file(self.base / "out/frame.png")}]})
        with patch("deliver_episode.validate_quality", return_value=[]), patch("deliver_episode.validate_completion", return_value=[]):
            result = delivery.deliver(path, update_state=False)
        self.assertFalse((Path(result["delivery_path"]) / "linked_short_comment.txt").exists())
        self.assertTrue((self.base / "linked_short_comment.txt").exists())

    def test_chapters_are_final_boundaries_and_link_comment_distinguishes_new_event(self):
        timeline = {"segments": [{"id": "one", "scene_id": "scene_start", "start_frame": 0},
                                 {"id": "two", "scene_id": "scene_end", "start_frame": 3720}]}
        self.cfg["scenes"][0]["title"] = "Opening event"
        self.cfg["scenes"][1]["title"] = "Final choice"
        self.assertEqual(["00:00 Opening event", "02:04 Final choice"], qa.longform_chapters(self.cfg, timeline))
        self.cfg["chapters"] = [{"title": "One chapter across two scenes", "cue_id": "one"}]
        self.assertEqual(["00:00 One chapter across two scenes"], qa.longform_chapters(self.cfg, timeline))
        self.cfg["planning"] = {"related_short": {"relationship": "new_event"}}
        self.assertIn("새로운 이야기", qa.linked_short_comment(self.cfg))
        self.cfg["planning"]["related_short"]["relationship"] = "continuation"
        self.assertEqual("이 대결의 전체 이야기는 롱폼에서 확인하세요. [롱폼 영상 링크]", qa.linked_short_comment(self.cfg))


if __name__ == "__main__":
    unittest.main()
