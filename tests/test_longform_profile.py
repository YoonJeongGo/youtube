"""Synthetic longform contracts. No real story, job, TTS or video is produced."""
import copy
import tempfile
from pathlib import Path
import unittest

import episode_core as core
from longform_profile import (STORY_PLAN_FIELDS, SCENE_FIELDS, longform_text_errors,
                              longform_production_errors)


def longform_fixture():
    cfg = {
        'schema_version': 2, 'format': 'longform',
        'production_profile': core.LONGFORM_PROFILE,
        'story_id': 'fixture-event', 'job_id': 'fixture-longform',
        'cta_presentation': 'comment_spoken', 'thumb': 'thumb.png',
        'a': {'name': 'Fixture A', 'image': 'a.png'},
        'b': {'name': 'Fixture B', 'image': 'b.png'},
        'story_plan': {key: 'Fixture only; no approved story.' for key in STORY_PLAN_FIELDS},
        'scenes': [{'id': key, **{field: 'Fixture causal map.' for field in SCENE_FIELDS}}
                   for key in ('scene_start', 'scene_end')],
        'cuts': [{'id': 'cut_start', 'scene_id': 'scene_start', 'image': 'first.png',
                  'moment': 'Synthetic opening moment.'},
                 {'id': 'cut_change', 'scene_id': 'scene_start', 'image': 'change.png',
                  'moment': 'Synthetic state change.', 'transition': 'hardcut'},
                 {'id': 'cut_end', 'scene_id': 'scene_end', 'image': 'last.png',
                  'moment': 'Synthetic result.'}],
        'story_beats': [],
        'narration_groups': [{'id': 'paragraph_one', 'segment_ids': ['cue_a', 'cue_b', 'cue_c']},
                             {'id': 'paragraph_two', 'segment_ids': ['cue_d', 'cta']}],
        'resolution': {'objective_resolved': True, 'outcome': 'The synthetic event is resolved.'},
    }
    for key, scene, cut in [('cue_a', 'scene_start', 'cut_start'),
                            ('cue_b', 'scene_start', 'cut_start'),
                            ('cue_c', 'scene_start', 'cut_change'),
                            ('cue_d', 'scene_end', 'cut_end')]:
        cfg['story_beats'].append({'id': key, 'scene_id': scene, 'cut_id': cut,
                                  'kind': 'story', 'display_text': '검증용 문장입니다.',
                                  'spoken_text': '검증용 문장입니다.'})
    return cfg


