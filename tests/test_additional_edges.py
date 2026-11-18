"""Optional second v4 exchange reaches assets, narration and the real renderer."""
import copy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from asset_tools import image_target
from check_episode import check_images, check_text
from episode_core import duration_limits, image_names, load_json, narration_groups, speech_segments
import make_voice as voice
from make_vs import COL_A, COL_B, Episode, photo_transition_frames


EXTRA = [
    {'condition': 'a', 'title': '다시 거리를 벌리면', 'image': 'a_atk_2.png',
     'lines': ['상대의 공격을 피해 옆으로 빠져나가면,', '다시 거리를 벌릴 여지가 생깁니다.']},
    {'condition': 'b', 'title': '퇴로를 좁히면', 'image': 'b_atk_2.png',
     'lines': ['물러나는 방향을 따라 길을 좁히면,', '다시 가까이 붙을 기회를 노릴 수 있죠.']},
]
BASE_IDS = ['hook', 'a.intro.0', 'a.intro.1', 'b.intro.0', 'b.intro.1',
            'setup.0', 'setup.1', 'a.edge.0', 'a.edge.1', 'b.edge.0', 'b.edge.1',
            'reason', 'question', 'cta']
EXTRA_IDS = ['a.edge2.0', 'a.edge2.1', 'b.edge2.0', 'b.edge2.1']


class AdditionalEdgesTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_json(Path(__file__).resolve().parents[1] / 'episode_example.json')
        for who in ('a', 'b'):
            self.cfg[who]['intro_images'][0] = who + '.png'

    def errors(self, cfg):
        errors = []
        check_text(cfg, errors)
        return errors

    def with_extra(self):
        self.cfg['additional_edges'] = copy.deepcopy(EXTRA)
        return self.cfg

    def test_absence_preserves_existing_fourteen_cues_nine_assets_and_groups(self):
        self.assertEqual([], self.errors(self.cfg))
        self.assertEqual(BASE_IDS, [cue['id'] for cue in speech_segments(self.cfg)])
        self.assertEqual(('thumb.png', 'a.png', 'b.png', 'a_2.png', 'b_2.png',
                          'scene.png', 'a_atk.png', 'b_atk.png', 'cliffhanger.png'), image_names(self.cfg))
        self.assertEqual(['opening', 'intro_a', 'intro_b', 'setup', 'edge_a', 'edge_b',
                          'ending', 'cta'], [group['id'] for group in narration_groups(self.cfg)])
        self.assertEqual((30, 60), duration_limits(self.cfg))

    def test_extra_exchange_is_eighteen_cues_eleven_assets_and_continuous_tts(self):
        cfg = self.with_extra()
        self.assertEqual([], self.errors(cfg))
        cues = speech_segments(cfg)
        ids = [cue['id'] for cue in cues]
        self.assertEqual(BASE_IDS[:-3] + EXTRA_IDS + BASE_IDS[-3:], ids)
        self.assertEqual(11, len(image_names(cfg)))
        self.assertEqual(11, len({cue['image'] for cue in cues}))
        self.assertEqual((30, 60), duration_limits(cfg))
        groups = narration_groups(cfg)
        self.assertEqual(ids, [key for group in groups for key in group['segment_ids']])
        self.assertEqual(['edge2_a', 'edge2_b', 'ending', 'cta'], [g['id'] for g in groups[-4:]])
        with tempfile.TemporaryDirectory() as folder:
            plan = voice.build_plan(cfg, Path(folder))
        for edge in EXTRA:
            who = edge['condition']
            unit = next(unit for unit in plan['narration_units'] if unit['id'] == 'group.edge2_' + who)
            self.assertEqual([f'{who}.edge2.0', f'{who}.edge2.1'], unit['segment_ids'])
            self.assertEqual(' '.join(edge['lines']), unit['spoken_text'])
        self.assertEqual(['reason', 'question', 'cta'], [cue['id'] for cue in cues[-3:]])
        self.assertTrue(all(cue['image'] == 'cliffhanger.png' for cue in cues[-3:]))

    def test_optional_clash_stays_before_first_exchange_with_additional_edges(self):
        cfg = self.with_extra()
        cfg['clash'] = {'title': '첫 맞대결', 'image': 'sword_clash.png', 'text': '서로 거리를 재며 맞섭니다.'}
        ids = [cue['id'] for cue in speech_segments(cfg)]
        self.assertEqual(['setup.1', 'sword_clash', 'a.edge.0'], ids[6:9])
        self.assertEqual(ids, [key for group in narration_groups(cfg) for key in group['segment_ids']])
        self.assertEqual([], self.errors(cfg))

    def test_missing_new_assets_fail_and_unique_correct_size_assets_pass(self):
        cfg = self.with_extra()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for index, name in enumerate(image_names(cfg)):
                if name not in ('a_atk_2.png', 'b_atk_2.png'):
                    Image.new('RGB', image_target(name), (index * 15, 40, 80)).save(base / name)
            errors = []
            check_images(cfg, base, errors)
            self.assertEqual(['그림 누락: a_atk_2.png', '그림 누락: b_atk_2.png'], errors)
            for index, name in enumerate(('a_atk_2.png', 'b_atk_2.png')):
                self.assertEqual((1024, 1536), image_target(name))
                Image.new('RGB', image_target(name), (200, index * 90, 10)).save(base / name)
            errors = []
            check_images(cfg, base, errors)
            self.assertEqual([], errors)

    def test_renderer_uses_second_images_headers_borders_and_static_same_image(self):
        cfg = self.with_extra()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            colors = {'a_atk_2.png': (210, 30, 40), 'b_atk_2.png': (20, 60, 220)}
            for name in image_names(cfg):
                Image.new('RGB', (40, 60), colors.get(name, (40, 90, 40))).save(base / name)
            episode = Episode(cfg, base)
            cues = {cue['id']: cue for cue in speech_segments(cfg)}
            for edge, border in zip(EXTRA, (COL_A, COL_B)):
                who = edge['condition']
                previous_id = 'b.edge.1' if who == 'a' else 'a.edge2.1'
                previous = dict(cues[previous_id], start_frame=0, end_frame=90)
                cue = dict(cues[f'{who}.edge2.0'], start_frame=90, end_frame=180,
                           start=3, end=6, speech_start=3, speech_end=6)
                self.assertEqual((edge['title'], '', border), episode.header_content(cue))
                self.assertEqual(6, photo_transition_frames(previous, cue, episode.settings))
                frame = episode.frame(cue, 96, previous)
                self.assertEqual(colors[edge['image']], frame.getpixel((540, 900)))
                self.assertEqual(border, frame.getpixel((15, 500)))
                continuation = dict(cues[f'{who}.edge2.1'], start_frame=180, end_frame=270)
                self.assertEqual(0, photo_transition_frames(cue, continuation, episode.settings))

    def test_invalid_extra_shapes_order_assets_and_text_report_errors(self):
        malformed = [None, [], EXTRA[:1], EXTRA + EXTRA[:1], list(reversed(EXTRA)), [None, EXTRA[1]]]
        for change in ({'condition': 'b'}, {'image': '../a_atk_2.png'}, {'image': 'a_atk.png'},
                       {'title': ''}, {'title': '가' * 17}, {'lines': ['한 문장.']},
                       {'lines': ['가' * 45, '두 번째 문장입니다.']},
                       {'lines': ['앞 절을 말하며,', '뒤 절이 끝나지 않음']},
                       {'reaction_image': 'b_react.png'}):
            altered = copy.deepcopy(EXTRA)
            altered[0].update(change)
            malformed.append(altered)
        for edges in malformed:
            with self.subTest(edges=edges):
                cfg = copy.deepcopy(self.cfg)
                cfg['additional_edges'] = edges
                self.assertTrue(any('additional_edges' in error for error in self.errors(cfg)))

    def test_explicit_groups_cannot_silently_omit_extra_cues_and_other_profiles_reject_field(self):
        cfg = self.with_extra()
        without = copy.deepcopy(cfg)
        del without['additional_edges']
        cfg['narration_groups'] = narration_groups(without)
        self.assertTrue(any('all cues' in error for error in self.errors(cfg)))
        del cfg['narration_groups']
        cfg['production_profile'] = 'setup_story_v3'
        self.assertTrue(any('additional_edges is only supported' in error for error in self.errors(cfg)))


if __name__ == '__main__':
    unittest.main()
