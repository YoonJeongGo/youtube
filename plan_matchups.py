"""Select researched 1:1 plans; the Codex session supplies research and media tools.

This module never invents citations or claims to perform web/image generation.
An empty/insufficient idea pool returns NEEDS_RESEARCH so the active session can
research additional candidates and call selection again.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from project_state import ProjectState, atomic_json, load_json, now, read_text
from episode_core import DEFAULT_PRODUCTION_PROFILE, DETAIL_PROFILE, STRUCTURED_PROFILES

CRITERIA = ("clear_pairing_reason", "contrast_or_commonality", "primary_sources", "both_strength_conditions", "distinct_visual_scenes", "understandable_in_20_45s", "variety_from_history")
REQUIRED_PLAN = ("premise", "core_question", "hook", "title", "pinned_comment")
RECOGNIZABLE_PROFILE = "recognizable_story_v1"
SELF_SCORE_KEYS = ("scene_uniqueness", "bait_strength", "recognition")
BUILTIN_IDENTITIES = {
    "history:lu_bu": ["여포", "呂布", "吕布", "Lu Bu", "Lü Bu"],
    "history:cheok_jun_gyeong": ["척준경", "拓俊京", "Cheok Jun-gyeong"],
    "myth:zeus": ["제우스", "Zeus"],
    "myth:thor": ["토르", "Thor"],
    "novel:sun_wukong": ["손오공", "제천대성", "Sun Wukong", "孫悟空"],
    "epic:achilles": ["아킬레우스", "Achilles"],
    "epic:siegfried": ["지크프리트", "Siegfried"],
    "legend:robin_hood": ["로빈 후드", "로빈후드", "Robin Hood"],
    "novel:hong_gil_dong": ["홍길동", "Hong Gil-dong"],
    "history:gwanggaeto": ["광개토대왕", "광개토왕", "Gwanggaeto"],
    "history:alexander_iii": ["알렉산더", "알렉산더 대왕", "Alexander the Great"],
}


def name_key(name):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", str(name)).casefold())


class IdentityRegistry:
    def __init__(self, pool):
        self.aliases = {}
        for canonical, aliases in BUILTIN_IDENTITIES.items():
            self.add(canonical, canonical)
            for alias in aliases:
                self.add(alias, canonical)
        for alias, canonical in pool.get("aliases", {}).items():
            self.add(alias, canonical)
        for idea in pool.get("candidates", []):
            for who in ("a", "b"):
                person = idea.get(who, {})
                if person.get("id") and person.get("name"):
                    self.add(person["id"], person["id"])
                    self.add(person["name"], person["id"])
                    for alias in person.get("aliases", []):
                        self.add(alias, person["id"])

    def add(self, alias, canonical):
        if not isinstance(canonical, str) or not canonical.strip() or "|" in canonical:
            raise ValueError("Canonical identity must be a nonempty string without |")
        key = name_key(alias)
        if key in self.aliases and self.aliases[key] != canonical:
            raise ValueError(f"Ambiguous alias {alias!r}; supply an unambiguous canonical person ID")
        self.aliases[key] = canonical

    def resolve(self, name):
        return self.aliases.get(name_key(name), "unresolved:" + name_key(name))

    def pair_key(self, a, b):
        ids = [self.resolve(a), self.resolve(b)]
        if ids[0] == ids[1]:
            raise ValueError("A person cannot face the same identity/alias")
        return "|".join(sorted(ids))

    def line_key(self, line):
        a, b = parse_pair(line)
        return self.pair_key(a, b)


def parse_pair(line):
    parts = re.split(r"\s+(?:vs\.?|versus|대)\s+", line.strip(), flags=re.I)
    if len(parts) != 2 or not all(parts):
        raise ValueError("Only two named individuals in 'A vs B' format are supported")
    return parts


def candidate_errors(idea):
    errors = []
    if idea.get("format") != "one_on_one":
        return ["EXPANSION_HOLD: three-way/army formats are not supported; preserve the original participants"]
    eligibility = idea.get("eligibility", {})
    if eligibility.get("status") == "BLOCKED":
        return [eligibility.get("reason") or "Prohibited matchup under the eligibility rules"]
    for who in ("a", "b"):
        person = idea.get(who, {})
        for key in ("id", "name", "version"):
            if not isinstance(person.get(key), str) or not person[key].strip():
                errors.append(f"{who}.{key} is required")
    if eligibility.get("status") != "ALLOWED":
        errors.append(eligibility.get("reason") or "Eligibility has not been verified")
    if not eligibility.get("reason") or not eligibility.get("evidence"):
        errors.append("Eligibility needs an explicit research reason and evidence")
    if not idea.get("id"):
        errors.append("Candidate id is required")
    for key in REQUIRED_PLAN:
        if not isinstance(idea.get(key), str) or not idea[key].strip():
            errors.append(f"{key} is required")
    for key in CRITERIA:
        item = idea.get("criteria", {}).get(key)
        # Each decision must contain its reasoning; booleans alone conceal missing review.
        if not isinstance(item, dict) or item.get("pass") is not True or not item.get("reason"):
            errors.append(f"Selection criterion not supported: {key}")
    conditions = idea.get("conditions", {})
    if not conditions.get("a") or not conditions.get("b") or conditions.get("a") == conditions.get("b"):
        errors.append("Two linked fictional exchanges are required" if idea.get("production_profile") == DETAIL_PROFILE
                      else "Two distinct, comparable fictional conditions are required")
    if not conditions.get("comparison_axis") or conditions.get("fictional") is not True:
        errors.append("Conditions need a shared comparison axis and explicit fictional=true")
    sources = idea.get("sources", [])
    if not sources:
        errors.append("Read primary-source evidence is required")
    for item in sources:
        if not all(item.get(k) for k in ("url", "work", "locator", "checked_at", "summary")) or item.get("read") is not True:
            errors.append("Every source needs a read body, work, locator, date, URL and support summary")
    if "unresolved" not in idea or not isinstance(idea.get("unresolved"), list):
        errors.append("unresolved must explicitly list outstanding checks")
    elif any(not isinstance(x, dict) or x.get("blocking", True) for x in idea["unresolved"]):
        errors.append("Blocking research questions remain")
    production = idea.get("production_profile")
    if production is not None and production not in STRUCTURED_PROFILES:
        errors.append("Unknown production_profile: " + str(production))
    profile = idea.get("planning_profile")
    if profile == RECOGNIZABLE_PROFILE:
        errors.extend(recognizable_story_errors(idea))
    elif profile is not None:
        errors.append("Unknown planning_profile: " + str(profile))
    return errors


def recognizable_story_errors(idea):
    """Validate recorded editorial decisions, never infer popularity or truth.

    The active research session must actually read and assess the sources. A
    structurally valid record does not prove the claims or a measured audience.
    This profile is opt-in: prior candidates/resumable work remain unchanged.
    """
    errors = []

    def need(ok, message):
        if not ok:
            errors.append(RECOGNIZABLE_PROFILE + ": " + message)

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def url(value):
        if not text(value):
            return False
        try:
            parsed = urlsplit(value)
            return parsed.scheme in ("https", "http") and bool(parsed.netloc)
        except ValueError:
            return False

    sources = idea.get("sources", [])
    source_urls = {item.get("url") for item in sources
                   if isinstance(item, dict) and url(item.get("url")) and item.get("read") is True}

    def evidence(record, label, locator_key="locator"):
        if not isinstance(record, dict):
            need(False, label + " needs an actual source URL and locator")
            return
        need(url(record.get("url")) and record.get("url") in source_urls,
             label + " URL must match a read primary source")
        need(text(record.get(locator_key)), label + " source locator required")

    for who in ("a", "b"):
        person = idea.get(who, {})
        if not isinstance(person, dict):
            need(False, who + " person object required")
            continue
        need(person.get("pre1900") is True, who + ": origin before 1900 must be verified")
        need(person.get("version_allowed") is True, who + ": selected old-source version must be allowed")
        need(text(person.get("origin")), who + ": origin/work explanation required")
        evidence(person.get("source"), who + ".source")
        recognition = person.get("recognition", {})
        need(isinstance(recognition, dict) and recognition.get("clear") is True and text(recognition.get("reason")),
             who + ": clear Korean-audience name recognition needs an editorial reason; ambiguous candidates are not eligible")

    pairing = idea.get("paired_by")
    need(isinstance(pairing, list) and bool(pairing)
         and all(type(code) is int and code in (1, 2, 3) for code in pairing)
         and len(pairing) == len({str(code) for code in pairing}),
         "paired_by must contain unique codes 1 (same role), 2 (opposed abilities), or 3 (comparable Eastern/Western roles)")
    need(text(idea.get("paired_by_reason")), "paired_by_reason must explain this particular pair")
    specific = idea.get("premise_specific", {})
    need(isinstance(specific, dict) and specific.get("pass") is True and text(specific.get("reason")),
         "premise_specific requires pass=true and why this scene depends on these two identities")
    conditions = idea.get("conditions", {})
    if idea.get("production_profile") != DETAIL_PROFILE:
        need(conditions.get("opposed") is True and text(conditions.get("opposed_reason")),
             "conditions require opposed=true and an explanation on the same comparison axis")
    # v5 reuses a/b + evidence for successive exchanges in the same event.
    # Their causal/semantic connection is reviewed in the existing storyboard
    # and source records, not fabricated by a string-level validator.
    strength_evidence = conditions.get("evidence", {})
    for who in ("a", "b"):
        evidence(strength_evidence.get(who) if isinstance(strength_evidence, dict) else None,
                 "conditions.evidence." + who)
    bait = idea.get("bait", {})
    if not isinstance(bait, dict):
        need(False, "bait object required")
    else:
        need(bait.get("type") in ("a", "b", "c"), "bait.type must be a, b, or c")
        need(text(bait.get("text")), "bait.text required")
        evidence(bait, "bait", locator_key="source_locator")
    final_question = idea.get("final_question")
    need(text(final_question) and final_question.rstrip().endswith("?"),
         "final_question must return to the imagined situation as a question")
    scores = idea.get("self_scores", {})
    reasons = idea.get("self_score_reasons", {})
    valid_scores = isinstance(scores, dict)
    for key in SELF_SCORE_KEYS:
        value = scores.get(key) if isinstance(scores, dict) else None
        valid = type(value) is int and 1 <= value <= 5
        valid_scores = valid_scores and valid
        need(valid, "self_scores." + key + " must be an integer from 1 to 5")
        need(isinstance(reasons, dict) and text(reasons.get(key)), "self_score_reasons." + key + " required")
    if valid_scores:
        total = sum(scores[key] for key in SELF_SCORE_KEYS)
        need(total >= 12, f"editorial score total {total}/15 is below 12; replace or research the candidate")
        if "total" in scores:
            need(type(scores["total"]) is int and scores["total"] == total, "self_scores.total does not match the three component scores")
    need(idea.get("scoring_basis") == "editorial_judgment_not_empirical",
         "scoring_basis must state editorial_judgment_not_empirical; these are not views, comments, or audience statistics")
    return errors


def scan_existing(root, state, registry):
    """Import unfinished/legacy folders conservatively instead of duplicating them."""
    changed = False
    migrated = []
    for job in state.data["jobs"]:
        names = job.get("names", [])
        if len(names) != 2 or not all(names):
            continue
        key = registry.pair_key(*names)
        if key != job["pair_key"]:
            migrated.append((job, key))
    proposed = {}
    for job in state.data["jobs"]:
        new_key = next((key for record, key in migrated if record is job), job["pair_key"])
        if job.get("status") != "COMPLETE":
            proposed.setdefault(new_key, []).append(job["job_id"])
    collisions = {key: ids for key, ids in proposed.items() if len(ids) > 1}
    if collisions and migrated:
        raise ValueError("Alias reconciliation found multiple unfinished jobs for one identity pair; preserve and reconcile them: " + repr(collisions))
    for job, key in migrated:
        job.setdefault("identity_history", []).append({"old_pair_key": job["pair_key"], "new_pair_key": key, "at": now()})
        job["pair_key"] = key
        job["person_ids"] = [registry.resolve(x) for x in job["names"]]
        changed = True
    if changed:
        state.save()
    for folder in sorted((Path(root) / "episodes").glob("*")):
        if not folder.is_dir():
            continue
        ep_path = folder / "episode.json"
        manifest = load_json(folder / "manifest.json", {})
        cfg = load_json(ep_path, {})
        names = manifest.get("names") or [cfg.get("a", {}).get("name"), cfg.get("b", {}).get("name")]
        if not all(names) or len(names) != 2:
            # Unnamed reserved folders are already represented in the ledger.
            continue
        key = registry.pair_key(*names)
        rel = str(ep_path.relative_to(root))
        number = int(folder.name.split("_", 1)[0]) if folder.name.split("_", 1)[0].isdigit() else 0
        status = manifest.get("status", "NEEDS_REVIEW")
        if status not in {"COMPLETE", "FAILED", "NEEDS_REVIEW", "IN_PROGRESS", "SKIPPED"}:
            status = "NEEDS_REVIEW"
        state.import_job({"job_id": manifest.get("job_id") or "legacy_" + hashlib.sha256(rel.encode()).hexdigest()[:16], "number": number, "pair_key": key, "candidate_id": cfg.get("matchup_idea_id"), "names": names, "person_ids": [registry.resolve(x) for x in names], "episode_path": rel, "source": "existing_folder", "original": None, "created_at": manifest.get("created_at", now()), "updated_at": now(), "status": status, "history": [{"status": status, "at": now(), "reason": "Imported existing work without reclassifying its QA"}], "attempt_count": 0})


def select_matchups(root, requested=None, count=1, force_new=False, remake=False, run_id=None):
    root = Path(root).resolve()
    with ProjectState(root) as state:
        return _select_matchups_locked(root, state, requested, count, force_new, remake, run_id)


def _select_matchups_locked(root, state, requested=None, count=1, force_new=False, remake=False, run_id=None):
    root = Path(root).resolve()
    pool_path = root / "matchup_ideas.json"
    pool = load_json(pool_path, {"schema_version": 1, "aliases": {}, "candidates": []})
    if pool.get("schema_version") != 1:
        raise ValueError("Unsupported matchup_ideas schema")
    default_profile = pool.get("default_planning_profile")
    if "default_planning_profile" in pool and default_profile != RECOGNIZABLE_PROFILE:
        raise ValueError("Unknown default_planning_profile: " + str(default_profile))
    default_production = pool.get("default_production_profile", DEFAULT_PRODUCTION_PROFILE)
    if default_production not in STRUCTURED_PROFILES:
        raise ValueError("Unknown default_production_profile: " + str(default_production))
    registry = IdentityRegistry(pool)
    ideas = pool.get("candidates", [])
    keys = {}
    for idea in ideas:
        try:
            key = registry.pair_key(idea["a"]["id"], idea["b"]["id"])
            keys.setdefault(key, []).append(idea)
        except (KeyError, ValueError):
            continue
    queue = [line.strip() for line in read_text(root / "queue.txt").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    done_keys = set()
    queue_keys = set()
    for line in read_text(root / "done.txt").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            try:
                done_keys.add(registry.line_key(line))
            except ValueError:
                raise ValueError(f"Unrecognized done.txt history must be reconciled first: {line}")
    for line in queue:
        try:
            queue_keys.add(registry.line_key(line))
        except ValueError:
            pass
    result = {"status": "NEEDS_RESEARCH", "selections": [], "skipped": [], "research_needed": [], "held": []}
    explicit = [requested] if isinstance(requested, str) else (list(requested) if requested else [])
    if explicit and force_new:
        raise ValueError("Explicit matchups and force_new are mutually exclusive")
    state.pair_key_resolver = registry.line_key
    scan_existing(root, state, registry)
    run_id = state.start_run(count, run_id)
    result["run_id"] = run_id
    result["attempt_limit"] = state.data["runs"][run_id]["limit"]
    attempted_keys = {state.get_job(j)["pair_key"] for j in state.data["runs"][run_id]["attempts"]}
    existing_keys = {j["pair_key"] for j in state.data["jobs"]}
    considered_keys = set()

    def selection_plan(idea, key):
        unfinished = [j for j in state.jobs_for(key)
                      if j["status"] in {"IN_PROGRESS", "FAILED", "NEEDS_REVIEW"}]
        if unfinished and not remake:
            saved = load_json(root / unfinished[-1]["episode_path"], {})
            if saved:
                plan = copy.deepcopy(idea)
                if "production_profile" in saved:
                    plan["production_profile"] = saved["production_profile"]
                else:
                    plan.pop("production_profile", None)
                return plan
            return idea
        plan = copy.deepcopy(idea)
        plan.setdefault("production_profile", default_production)
        return plan

    def selection_errors(idea, key):
        plan = selection_plan(idea, key)
        # This default governs new reservations, not a retroactive migration of
        # a running/failed/review job. Explicit remakes still need the new plan.
        unfinished = any(j["status"] in {"IN_PROGRESS", "FAILED", "NEEDS_REVIEW"}
                         for j in state.jobs_for(key))
        # A newer candidate remains subject to its own planning rules. Restoring
        # an older episode's renderer profile must not reinterpret that candidate
        # as an old opposed-conditions proposal. Resume reads the saved episode.
        errors = candidate_errors(idea if unfinished and not remake else plan)
        if (default_profile and (remake or not unfinished)
                and idea.get("planning_profile") != default_profile):
            errors.append("New reservations and explicit remakes require planning_profile=" + default_profile)
        if ("default_production_profile" in pool and (remake or not unfinished)
                and plan.get("production_profile") != default_production):
            errors.append("New reservations require production_profile=" + default_production
                          + "; re-review this older candidate without rewriting existing episodes")
        return errors

    blocked_keys = {r["pair_key"] for r in state.data["skipped"]
                    if r.get("candidate_id") and r.get("source") == "queue"
                    and not any(not selection_errors(x, r["pair_key"])
                                for x in keys.get(r["pair_key"], []))}

    def consider(line, source, supplied_idea=None):
        if not state.remaining(run_id):
            return
        try:
            key = registry.line_key(line)
        except ValueError as exc:
            record = state.skip("unparsed:" + name_key(line), line, str(exc), source=source)
            result["held"].append(record)
            return
        if key in attempted_keys:
            return
        if key in considered_keys:
            return
        considered_keys.add(key)
        if source == "new" and key in queue_keys | done_keys | existing_keys:
            return
        if source == "queue" and key in blocked_keys:
            return
        if key in done_keys and not remake:
            result["skipped"].append({"original": line, "reason": "Already in done history; explicit remake required", "pair_key": key})
            return
        available = [supplied_idea] if supplied_idea else keys.get(key, [])
        candidate = next((x for x in available if not selection_errors(x, key)), None)
        if candidate is None:
            if not available:
                result["research_needed"].append({"pair": line, "pair_key": key, "source": source, "reason": "No researched candidate plan; verify eligibility and source versions"})
                return
            reasons = [f"{x.get('id', '?')}: " + "; ".join(selection_errors(x, key)) for x in available]
            hard_blocked = all(x.get("eligibility", {}).get("status") == "BLOCKED" or x.get("format") != "one_on_one" for x in available)
            if hard_blocked:
                evidence = [item for x in available for item in x.get("eligibility", {}).get("evidence", [])]
                rec = state.skip(key, line, " | ".join(reasons), available[0].get("id"), source, evidence=evidence)
                result["skipped"].append(rec)
                if any(x.get("format") != "one_on_one" for x in available):
                    result["held"].append(rec)
            else:
                result["research_needed"].append({"pair": line, "pair_key": key, "source": source, "reason": " | ".join(reasons)})
            return
        try:
            effective_plan = selection_plan(candidate, key)
            job = state.reserve(run_id, effective_plan, key, source, line, remake)
        except RuntimeError as exc:
            result["skipped"].append({"original": line, "pair_key": key, "reason": str(exc)})
            return
        attempted_keys.add(key)
        existing_keys.add(key)
        selection = {"job_id": job["job_id"], "candidate_id": candidate["id"], "pair_key": key, "names": job["names"], "source": source, "action": job["history"][-1]["action"], "episode_path": job["episode_path"], "plan": effective_plan}
        result["selections"].append(selection)
        candidate.setdefault("adoptions", []).append({"job_id": job["job_id"], "episode_path": job["episode_path"], "run_id": run_id, "at": now()})

    if explicit:
        for line in explicit:
            consider(line, "explicit")
    else:
        if not force_new:
            for line in queue:
                consider(line, "queue")
        queue_still_needs_research = any(x.get("source") == "queue" for x in result["research_needed"])
        if state.remaining(run_id) and not queue_still_needs_research:
            # Keep queue and explicit order unchanged. The configured current
            # profile ranks fresh discoveries by editorial score, with stable
            # file-order ties. Invalid records cannot gain priority via scores.
            def discovery_score(idea):
                try:
                    key = registry.pair_key(idea["a"]["id"], idea["b"]["id"])
                except (KeyError, ValueError):
                    return -1
                if idea.get("planning_profile") != RECOGNIZABLE_PROFILE or selection_errors(idea, key):
                    return -1
                return sum(idea["self_scores"][field] for field in SELF_SCORE_KEYS)

            discoveries = sorted(ideas, key=discovery_score, reverse=True) if default_profile else ideas
            if default_profile:
                used_people = {registry.resolve(name)
                               for job_id in state.data["runs"][run_id]["attempts"]
                               for name in state.get_job(job_id)["names"]}
                while discoveries and state.remaining(run_id):
                    eligible = []
                    for index, idea in enumerate(discoveries):
                        try:
                            key = registry.pair_key(idea["a"]["id"], idea["b"]["id"])
                        except (KeyError, ValueError):
                            continue
                        if (key not in queue_keys | done_keys | existing_keys | considered_keys
                                and not selection_errors(idea, key)):
                            ids = {registry.resolve(idea[who]["id"]) for who in ("a", "b")}
                            eligible.append((index, ids))
                    # Scores remain unchanged. Among available valid plans,
                    # prefer a fresh pair of participants before reusing any.
                    choice = next((item for item in eligible if not item[1] & used_people),
                                  eligible[0] if eligible else (0, set()))
                    index, ids = choice
                    idea = discoveries.pop(index)
                    if not idea.get("a", {}).get("name") or not idea.get("b", {}).get("name"):
                        continue
                    before = len(result["selections"])
                    overlap = sorted(ids & used_people)
                    consider(f"{idea['a']['name']} vs {idea['b']['name']}", "new", idea)
                    if len(result["selections"]) > before:
                        if overlap:
                            result["selections"][-1]["selection_reason"] = "No eligible fresh candidate avoids participants already attempted in this run; reused participants are reported without changing editorial scores."
                            result["selections"][-1]["reused_person_ids"] = overlap
                        else:
                            result["selections"][-1]["selection_reason"] = "Highest editorial score among eligible candidates with no participant already attempted in this run."
                        used_people.update(ids)
            else:
                for idea in discoveries:
                    if idea.get("a", {}).get("name") and idea.get("b", {}).get("name"):
                        consider(f"{idea['a']['name']} vs {idea['b']['name']}", "new", idea)
    if result["selections"]:
        atomic_json(pool_path, pool)
        result["status"] = "SELECTED"
    elif not state.remaining(run_id):
        result["status"] = "ATTEMPT_LIMIT_REACHED"
    if state.remaining(run_id) and not explicit:
        pending_queue = [x for x in result["research_needed"] if x.get("source") == "queue"]
        result["candidate_request"] = {"remaining_attempts": state.remaining(run_id), "minimum_new_candidates": state.remaining(run_id) + 2, "instruction": "First resolve the existing queue research requests; only if no eligible queue remains, research additional distinct 1:1 candidates and select again with this run_id. Do not ask the user to pick each matchup.", "queue_research_first": bool(pending_queue)}
    result["attempts_used"] = len(state.data["runs"][run_id]["attempts"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select")
    select.add_argument("--matchup", action="append")
    select.add_argument("--count", type=int, default=1)
    select.add_argument("--new", action="store_true")
    select.add_argument("--remake", action="store_true")
    select.add_argument("--run-id")
    sub.add_parser("status")
    finish = sub.add_parser("finish-run")
    finish.add_argument("run_id")
    status = sub.add_parser("set-status")
    status.add_argument("job_id")
    status.add_argument("status", choices=["FAILED", "NEEDS_REVIEW", "SKIPPED"])
    status.add_argument("--reason", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.command == "select":
        value = select_matchups(root, args.matchup, args.count, args.new, args.remake, args.run_id)
    else:
        with ProjectState(root) as state:
            if args.command == "finish-run":
                state.finish_run(args.run_id)
            elif args.command == "set-status":
                state.set_status(args.job_id, args.status, args.reason)
            value = state.data
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
