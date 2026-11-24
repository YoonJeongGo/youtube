"""Synthetic timing/guidance fixtures only: no media or production state writes."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import check_episode
import plan_matchups
from episode_core import atomic_json, script_hash, speech_segments
from longform_reporting import longform_timing_report, timing_report_from_episode
from tests.test_longform_profile import longform_fixture


def fixture_timeline(cfg):
    boundaries = [0, 300, 900, 1500, 2100, 2400]
    return {'script_hash': script_hash(cfg), 'fps': 30, 'total_frames': 2400,
            'segments': [dict(id=cue['id'], start_frame=boundaries[index], end_frame=boundaries[index + 1])
                         for index, cue in enumerate(speech_segments(cfg))]}


class LongformReportingTests(unittest.TestCase):
    def test_inclusive_ranges_use_frames_and_overlaps_count_once(self):
        cfg = longform_fixture()
        report = longform_timing_report(cfg, fixture_timeline(cfg), ['cue_a:cue_c', 'cue_b:cue_d'])
        self.assertEqual(80, report['timeline_total_seconds'])
        self.assertEqual([50, 60], [row['duration_seconds'] for row in report['combat_ranges']])
        self.assertEqual(70, report['combat_total_seconds'])
        self.assertIsNone(report['measured_video_seconds'])
        self.assertEqual('REPORT_ONLY_NOT_QUALITY_REVIEW', report['status'])

    def test_missing_timeline_is_not_zero_or_a_measurement(self):
        cfg = longform_fixture()
        cfg['planning'] = {'candidate': {'planned_duration_seconds': 650}}
        report = longform_timing_report(cfg, None, ['cue_b:cue_d'])
        self.assertEqual(650, report['planned_total_seconds'])
        self.assertIsNone(report['timeline_total_seconds'])
        self.assertIsNone(report['combat_total_seconds'])
        self.assertEqual('TIMELINE_NOT_GENERATED', report['combat_status'])
        report = longform_timing_report(cfg, fixture_timeline(cfg))
        self.assertEqual('NOT_CLASSIFIED', report['combat_status'])
        self.assertIsNone(report['combat_total_seconds'])

    def test_180_seconds_is_not_a_threshold(self):
        cfg = longform_fixture()
        statuses = []
        for multiplier in (1, 3, 4):
            timeline = fixture_timeline(cfg)
            timeline['total_frames'] *= multiplier
            for row in timeline['segments']:
                row['start_frame'] *= multiplier
                row['end_frame'] *= multiplier
            report = longform_timing_report(cfg, timeline, ['cue_b:cue_d'])
            statuses.append(report['combat_status'])
            self.assertNotIn('quality_pass', report)
            self.assertEqual([], report['warnings'])
        self.assertEqual(1, len(set(statuses)))

    def test_stale_or_malformed_timing_cannot_be_current_measurement(self):
        cfg = longform_fixture()
        mutations = [lambda t: t.update(script_hash='old'),
                     lambda t: t['segments'][1].update(id='unknown'),
                     lambda t: t['segments'][1].update(start_frame=301),
                     lambda t: t.update(total_frames=2401),
                     lambda t: t.update(fps=float('nan'))]
        for mutate in mutations:
            timeline = fixture_timeline(cfg)
            mutate(timeline)
            with self.assertRaises(ValueError):
                longform_timing_report(cfg, timeline, ['cue_b:cue_d'])
        for value in ('missing:cue_d', 'cue_d:cue_a', 'cue_a', 'cue_a:cue_b:cue_d'):
            with self.assertRaises(ValueError):
                longform_timing_report(cfg, None, [value])

    def test_video_probe_and_timeline_are_distinct_and_inputs_are_unchanged(self):
        cfg = longform_fixture()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            atomic_json(base / 'episode.json', cfg)
            atomic_json(base / 'timeline.json', fixture_timeline(cfg))
            (base / 'out').mkdir()
            (base / 'out/video.mp4').write_bytes(b'SYNTHETIC FIXTURE NOT VIDEO')
            before = {path: path.read_bytes() for path in base.rglob('*') if path.is_file()}
            result = SimpleNamespace(stdout=json.dumps({'format': {'duration': '81'}}))
            with patch('longform_reporting.subprocess.run', return_value=result) as probe:
                report = timing_report_from_episode(base / 'episode.json', ['cue_b:cue_d'])
            self.assertEqual('ffprobe', probe.call_args.args[0][0])
            self.assertEqual(80, report['timeline_total_seconds'])
            self.assertEqual(81, report['measured_video_seconds'])
            self.assertEqual(60, report['combat_total_seconds'])
            self.assertTrue(report['warnings'])
            self.assertEqual('MEASURED_BY_FFPROBE', report['video_source']['status'])
            self.assertEqual(before, {path: path.read_bytes() for path in base.rglob('*') if path.is_file()})
            with patch('longform_reporting.subprocess.run', side_effect=OSError('fixture unavailable')):
                failed = timing_report_from_episode(base / 'episode.json')
            self.assertIsNone(failed['measured_video_seconds'])
            self.assertEqual('PROBE_FAILED', failed['video_source']['status'])

    def test_guidance_command_reads_actual_docs_without_reserving(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'docs').mkdir()
            for name in ('longform_planning_prompt', 'longform_script_review'):
                (base / ('docs/' + name + '.md')).write_text('Current ' + name, encoding='utf-8')
            output = io.StringIO()
            with patch('sys.argv', ['plan_matchups.py', '--root', temp, 'longform-guidance']), redirect_stdout(output):
                plan_matchups.main()
            report = json.loads(output.getvalue())
            self.assertEqual('Current longform_planning_prompt', report['documents']['planning_prompt']['text'])
            self.assertFalse((base / 'state').exists())

    def test_cli_timing_mode_never_prints_quality_pass(self):
        cfg = longform_fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'episode.json'
            atomic_json(path, cfg)
            output = io.StringIO()
            with patch('sys.argv', ['check_episode.py', str(path), '--longform-timing-report',
                                   '--combat-range', 'cue_b:cue_d']), redirect_stdout(output):
                check_episode.main()
            report = json.loads(output.getvalue())
            self.assertEqual('TIMELINE_NOT_GENERATED', report['combat_status'])
            self.assertNotIn('통과', output.getvalue())

    def test_guidance_message_does_not_crash_existing_profile_error_reporting(self):
        cfg = longform_fixture()
        cfg['production_profile'] = 'invalid_fixture_profile'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'episode.json'
            atomic_json(path, cfg)
            output = io.StringIO()
            with patch('sys.argv', ['check_episode.py', str(path), '--text-only']), redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    check_episode.main()
            self.assertEqual(1, raised.exception.code)
            self.assertIn('고칠 곳', output.getvalue())


if __name__ == '__main__':
    unittest.main()
