"""New defaults keep continuous introductions and a separate unresolved ending."""
import copy
from pathlib import Path
import unittest
import tempfile
from PIL import Image, ImageChops
from asset_tools import image_target
from check_episode import check_text
from episode_core import load_json, speech_segments, narration_groups, image_names, duration_limits


class ShowdownProfileTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_json(Path(__file__).resolve().parents[1] / 'episode_example.json')

    def errors(self, cfg=None):
        result = []
        check_text(cfg or self.cfg, result)
        return result

    def test_default_is_valid_with_nine_distinct_screen_scenes(self):
        self.assertEqual([], self.errors())
        segments = speech_segments(self.cfg)
        self.assertEqual(14, len(segments))
        self.assertEqual(9, len({s['image'] for s in segments}))
        self.assertEqual(11, len(image_names(self.cfg)))
        self.assertEqual((30, 60), duration_limits(self.cfg))

    def test_solo_reference_reuse_does_not_require_unused_intro_assets(self):
        for who in ('a','b'):
            self.cfg[who]['intro_images'][0] = who+'.png'
        self.assertEqual([], self.errors())
        self.assertEqual(9, len(image_names(self.cfg)))
        self.assertNotIn('a_1.png',image_names(self.cfg))
        self.assertEqual((1024,1536),image_target('cliffhanger.png'))

    def test_last_two_cues_use_new_art_and_never_spoken_cta(self):
        self.cfg['cta_presentation']='overlay'  # Preserve previous releases.
        segments=speech_segments(self.cfg)
        self.assertEqual(['reason','question'],[s['id'] for s in segments[-2:]])
        self.assertTrue(all(s['image']=='cliffhanger.png' for s in segments[-2:]))
        self.assertTrue(all('좋아요와 구독도 부탁드려요' in s['cta_overlay'] for s in segments[-2:]))
        self.assertFalse(any(s['id']=='cta' for s in segments))
        self.cfg['cta_presentation']='spoken'
        self.assertTrue(any('screen-only' in e for e in self.errors()))

    def test_comment_only_cta_follows_question_on_same_art(self):
        from make_vs import CTA_COMMENT_SAY, CTA_SHOW
        segments=speech_segments(self.cfg)
        self.assertEqual(['reason','question','cta'],[s['id'] for s in segments[-3:]])
        self.assertEqual(CTA_COMMENT_SAY,segments[-1]['spoken_text'])
        self.assertEqual(CTA_SHOW[0],segments[-1]['display_text'])
        self.assertEqual('cliffhanger.png',segments[-1]['image'])
        self.assertTrue(segments[-1]['caption_in_overlay'])
        self.assertNotIn('좋아요',' '.join(s['spoken_text'] for s in segments))
        self.assertEqual(['cta'],narration_groups(self.cfg)[-1]['segment_ids'])

    def test_spoken_comment_uses_existing_overlay_without_duplicate_caption(self):
        from make_vs import Episode, BLACK
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            for name in image_names(self.cfg):Image.new('RGB',(40,60),'#557799').save(base/name)
            episode=Episode(self.cfg,base)
            s=dict(speech_segments(self.cfg)[-1],start_frame=0,end_frame=90,start=0,end=3,speech_start=0,speech_end=3)
            frame=episode.frame(s,30)
            # Overlay contains the comment/subscription lines; bottom caption
            # area must stay clear instead of repeating the spoken comment.
            overlay=frame.crop((72,1340,948,1495));bottom=frame.crop((72,1510,948,1730))
            self.assertIsNotNone(ImageChops.difference(overlay,Image.new('RGB',overlay.size,BLACK)).getbbox())
            self.assertIsNone(ImageChops.difference(bottom,Image.new('RGB',bottom.size,BLACK)).getbbox())

    def test_intro_and_feat_must_share_narration_group(self):
        groups=narration_groups(self.cfg)
        self.assertTrue(any(g['segment_ids']==['a.intro.0','a.intro.1'] for g in groups))
        self.cfg['narration_groups']=[{'id':'isolated_'+str(i),'segment_ids':[s['id']]} for i,s in enumerate(speech_segments(self.cfg))]
        self.assertTrue(any('one narration group' in e for e in self.errors()),self.errors())

    def test_missing_or_reused_ending_is_rejected(self):
        for mutation in (lambda c:c.pop('cliffhanger'),lambda c:c['cliffhanger'].update(image='a_atk.png'),lambda c:c['cliffhanger'].update(winner_revealed=True)):
            cfg=copy.deepcopy(self.cfg);mutation(cfg)
            self.assertTrue(any('cliffhanger' in e for e in self.errors(cfg)))

    def test_unsubstantiated_or_cross_version_feat_is_rejected(self):
        for mutation in (lambda c:c['b'].pop('featured_feat'),lambda c:c['b']['featured_feat'].update(version='different version'),lambda c:c['b']['featured_feat'].update(source={})): 
            cfg=copy.deepcopy(self.cfg);mutation(cfg)
            self.assertTrue(any('featured_feat' in e for e in self.errors(cfg)))

    def test_opening_must_finish_with_channel_question(self):
        self.cfg['hook']='분신 앞에서 활이 통할까요?'
        self.assertTrue(any('누가 이길까요?' in e for e in self.errors()))

    def test_clause_allowed_but_feat_completes_sentence(self):
        self.assertEqual([], self.errors())
        self.cfg['a']['intro'][1]='분신을 보내 팔도를'
        self.assertTrue(any('sentence must end' in e for e in self.errors()))


if __name__=='__main__': unittest.main()
