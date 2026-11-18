"""V5 selection/voice/timing/render-metadata integration, with no media service or render."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import episode_core as core
import make_voice as voice
import make_vs as video
from plan_matchups import candidate_errors, select_matchups
from project_state import ProjectState, atomic_json, atomic_text, load_json
from tests.test_detail_profile import CUE_IDS, detail_fixture
from tests.test_planning import recognizable_idea
from tests.test_story_voice import synthetic


class DetailIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'episodes').mkdir()
        atomic_text(self.root / 'queue.txt', '')
        atomic_text(self.root / 'done.txt', '')

    def pool(self, candidate, configured=True):
        pool = {'schema_version': 1, 'aliases': {}, 'candidates': [candidate]}
        if configured:
            pool['default_production_profile'] = core.DETAIL_PROFILE
        atomic_json(self.root / 'matchup_ideas.json', pool)

    def test_new_selection_defaults_to_v5_without_rewriting_candidate(self):
        candidate = recognizable_idea()
        candidate['conditions'].pop('opposed')
        candidate['conditions'].pop('opposed_reason')
        self.pool(candidate)
        result = select_matchups(self.root)
        self.assertEqual(core.DETAIL_PROFILE, result['selections'][0]['plan']['production_profile'])
        saved = load_json(self.root / 'matchup_ideas.json')['candidates'][0]
        self.assertNotIn('production_profile', saved)
        self.assertNotIn('opposed', saved['conditions'])
        self.assertEqual(1, result['attempts_used'])

    def test_explicit_old_fresh_candidate_requires_review_not_automatic_migration(self):
        candidate = recognizable_idea()
        candidate['production_profile'] = core.SHOWDOWN_PROFILE
        self.pool(candidate)
        result = select_matchups(self.root)
        self.assertEqual([], result['selections'])
        self.assertEqual(0, result['attempts_used'])
        self.assertTrue(any('production_profile=' in item['reason'] for item in result['research_needed']))
        self.assertEqual(core.SHOWDOWN_PROFILE, load_json(self.root / 'matchup_ideas.json')['candidates'][0]['production_profile'])

    def test_previously_blocked_queue_uses_v5_default_after_research_is_corrected(self):
        candidate = recognizable_idea()
        candidate['eligibility']['status'] = 'BLOCKED'
        self.pool(candidate)
        atomic_text(self.root / 'queue.txt', '인물가 vs 인물나\n')
        self.assertEqual([], select_matchups(self.root)['selections'])
        candidate['eligibility']['status'] = 'ALLOWED'
        candidate['conditions'].pop('opposed')
        candidate['conditions'].pop('opposed_reason')
        self.pool(candidate)
        result = select_matchups(self.root)
        self.assertEqual(1, len(result['selections']))
        self.assertEqual('queue', result['selections'][0]['source'])
        self.assertEqual(core.DETAIL_PROFILE, result['selections'][0]['plan']['production_profile'])

    def test_resume_reads_saved_v4_profile_even_when_pool_is_new_v5(self):
        candidate = recognizable_idea()
        candidate['production_profile'] = core.SHOWDOWN_PROFILE
        self.pool(candidate, configured=False)
        original = select_matchups(self.root)['selections'][0]
        episode = self.root / original['episode_path']
        cfg = core.load_json(Path(__file__).resolve().parents[1] / 'episode_example.json')
        cfg.update(job_id=original['job_id'])
        atomic_json(episode, cfg)
        before = episode.read_bytes()
        with ProjectState(self.root) as state:
            state.set_status(original['job_id'], 'NEEDS_REVIEW', 'Fixture, no production')
        candidate['production_profile'] = core.DETAIL_PROFILE
        candidate['conditions'].pop('opposed')
        candidate['conditions'].pop('opposed_reason')
        self.pool(candidate)
        resumed = select_matchups(self.root, requested='인물가 vs 인물나')['selections'][0]
        self.assertEqual(original['job_id'], resumed['job_id'])
        self.assertEqual('resume', resumed['action'])
        self.assertEqual(core.SHOWDOWN_PROFILE, resumed['plan']['production_profile'])
        self.assertEqual(before, episode.read_bytes())

    def test_opposed_conditions_are_required_only_for_old_recognizable_profile(self):
        candidate = recognizable_idea()
        candidate['conditions'].pop('opposed')
        candidate['conditions'].pop('opposed_reason')
        candidate['production_profile'] = core.DETAIL_PROFILE
        self.assertEqual([], candidate_errors(candidate))
        candidate['production_profile'] = core.SHOWDOWN_PROFILE
        self.assertTrue(any('opposed' in error for error in candidate_errors(candidate)))

    def test_grouped_pcm_word_boundaries_and_srt_use_same_v5_cues(self):
        cfg = detail_fixture()
        # Only metadata functions are mocked; no service, decoder or renderer runs.
        with patch.object(voice, 'engine_info', return_value={'name': 'test', 'version': '1'}), \
             patch.object(voice, 'decoder_info', return_value={'name': 'test', 'version': '1'}), \
             patch.object(voice, 'fetch_tts', side_effect=AssertionError('TTS forbidden')), \
             patch.object(video, 'render', side_effect=AssertionError('Render forbidden')):
            plan = voice.build_plan(cfg, self.root)
            self.assertEqual('voice', plan['audio_mode'])
            self.assertEqual(voice.STORY_TRIM_POLICY, plan['pcm_trim_policy'])
            self.assertEqual(8, len(plan['narration_units']))
            self.assertTrue(all(unit['explicit_group'] for unit in plan['narration_units']))
            decoded = {}
            for unit in voice.generation_units(plan):
                raw, boundaries = synthetic(unit['spoken_text'], scale=.2)
                digest = hashlib.sha256(raw).hexdigest()
                decoded[unit['id']] = raw, {'boundaries': boundaries,
                    'audio_file': unit['id'] + '.mp3', 'audio_sha256': 'fixture',
                    'input_hash': 'fixture', 'decoded_pcm_sha256': digest,
                    'raw_pcm_file': unit['id'] + '.wav', 'raw_pcm_sha256': digest}
            pcm, timeline = voice.assemble_pcm(cfg, plan, decoded)
            self.assertEqual(CUE_IDS, [cue['id'] for cue in timeline['segments']])
            self.assertEqual(timeline['total_samples'] * voice.SAMPLE_WIDTH, len(pcm))
            for group in timeline['narration_groups']:
                self.assertEqual(0, group['internal_inserted_silence_samples'])
                for boundary in group['measured_cue_boundaries']:
                    self.assertLessEqual(abs(boundary['alignment_error_samples']), voice.SAMPLES_PER_FRAME / 2)
            for cue in timeline['segments']:
                parent = cue['parent_audio']
                raw = decoded[parent['id']][0]
                sliced = raw[parent['source_start_sample'] * 2:parent['source_end_sample'] * 2]
                self.assertEqual(hashlib.sha256(sliced).hexdigest(), parent['slice_pcm_sha256'])
            video.write_subtitles(timeline, self.root)
            subtitles = (self.root / 'captions.srt').read_text('utf-8')
            self.assertEqual(14, subtitles.count(' --> '))
            self.assertEqual(1, subtitles.count(video.CTA_SHOW[0]))
            self.assertEqual(video.CTA_COMMENT_SAY, timeline['segments'][-1]['spoken_text'])
            self.assertNotIn('좋아요', subtitles)
            self.assertTrue(timeline['segments'][-1]['caption_in_overlay'])

    def test_exchange_header_and_static_photo_transitions_keep_action_identity(self):
        cfg = detail_fixture()
        ep = video.Episode.__new__(video.Episode)
        ep.cfg = cfg
        cues = core.speech_segments(cfg)
        for who, color, fallback in [('a', video.COL_A, '첫 공방'), ('b', video.COL_B, '두 번째 공방')]:
            cue = next(item for item in cues if item['id'] == who + '.edge.0')
            self.assertEqual((cfg[who]['edge']['title'], '', color), ep.header_content(cue))
            del cfg[who]['edge']['title']
            self.assertEqual((fallback, '', color), ep.header_content(cue))
        first = dict(cues[7], start_frame=0, end_frame=100)
        second = dict(cues[8], start_frame=100, end_frame=200)
        third = dict(cues[9], start_frame=200, end_frame=300)
        settings = {'image_transition_ms': 200}
        self.assertEqual(0, video.photo_transition_frames(first, second, settings))
        self.assertEqual(6, video.photo_transition_frames(second, third, settings))
        cfg['production_profile'] = core.SHOWDOWN_PROFILE
        self.assertEqual(cfg['a']['name'] + '의 조건', ep.header_content(first)[0])


if __name__ == '__main__':
    unittest.main()
