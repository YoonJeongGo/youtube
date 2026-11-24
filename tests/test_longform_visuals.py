"""No TTS or encoder: test independent picture cuts within one spoken cue."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
import make_vs as render
from longform_visuals import VisualShotPlan, timeline_picture_shots
from test_longform_profile import longform_fixture

class VisualShotTests(unittest.TestCase):
    def test_rejects_gaps_overlap_undeclared_images_and_disguised_long_hold(self):
        base={'start_frame':0,'end_frame':300,'image':'first.png'}
        for shots in (
            [dict(base,start_frame=1)],
            [base,dict(base,start_frame=299,end_frame=600)],
            [dict(base,image='../foreign.png')],
            [dict(base,end_frame=400),dict(base,start_frame=400,end_frame=700)],
            [dict(base,crop=[-1,0,100,100])],
        ):
            with self.assertRaises(ValueError):VisualShotPlan(shots,{'first.png'})
        with self.assertRaises(ValueError):VisualShotPlan([base],{'first.png'},301)

    def test_missing_plan_cannot_hide_a_long_hold_by_changing_captions(self):
        timeline={"segments":[{"start_frame":0,"end_frame":400,"image":"first.png"},
                              {"start_frame":400,"end_frame":650,"image":"first.png"}]}
        inferred=timeline_picture_shots(timeline)
        self.assertEqual(1,len(inferred))
        with self.assertRaisesRegex(ValueError,"20 seconds"):
            VisualShotPlan(inferred,{"first.png"},650)

    def test_a_picture_change_inside_one_cue_changes_pixels_without_changing_speech(self):
        cfg=longform_fixture()
        settings=render.settings_for(cfg=cfg)
        settings.update(show_header=False,show_overlay_bands=False,white_captions=True)
        settings['visual_shots']=[
            {'start_frame':0,'end_frame':150,'image':'first.png','crop':[0,0,960,1080]},
            {'start_frame':150,'end_frame':300,'image':'first.png','crop':[960,0,1920,1080]},
        ]
        art=Image.new('RGB',(1920,1080),'red')
        ImageDraw.Draw(art).rectangle((960,0,1919,1079),fill='blue')
        segment=dict(render.speech_segments(cfg)[0],start_frame=0,end_frame=300,start=0,end=10)
        before=copy.deepcopy(segment)
        with tempfile.TemporaryDirectory() as temp, patch.object(render.Image,'open',side_effect=lambda _:art.copy()):
            ep=render.Episode(cfg,Path(temp),settings)
            self.assertEqual((255,0,0),ep.frame(segment,149).getpixel((900,400)))
            self.assertEqual((0,0,255),ep.frame(segment,150).getpixel((900,400)))
            self.assertEqual(before,segment)
            self.assertEqual(300,segment['end_frame'])

    def test_crop_transition_and_caption_relocation_have_actual_review_samples(self):
        cfg=longform_fixture()
        settings=render.settings_for(cfg=cfg)
        settings.update(show_header=False,show_overlay_bands=False)
        settings['visual_shots']=[
            {'start_frame':0,'end_frame':150,'image':'first.png','crop':[0,0,960,1080]},
            {'start_frame':150,'end_frame':300,'image':'first.png','crop':[960,0,1920,1080],
             'transition':'crossfade','caption_box':[96,64,1824,240]},
        ]
        art=Image.new('RGB',(1920,1080),'red')
        ImageDraw.Draw(art).rectangle((960,0,1919,1079),fill='blue')
        segment=dict(render.speech_segments(cfg)[0],start_frame=0,end_frame=300,start=0,end=10)
        with tempfile.TemporaryDirectory() as temp, patch.object(render.Image,'open',side_effect=lambda _:art.copy()):
            ep=render.Episode(cfg,Path(temp),settings)
            self.assertEqual((255,0,0),ep.frame(segment,150).getpixel((900,400)))
            self.assertEqual((0,0,255),ep.frame(segment,155).getpixel((900,400)))
            layout=next(iter(ep.shot_caption_layouts.values()))
            self.assertTrue(all(64 <= line['bounds'][1] <= line['bounds'][3] <= 240 for line in layout))
        reviews=render.review_samples({'segments':[segment],'total_frames':300},settings)
        frames={s['frame'] for s in reviews}
        self.assertTrue({150,153,224,299}<=frames)
if __name__=='__main__':unittest.main()

