"""V5 contract regressions. No image, TTS, audio or video generation is run."""
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import unicodedata

import check_episode as check
import episode_core as core
import make_voice as voice
from make_vs import CTA_COMMENT_SAY, CTA_SHOW


BASE = Path(__file__).resolve().parents[1]
CUE_IDS = ['hook', 'a.intro.0', 'a.intro.1', 'b.intro.0', 'b.intro.1',
           'setup.0', 'setup.1', 'a.edge.0', 'a.edge.1', 'b.edge.0', 'b.edge.1',
           'reason', 'question', 'cta']


def detail_fixture():
    """Existing fixture metadata, synthetic authored prose; no factual approval."""
    cfg = core.load_json(BASE / 'episode_example.json')
    cfg['production_profile'] = core.DETAIL_PROFILE
    cfg['hook'] = '검끝이 빗나간 순간, 누가 먼저 돌아설까요?'
    cfg['a']['edge']['lines'] = ['검을 옆으로 걷어내자,', '상대의 칼끝이 오른쪽으로 빗나갑니다.']
    cfg['b']['edge']['lines'] = ['그 칼날을 따라 몸을 돌리며,', '비어 있는 왼쪽으로 검을 내밀죠.']
    for who in ('a', 'b'):
        cfg[who]['intro_images'][0] = who + '.png'
    return cfg


def errors_for(cfg):
    errors = []
    check.check_text(cfg, errors)
    return errors


def set_path(cfg, path, value):
    target = cfg
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