class LongformProfileTests(unittest.TestCase):
    def test_profile_is_opt_in_and_shorts_default_unchanged(self):
        self.assertEqual(core.DETAIL_PROFILE, core.DEFAULT_PRODUCTION_PROFILE)
        self.assertNotIn(core.LONGFORM_PROFILE, core.STRUCTURED_PROFILES)
        self.assertFalse(core.is_longform({}))
        self.assertEqual((20, 45), core.duration_limits({}))
        self.assertEqual((30, 60), core.duration_limits({'production_profile': core.DETAIL_PROFILE}))
        self.assertEqual((540, 900), core.duration_limits(longform_fixture()))
        self.assertEqual({'width': 1920, 'height': 1080, 'fps': 30,
                          'duration_min': 540, 'duration_max': 900}, core.video_spec(longform_fixture()))

    def test_no_silent_longform_fallback(self):
        for patch in ({'format': 'shorts'}, {'format': None},
                      {'production_profile': core.DETAIL_PROFILE}, {'format': 'other'}):
            cfg = longform_fixture()
            cfg.update(patch)
            for helper in (core.image_names, core.speech_segments, core.narration_groups,
                           core.duration_limits, core.video_spec):
                with self.subTest(patch=patch, helper=helper.__name__), self.assertRaises(ValueError):
                    helper(cfg)

    def test_scene_cut_cue_group_are_not_one_to_one(self):
        cfg = longform_fixture()
        self.assertEqual([], longform_text_errors(cfg))
        cues = core.speech_segments(cfg)
        self.assertEqual(5, len(cues))
        self.assertEqual(2, len(cfg['scenes']))
        self.assertEqual(3, len(cfg['cuts']))
        self.assertEqual(2, len(core.narration_groups(cfg)))
        self.assertEqual('cut_start', cues[0]['cut_id'])
        self.assertEqual('cut_start', cues[1]['cut_id'])
        self.assertEqual('scene_start', cues[2]['scene_id'])
        self.assertEqual('hardcut', cues[2]['transition'])
        self.assertEqual('first.png', cues[0]['image'])
        self.assertNotEqual(cfg['thumb'], cues[0]['image'])
        self.assertEqual(('thumb.png', 'a.png', 'b.png', 'first.png', 'change.png', 'last.png'),
                         core.image_names(cfg))

    def test_cta_spoken_once_on_last_cut_and_not_on_intermediate_questions(self):
        cfg = longform_fixture()
        cfg['story_beats'][1]['kind'] = 'question'
        cues = core.speech_segments(cfg)
        self.assertNotIn('cta_overlay', cues[1])
        self.assertEqual('여러분의 생각을 댓글로 남겨 주세요.', cues[-1]['spoken_text'])
        self.assertEqual('last.png', cues[-1]['image'])
        self.assertEqual('cut_end', cues[-1]['cut_id'])
        self.assertEqual('hold', cues[-1]['transition'])
        self.assertTrue(cues[-1]['caption_in_overlay'])
        self.assertNotIn('좋아요', ' '.join(c['spoken_text'] for c in cues))
        self.assertEqual(1, sum(c['id'] == 'cta' for c in cues))

    def test_resolved_outcome_is_allowed_and_unrequested_open_ending_is_rejected(self):
        cfg = longform_fixture()
        cfg['winner'] = 'a'
        self.assertEqual([], longform_text_errors(cfg))
        cfg['resolution']['objective_resolved'] = False
        self.assertTrue(longform_text_errors(cfg))
        cfg['resolution'].update(open_ending_request={'user_statement': 'Fixture explicit series request'},
                                 resolved_part='Fixture resolved event', remaining_question='Fixture sequel question')
        self.assertEqual([], longform_text_errors(cfg))

    def test_group_coverage_and_ids_are_checked(self):
        for change in ('missing_cta', 'duplicate_group', 'one_request', 'reordered', 'bad_id'):
            cfg = longform_fixture()
            if change == 'missing_cta':
                cfg['narration_groups'][-1]['segment_ids'].pop()
            elif change == 'duplicate_group':
                cfg['narration_groups'][1]['id'] = cfg['narration_groups'][0]['id']
            elif change == 'one_request':
                cfg['narration_groups'] = [{'id': 'all', 'segment_ids': ['cue_a','cue_b','cue_c','cue_d','cta']}]
            elif change == 'reordered':
                cfg['narration_groups'][0]['segment_ids'].reverse()
            else:
                cfg['narration_groups'][0]['id'] = '../group'
            with self.subTest(change=change):
                self.assertTrue(longform_text_errors(cfg))
                with self.assertRaises(ValueError):
                    core.narration_groups(cfg)

    def test_cut_mapping_paths_and_duplicate_ids_are_checked(self):
        mutations = [lambda c: c['cuts'][0].update(image='../outside.png'),
                     lambda c: c['cuts'][0].update(image='C:\\outside.png'),
                     lambda c: c['cuts'][0].update(transition='pan'),
                     lambda c: c['story_beats'][1].update(scene_id='scene_end'),
                     lambda c: c['story_beats'][0].update(cut_id='missing'),
                     lambda c: c['story_beats'][0].update(image='other.png'),
                     lambda c: c['story_beats'][1].update(id='cue_a'),
                     lambda c: c['scenes'].append(copy.deepcopy(c['scenes'][0]))]
        for mutate in mutations:
            cfg = longform_fixture()
            mutate(cfg)
            self.assertTrue(longform_text_errors(cfg))

    def test_cut_image_reuse_is_allowed_with_distinct_moment_ids(self):
        cfg = longform_fixture()
        cfg['cuts'][1]['image'] = cfg['cuts'][0]['image']
        self.assertEqual([], longform_text_errors(cfg))
        self.assertEqual(5, len(core.image_names(cfg)))

    def test_planning_and_causal_scene_map_are_required_before_production(self):
        for key in STORY_PLAN_FIELDS:
            cfg = longform_fixture()
            cfg['story_plan'].pop(key)
            self.assertTrue(any('story_plan.' + key in e for e in longform_text_errors(cfg)))
        cfg = longform_fixture()
        cfg['scenes'][0].pop('next_reason')
        self.assertTrue(any('next_reason' in e for e in longform_text_errors(cfg)))

    def test_approval_is_external_current_and_scope_bound(self):
        cfg = longform_fixture()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.assertTrue(longform_production_errors(cfg, base))
            record = {'approved': True, 'approval_scope': 'full_script_and_premises',
                      'script_hash': core.script_hash(cfg), 'user_statement': 'Fixture approval only.'}
            core.atomic_json(base / 'production_authorization.json', record)
            self.assertEqual([], longform_production_errors(cfg, base))
            cfg['story_beats'][0]['spoken_text'] = '수정된 검증 문장입니다.'
            self.assertTrue(longform_production_errors(cfg, base))
            record['script_hash'] = core.script_hash(cfg)
            record['approval_scope'] = 'topic_only'
            core.atomic_json(base / 'production_authorization.json', record)
            self.assertTrue(longform_production_errors(cfg, base))

    def test_legacy_release_waiver_cannot_bypass_longform_checks(self):
        from release_checks import release_errors
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            core.atomic_json(base / 'episode.json', longform_fixture())
            core.atomic_json(base / 'user_release_acceptance.json', {'accepted_quality_limits': ['visual error']})
            errors = ['visual error', 'AUDIO_REVIEW_REQUIRED: whole final video not approved', 'ASR NOT_RUN']
            self.assertEqual(errors, release_errors(base / 'episode.json', errors))


if __name__ == '__main__':
    unittest.main()