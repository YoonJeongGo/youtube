"""Exact user-script opt-in regressions; no generated media or listening claims."""
import unittest

import check_episode
import episode_core as core
from make_vs import CTA_COMMENT_SAY

SCRIPT = '''로키와 김선달, 사기꾼끼리 붙으면 누가 먼저 속을까요?
로키는 머리 내기에 지고도 '목은 안 걸었다'며 빠져나간 신이고,
김선달은 주인도 없는 대동강 물을 팔아먹은 사내죠.
나루에서 둘이 내기를 겁니다. 먼저 속는 쪽이 머리를 내놓기로요.
김선달이 덧붙입니다. "머리부터 목까지 전부요." 로키의 웃음이 멈추죠.
로키가 대동강을 사겠다며 묵직한 금 주머니를 건넵니다. 안에는 자갈뿐입니다.
김선달은 싱글벙글 받고, 강 문서를 내줍니다.
로키가 먼저 웃습니다. "주머니는 열어 봤소?"
김선달도 웃습니다. "강은 내 거라고 했소?"
둘 다 속였고, 둘 다 속았습니다. 그럼 머리는 누가 내놓아야 할까요?
여러분의 생각을 댓글로 남겨 주세요.'''


def fixture():
    lines = SCRIPT.splitlines()
    lines[9:10] = ['둘 다 속였고, 둘 다 속았습니다.', '그럼 머리는 누가 내놓아야 할까요?']
    lines = lines[:-1]
    ids = ['hook', 'a.intro', 'b.intro', 'wager', 'neck', 'bogus_gold', 'river_deed',
           'loki_taunt', 'kim_taunt', 'reason', 'question']
    images = ['thumb', 'a', 'b', 'scene', 'a_2', 'a_atk', 'b_atk', 'b_atk', 'b_2',
              'cliffhanger', 'cliffhanger']
    kinds = ['hook', 'intro', 'intro', 'story', 'story', 'edge', 'edge', 'story', 'story', 'reason', 'question']
    return dict(schema_version=2, production_profile=core.DETAIL_PROFILE, format='shorts',
                cta_presentation='comment_spoken', winner='none', thumb='thumb.png',
                user_verbatim_script=SCRIPT, fictional_matchup=True, fictional_premise='A fictional duel.',
                hook=lines[0], reason=lines[-2], question=lines[-1],
                a=dict(name='로키', tag='사기꾼', image='a.png'),
                b=dict(name='김선달', tag='사기꾼', image='b.png'),
                cliffhanger=dict(image='cliffhanger.png', winner_revealed=False),
                story_beats=[dict(id=key, kind=kind, image=image+'.png', spoken_text=line, display_text=line)
                             for key, kind, image, line in zip(ids, kinds, images, lines)],
                narration_groups=[dict(id='opening', segment_ids=['hook']),
                                  dict(id='introductions', segment_ids=['a.intro', 'b.intro']),
                                  *[dict(id=key, segment_ids=[key]) for key in ids[3:-2]],
                                  dict(id='ending', segment_ids=ids[-2:])])


class VerbatimShortTests(unittest.TestCase):
    def test_exact_script_accepts_natural_twelve_cues_and_quote_endings(self):
        cfg = fixture()
        errors = []
        check_episode.check_text(cfg, errors)
        self.assertEqual([], errors)
        cues = core.speech_segments(cfg)
        self.assertEqual(12, len(cues))
        self.assertEqual(' '.join(SCRIPT.split()), ' '.join(c['spoken_text'] for c in cues))
        self.assertEqual(CTA_COMMENT_SAY, cues[-1]['spoken_text'])
        self.assertEqual(CTA_COMMENT_SAY, cues[-1]['display_text'])
        self.assertFalse(cues[-1]['caption_in_overlay'])
        self.assertTrue(all('cta_overlay' not in cue for cue in cues))
        self.assertTrue(all(cue['display_text'] == cue['spoken_text'] for cue in cues))
        self.assertEqual(set(core.USER_VERBATIM_IMAGE_NAMES), {c['image'] for c in cues})
        self.assertEqual((30, 60), core.duration_limits(cfg))
        self.assertEqual(30, core.video_spec(cfg)['fps'])
        self.assertEqual(['a.intro', 'b.intro'], core.narration_groups(cfg)[1]['segment_ids'])

    def test_exact_script_rejects_changed_reordered_and_duplicated_text_before_tts(self):
        for kind in ('changed', 'reordered', 'duplicate_cta', 'caption'):
            cfg = fixture()
            if kind == 'changed':
                cfg['story_beats'][0]['spoken_text'] = '누가 이길까요?'
            elif kind == 'reordered':
                cfg['story_beats'][1:3] = cfg['story_beats'][1:3][::-1]
            elif kind == 'duplicate_cta':
                cfg['story_beats'][-1]['spoken_text'] += ' ' + CTA_COMMENT_SAY
            else:
                cfg['story_beats'][1]['display_text'] = '바뀐 자막입니다.'
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'user_verbatim_script'):
                core.speech_segments(cfg)

    def test_default_v5_still_rejects_free_story_and_incomplete_groups(self):
        cfg = fixture()
        del cfg['user_verbatim_script']
        with self.assertRaisesRegex(ValueError, 'story_beats is not supported'):
            core.speech_segments(cfg)
        cfg = fixture()
        cfg['narration_groups'].pop()
        with self.assertRaisesRegex(ValueError, 'all cues'):
            core.narration_groups(cfg)

    def test_preserved_punctuation_cannot_hide_an_incomplete_spoken_group(self):
        cfg = fixture()
        cfg['narration_groups'][1:2] = [dict(id='loki', segment_ids=['a.intro']),
                                       dict(id='kim', segment_ids=['b.intro'])]
        errors = []
        check_episode.check_text(cfg, errors)
        self.assertTrue(any('complete spoken passage' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