class DetailProfileTests(unittest.TestCase):
    def setUp(self):
        self.cfg = detail_fixture()

    def test_new_default_does_not_rewrite_saved_profiles(self):
        self.assertEqual('showdown_detail_v5', core.DEFAULT_PRODUCTION_PROFILE)
        self.assertIn(core.DETAIL_PROFILE, core.STRUCTURED_PROFILES)
        old = core.load_json(BASE / 'episode_example.json')
        before = copy.deepcopy(old)
        self.assertEqual(core.SHOWDOWN_PROFILE, old['production_profile'])
        self.assertEqual([], errors_for(old))
        self.assertEqual(CUE_IDS, [s['id'] for s in core.speech_segments(old)])
        self.assertEqual(before, old)
        self.assertEqual((30, 60), core.duration_limits(self.cfg))
        self.assertEqual((20, 45), core.duration_limits({}))

    def test_exact_cues_images_groups_and_both_intro_asset_routes(self):
        for separate in (False, True):
            with self.subTest(separate=separate):
                cfg = copy.deepcopy(self.cfg)
                if separate:
                    for who in ('a', 'b'):
                        cfg[who]['intro_images'][0] = who + '_1.png'
                self.assertEqual([], errors_for(cfg))
                cues = core.speech_segments(cfg)
                self.assertEqual(CUE_IDS, [s['id'] for s in cues])
                expected_images = ['thumb.png', cfg['a']['intro_images'][0], 'a_2.png',
                                   cfg['b']['intro_images'][0], 'b_2.png', 'scene.png', 'scene.png',
                                   'a_atk.png', 'a_atk.png', 'b_atk.png', 'b_atk.png',
                                   'cliffhanger.png', 'cliffhanger.png', 'cliffhanger.png']
                self.assertEqual(expected_images, [s['image'] for s in cues])
                self.assertEqual(9, len(set(expected_images)))
                self.assertEqual(11 if separate else 9, len(core.image_names(cfg)))
                self.assertEqual(CUE_IDS, [key for g in core.narration_groups(cfg) for key in g['segment_ids']])
                self.assertEqual(CTA_COMMENT_SAY, cues[-1]['spoken_text'])
                self.assertEqual(CTA_SHOW[0], cues[-1]['display_text'])
                self.assertTrue(cues[-1]['caption_in_overlay'])
                self.assertNotIn('좋아요', ' '.join(s['spoken_text'] for s in cues))

    def test_v5_limits_are_nfc_counted_without_changing_v4_limits(self):
        cases = [(['hook'], 40, '?'), (['a', 'tag'], 14, ''),
                 (['a', 'intro', 0], 48, ','), (['a', 'intro', 1], 48, '.'),
                 (['setup', 'title'], 22, ''), (['setup', 'lines', 0], 48, ','),
                 (['setup', 'lines', 1], 48, '.'), (['a', 'edge', 'title'], 16, ''),
                 (['a', 'edge', 'lines', 0], 48, ','), (['a', 'edge', 'lines', 1], 48, '.'),
                 (['reason'], 48, '.'), (['question'], 36, '?')]
        for path, limit, ending in cases:
            with self.subTest(path=path):
                cfg = copy.deepcopy(self.cfg)
                at_limit = '가' * (limit - len(ending)) + ending
                set_path(cfg, path, unicodedata.normalize('NFD', at_limit))
                self.assertEqual([], errors_for(cfg))
                set_path(cfg, path, at_limit[:-len(ending)] + '가' + ending if ending else at_limit + '가')
                self.assertTrue(errors_for(cfg))
        v4 = core.load_json(BASE / 'episode_example.json')
        for path, marker in ((['a', 'edge', 'lines', 0], 'at most 44'), (['reason'], 'at most 40')):
            cfg = copy.deepcopy(v4)
            set_path(cfg, path, '가' * 47 + '.')
            self.assertTrue(any(marker in e for e in errors_for(cfg)))

    def test_hook_is_question_but_not_the_v4_fixed_opening(self):
        self.assertEqual([], errors_for(self.cfg))
        for hook in ('누가 이길까요?', '누가  이길까요?', '검끝이 옆으로 빗나갑니다.'):
            cfg = copy.deepcopy(self.cfg)
            cfg['hook'] = hook
            self.assertTrue(any('hook:' in e for e in errors_for(cfg)))
        v4 = core.load_json(BASE / 'episode_example.json')
        v4['hook'] = self.cfg['hook']
        self.assertTrue(any('누가 이길까요?' in e for e in errors_for(v4)))

    def test_unsupported_combinations_fail_in_shared_paths_not_silently_fall_back(self):
        mutations = [(lambda c, k=k: c.update({k: []}), k)
                     for k in ('story_beats', 'clash', 'additional_edges', 'reaction_image')]
        mutations.append((lambda c: c['a']['edge'].update(reaction_image='b_react.png'), 'reaction_image'))
        for mutate, marker in mutations:
            cfg = copy.deepcopy(self.cfg)
            mutate(cfg)
            with self.subTest(marker=marker):
                self.assertTrue(any(marker in e for e in errors_for(cfg)))
                for helper in (core.speech_segments, core.image_names, core.narration_groups):
                    with self.assertRaisesRegex(ValueError, marker):
                        helper(cfg)

    def test_cta_mode_must_be_explicit_and_legacy_modes_stay_v4_only(self):
        for mode in (None, 'overlay', 'spoken', 'unknown'):
            cfg = copy.deepcopy(self.cfg)
            if mode is None:
                cfg.pop('cta_presentation')
            else:
                cfg['cta_presentation'] = mode
            with self.subTest(mode=mode):
                self.assertTrue(any('comment_spoken' in e for e in errors_for(cfg)))
                for helper in (core.speech_segments, core.image_names, core.narration_groups):
                    with self.assertRaisesRegex(ValueError, 'comment_spoken'):
                        helper(cfg)
        v4 = core.load_json(BASE / 'episode_example.json')
        v4['cta_presentation'] = 'overlay'
        self.assertEqual([], errors_for(v4))
        self.assertEqual(13, len(core.speech_segments(v4)))

    def test_missing_structure_wrong_assets_and_unresolved_ending_fail_cleanly(self):
        mutations = [lambda c: c['a'].update(intro=['하나뿐입니다.']),
                     lambda c: c['a'].update(intro_images=[]),
                     lambda c: c['setup'].update(lines=[]),
                     lambda c: c['b']['edge'].update(lines=['하나.', '둘.', '셋.']),
                     lambda c: c['a']['edge'].update(image='scene.png'),
                     lambda c: c['setup'].update(image='a_atk.png'),
                     lambda c: c.pop('cliffhanger'),
                     lambda c: c['cliffhanger'].update(image='a_atk.png'),
                     lambda c: c['cliffhanger'].update(winner_revealed=True),
                     lambda c: c['a']['featured_feat'].update(version='wrong version')]
        for mutate in mutations:
            cfg = copy.deepcopy(self.cfg)
            mutate(cfg)
            with self.subTest(mutation=mutate):
                self.assertTrue(errors_for(cfg))

    def test_groups_must_cover_every_cue_in_order_and_keep_intro_together(self):
        groups = core.narration_groups(self.cfg)
        invalid = [groups[:-1], list(reversed(groups)), groups + [groups[0]],
                   [{'id': 'solo_' + str(i), 'segment_ids': [key]} for i, key in enumerate(CUE_IDS)],
                   [{'id': 'unsafe/../', 'segment_ids': CUE_IDS}],
                   [None]]
        for bad in invalid:
            cfg = copy.deepcopy(self.cfg)
            cfg['narration_groups'] = copy.deepcopy(bad)
            with self.subTest(groups=bad):
                self.assertTrue(errors_for(cfg))

    def test_incomplete_explicit_groups_cannot_fall_back_to_single_cue_tts(self):
        for groups in ([], core.narration_groups(self.cfg)[:-1],
                       [{'id': 'one_' + str(i), 'segment_ids': [key]} for i, key in enumerate(CUE_IDS)]):
            cfg = copy.deepcopy(self.cfg)
            cfg['narration_groups'] = groups
            with self.subTest(groups=groups):
                with self.assertRaisesRegex(ValueError, core.DETAIL_PROFILE):
                    core.narration_groups(cfg)
                with tempfile.TemporaryDirectory() as folder, \
                     patch.object(voice, 'engine_info', return_value={'name': 'mock', 'version': 'test'}), \
                     patch.object(voice, 'decoder_info', return_value={'name': 'mock', 'version': 'test'}), \
                     patch.object(voice, 'fetch_tts', side_effect=AssertionError('TTS forbidden')) as fetch:
                    with self.assertRaisesRegex(ValueError, core.DETAIL_PROFILE):
                        voice.build_plan(cfg, Path(folder))
                    self.assertEqual([], list(Path(folder).iterdir()))
                    fetch.assert_not_called()

    def test_abstract_words_are_identified_warnings_not_validation_errors(self):
        self.cfg['a']['edge']['lines'][0] = '칼날을 옆으로 걷어내며 반격하고,'
        self.cfg['b']['edge']['lines'][0] = '공격을 퍼부으며 몰아붙이는 순간,'
        self.assertEqual([], errors_for(self.cfg))
        warnings = check.text_warnings(self.cfg)
        self.assertTrue(any('a.edge.0' in w and '반격하고' in w and '반격한다' in w for w in warnings))
        self.assertTrue(any('b.edge.0' in w and '공격을 퍼부으며' in w for w in warnings))
        self.assertTrue(any('b.edge.0' in w and '몰아붙이는' in w for w in warnings))
        # Absence of listed words does not certify meaning or even valid structure.
        cfg = detail_fixture()
        cfg['a']['edge']['lines'] = []
        self.assertEqual([], check.text_warnings(cfg))
        self.assertTrue(errors_for(cfg))
        cfg['production_profile'] = core.SHOWDOWN_PROFILE
        self.assertEqual([], check.text_warnings(cfg))

    def test_cli_prints_warning_and_scope_without_failing_valid_structure(self):
        self.cfg['a']['edge']['lines'][0] = '칼날을 걷어내며 반격하고,'
        output = io.StringIO()
        with patch.object(check, 'load_json', return_value=self.cfg), \
             patch.object(check, 'validate_episode', return_value=[]), \
             patch('sys.argv', ['check_episode.py', 'fixture.json', '--text-only']), \
             patch('sys.stdout', output):
            check.main()
        self.assertIn('a.edge.0', output.getvalue())
        self.assertIn('합격·불합격 판정이 아닙니다', output.getvalue())
        self.assertIn('통과 (대본만 검사)', output.getvalue())

    def test_voice_plan_uses_grouped_word_boundaries_pcm_and_existing_trim_without_generation(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(voice, 'engine_info', return_value={'name': 'mock', 'version': 'test'}), \
             patch.object(voice, 'decoder_info', return_value={'name': 'mock', 'version': 'test'}), \
             patch.object(voice, 'fetch_tts', side_effect=AssertionError('TTS forbidden')) as fetch, \
             patch.object(voice, 'decode_pcm', side_effect=AssertionError('decode forbidden')) as decode:
            plan = voice.build_plan(self.cfg, Path(folder))
            self.assertEqual([], list(Path(folder).iterdir()))
        fetch.assert_not_called()
        decode.assert_not_called()
        self.assertEqual('voice', plan['audio_mode'])
        self.assertEqual(voice.STORY_TRIM_POLICY, plan['pcm_trim_policy'])
        self.assertEqual({'sample_rate': 48000, 'channels': 1, 'sample_width': 2}, plan['pcm_format'])
        self.assertIn('WordBoundary exact', plan['timing_policy'])
        self.assertEqual(8, len(plan['narration_units']))
        self.assertEqual(CUE_IDS, [key for unit in plan['narration_units'] for key in unit['segment_ids']])
        intro = next(unit for unit in plan['narration_units'] if unit['id'] == 'group.intro_a')
        self.assertEqual(' '.join(self.cfg['a']['intro']), intro['spoken_text'])

    def test_cliffhanger_review_keeps_observation_and_both_reference_gates(self):
        reviews = [{'file': name, 'status': 'PASS', 'generation_version': 1,
                    'full_observations': 'test-only', 'detail_observations': 'test-only',
                    'reviewed_at': 'test-only', 'issues': [], 'reference_hashes': {}}
                   for name in core.image_names(self.cfg)]
        documents = {'image_review.json': {'images': reviews},
                     'character_bible.json': {}, 'storyboard.json': {}}
        with patch.object(check, '_read', side_effect=lambda base, name, errors: documents[name]), \
             patch.object(check, '_hash_matches') as hashes:
            errors = []
            check.validate_image_reviews(Path('mock'), errors, self.cfg)
            self.assertEqual(['cliffhanger.png: observations of both strengths and the unresolved outcome required'], errors)
            cliff = next(r for r in reviews if r['file'] == 'cliffhanger.png')
            cliff['cliffhanger_observations'] = 'mock-only, not actual artwork approval'
            errors = []
            check.validate_image_reviews(Path('mock'), errors, self.cfg)
            self.assertEqual([], errors)
            labels = [call.args[-1] for call in hashes.call_args_list if len(call.args) > 4]
            self.assertIn('cliffhanger.png reference a.png', labels)
            self.assertIn('cliffhanger.png reference b.png', labels)


if __name__ == '__main__':
    unittest.main()
