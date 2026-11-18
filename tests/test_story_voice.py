"""Grouped narration checks use synthetic PCM and mocked TTS only."""
from array import array
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import wave

from episode_core import (IMAGE_NAMES, SETUP_IMAGE_NAMES, atomic_json, duration_limits, image_names,
                          load_json, narration_groups, sha256_file, speech_segments)
import make_voice as voice


def story_fixture():
    lines = ['홍길동은 곡식 창고를 나섰다.', '길목에서 로빈 후드가 활을 당긴다.', '분신을 만들면 어느 쪽을 맞힐까?']
    return {'schema_version': 2, 'story_beats': [
        {'id': f'beat{i}', 'kind': 'hook' if i == 0 else 'story', 'image': 'a_1.png' if i == 0 else 'b_1.png',
         'display_text': text, 'spoken_text': text, 'condition': None, 'header': '가상 추격전'}
        for i, text in enumerate(lines)],
        'narration_groups': [{'id': 'chase', 'segment_ids': ['beat0', 'beat1', 'beat2']}]}


def setup_fixture():
    return {'schema_version': 2, 'production_profile': 'setup_story_v3',
            'hook': '누가 막을까?', 'thumb': 'thumb.png', 'winner': 'none',
            'setup': {'title': '길목의 대결', 'lines': ['창고를 나섭니다.', '길목을 막는다면?'], 'image': 'scene.png'},
            'reason': '보이면 조준합니다.', 'question': '그전에 달아날까?',
            **{who: {'name': name, 'image': who + '.png', 'tag': '원전 인물',
                      'intro': ['이야기의 인물입니다.', '기회를 노리죠.'],
                      'intro_images': [who + '_1.png', who + '_2.png'],
                      'edge': {'title': '조건의 전환', 'image': who + '_atk.png',
                               'lines': ['본체를 숨깁니다.', '소설의 분신이죠.'] if who == 'a'
                               else ['하지만 본체가 보인다면.', '기록에는 명궁이죠.']}}
               for who, name in [('a', '홍길동'), ('b', '로빈 후드')]}}


def synthetic(text, scale=1):
    words = re.findall(r'[^\W_]+', text)
    cursor, boundaries = .35, []
    for index, word in enumerate(words):
        duration = (.75 + (index % 3) * .2) * scale
        boundaries.append({'type': 'WordBoundary', 'offset_ticks': round(cursor * 10_000_000),
                           'duration_ticks': round(duration * 10_000_000), 'text': word})
        cursor += duration + .12
    pcm = array('h', [1]) * round((cursor + 1) * voice.SAMPLE_RATE)
    for boundary in boundaries:
        start = round(boundary['offset_ticks'] * voice.SAMPLE_RATE / 10_000_000)
        end = round((boundary['offset_ticks'] + boundary['duration_ticks']) * voice.SAMPLE_RATE / 10_000_000)
        pcm[start:end] = array('h', [1000]) * (end - start)
    return pcm.tobytes(), boundaries


class StoryVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cfg = story_fixture()
        self.path = self.base / 'episode.json'
        atomic_json(self.path, self.cfg)
        self.patches = [patch.object(voice, 'engine_info', return_value={'name': 'synthetic', 'version': '1'}),
                        patch.object(voice, 'decoder_info', return_value={'name': 'synthetic', 'version': '1'}),
                        patch.object(voice, 'verify_voices', new_callable=AsyncMock),
                        patch.object(voice, 'decode_pcm', side_effect=lambda path: synthetic(Path(path).read_text('utf-8'))[0]),
                        patch.object(voice, 'fetch_tts', new_callable=AsyncMock, side_effect=self.fetch)]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    async def fetch(self, entry, path):
        Path(path).write_text(entry['spoken_text'], encoding='utf-8')
        return synthetic(entry['spoken_text'])[1]

    def build(self, **kwargs):
        return voice.main(self.path, **kwargs)

    def test_story_normalizes_cues_and_appends_exactly_one_shared_cta(self):
        with patch('make_vs.CTA_SAY', '공통 마무리.'):
            segments = speech_segments(self.cfg)
        self.assertEqual(['beat0', 'beat1', 'beat2', 'cta'], [segment['id'] for segment in segments])
        self.assertEqual('가상 추격전', segments[0]['header'])
        self.assertEqual('공통 마무리.', segments[-1]['spoken_text'])
        bad = copy.deepcopy(self.cfg)
        bad['story_beats'][0]['id'] = 'cta'
        with self.assertRaises(ValueError):
            speech_segments(bad)

    def test_setup_profile_maps_ten_assets_and_structured_cues_without_reactions(self):
        cfg = setup_fixture()
        segments = speech_segments(cfg)
        self.assertEqual(14, len(segments))
        self.assertEqual(10, len(image_names(cfg)))
        self.assertEqual(SETUP_IMAGE_NAMES, image_names(cfg))
        self.assertEqual(IMAGE_NAMES, image_names(self.cfg))
        self.assertEqual((30, 60), duration_limits(cfg))
        self.assertEqual((20, 45), duration_limits(self.cfg))
        by_id = {item['id']: item for item in segments}
        self.assertEqual('a_1.png', by_id['a.intro.0']['image'])
        self.assertEqual('a_2.png', by_id['a.intro.1']['image'])
        self.assertEqual('scene.png', by_id['setup.1']['image'])
        self.assertEqual('story', by_id['setup.0']['kind'])
        self.assertEqual('길목의 대결', by_id['setup.0']['header'])
        self.assertEqual('a_atk.png', by_id['a.edge.1']['image'])
        self.assertFalse(any(str(item['image']).endswith('_react.png') for item in segments))
        self.assertEqual([item['id'] for item in segments],
                         [key for group in narration_groups(cfg) for key in group['segment_ids']])
        plan = voice.build_plan(cfg, self.base)
        self.assertEqual(8, len(plan['narration_units']))
        self.assertEqual(voice.STORY_TRIM_POLICY, plan['pcm_trim_policy'])

    def test_setup_explicit_groups_override_automatic_groups(self):
        cfg = setup_fixture()
        ids = [item['id'] for item in speech_segments(cfg)]
        cfg['narration_groups'] = [{'id': 'whole', 'segment_ids': ids}]
        self.assertEqual(cfg['narration_groups'], narration_groups(cfg))
        plan = voice.build_plan(cfg, self.base)
        self.assertEqual(1, len(plan['narration_units']))
        self.assertEqual(ids, plan['narration_units'][0]['segment_ids'])

    def test_overlay_cta_is_visible_at_ending_but_never_sent_to_tts(self):
        from make_vs import CTA_SHOW, CTA_SAY
        cfg = setup_fixture()
        cfg['cta_presentation'] = 'overlay'
        segments = speech_segments(cfg)
        self.assertNotIn('cta', [s['id'] for s in segments])
        self.assertEqual(['reason', 'question'],
                         [s['id'] for s in segments if s.get('cta_overlay')])
        self.assertEqual('\n'.join(CTA_SHOW), segments[-1]['cta_overlay'])
        plan = voice.build_plan(cfg, self.base)
        self.assertEqual(7, len(plan['narration_units']))
        spoken = ' '.join(unit['spoken_text'] for unit in plan['narration_units'])
        self.assertNotIn(CTA_SAY, spoken)
        self.assertNotIn('구독', spoken)
        self.assertEqual([s['id'] for s in segments],
                         [key for group in narration_groups(cfg) for key in group['segment_ids']])

    def test_story_overlay_preserves_explicit_narration_group(self):
        cfg = story_fixture()
        cfg['cta_presentation'] = 'overlay'
        cfg['story_beats'][-1]['kind'] = 'question'
        plan = voice.build_plan(cfg, self.base)
        self.assertEqual(1, len(plan['narration_units']))
        self.assertEqual(['beat0', 'beat1', 'beat2'], plan['narration_units'][0]['segment_ids'])
        self.assertTrue(speech_segments(cfg)[-1]['cta_overlay'])

    def test_setup_actual_grouped_build_above_45_seconds_is_valid(self):
        cfg = setup_fixture()
        atomic_json(self.path, cfg)
        async def scaled_fetch(entry, path):
            Path(path).write_text(entry['spoken_text'], encoding='utf-8')
            return synthetic(entry['spoken_text'], scale=1.5)[1]
        with patch.object(voice, 'fetch_tts', new_callable=AsyncMock, side_effect=scaled_fetch) as fetch:
            with patch.object(voice, 'decode_pcm', side_effect=lambda path: synthetic(Path(path).read_text('utf-8'), scale=1.5)[0]):
                timeline = self.build()
                self.assertEqual([], voice.validate_voice(cfg, self.base))
        self.assertEqual(8, fetch.await_count)
        self.assertGreater(timeline['duration'], 45)
        self.assertLessEqual(timeline['duration'], 60)

    def test_setup_rejects_shorter_than_30_seconds(self):
        cfg = setup_fixture()
        atomic_json(self.path, cfg)
        async def scaled_fetch(entry, path):
            Path(path).write_text(entry['spoken_text'], encoding='utf-8')
            return synthetic(entry['spoken_text'], scale=.4)[1]
        with patch.object(voice, 'fetch_tts', new_callable=AsyncMock, side_effect=scaled_fetch):
            with patch.object(voice, 'decode_pcm', side_effect=lambda path: synthetic(Path(path).read_text('utf-8'), scale=.4)[0]):
                with self.assertRaisesRegex(voice.VoiceError, '30~60'):
                    self.build()
        self.assertTrue(any('30~60' in item for item in voice.validate_voice(cfg, self.base)))

    def test_group_reads_once_continuous_pcm_and_measured_cue_boundaries(self):
        timeline = self.build()
        self.assertEqual(2, self.mocks[4].await_count)
        self.assertEqual([], voice.validate_voice(self.cfg, self.base))
        units = load_json(self.base / 'voice_plan.json')['narration_units']
        self.assertEqual(' '.join(beat['spoken_text'] for beat in self.cfg['story_beats']), units[0]['spoken_text'])
        first, second, third = timeline['segments'][:3]
        self.assertEqual(first['audio_file'], second['audio_file'])
        self.assertEqual(first['speech_end_sample'], second['speech_start_sample'])
        self.assertEqual(second['speech_end_sample'], third['speech_start_sample'])
        self.assertEqual(0, timeline['narration_groups'][0]['internal_inserted_silence_samples'])
        self.assertEqual(voice.STORY_TRIM_POLICY, timeline['narration_groups'][0]['pcm_trim']['policy'])
        for segment in timeline['segments']:
            parent = segment['parent_audio']
            with wave.open(str(self.base / parent['raw_pcm_file']), 'rb') as audio:
                audio.setpos(parent['source_start_sample'])
                data = audio.readframes(parent['source_end_sample'] - parent['source_start_sample'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), parent['slice_pcm_sha256'])
        for boundary in timeline['narration_groups'][0]['measured_cue_boundaries']:
            self.assertLessEqual(abs(boundary['alignment_error_samples']), 800)
            measured = (boundary['previous_word_end_sample'] + boundary['next_word_start_sample']) / 2
            self.assertEqual(measured, boundary['parent_gap_midpoint_sample'])

    def test_stale_member_changes_regenerate_whole_group_only(self):
        self.build()
        self.build()
        self.assertEqual(2, self.mocks[4].await_count)
        self.cfg['story_beats'][1]['spoken_text'] = '길목에서 로빈 후드가 큰 활을 당긴다.'
        atomic_json(self.path, self.cfg)
        self.assertTrue(voice.validate_voice(self.cfg, self.base))
        self.build()
        self.assertEqual(3, self.mocks[4].await_count)
        state = load_json(self.base / 'voice_state.json')
        self.assertEqual(2, state['groups']['group.chase']['attempts'])
        self.assertEqual([2, 2, 2, 1], [state['sentences'][key]['attempts'] for key in ['beat0', 'beat1', 'beat2', 'cta']])
        archived = list((self.base / 'revisions/voice/v_group_chase').glob('*/v_group_chase_decoded.wav'))
        self.assertEqual(1, len(archived))

    def test_group_prosody_setting_is_applied_and_processor_change_reuses_source(self):
        self.build()
        plan = load_json(self.base / 'voice_plan.json')
        plan['narration_units'][0]['rate'] = '+12%'
        atomic_json(self.base / 'voice_plan.json', plan)
        self.assertTrue(voice.validate_voice(self.cfg, self.base))
        self.build()
        actual = load_json(self.base / 'v_group_chase.json')['settings']
        self.assertEqual('+12%', actual['rate'])
        state_before = load_json(self.base / 'voice_state.json')
        plan = voice.build_plan(self.cfg, self.base)
        plan['producer_sha256'] = 'test-only PCM processing change'
        with patch.object(voice, 'build_plan', return_value=plan):
            self.build()
            self.assertEqual([], voice.validate_voice(self.cfg, self.base))
        self.assertEqual(state_before, load_json(self.base / 'voice_state.json'))
        self.assertEqual(3, self.mocks[4].await_count)
        self.assertTrue(load_json(self.base / 'v_group_chase.json')['processing_history'])

    def test_group_rename_does_not_reset_member_retry_budget(self):
        self.build()
        self.build(regenerate=['beat1'])
        self.build(regenerate=['group.chase'])
        self.cfg['narration_groups'][0]['id'] = 'renamed'
        atomic_json(self.path, self.cfg)
        with self.assertRaisesRegex(voice.VoiceError, '2회 한도'):
            self.build()
        self.assertEqual(4, self.mocks[4].await_count)  # Initial group+CTA, two group retries.

    def test_missing_or_cross_cue_word_boundaries_fail_without_ratio_fallback(self):
        plan = voice.build_plan(self.cfg, self.base)
        unit = plan['narration_units'][0]
        raw, boundaries = synthetic(unit['spoken_text'])
        with self.assertRaisesRegex(voice.VoiceError, '전체 낭독문'):
            voice.align_group_words(unit, {'boundaries': boundaries[:-1]}, len(raw) // 2)
        crossing = copy.deepcopy(boundaries)
        crossing[3]['text'] += crossing[4]['text']
        crossing[3]['duration_ticks'] = crossing[4]['offset_ticks'] + crossing[4]['duration_ticks'] - crossing[3]['offset_ticks']
        del crossing[4]
        with self.assertRaisesRegex(voice.VoiceError, '단어 안'):
            voice.align_group_words(unit, {'boundaries': crossing}, len(raw) // 2)

    def test_noncontiguous_duplicate_and_filename_collision_groups_rejected(self):
        for groups in ([{'id': 'x', 'segment_ids': ['beat0', 'beat2']}],
                       [{'id': 'x', 'segment_ids': ['beat0']}, {'id': 'x', 'segment_ids': ['beat1']}],
                       [{'id': 'x.y', 'segment_ids': ['beat0']}, {'id': 'x_y', 'segment_ids': ['beat1']}]):
            cfg = dict(self.cfg, narration_groups=groups)
            with self.assertRaises(voice.VoiceError):
                voice.build_plan(cfg, self.base)

    def test_story_trim_keeps_low_level_word_edges_and_boundary_guards(self):
        raw = array('h', [1]) * 48000
        raw[19200:24000] = array('h', [100]) * 4800
        bounds = [{'type': 'WordBoundary', 'offset_ticks': 2000000, 'duration_ticks': 5000000, 'text': '검사'}]
        kept, info = voice.trim_story_padding(raw.tobytes(), bounds)
        self.assertEqual(2400, info['leading_trim_samples'])  # First word .2s minus .15s guard.
        self.assertEqual(7200, info['trailing_trim_samples'])  # Last word .7s plus .15s guard.
        self.assertEqual(1, info['removed_head_peak_abs'])
        self.assertEqual(1, info['removed_tail_peak_abs'])
        self.assertEqual(raw.tobytes()[4800:-14400], kept)
        legacy, old = voice.trim_digital_silence(raw.tobytes())
        self.assertEqual(raw.tobytes(), legacy)  # Legacy retains every nonzero sample.

    def test_parent_provenance_tamper_rejected_even_with_updated_timeline_digest(self):
        self.build()
        timeline = load_json(self.base / 'timeline.json')
        timeline['segments'][0]['parent_audio']['source_end_sample'] += 1
        atomic_json(self.base / 'timeline.json', timeline)
        build = load_json(self.base / 'voice_build.json')
        build['timeline_sha256'] = sha256_file(self.base / 'timeline.json')
        atomic_json(self.base / 'voice_build.json', build)
        self.assertTrue(any('재구성' in item for item in voice.validate_voice(self.cfg, self.base)))

    def test_explicit_story_silent_never_fetches_audio(self):
        # Long display-only text gives a meaningful silent reading duration.
        for beat in self.cfg['story_beats']:
            beat['display_text'] *= 5
        atomic_json(self.path, self.cfg)
        with patch.object(voice, 'engine_info', side_effect=AssertionError('No engine for silent mode')):
            timeline = self.build(silent=True)
            self.assertEqual([], voice.validate_voice(self.cfg, self.base))
        self.assertEqual('silent', timeline['audio_mode'])
        self.mocks[4].assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
