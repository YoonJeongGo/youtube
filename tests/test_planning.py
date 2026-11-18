import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plan_matchups import CRITERIA, RECOGNIZABLE_PROFILE, SELF_SCORE_KEYS, IdentityRegistry, candidate_errors, select_matchups
from project_state import ProjectState, atomic_json, atomic_text, digest_text, load_json


def idea(a="인물가", b="인물나", aid="person:a", bid="person:b", cid="idea-1"):
    return {
        "id": cid, "format": "one_on_one",
        "a": {"id": aid, "name": a, "version": "테스트용 판본 A"},
        "b": {"id": bid, "name": b, "version": "테스트용 판본 B"},
        "eligibility": {"status": "ALLOWED", "reason": "Test fixture only, never production evidence", "evidence": ["test-only"]},
        "criteria": {key: {"pass": True, "reason": "Test-only reason"} for key in CRITERIA},
        "premise": "서로 다른 무기를 비교해요.", "core_question": "거리가 바뀌면 어떨까요?",
        "conditions": {"a": "가까운 거리", "b": "먼 거리", "comparison_axis": "거리", "fictional": True},
        "hook": "거리가 바뀌면 어떨까요?", "title": a + " vs " + b,
        "pinned_comment": "어느 조건을 고르실까요?",
        "sources": [{"url": "https://example.invalid/test-fixture", "work": "Fixture", "locator": "1", "checked_at": "2026-11-16", "summary": "Not real evidence", "read": True}],
        "unresolved": [],
    }


