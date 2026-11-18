"""Optional v4 action cue and narrowly scoped revision authorization regressions."""
import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from asset_tools import image_target
from check_episode import check_images, check_text
from episode_core import (atomic_json, image_names, load_json, narration_groups,
                          script_hash, speech_segments)
import make_voice as voice
from make_vs import Episode, photo_transition_frames
from tests.test_voice import fixture as legacy_fixture


CLASH = {
    'title': '칼과 칼이 부딪친다면',
    'image': 'sword_clash.png',
    'text': '칼날이 맞부딪친 뒤, 누가 먼저 다음 공격을 이어갈까요?',
}


class OptionalSwordClashTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_json(Path(__file__).resolve().parents[1] / 'episode_example.json')
        for who in ('a', 'b'):
            self.cfg[who]['intro_images'][0] = who + '.png'
        self.cfg['clash'] = copy.deepcopy(CLASH)

    def test_clash_is_one_extra_scene_with_its_own_tts_group_in_playback_order(self):
        errors = []
        check_text(self.cfg, errors)
        self.assertEqual([], errors)
        cues = speech_segments(self.cfg)
        ids = [cue['id'] for cue in cues]
        index = ids.index('setup.1')
        self.assertEqual(['setup.1', 'sword_clash', 'a.edge.0'], ids[index:index + 3])
        self.assertEqual(15, len(cues))
        self.assertEqual(10, len(image_names(self.cfg)))
        self.assertEqual(10, len({cue['image'] for cue in cues}))
        groups = narration_groups(self.cfg)
        self.assertEqual(ids, [key for group in groups for key in group['segment_ids']])
        self.assertIn({'id': 'sword_clash', 'segment_ids': ['sword_clash']}, groups)
        with tempfile.TemporaryDirectory() as folder:
            plan = voice.build_plan(self.cfg, Path(folder))
        unit = next(unit for unit in plan['narration_units'] if unit['id'] == 'group.sword_clash')
        self.assertEqual(['sword_clash'], unit['segment_ids'])
        self.assertEqual(CLASH['text'], unit['spoken_text'])

    def test_invalid_clash_objects_paths_and_text_return_validation_errors(self):
        invalid = [None, [], {},
                   {**CLASH, 'image': 'a_atk.png'},
                   {**CLASH, 'image': '../sword_clash.png'},
                   {**CLASH, 'title': ''},
                   {**CLASH, 'title': '가' * 23},
                   {**CLASH, 'text': '가' * 48 + '.'},
                   {**CLASH, 'text': '문장 끝이 없음'},
                   {**CLASH, 'text': '첫 문장. 둘째 문장.'}]
        for clash in invalid:
            with self.subTest(clash=clash):
                cfg = copy.deepcopy(self.cfg)
                cfg['clash'] = clash
                errors = []
                check_text(cfg, errors)
                self.assertTrue(any('clash' in error for error in errors), errors)

    def test_new_asset_is_required_and_uses_standard_portrait_scene_dimensions(self):
        self.assertEqual((1024, 1536), image_target('sword_clash.png'))
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for index, name in enumerate(image_names(self.cfg)):
                if name != 'sword_clash.png':
                    Image.new('RGB', image_target(name), (index * 16, 40, 80)).save(base / name)
            errors = []
            check_images(self.cfg, base, errors)
            self.assertEqual(['그림 누락: sword_clash.png'], errors)

    def test_story_renderer_uses_clash_art_header_and_existing_photo_transition(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in image_names(self.cfg):
                color = (220, 20, 30) if name == 'sword_clash.png' else (20, 40, 220)
                Image.new('RGB', (40, 60), color).save(base / name)
            episode = Episode(self.cfg, base)
            cues = {cue['id']: cue for cue in speech_segments(self.cfg)}
            cue = dict(cues['sword_clash'], start_frame=90, end_frame=180,
                       start=3, end=6, speech_start=3, speech_end=6)
            previous = dict(cues['setup.1'], start_frame=0, end_frame=90)
            self.assertEqual('story', cue['kind'])
            self.assertEqual(CLASH['title'], episode.header_content(cue)[0])
            self.assertEqual(6, photo_transition_frames(previous, cue, episode.settings))
            self.assertEqual((220, 20, 30), episode.frame(cue, 96, previous).getpixel((540, 900)))

    def test_absence_and_other_profiles_preserve_existing_sequences(self):
        without = copy.deepcopy(self.cfg)
        del without['clash']
        self.assertEqual(14, len(speech_segments(without)))
        self.assertNotIn('sword_clash.png', image_names(without))
        self.assertNotIn('sword_clash', [group['id'] for group in narration_groups(without)])
        for cfg in (legacy_fixture(), {**without, 'production_profile': 'setup_story_v3'}):
            with self.subTest(profile=cfg.get('production_profile', 'legacy')):
                expected = (speech_segments(cfg), narration_groups(cfg), image_names(cfg))
                cfg['clash'] = copy.deepcopy(CLASH)
                self.assertEqual(expected, (speech_segments(cfg), narration_groups(cfg), image_names(cfg)))
        self.assertIn('clash', [cue['id'] for cue in speech_segments(legacy_fixture())])


class RevisionAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cfg = legacy_fixture()
        self.cfg['job_id'] = 'test-revision-job'
        atomic_json(self.base / 'episode.json', self.cfg)
        self.plan = voice.build_plan(self.cfg, self.base)
        self.entry = self.plan['segments'][0]
        self.authorization = {'job_id': self.cfg['job_id'], 'user_statement': '승인된 대본으로 수정해.',
                              'revised_script_hash': script_hash(self.cfg),
                              'authorized_extra_voice_generations': 1}

    async def fake_fetch(self, entry, path):
        Path(path).write_bytes(b'test-only-raw-audio')
        return []

    def generate_at_limit(self):
        state = {'sentences': {self.entry['id']: {'attempts': 3, 'history': []}}}
        with patch.object(voice, 'fetch_tts', new_callable=AsyncMock, side_effect=self.fake_fetch) as fetch:
            with patch.object(voice, 'decode_pcm', return_value=b'\x10\x00' * 48000):
                asyncio.run(voice.generate_sentence(self.base, self.entry, self.plan, state, force=True))
        self.assertEqual(1, fetch.await_count)
        self.assertEqual(4, state['sentences'][self.entry['id']]['attempts'])

    def test_production_revision_authorization_does_not_require_release_acceptance(self):
        atomic_json(self.base / 'production_revision_authorization.json', self.authorization)
        self.generate_at_limit()
        self.assertFalse((self.base / 'user_release_acceptance.json').exists())

    def test_legacy_release_acceptance_is_still_used_when_revision_file_absent(self):
        atomic_json(self.base / 'user_release_acceptance.json', self.authorization)
        self.generate_at_limit()

    def test_invalid_primary_authorization_cannot_fall_back_or_bypass_existing_guards(self):
        atomic_json(self.base / 'user_release_acceptance.json', self.authorization)
        invalid = [{'job_id': 'wrong-job'}, {'user_statement': ''}, {'revised_script_hash': 'stale'},
                   {'authorized_extra_voice_generations': True},
                   {'authorized_extra_voice_generations': -1},
                   {'authorized_extra_voice_generations': 1.5}]
        for change in invalid:
            with self.subTest(change=change):
                atomic_json(self.base / 'production_revision_authorization.json', {**self.authorization, **change})
                state = {'sentences': {self.entry['id']: {'attempts': 3, 'history': []}}}
                with patch.object(voice, 'fetch_tts', new_callable=AsyncMock) as fetch:
                    with self.assertRaisesRegex(voice.VoiceError, '2회 한도'):
                        asyncio.run(voice.generate_sentence(self.base, self.entry, self.plan, state, force=True))
                    fetch.assert_not_awaited()
                self.assertEqual(3, state['sentences'][self.entry['id']]['attempts'])


if __name__ == '__main__':
    unittest.main()
