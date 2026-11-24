"""Safe pause-edit and retained-source reconstruction contracts; synthetic PCM only."""
from array import array
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import episode_core as core
import make_voice as voice
import voice_engine_v2
import voice_pacing as pacing
from test_longform_profile import longform_fixture


POLICY = dict(schema_version=1, enabled=True, sentence_early_seconds=.3,
              sentence_late_seconds=.4, paragraph_seconds=.7, chapter_seconds=1.2,
              early_scene_count=7, quiet_rms_dbfs=-45, max_removed_abs_sample=18,
              quiet_edge_guard_seconds=.05)


class PacingTests(unittest.TestCase):
    def test_alias_preserves_engine_patch_targets(self):
        self.assertIs(voice, voice_engine_v2)
        with patch.object(voice, 'engine_info', return_value={'name': 'patched'}):
            self.assertEqual({'name': 'patched'}, voice_engine_v2.engine_info())

    def test_only_verified_silence_is_replaced_and_word_edges_are_guarded(self):
        samples = array('h', [800] * 4800 + [0] * 62400 + [-900] * 4800)
        raw = samples.tobytes()
        boundary = dict(kind='sentence', early=True, left_end_sample=9600,
                        right_start_sample=62400, before='left', after='right')
        edits, obs = pacing.build_edits(raw, [boundary], POLICY)
        self.assertEqual(1, len(edits))
        self.assertAlmostEqual(.3, obs[0]['actual_quiet_seconds'])
        edited = pacing.pcm_array(pacing.apply_edits(raw, edits))
        self.assertEqual([x for x in samples if x], [x for x in edited if x])
        self.assertGreaterEqual(edits[0]['source_start_sample'], 4800 + 2400)
        self.assertLessEqual(edits[0]['source_end_sample'], 67200 - 2400)
        mapping = pacing.SampleMap(edits)
        self.assertGreater(mapping.at(62400, 'start'), mapping.at(9600, 'end'))
        self.assertEqual(800, edited[4799])
        self.assertEqual(-900, edited[-4800])

    def test_weak_noise_or_no_quiet_core_is_retained(self):
        raw = (array('h', [1000] * 4800 + [30] * 62400 + [1000] * 4800)).tobytes()
        edits, obs = pacing.build_edits(raw, [dict(kind='sentence', early=True,
                                      left_end_sample=9600, right_start_sample=62400)], POLICY)
        self.assertEqual([], edits)
        self.assertEqual('NO_SAFE_CORE_RETAINED', obs[0]['status'])
        self.assertEqual(raw, pacing.apply_edits(raw, edits))

    def test_tampered_or_audible_cut_is_rejected(self):
        raw = (array('h', [800] * 4800 + [0] * 62400 + [900] * 4800)).tobytes()
        edits, _ = pacing.build_edits(raw, [dict(kind='sentence', early=True,
                                    left_end_sample=9600, right_start_sample=62400)], POLICY)
        changed = bytearray(raw)
        a = edits[0]['source_start_sample']
        changed[a * 2:a * 2 + 2] = (1000).to_bytes(2, 'little', signed=True)
        edits[0]['removed_pcm_sha256'] = hashlib.sha256(changed[a * 2:edits[0]['source_end_sample'] * 2]).hexdigest()
        with self.assertRaisesRegex(ValueError, 'audible'):
            pacing.apply_edits(bytes(changed), edits)

    def test_unsupported_pause_range_rejected(self):
        for key, value in [('sentence_early_seconds', .2), ('sentence_late_seconds', .6),
                           ('paragraph_seconds', 1.0), ('max_removed_abs_sample', 100)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                pacing.checked_policy(dict(POLICY, **{key: value}))

    def test_measured_noise_floor_policy_keeps_higher_amplitude_tail(self):
        samples = array('h', [1000] * 4800 + [70] * 4800 + [40] * 62400 + [-70] * 4800 + [-1000] * 4800)
        raw = samples.tobytes()
        policy = dict(POLICY, max_removed_abs_sample=48)
        edits, _ = pacing.build_edits(raw, [dict(kind='sentence', early=True,
                                     left_end_sample=14400, right_start_sample=67200)], policy)
        self.assertEqual(1, len(edits))
        self.assertEqual(40, edits[0]['removed_max_abs_sample'])
        changed = pacing.pcm_array(pacing.apply_edits(raw, edits))
        self.assertEqual([x for x in samples if abs(x) > 48], [x for x in changed if abs(x) > 48])

    def test_full_pcm_word_timeline_reconstruction_and_policy_cache_independence(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(voice, 'engine_info', return_value={'name': 'fixture'}), \
                patch.object(voice, 'decoder_info', return_value={'name': 'fixture'}), \
                patch.object(voice, 'duration_limits', return_value=(1, 900)):
            base = Path(temp)
            cfg = longform_fixture()
            core.atomic_json(base / pacing.POLICY_FILE, POLICY)
            plan = voice.build_plan(cfg, base)
            decoded = {}
            for unit in voice.generation_units(plan):
                data, words = array('h', [0] * 10000), []
                for token in unit['spoken_text'].split():
                    start = len(data)
                    data.extend([1000] * 5760)
                    gap = 67200 if token.endswith('.') else 4800
                    data.extend([0] * gap)
                    words.append(dict(type='WordBoundary', text=token,
                                      offset_ticks=round((start - 1440) / 48000 * 10_000_000),
                                      duration_ticks=round(8640 / 48000 * 10_000_000)))
                raw = data.tobytes()
                name = voice.audio_name(unit['id'])
                (base / name).write_bytes(b'fixture-not-real-mp3')
                raw_name = Path(name).stem + '_decoded.wav'
                voice.write_wav(base / raw_name, raw)
                meta = dict(boundaries=words, audio_file=name, audio_sha256=core.sha256_file(base / name),
                            input_hash=voice.sentence_hash(plan, unit), engine=plan['engine'], settings=unit,
                            sample_count=len(data), decoded_pcm_sha256=hashlib.sha256(raw).hexdigest(),
                            raw_pcm_file=raw_name, raw_pcm_sha256=core.sha256_file(base / raw_name))
                core.atomic_json((base / name).with_suffix('.json'), meta)
                decoded[unit['id']] = raw, meta
            pcm, timeline = voice.assemble_grouped_pcm(cfg, plan, decoded)
            self.assertTrue(timeline['pacing_applied'])
            self.assertGreater(len(timeline['pacing_manifest']['edits']), 0)
            self.assertEqual(1, sum(e['kind'] == 'chapter' for e in timeline['pacing_manifest']['observations']))
            for segment in timeline['segments']:
                self.assertLessEqual(segment['start'], segment['boundaries'][0]['start'])
                self.assertLessEqual(segment['boundaries'][-1]['end'], segment['end'])
                segment['audio_metadata_sha256'] = core.sha256_file((base / segment['audio_file']).with_suffix('.json'))
            voice.write_wav(base / 'voice.wav', pcm)
            timeline['pcm_sha256'] = core.sha256_file(base / 'voice.wav')
            core.atomic_json(base / 'voice_plan.json', plan)
            core.atomic_json(base / 'timeline.json', timeline)
            core.atomic_json(base / timeline['pacing_manifest_file'], timeline['pacing_manifest'])
            core.atomic_json(base / 'voice_build.json', dict(status='COMPLETE', input_hash=voice.input_hash(plan),
                             timeline_sha256=core.sha256_file(base / 'timeline.json')))
            self.assertEqual([], voice.validate_voice(cfg, base))
            altered = copy.deepcopy(timeline)
            altered['pacing_manifest']['edits'][0]['replacement_samples'] += 1600
            core.atomic_json(base / 'timeline.json', altered)
            self.assertTrue(voice.validate_voice(cfg, base))
            # Pacing is a postprocessor, so changing it must invalidate the master
            # but must not consume TTS retry budgets for identical source requests.
            core.atomic_json(base / pacing.POLICY_FILE, dict(POLICY, sentence_early_seconds=.4))
            updated = voice.build_plan(cfg, base)
            self.assertNotEqual(voice.input_hash(plan), voice.input_hash(updated))
            self.assertEqual([voice.sentence_hash(plan, u) for u in voice.generation_units(plan)],
                             [voice.sentence_hash(updated, u) for u in voice.generation_units(updated)])


if __name__ == '__main__':
    unittest.main()