def recognizable_idea(*args, **kwargs):
    value = idea(*args, **kwargs)
    source = {"url": value["sources"][0]["url"], "locator": "Test-only source passage"}
    value.update(
        planning_profile=RECOGNIZABLE_PROFILE, paired_by=[1, 2],
        paired_by_reason="Test-only role and skill contrast",
        premise_specific={"pass": True, "reason": "Test-only identity-dependent scene"},
        bait={"type": "c", "text": "Test-only conditional reversal", "source_locator": source["locator"], "url": source["url"]},
        final_question="조건이 바뀌면 누가 먼저 움직일까요?",
        self_scores={key: 4 for key in SELF_SCORE_KEYS},
        self_score_reasons={key: "Test-only editorial reason, not measured data" for key in SELF_SCORE_KEYS},
        scoring_basis="editorial_judgment_not_empirical",
    )
    for who in ("a", "b"):
        value[who].update(pre1900=True, version_allowed=True, origin="Old-source test fixture",
                          source=dict(source), recognition={"clear": True, "reason": "Test-only Korean-audience editorial judgment"})
    value["conditions"].update(opposed=True, opposed_reason="Test-only opposite states of one axis",
                                evidence={who: dict(source) for who in ("a", "b")})
    return value


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "episodes").mkdir()
        atomic_text(self.root / "queue.txt", "")
        atomic_text(self.root / "done.txt", "")

    def tearDown(self):
        self.temp.cleanup()

    def pool(self, *candidates, aliases=None, default_profile=None):
        pool = {"schema_version": 1, "aliases": aliases or {}, "candidates": list(candidates)}
        if default_profile is not None:
            pool["default_planning_profile"] = default_profile
        atomic_json(self.root / "matchup_ideas.json", pool)

    def test_default_profile_blocks_legacy_new_reservations_and_remakes(self):
        for source in ("new", "queue", "explicit", "remake"):
            with self.subTest(source=source):
                self.pool(idea(), default_profile=RECOGNIZABLE_PROFILE)
                atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n" if source == "queue" else "")
                atomic_text(self.root / "done.txt", "인물가 vs 인물나\n" if source == "remake" else "")
                result = select_matchups(self.root,
                    requested="인물가 vs 인물나" if source in {"explicit", "remake"} else None,
                    remake=source == "remake")
                self.assertEqual([], result["selections"])
                self.assertEqual(0, result["attempts_used"])
                self.assertTrue(any("require planning_profile=" in item["reason"] for item in result["research_needed"]))
                self.assertEqual([], list((self.root / "episodes").iterdir()))
        self.pool(idea(), default_profile="unknown_profile")
        with self.assertRaisesRegex(ValueError, "Unknown default_planning_profile"):
            select_matchups(self.root)

    def test_default_profile_ranks_only_new_discoveries_by_valid_score(self):
        low = recognizable_idea()
        high = recognizable_idea("다", "라", "person:c", "person:d", "high")
        high["self_scores"] = {key: 5 for key in SELF_SCORE_KEYS}
        invalid = recognizable_idea("마", "바", "person:e", "person:f", "invalid")
        invalid["self_scores"] = {key: 99 for key in SELF_SCORE_KEYS}
        self.pool(low, invalid, high, default_profile=RECOGNIZABLE_PROFILE)
        result = select_matchups(self.root, force_new=True)
        self.assertEqual("high", result["selections"][0]["candidate_id"])
        # Even the lower score retains queue priority on a subsequent run.
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        queued = select_matchups(self.root)
        self.assertEqual("idea-1", queued["selections"][0]["candidate_id"])
        self.assertEqual("queue", queued["selections"][0]["source"])

    def test_default_profile_preserves_existing_legacy_resume(self):
        self.pool(idea())
        original = select_matchups(self.root)["selections"][0]
        self.pool(idea(), default_profile=RECOGNIZABLE_PROFILE)
        for status in ("FAILED", "NEEDS_REVIEW"):
            with self.subTest(status=status):
                with ProjectState(self.root) as state:
                    state.set_status(original["job_id"], status, "Test-only interruption")
                result = select_matchups(self.root, "인물가 vs 인물나")
                resumed = result["selections"][0]
                self.assertEqual(original["job_id"], resumed["job_id"])
                self.assertEqual(original["episode_path"], resumed["episode_path"])
                self.assertEqual("resume", resumed["action"])
                self.assertNotIn("planning_profile", resumed["plan"])
                self.assertEqual(1, len(list((self.root / "episodes").iterdir())))
        # Existing IN_PROGRESS work still obeys run locking, and can resume
        # its old plan once the previous run is explicitly finished.
        with ProjectState(self.root) as state:
            state.finish_run(result["run_id"])
        resumed = select_matchups(self.root, "인물가 vs 인물나")["selections"][0]
        self.assertEqual(original["job_id"], resumed["job_id"])
        self.assertEqual("resume", resumed["action"])

    def test_new_discoveries_prefer_distinct_participants_then_report_reuse(self):
        first = recognizable_idea("홈즈", "가", "person:holmes", "person:a", "first")
        repeat = recognizable_idea("홈즈", "나", "person:holmes", "person:b", "repeat")
        distinct = recognizable_idea("다", "라", "person:c", "person:d", "distinct")
        first["self_scores"] = {key: 5 for key in SELF_SCORE_KEYS}
        repeat["self_scores"].update(scene_uniqueness=5, bait_strength=5)
        self.pool(first, repeat, distinct, default_profile=RECOGNIZABLE_PROFILE)
        result = select_matchups(self.root, count=3, force_new=True)
        self.assertEqual(["first", "distinct", "repeat"], [x["candidate_id"] for x in result["selections"]])
        self.assertEqual(["person:holmes"], result["selections"][2]["reused_person_ids"])
        self.assertIn("No eligible fresh candidate", result["selections"][2]["selection_reason"])
        # Persisted plans retain their order and original editorial scores.
        stored = load_json(self.root / "matchup_ideas.json")["candidates"]
        self.assertEqual(["first", "repeat", "distinct"], [x["id"] for x in stored])
        self.assertEqual(14, sum(stored[1]["self_scores"].values()))

    def test_explicit_and_queue_order_are_not_reordered_for_diversity(self):
        first = recognizable_idea("홈즈", "가", "person:holmes", "person:a", "first")
        repeat = recognizable_idea("홈즈", "나", "person:holmes", "person:b", "repeat")
        distinct = recognizable_idea("다", "라", "person:c", "person:d", "distinct")
        for source in ("explicit", "queue"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                atomic_json(root / "matchup_ideas.json", {"schema_version": 1,
                    "default_planning_profile": RECOGNIZABLE_PROFILE, "candidates": [distinct, repeat, first]})
                lines = ["홈즈 vs 가", "홈즈 vs 나", "다 vs 라"]
                atomic_text(root / "queue.txt", "\n".join(lines) if source == "queue" else "")
                result = select_matchups(root, requested=lines if source == "explicit" else None, count=3)
                self.assertEqual(["first", "repeat", "distinct"], [x["candidate_id"] for x in result["selections"]])

    def test_recognizable_profile_valid_and_old_candidate_unchanged(self):
        self.assertEqual([], candidate_errors(recognizable_idea()))
        self.assertEqual([], candidate_errors(idea()))
        old = idea()
        old["planning_profile"] = "misspelled_profile"
        self.assertTrue(any("Unknown planning_profile" in e for e in candidate_errors(old)))

    def test_recognizable_origin_version_and_unclear_names_rejected(self):
        mutations = [
            (lambda c: c["a"].update(pre1900=False), "origin before 1900"),
            (lambda c: c["a"].update(pre1900=1), "origin before 1900"),
            (lambda c: c["b"].update(version_allowed=False), "old-source version"),
            (lambda c: c["a"].update(origin=""), "origin/work"),
            (lambda c: c["a"]["source"].update(url="https://example.invalid/unread"), "read primary source"),
            (lambda c: c["b"]["recognition"].update(clear=False), "name recognition"),
            (lambda c: c["a"]["recognition"].update(reason=""), "name recognition"),
        ]
        for mutation, marker in mutations:
            with self.subTest(marker=marker):
                candidate = recognizable_idea()
                mutation(candidate)
                self.assertTrue(any(marker in e for e in candidate_errors(candidate)), candidate_errors(candidate))

    def test_recognizable_pairing_specific_scene_opposition_and_bait_required(self):
        mutations = [
            (lambda c: c.update(paired_by=[]), "paired_by"),
            (lambda c: c.update(paired_by=[True]), "paired_by"),
            (lambda c: c.update(paired_by=[1, 1]), "paired_by"),
            (lambda c: c.update(paired_by=[4]), "paired_by"),
            (lambda c: c.update(paired_by_reason=""), "paired_by_reason"),
            (lambda c: c["premise_specific"].update(pass_=False, reason=""), "premise_specific"),
            (lambda c: c["conditions"].update(opposed=False), "opposed=true"),
            (lambda c: c["conditions"]["evidence"].pop("b"), "conditions.evidence.b"),
            (lambda c: c["bait"].update(type="d"), "bait.type"),
            (lambda c: c["bait"].update(source_locator=""), "bait source locator"),
            (lambda c: c["bait"].update(url="https://example.invalid/unread"), "bait URL"),
            (lambda c: c.update(final_question="확정된 승리입니다."), "final_question"),
        ]
        for mutation, marker in mutations:
            with self.subTest(marker=marker):
                candidate = recognizable_idea()
                mutation(candidate)
                self.assertTrue(any(marker in e for e in candidate_errors(candidate)), candidate_errors(candidate))

    def test_editorial_scores_threshold_types_reasons_and_nonempirical_basis(self):
        for score in (True, 0, 6, 4.0, "4"):
            with self.subTest(score=score):
                candidate = recognizable_idea()
                candidate["self_scores"]["recognition"] = score
                self.assertTrue(any("integer from 1 to 5" in e for e in candidate_errors(candidate)))
        candidate = recognizable_idea()
        candidate["self_scores"]["recognition"] = 3
        self.assertTrue(any("11/15 is below 12" in e for e in candidate_errors(candidate)))
        candidate = recognizable_idea()
        candidate["self_scores"]["total"] = 15
        self.assertTrue(any("does not match" in e for e in candidate_errors(candidate)))
        candidate = recognizable_idea()
        candidate["scoring_basis"] = "predicted_views"
        candidate["self_score_reasons"]["bait_strength"] = ""
        errors = candidate_errors(candidate)
        self.assertTrue(any("not views" in e for e in errors), errors)
        self.assertTrue(any("self_score_reasons.bait_strength" in e for e in errors), errors)

    def test_new_profile_does_not_override_prohibition_or_duplicate_history(self):
        blocked = recognizable_idea()
        blocked["eligibility"] = {"status": "BLOCKED", "reason": "Current worship prohibited", "evidence": ["test-only"]}
        blocked["self_scores"] = {key: 5 for key in SELF_SCORE_KEYS}
        self.assertEqual(["Current worship prohibited"], candidate_errors(blocked))
        self.pool(recognizable_idea())
        atomic_text(self.root / "done.txt", "인물나 vs 인물가\n")
        result = select_matchups(self.root, force_new=True)
        self.assertEqual([], result["selections"])
        self.assertEqual(0, result["attempts_used"])

    def test_ten_plans_are_not_ten_attempts_and_under_threshold_never_reserved(self):
        candidates = [recognizable_idea(f"A{i}", f"B{i}", f"a:{i}", f"b:{i}", f"idea-{i}") for i in range(10)]
        candidates[0]["self_scores"]["recognition"] = 3
        self.pool(*candidates)
        self.assertFalse((self.root / "state/project_state.json").exists())
        result = select_matchups(self.root)
        self.assertEqual(1, result["attempts_used"])
        self.assertEqual("idea-1", result["selections"][0]["candidate_id"])
        self.assertTrue(any("11/15" in x["reason"] for x in result["research_needed"]))

    def test_explicit_beats_queue(self):
        q = idea()
        explicit = idea("다", "라", "person:c", "person:d", "idea-2")
        self.pool(q, explicit)
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        result = select_matchups(self.root, "다 vs 라")
        self.assertEqual(result["selections"][0]["candidate_id"], "idea-2")
        self.assertEqual(result["attempts_used"], 1)

    def test_queue_order_then_auto_fallback(self):
        first, second = idea(), idea("다", "라", "person:c", "person:d", "idea-2")
        self.pool(first, second)
        atomic_text(self.root / "queue.txt", "다 vs 라\n")
        result = select_matchups(self.root)
        self.assertEqual(result["selections"][0]["candidate_id"], "idea-2")
        # New candidate is used only after queue no longer offers eligible work.
        job = result["selections"][0]
        with ProjectState(self.root) as state:
            state.set_status(job["job_id"], "NEEDS_REVIEW", "Fixture")
        atomic_text(self.root / "queue.txt", "")
        next_result = select_matchups(self.root)
        self.assertEqual(next_result["selections"][0]["candidate_id"], "idea-1")

    def test_unknown_queue_requires_research_before_new(self):
        self.pool(idea())
        atomic_text(self.root / "queue.txt", "미확인인물 vs 다른인물\n")
        result = select_matchups(self.root)
        self.assertEqual(result["status"], "NEEDS_RESEARCH")
        self.assertEqual(result["attempts_used"], 0)
        self.assertEqual(result["research_needed"][0]["source"], "queue")

    def test_force_new_avoids_queue_reversed_and_done(self):
        a = idea()
        b = idea("다", "라", "person:c", "person:d", "idea-2")
        c = idea("마", "바", "person:e", "person:f", "idea-3")
        self.pool(a, b, c)
        atomic_text(self.root / "queue.txt", "인물나 vs 인물가\n")
        atomic_text(self.root / "done.txt", "라 vs 다\n")
        result = select_matchups(self.root, force_new=True)
        self.assertEqual(result["selections"][0]["candidate_id"], "idea-3")

    def test_alias_symmetric_key_without_fuzzy_merge(self):
        registry = IdentityRegistry({})
        self.assertEqual(registry.line_key("여포 vs 척준경"), registry.line_key("Cheok Jun-gyeong vs Lü Bu"))
        self.assertNotEqual(registry.resolve("Lu Boo"), registry.resolve("Lu Bu"))
        with self.assertRaises(ValueError):
            registry.line_key("손오공 vs 제천대성")

    def test_ambiguous_alias_rejected(self):
        with self.assertRaises(ValueError):
            IdentityRegistry({"aliases": {"여포": "not-lu-bu"}})

    def test_failed_and_review_jobs_resume_same_path(self):
        self.pool(idea())
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        first = select_matchups(self.root)
        job = first["selections"][0]
        with ProjectState(self.root) as state:
            state.set_status(job["job_id"], "FAILED", "fixture failure")
        second = select_matchups(self.root)
        resumed = second["selections"][0]
        self.assertEqual(resumed["job_id"], job["job_id"])
        self.assertEqual(resumed["episode_path"], job["episode_path"])
        self.assertEqual(resumed["action"], "resume")
        self.assertEqual(len(list((self.root / "episodes").iterdir())), 1)
        with ProjectState(self.root) as state:
            state.set_status(job["job_id"], "NEEDS_REVIEW", "audio requires listening")
        third = select_matchups(self.root)
        self.assertEqual(third["selections"][0]["job_id"], job["job_id"])

    def test_in_progress_is_not_duplicated(self):
        self.pool(idea())
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        first = select_matchups(self.root)
        second = select_matchups(self.root)
        self.assertFalse(second["selections"])
        self.assertIn("IN_PROGRESS", second["skipped"][0]["reason"])
        self.assertEqual(len(list((self.root / "episodes").iterdir())), 1)

    def test_blocked_queue_does_not_count_as_attempt(self):
        blocked = idea()
        blocked["eligibility"] = {"status": "BLOCKED", "reason": "Test prohibited identity", "evidence": ["fixture"]}
        allowed = idea("다", "라", "person:c", "person:d", "idea-2")
        self.pool(blocked, allowed)
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n다 vs 라\n")
        result = select_matchups(self.root)
        self.assertEqual(result["attempts_used"], 1)
        self.assertEqual(result["selections"][0]["candidate_id"], "idea-2")
        self.assertEqual(len(result["skipped"]), 1)
        same_run = select_matchups(self.root, run_id=result["run_id"])
        self.assertEqual(same_run["status"], "ATTEMPT_LIMIT_REACHED")
        self.assertFalse(same_run["selections"])
        self.assertEqual(len(load_json(self.root / "state/project_state.json")["skipped"]), 1)

    def test_maximum_five_attempts_and_reject_zero(self):
        self.pool(*(idea(f"A{i}", f"B{i}", f"a:{i}", f"b:{i}", f"idea-{i}") for i in range(8)))
        result = select_matchups(self.root, count=10)
        self.assertEqual(len(result["selections"]), 5)
        self.assertEqual(result["attempt_limit"], 5)
        with self.assertRaises(ValueError):
            select_matchups(self.root, count=0)

    def test_expansion_and_incomplete_research_are_not_selected(self):
        expansion = idea()
        expansion["format"] = "three_way"
        expansion["participants"] = ["A", "B", "C"]
        self.pool(expansion)
        result = select_matchups(self.root)
        self.assertFalse(result["selections"])
        self.assertTrue(result["held"])
        self.assertIn("EXPANSION_HOLD", result["held"][0]["reason"])
        incomplete = idea()
        incomplete["unresolved"] = [{"question": "Did the episode occur?", "blocking": True}]
        self.assertTrue(candidate_errors(incomplete))

    def test_episode_folder_import_prevents_duplicate(self):
        plan = idea()
        self.pool(plan)
        folder = self.root / "episodes/006_old"
        folder.mkdir()
        atomic_json(folder / "episode.json", {"a": {"name": "인물나"}, "b": {"name": "인물가"}})
        atomic_json(folder / "manifest.json", {"job_id": "existing-job", "status": "NEEDS_REVIEW"})
        result = select_matchups(self.root, "인물가 vs 인물나")
        self.assertEqual(result["selections"][0]["job_id"], "existing-job")
        self.assertEqual(result["selections"][0]["action"], "resume")
        self.assertEqual(len(list((self.root / "episodes").iterdir())), 1)

    def test_completion_gate_blocks_unreviewed_job(self):
        self.pool(idea())
        result = select_matchups(self.root)
        job = result["selections"][0]
        atomic_json((self.root / job["episode_path"]).parent / "manifest.json", {"job_id": job["job_id"]})
        with patch("check_episode.validate_completion", return_value=["AUDIO_REVIEW_REQUIRED"], create=True):
            with ProjectState(self.root) as state:
                with self.assertRaisesRegex(ValueError, "AUDIO_REVIEW_REQUIRED"):
                    state.set_status(job["job_id"], "COMPLETE")
        self.assertEqual((self.root / "done.txt").read_text(), "")
        self.assertEqual(load_json(self.root / "state/project_state.json")["jobs"][0]["status"], "IN_PROGRESS")

    def test_complete_transaction_recovers_queue_and_done_once(self):
        self.pool(idea())
        registry = IdentityRegistry(load_json(self.root / "matchup_ideas.json"))
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n인물나 vs 인물가\n")
        result = select_matchups(self.root)
        job = result["selections"][0]
        atomic_json((self.root / job["episode_path"]).parent / "manifest.json", {"job_id": job["job_id"]})
        with patch("check_episode.validate_completion", return_value=[], create=True):
            with ProjectState(self.root, registry.line_key) as state:
                with patch.object(state, "recover", side_effect=OSError("simulated crash before queue replace")):
                    with self.assertRaises(OSError):
                        state.set_status(job["job_id"], "COMPLETE")
        self.assertTrue(load_json(self.root / "state/project_state.json")["pending_transaction"])
        with ProjectState(self.root, registry.line_key) as state:
            self.assertIsNone(state.data["pending_transaction"])
        self.assertEqual((self.root / "queue.txt").read_text(encoding="utf-8"), "")
        self.assertEqual((self.root / "done.txt").read_text(encoding="utf-8"), "인물가 vs 인물나\n")
        with ProjectState(self.root, registry.line_key):
            pass
        self.assertEqual((self.root / "done.txt").read_text(encoding="utf-8").count(" vs "), 1)

    def test_recovery_preserves_intervening_user_edits(self):
        with ProjectState(self.root) as state:
            state.data["pending_transaction"] = {
                "queue.txt": {"before_hash": digest_text(""), "after": "planned\n"},
                "done.txt": {"before_hash": digest_text(""), "after": "done\n"},
            }
            state.save()
        atomic_text(self.root / "queue.txt", "user-added\n")
        with self.assertRaisesRegex(RuntimeError, "Recovery conflict"):
            with ProjectState(self.root):
                pass
        self.assertEqual((self.root / "queue.txt").read_text(), "user-added\n")
        self.assertEqual((self.root / "done.txt").read_text(), "")

    def test_os_lock_prevents_concurrent_state_mutation(self):
        with ProjectState(self.root):
            with self.assertRaises(RuntimeError):
                with ProjectState(self.root):
                    pass

    def test_ended_interrupted_run_resumes_same_job(self):
        self.pool(idea())
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        first = select_matchups(self.root)
        with ProjectState(self.root) as state:
            state.finish_run(first["run_id"])
        second = select_matchups(self.root)
        self.assertEqual(second["selections"][0]["job_id"], first["selections"][0]["job_id"])
        self.assertEqual(second["selections"][0]["action"], "resume")

    def test_skip_can_be_reconsidered_after_research_correction(self):
        candidate = idea()
        candidate["eligibility"]["status"] = "BLOCKED"
        self.pool(candidate)
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        first = select_matchups(self.root)
        self.assertFalse(first["selections"])
        candidate["eligibility"]["status"] = "ALLOWED"
        self.pool(candidate)
        second = select_matchups(self.root)
        self.assertEqual(second["selections"][0]["candidate_id"], candidate["id"])

    def test_new_alias_reconciles_existing_unfinished_identity(self):
        self.pool(idea())
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        first = select_matchups(self.root)
        job_id = first["selections"][0]["job_id"]
        with ProjectState(self.root) as state:
            state.set_status(job_id, "FAILED", "fixture")
            job = state.get_job(job_id)
            job["pair_key"] = "unresolved:인물가|unresolved:인물나"
            job["person_ids"] = ["unresolved:인물가", "unresolved:인물나"]
            state.save()
        second = select_matchups(self.root)
        self.assertEqual(second["selections"][0]["job_id"], job_id)
        self.assertEqual(len(list((self.root / "episodes").iterdir())), 1)
        data = load_json(self.root / "state/project_state.json")
        self.assertTrue(data["jobs"][0]["identity_history"])

    def test_state_lock_precedes_pool_read(self):
        self.pool(idea())
        import plan_matchups
        original = plan_matchups.load_json
        def assert_locked(path, default=None):
            if Path(path).name == "matchup_ideas.json":
                with self.assertRaises(RuntimeError):
                    with ProjectState(self.root):
                        pass
            return original(path, default)
        with patch("plan_matchups.load_json", side_effect=assert_locked):
            result = select_matchups(self.root)
        self.assertEqual(result["attempts_used"], 1)

    def test_prohibited_plan_does_not_require_unnecessary_creative_fields(self):
        blocked = {"id": "blocked", "format": "one_on_one", "a": {"id": "person:a", "name": "인물가", "version": "fixture"}, "b": {"id": "person:b", "name": "인물나", "version": "fixture"}, "eligibility": {"status": "BLOCKED", "reason": "Direct exclusion reason", "evidence": [{"url": "https://example.invalid/eligibility", "summary": "fixture only"}]}}
        self.pool(blocked)
        atomic_text(self.root / "queue.txt", "인물가 vs 인물나\n")
        result = select_matchups(self.root)
        skipped = result["skipped"][0]
        self.assertEqual(skipped["reason"], "blocked: Direct exclusion reason")
        self.assertEqual(skipped["source"], "queue")
        self.assertEqual(skipped["evidence"], blocked["eligibility"]["evidence"])
        self.assertEqual(result["attempts_used"], 0)

    def test_completion_rejects_manifest_from_another_job(self):
        self.pool(idea())
        result = select_matchups(self.root)
        job = result["selections"][0]
        atomic_json((self.root / job["episode_path"]).parent / "manifest.json", {"job_id": "another-job"})
        with patch("check_episode.validate_completion", return_value=[]) as gate:
            with ProjectState(self.root) as state:
                with self.assertRaisesRegex(ValueError, "job_id does not match"):
                    state.set_status(job["job_id"], "COMPLETE")
            gate.assert_not_called()
        self.assertEqual((self.root / "done.txt").read_text(), "")


if __name__ == "__main__":
    unittest.main()
