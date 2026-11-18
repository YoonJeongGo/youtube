"""Story-mode structure checks; fixtures do not represent factual/review approval."""
import copy
import unittest
import unicodedata

from check_episode import check_text
from episode_core import speech_segments


def story_config():
    lines = [
        ("hook", "hook", "thumb.png", "홍길동의 탈출을 막을 수 있을까?", None),
        ("escape", "story", "a_1.png", "곡식 창고를 빠져나오자,", "a"),
        ("pursuit", "story", "b_1.png", "길목에서 로빈 후드가 활을 당긴다.", "b"),
        ("decoy", "edge", "a_atk.png", "홍길동이 분신을 펼쳐요.", "a"),
        ("reason", "reason", "b_react.png", "분신을 가려낼 틈이 있을까,", None),
        ("question", "question", None, "그전에 홍길동이 빠져나갈까?", None),
    ]
    cfg = {
        "schema_version": 2, "winner": "none", "thumb": "thumb.png",
        "fictional_matchup": True, "fictional_premise": "탈출하는 홍길동과 길목의 로빈 후드를 가정한다.",
        "hook": lines[0][3], "reason": lines[-2][3], "question": lines[-1][3],
        "story_beats": [dict(id=key, kind=kind, image=image, display_text=text,
                             spoken_text=text, condition=condition, header="가상 추격전")
                        for key, kind, image, text, condition in lines],
        "narration_groups": [
            {"id": "opening", "segment_ids": ["hook"]},
            {"id": "chase", "segment_ids": ["escape", "pursuit"]},
            {"id": "action", "segment_ids": ["decoy"]},
            {"id": "closing", "segment_ids": ["reason", "question"]},
        ],
    }
    for who, opponent in (("a", "b"), ("b", "a")):
        cfg[who] = {
            "name": "홍길동" if who == "a" else "로빈 후드", "tag": "옛 이야기 인물",
            "image": f"{who}.png", "intro_images": [f"{who}_1.png", f"{who}_2.png"],
            "edge": {"title": "탈출의 조건", "image": f"{who}_atk.png",
                     "reaction_image": f"{opponent}_react.png"},
        }
    return cfg


def errors_for(cfg):
    errors = []
    check_text(cfg, errors)
    return errors


class StoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = story_config()

    def assert_invalid(self, cfg, marker):
        errors = errors_for(cfg)
        self.assertTrue(any(marker in error for error in errors), errors)

    def test_clause_reason_mixed_endings_and_no_legacy_lines_pass(self):
        self.assertEqual([], errors_for(self.cfg))
        segments = speech_segments(self.cfg)
        self.assertEqual([beat["id"] for beat in self.cfg["story_beats"]] + ["cta"],
                         [segment["id"] for segment in segments])
        self.assertEqual("reason", self.cfg["narration_groups"][-1]["segment_ids"][0])
        self.assertTrue(self.cfg["reason"].endswith(","))

    def test_nfc_matching_and_length(self):
        self.cfg["hook"] = unicodedata.normalize("NFD", self.cfg["hook"])
        self.assertEqual([], errors_for(self.cfg))
        self.cfg["hook"] = "가" * 30 + "?"
        for field in ("display_text", "spoken_text"):
            self.cfg["story_beats"][0][field] = self.cfg["hook"]
        self.assert_invalid(self.cfg, "at most 30 NFC")

    def test_explicit_fiction_and_premise_required(self):
        for field, value in (("fictional_matchup", False), ("fictional_matchup", 1),
                             ("fictional_premise", " "), ("fictional_premise", None)):
            with self.subTest(field=field, value=value):
                cfg = copy.deepcopy(self.cfg)
                cfg[field] = value
                self.assert_invalid(cfg, field)

    def test_story_count_and_malformed_beats_rejected(self):
        for beats in (None, [], self.cfg["story_beats"][:5], self.cfg["story_beats"] * 4):
            with self.subTest(beats=beats):
                cfg = copy.deepcopy(self.cfg)
                cfg["story_beats"] = beats
                self.assert_invalid(cfg, "6~20")
        self.cfg["story_beats"][1] = None
        self.assert_invalid(self.cfg, "must be an object")

    def test_unsafe_reserved_duplicate_and_colliding_ids_rejected(self):
        for key, marker in (("../escape", "safe"), ("cta", "safe"), ("CTA", "safe"),
                            ("hook", "duplicate cue"), ("HOOK", "filename collision"),
                            (None, "safe"), (["escape"], "safe")):
            with self.subTest(key=key):
                cfg = copy.deepcopy(self.cfg)
                cfg["story_beats"][1]["id"] = key
                self.assert_invalid(cfg, marker)
        self.cfg["story_beats"][1]["id"] = "escape.one"
        self.cfg["story_beats"][2]["id"] = "escape_one"
        self.assert_invalid(self.cfg, "filename collision")

    def test_kind_image_condition_header_and_actual_text_checked(self):
        for field, value, marker in (
            ("kind", "cta", "invalid kind"), ("kind", [], "invalid kind"),
            ("image", "../a.png", "fixed 11 images"), ("image", [], "fixed 11 images"),
            ("condition", "both", "condition"), ("condition", {}, "condition"),
            ("header", 9, "header"), ("spoken_text", " , ", "actual text"),
            ("display_text", None, "actual text"), ("cta", "local CTA", "cannot define"),
            ("stats", {}, "cannot define"),
        ):
            with self.subTest(field=field, value=value):
                cfg = copy.deepcopy(self.cfg)
                cfg["story_beats"][1][field] = value
                self.assert_invalid(cfg, marker)

    def test_top_hook_reason_question_match_each_corresponding_cue(self):
        for index, name in ((0, "hook"), (4, "reason"), (5, "question")):
            for field in ("display_text", "spoken_text"):
                with self.subTest(name=name, field=field):
                    cfg = copy.deepcopy(self.cfg)
                    cfg["story_beats"][index][field] = "다른 문장이다."
                    self.assert_invalid(cfg, f"{name} cue {field} differs")
        self.cfg["story_beats"][2]["kind"] = "reason"
        self.assert_invalid(self.cfg, "exactly one corresponding reason")

    def test_hook_and_question_need_question_marks(self):
        for index, name in ((0, "hook"), (5, "question")):
            with self.subTest(name=name):
                cfg = copy.deepcopy(self.cfg)
                text = cfg[name][:-1] + "."
                cfg[name] = text
                cfg["story_beats"][index].update(display_text=text, spoken_text=text)
                self.assert_invalid(cfg, f"{name} must end with a question mark")

    def test_opening_and_closing_order_checked(self):
        self.cfg["story_beats"][0], self.cfg["story_beats"][1] = self.cfg["story_beats"][1], self.cfg["story_beats"][0]
        self.assert_invalid(self.cfg, "first cue must be the hook")
        self.cfg["story_beats"][-1], self.cfg["story_beats"][-2] = self.cfg["story_beats"][-2], self.cfg["story_beats"][-1]
        self.assert_invalid(self.cfg, "last story cue must be the question")

    def test_groups_cover_all_cues_once_and_exclude_shared_cta(self):
        variants = [None, [], self.cfg["narration_groups"][:-1],
                    self.cfg["narration_groups"] + [self.cfg["narration_groups"][-1]]]
        for groups in variants:
            with self.subTest(groups=groups):
                cfg = copy.deepcopy(self.cfg)
                cfg["narration_groups"] = groups
                self.assert_invalid(cfg, "every story cue")
        for member in ("cta", "unknown", 2):
            with self.subTest(member=member):
                cfg = copy.deepcopy(self.cfg)
                cfg["narration_groups"][-1]["segment_ids"].append(member)
                self.assert_invalid(cfg, "unknown/CTA")

    def test_groups_reject_reordering_gaps_and_repeated_members(self):
        for members in (["pursuit", "escape"], ["escape", "decoy"], ["escape", "escape"]):
            with self.subTest(members=members):
                cfg = copy.deepcopy(self.cfg)
                cfg["narration_groups"][1]["segment_ids"] = members
                self.assert_invalid(cfg, "consecutive cues in script order")
        self.cfg["narration_groups"][1], self.cfg["narration_groups"][2] = self.cfg["narration_groups"][2], self.cfg["narration_groups"][1]
        self.assert_invalid(self.cfg, "consecutive cues in script order")

    def test_group_identifiers_safe_unique_and_filename_unique(self):
        for key, marker in (("../escape", "safe group ID"), ("opening", "duplicate narration group ID"),
                            ("OPENING", "filename collision")):
            with self.subTest(key=key):
                cfg = copy.deepcopy(self.cfg)
                cfg["narration_groups"][1]["id"] = key
                self.assert_invalid(cfg, marker)
        self.cfg["narration_groups"][0]["id"] = "group.one"
        self.cfg["narration_groups"][1]["id"] = "group_one"
        self.assert_invalid(self.cfg, "filename collision")

    def test_comma_only_internal_to_complete_spoken_group(self):
        self.cfg["narration_groups"][-1:] = [
            {"id": "reason_only", "segment_ids": ["reason"]},
            {"id": "question_only", "segment_ids": ["question"]},
        ]
        self.assert_invalid(self.cfg, "a comma is only internal")

    def test_basic_metadata_winner_and_schema_still_required(self):
        for field, value, marker in (("schema_version", 1, "schema_version"),
                                     ("winner", "a", "winner"), ("stats", {}, "stats/cta"),
                                     ("cta", "local CTA", "stats/cta"), ("thumb", "other.png", "thumb:")):
            with self.subTest(field=field):
                cfg = copy.deepcopy(self.cfg)
                cfg[field] = value
                self.assert_invalid(cfg, marker)
        self.cfg["a"]["edge"]["reaction_image"] = "a_react.png"
        self.cfg["b"]["image"] = "other.png"
        self.cfg["a"]["tag"] = "긴" * 15
        self.assert_invalid(self.cfg, "a.edge.reaction_image")
        self.assert_invalid(self.cfg, "b.image")
        self.assert_invalid(self.cfg, "a.tag")

    def test_legacy_episode_still_requires_yo_intro_and_edge_lines(self):
        del self.cfg["story_beats"]
        del self.cfg["narration_groups"]
        errors = errors_for(self.cfg)
        for marker in ("hook:", "reason:", "question:", "a.intro:", "b.intro:", "a.edge.lines:", "b.edge.lines:"):
            with self.subTest(marker=marker):
                self.assertTrue(any(marker in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
