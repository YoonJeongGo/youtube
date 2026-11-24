"""Longform engine fixtures: no TTS, generated artwork, or MP4 is produced."""
from array import array
import asyncio
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageChops

import asset_tools
import episode_core as core
import make_voice as voice
import make_vs as render
from subtitle_checks import parse_srt, expected_cues, validate_subtitles
from test_longform_profile import longform_fixture


def measured_fixture(cfg, base):
    """Synthetic constant PCM and exact WordBoundary data stay in memory."""
    with patch.object(voice, 'engine_info', return_value={'name': 'fixture'}), \
            patch.object(voice, 'decoder_info', return_value={'name': 'fixture'}):
        plan = voice.build_plan(cfg, base)
    decoded = {}
    for unit in plan['narration_units']:
        unit['pause_before'] = unit['pause_after'] = 0
        samples = len(unit['members']) * 16000 + 37
        raw = (array('h', [1000]) * samples).tobytes()
        words = []
        for i, member in enumerate(unit['members']):
            start = 2000 + i * 16000
            words.append({'type': 'WordBoundary', 'text': member['spoken_text'],
                          'offset_ticks': round(start / 48000 * 10_000_000),
                          'duration_ticks': round(4000 / 48000 * 10_000_000)})
        decoded[unit['id']] = (raw, {
            'boundaries': words, 'audio_file': voice.audio_name(unit['id']),
            'audio_sha256': 'fixture-source', 'input_hash': voice.sentence_hash(plan, unit),
            'decoded_pcm_sha256': hashlib.sha256(raw).hexdigest(),
            'raw_pcm_file': unit['id'] + '.wav', 'raw_pcm_sha256': 'fixture-raw',
        })
    return plan, decoded


def timeline_fixture(cfg, duration):
    sources = core.speech_segments(cfg)
    total = round(duration * 30)
    edges = [round(total * index / len(sources)) for index in range(len(sources) + 1)]
    segments = [{**source, 'start_frame': start, 'end_frame': end,
                 'start': start / 30, 'end': end / 30,
                 'speech_start': start / 30 + .01, 'speech_end': end / 30 - .01}
                for source, start, end in zip(sources, edges, edges[1:])]
    return {'schema_version': 2, 'audio_mode': 'voice', 'fps': 30,
            'script_hash': core.script_hash(cfg), 'total_frames': total, 'duration': total / 30,
            'pcm_path': 'voice.wav', 'pcm_sha256': 'fixture', 'segments': segments}


class LongformEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cfg = longform_fixture()

    def test_horizontal_settings_do_not_inherit_portrait_coordinates(self):
        short = render.settings_for()
        long = render.settings_for(cfg=self.cfg)
        self.assertEqual((1080, 1920), (render.W, render.H))
        self.assertEqual(300, short['art_top'])
        self.assertEqual(0, long['art_top'])
        self.assertEqual(1080, long['art_bottom'])
        self.assertLessEqual(long['caption_bottom'], 1080 - long['safe_margins']['bottom'])
        self.assertEqual(0, long['zoom_amount'])
        path = self.base / 'settings.json'
        core.atomic_json(path, {'art_top': 300, 'caption_top': 1555,
                               'longform': {'subtitle_size': 54}})
        self.assertEqual(54, render.settings_for(path, cfg=self.cfg)['subtitle_size'])
        self.assertEqual(856, render.settings_for(path, cfg=self.cfg)['caption_top'])

    def test_dynamic_assets_separate_reference_portraits_from_horizontal_scenes(self):
        for name in ('first.png', 'change.png', 'last.png', 'thumb.png'):
            self.assertEqual((1920, 1080), asset_tools.image_target(name, self.cfg))
        self.assertEqual((1024, 1024), asset_tools.image_target('a.png', self.cfg))
        self.assertEqual((1024, 1536), asset_tools.image_target('a_atk.png'))
        with self.assertRaises(ValueError):
            asset_tools.image_target('undeclared.png', self.cfg)

    def test_renaming_asset_does_not_reset_stable_cut_revision_budget(self):
        core.atomic_json(self.base / 'generation_records.json', {'schema_version': 1, 'assets': [
            {'file': 'first.png', 'asset_ids': ['cut:cut_start'], 'generation_version': 4}]})
        self.cfg['cuts'][0]['image'] = 'renamed.png'
        self.assertEqual(['cut:cut_start'], asset_tools.logical_asset_ids('renamed.png', self.cfg))
        with patch('longform_profile.longform_production_errors', return_value=[]), \
                patch.object(Path, 'is_file', return_value=True), patch.object(asset_tools.Image, 'open') as artwork:
            with self.assertRaisesRegex(ValueError, 'advance.*history'):
                asset_tools.normalize_asset(self.base / 'fixture-source.png', self.base / 'renamed.png',
                                            prompt='Fixture only', generation_version=1, episode_cfg=self.cfg)
            artwork.assert_not_called()
        self.assertEqual(['generation_records.json'], [p.name for p in self.base.iterdir()])

    def test_first_scene_is_not_thumbnail_and_photos_remain_still(self):
        colors = {'first.png': 'red', 'change.png': 'blue', 'last.png': 'green', 'thumb.png': 'yellow'}
        def opened(path):
            return Image.new('RGB', (160, 90), colors.get(Path(path).name, 'black'))
        with patch.object(render.Image, 'open', side_effect=opened):
            episode = render.Episode(self.cfg, self.base)
            timeline = timeline_fixture(self.cfg, 540)
            source = timeline['segments'][0]
            first = episode.frame(source, 2)
            last = episode.frame(source, source['end_frame'] - 1)
        self.assertEqual((1920, 1080), first.size)
        self.assertEqual((255, 0, 0), first.getpixel((960, 400)))
        self.assertIsNone(ImageChops.difference(first, last).getbbox())
        for layout in episode.layouts.values():
            self.assertLessEqual(len(layout), 2)
            for line in layout:
                x0, y0, x1, y1 = line['bounds']
                self.assertGreaterEqual(x0, 96)
                self.assertLessEqual(x1, 1824)
                self.assertGreaterEqual(y0, 856)
                self.assertLessEqual(y1, 1016)

    def test_longform_image_caches_are_bounded_and_reload_for_transitions(self):
        def opened(path):
            return Image.new('RGB', (160, 90), 'red' if Path(path).name == 'first.png' else 'blue')
        with patch.object(render.Image, 'open', side_effect=opened) as files:
            episode = render.Episode(self.cfg, self.base)
            files.assert_not_called()
            for index in range(10):
                name = f'fixture-{index}.png'
                episode.images[name] = None
                episode.photo_layer(name)
            self.assertLessEqual(sum(value is not None for value in episode.images.values()), 4)
            self.assertLessEqual(len(episode._photo_cache), 4)
            self.assertLessEqual(len(episode._art_cache), 4)
            segments = timeline_fixture(self.cfg, 540)['segments']
            target = dict(segments[3], image='change.png')
            previous = dict(segments[2], image='first.png')
            first = episode.frame(target, target['start_frame'], previous)
            last = episode.frame(target, target['start_frame'] + 5, previous)
            self.assertEqual((255, 0, 0), first.getpixel((960, 400)))
            self.assertEqual((0, 0, 255), last.getpixel((960, 400)))

    def test_transitions_only_on_image_change_and_action_cuts_are_hard(self):
        segments = timeline_fixture(self.cfg, 540)['segments']
        settings = render.settings_for(self.cfg)
        self.assertEqual(0, render.photo_transition_frames(segments[0], segments[1], settings))
        self.assertEqual(0, render.photo_transition_frames(segments[1], segments[2], settings))
        self.assertEqual(6, render.photo_transition_frames(segments[2], segments[3], settings))
        self.assertEqual(0, render.photo_transition_frames(segments[3], segments[4], settings))
        reviews = render.review_samples({'segments': segments, 'total_frames': 16200}, settings)
        labels = [label for item in reviews for label in item['labels'] if '교차전환 중간' in label]
        self.assertEqual(['cue_d 사진 교차전환 중간'], labels)

    def test_duration_boundaries_are_measured_including_cta(self):
        self.cfg['winner'] = 'a'
        with patch.object(Path, 'is_file', return_value=True), patch.object(render, 'sha256_file', return_value='fixture'):
            for seconds, passes in ((539 + 29 / 30, False), (540, True), (900, True), (900 + 1 / 30, False)):
                errors = render.validate_timeline(self.cfg, timeline_fixture(self.cfg, seconds), self.base)
                self.assertEqual(passes, not errors, (seconds, errors))

    def test_global_pcm_alignment_pads_once_and_all_words_stay_inside_cues(self):
        plan, decoded = measured_fixture(self.cfg, self.base)
        pcm, timeline = voice.assemble_pcm(self.cfg, plan, decoded)
        raw_samples = sum(len(raw) // 2 for raw, _ in decoded.values())
        individually_padded = sum(math.ceil(len(raw) / 2 / 1600) * 1600 for raw, _ in decoded.values())
        self.assertEqual(raw_samples, timeline['unrounded_total_samples'])
        self.assertEqual(math.ceil(raw_samples / 1600) * 1600, timeline['total_samples'])
        self.assertLess(timeline['total_samples'], individually_padded)
        self.assertEqual(b''.join(raw for raw, _ in decoded.values()), pcm[:raw_samples * 2])
        self.assertLess(timeline['final_alignment_tail_samples'], 1600)
        self.assertEqual(0, sum(group['alignment_tail_samples'] for group in timeline['narration_groups']))
        self.assertEqual(0, sum(group['internal_inserted_silence_samples'] for group in timeline['narration_groups']))
        self.assertNotEqual(0, timeline['narration_groups'][1]['start_sample'] % 1600)
        self.assertEqual('cta', timeline['segments'][-1]['id'])
        self.assertEqual(5, len(timeline['segments']))
        self.assertEqual(2, len(timeline['narration_groups']))
        for segment in timeline['segments']:
            self.assertLessEqual(segment['start'], segment['word_speech_start'])
            self.assertLessEqual(segment['word_speech_end'], segment['end'])
            self.assertLessEqual(segment['speech_end'], segment['end'])
            self.assertEqual(segment['start_frame'] / 30, segment['start'])

    def test_missing_or_cross_word_boundaries_never_use_text_ratio(self):
        plan, decoded = measured_fixture(self.cfg, self.base)
        unit = plan['narration_units'][0]
        raw, metadata = decoded[unit['id']]
        metadata['boundaries'] = []
        with self.assertRaisesRegex(voice.VoiceError, '비율'):
            voice.assemble_pcm(self.cfg, plan, decoded)

    def test_visual_only_edits_keep_per_group_generation_inputs(self):
        first, _ = measured_fixture(self.cfg, self.base)
        changed = copy.deepcopy(self.cfg)
        changed['cuts'][0]['image'] = 'new-first.png'
        second, _ = measured_fixture(changed, self.base)
        self.assertNotEqual(first['script_hash'], second['script_hash'])
        self.assertEqual([voice.sentence_hash(first, u) for u in first['narration_units']],
                         [voice.sentence_hash(second, u) for u in second['narration_units']])
        changed['story_beats'][0]['spoken_text'] = '변경된 검증용 문장입니다.'
        third, _ = measured_fixture(changed, self.base)
        self.assertNotEqual(voice.sentence_hash(first, first['narration_units'][0]),
                            voice.sentence_hash(third, third['narration_units'][0]))
        self.assertEqual(voice.sentence_hash(first, first['narration_units'][1]),
                         voice.sentence_hash(third, third['narration_units'][1]))

    def test_subtitle_only_edit_reuses_source_and_preserves_pronunciation_override(self):
        plan, decoded = measured_fixture(self.cfg, self.base)
        plan['segments'][0]['spoken_override'] = '별도 검증 발음입니다.'
        core.atomic_json(self.base / 'voice_plan.json', plan)
        with patch.object(voice, 'engine_info', return_value={'name': 'fixture'}), \
                patch.object(voice, 'decoder_info', return_value={'name': 'fixture'}):
            original = voice.build_plan(self.cfg, self.base)
            core.atomic_json(self.base / 'voice_plan.json', original)
            changed = copy.deepcopy(self.cfg)
            changed['story_beats'][0]['display_text'] = '표시만 변경한 검증 문장입니다.'
            current = voice.build_plan(changed, self.base)
        self.assertEqual(original['segments'][0]['spoken_text'], current['segments'][0]['spoken_text'])
        before, after = original['narration_units'][0], current['narration_units'][0]
        self.assertEqual(voice.sentence_hash(original, before), voice.sentence_hash(current, after))
        source = self.base / voice.audio_name(before['id'])
        source.write_bytes(b'fixture marker, not actual audio')
        pcm = (array('h', [1000]) * 16000).tobytes()
        core.atomic_json(source.with_suffix('.json'), {
            'input_hash': voice.sentence_hash(original, before), 'audio_sha256': core.sha256_file(source),
            'settings': before, 'engine': original['engine'], 'decoded_pcm_sha256': hashlib.sha256(pcm).hexdigest()})
        with patch.object(voice, 'decode_pcm', return_value=pcm), \
                patch.object(voice, 'fetch_tts', new_callable=AsyncMock) as fetch:
            result = voice.cached_audio(self.base, after, voice.sentence_hash(current, after), current)
            fetch.assert_not_awaited()
        self.assertIsNotNone(result)
        self.assertEqual(after, result[1]['settings'])
        self.assertEqual(pcm, result[0])

    def test_approval_gate_precedes_tts_renderer_locks_and_media_writes(self):
        path = self.base / 'episode.json'
        core.atomic_json(path, self.cfg)
        with patch.object(voice, 'verify_voices', new_callable=AsyncMock) as service, \
                patch.object(render.subprocess, 'Popen') as encoder:
            with self.assertRaisesRegex(voice.VoiceError, 'APPROVAL_REQUIRED'):
                voice.main(path)
            with self.assertRaisesRegex(voice.VoiceError, 'APPROVAL_REQUIRED'):
                asyncio.run(voice._build(self.cfg, self.base))
            with self.assertRaisesRegex(ValueError, 'APPROVAL_REQUIRED'):
                render.render_episode(path)
            with self.assertRaisesRegex(voice.VoiceError, '--silent'):
                voice.main(path, silent=True)
            with self.assertRaisesRegex(ValueError, '--silent'):
                render.render_episode(path, silent=True)
            with self.assertRaisesRegex(ValueError, 'APPROVAL_REQUIRED'):
                asset_tools.normalize_asset(self.base / 'missing.png', self.base / 'first.png',
                                            prompt='Fixture only', episode_cfg=self.cfg)
            service.assert_not_awaited()
            encoder.assert_not_called()
        self.assertEqual(['episode.json'], [p.name for p in self.base.iterdir()])

    def test_srt_is_one_global_file_through_final_comment(self):
        plan, decoded = measured_fixture(self.cfg, self.base)
        _, timeline = voice.assemble_pcm(self.cfg, plan, decoded)
        out = self.base / 'out'
        out.mkdir()
        render.write_subtitles(timeline, out)
        actual = parse_srt((out / 'captions.srt').read_text('utf-8'))
        expected = expected_cues(timeline)
        self.assertEqual(len(expected), len(actual))
        self.assertEqual('여러분의 생각을 댓글로 남겨 주세요', actual[-1]['text'])
        self.assertEqual(round(timeline['duration'] * 1000), actual[-1]['end_ms'])
        errors = []
        validate_subtitles(self.base, timeline, errors, require_visual=False)
        self.assertEqual([], errors)

    def test_old_listening_record_is_preserved_but_not_promoted_to_pass(self):
        previous = {'input_hash': 'old', 'pcm_sha256': 'old', 'status': 'PASS',
                    'user_approval': {'statement': 'Synthetic historical record, not a real approval'},
                    'technical': {'status': 'PASS'}}
        core.atomic_json(self.base / 'audio_review.json', previous)
        voice.update_review(self.base, {'input_hash': 'new', 'pcm_sha256': 'new', 'audio_mode': 'voice',
                                       'script_hash': 'new'}, cfg=self.cfg)
        current = core.load_json(self.base / 'audio_review.json')
        self.assertEqual(previous['user_approval'], current['user_approval'])
        self.assertEqual('NOT_RUN', current['technical']['status'])
        self.assertEqual('AUDIO_REVIEW_REQUIRED', current['status'])
        self.assertFalse(current['listened'])
        self.assertEqual(1, len(list((self.base / 'revisions').glob('audio_review_*.json'))))

    def test_mocked_ffprobe_uses_horizontal_specs_and_final_audio_identity(self):
        for seconds, valid in ((540, True), (900, True), (539.9, False), (900.1, False)):
            timeline = timeline_fixture(self.cfg, seconds)
            metadata = {'format': {'duration': str(seconds)}, 'streams': [
                {'codec_type': 'video', 'width': 1920, 'height': 1080, 'avg_frame_rate': '30/1',
                 'codec_name': 'h264', 'pix_fmt': 'yuv420p', 'nb_frames': timeline['total_frames']},
                {'codec_type': 'audio', 'codec_name': 'aac', 'duration': str(seconds)}]}
            with patch.object(render.subprocess, 'run', side_effect=[
                    SimpleNamespace(stdout=json.dumps(metadata)), SimpleNamespace(returncode=0, stderr='')]), \
                    patch('check_episode.final_audio_sha256', return_value='fixture-final-audio'), \
                    patch('check_episode.audio_timing_hash', return_value='fixture-timing'):
                result = render.technical_checks(self.base / 'never-created.mp4', timeline, cfg=self.cfg)
            self.assertEqual(valid, result['passed'])
            self.assertEqual('fixture-final-audio', result['final_audio_sha256'])
            self.assertEqual('fixture-timing', result['audio_timing_hash'])


if __name__ == '__main__':
    unittest.main()
