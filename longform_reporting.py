"""Read-only longform guidance and timing reports; never a semantic QA PASS."""
import json
import math
import subprocess
from pathlib import Path

from episode_core import is_longform, load_json, script_hash, sha256_file, speech_segments


GUIDANCE_FILES = {
    'planning_prompt': 'docs/longform_planning_prompt.md',
    'script_review': 'docs/longform_script_review.md',
}


def longform_guidance(root, include_text=False):
    documents = {}
    for key, name in GUIDANCE_FILES.items():
        path = Path(root).resolve() / name
        item = {'path': str(path), 'available': path.is_file()}
        if include_text:
            item['text'] = path.read_text(encoding='utf-8-sig')
        documents[key] = item
    return {
        'documents': documents,
        'instruction': 'Read the current planning prompt and script review before drafting or reviewing. '
                       'Plan the full 9–15 minute story and about 3 minutes of consequential combat together. '
                       'No fixed combat ratio, action count, image count, or exact 180-second gate. '
                       'Schema success does not establish story/source/visual/listening quality.',
    }


def _seconds(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return value
    return None


def longform_timing_report(cfg, timeline=None, combat_ranges=(), measured_video_seconds=None):
    """Report explicitly selected inclusive cue ranges using existing timeline frames.

    The reviewer chooses actual combat ranges, excluding repeated hooks and long
    flashbacks/commentary. No semantic classification or approval is inferred.
    Missing measurements stay null. Overlapping selections are counted once.
    """
    if not is_longform(cfg):
        raise ValueError('Timing report requires a longform episode')
    estimated = cfg.get('planned_duration_seconds')
    if estimated is None:
        estimated = cfg.get('planning', {}).get('candidate', {}).get('planned_duration_seconds')
    report = {
        'status': 'REPORT_ONLY_NOT_QUALITY_REVIEW',
        'planned_total_seconds': _seconds(estimated),
        'planned_scene_seconds': [
            {'scene_id': row['id'], 'estimated_seconds': _seconds(row.get('planned_duration_seconds'))}
            for row in cfg.get('scenes', [])
        ],
        'timeline_total_seconds': None,
        'measured_video_seconds': _seconds(measured_video_seconds),
        'combat_target': {'seconds': 180, 'kind': 'approximate_guideline_not_a_pass_fail_threshold'},
        'combat_status': 'NOT_CLASSIFIED',
        'combat_ranges': [],
        'combat_total_seconds': None,
        'warnings': [],
        'review_required': 'Select consequential combat only; exclude repeated opening action, long '
                           'source flashbacks and repetitive explanation. Timing does not prove this '
                           'classification, final video/timeline identity, or visual/listening approval.',
    }
    if measured_video_seconds is not None and report['measured_video_seconds'] is None:
        raise ValueError('Measured video duration must be finite and nonnegative')
    cue_ids = [row['id'] for row in speech_segments(cfg)]
    parsed = []
    for value in combat_ranges:
        parts = value.split(':') if isinstance(value, str) else []
        if len(parts) != 2 or any(part not in cue_ids for part in parts):
            raise ValueError('Combat range must be existing start_cue_id:end_cue_id')
        first, last = (cue_ids.index(part) for part in parts)
        if first > last:
            raise ValueError('Combat cue range is reversed')
        parsed.append((parts, first, last))
    if timeline is None:
        report['combat_status'] = 'TIMELINE_NOT_GENERATED' if parsed else 'NOT_CLASSIFIED'
        report['requested_combat_ranges'] = list(combat_ranges)
        return report
    if timeline.get('script_hash') != script_hash(cfg):
        raise ValueError('Timeline script_hash is stale; do not report it as current measurements')
    segments = timeline.get('segments', [])
    if [row.get('id') for row in segments] != cue_ids:
        raise ValueError('Timeline must cover every current cue once in script order')
    fps = _seconds(timeline.get('fps'))
    if not fps:
        raise ValueError('Timeline fps must be positive and finite')
    previous = 0
    for row in segments:
        start, end = row.get('start_frame'), row.get('end_frame')
        if type(start) is not int or type(end) is not int or start != previous or end <= start:
            raise ValueError('Timeline requires contiguous positive frame ranges')
        previous = end
    if type(timeline.get('total_frames')) is not int or timeline['total_frames'] != previous:
        raise ValueError('Timeline total_frames disagrees with cue coverage')
    report['timeline_total_seconds'] = previous / fps
    measured = report['measured_video_seconds']
    if measured is not None and abs(measured - previous / fps) > 1 / fps:
        report['warnings'].append('Measured MP4 duration differs from timeline by more than one frame; '
                                  'verify current final video/timeline identity before using interval timings.')
    intervals = []
    for (start_id, end_id), first, last in parsed:
        start, end = segments[first]['start_frame'], segments[last]['end_frame']
        intervals.append((start, end))
        report['combat_ranges'].append({'from_cue': start_id, 'to_cue': end_id,
            'start_frame': start, 'end_frame': end, 'start_seconds': start / fps,
            'end_seconds': end / fps, 'duration_seconds': (end - start) / fps})
    if intervals:
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        report['combat_status'] = 'TIMELINE_MEASURED_SCOPE_REQUIRES_REVIEW'
        report['combat_total_seconds'] = sum(end - start for start, end in merged) / fps
        report['overlap_counted_once'] = True
    return report


def timing_report_from_episode(episode_path, combat_ranges=()):
    """Inspect existing files only; ffprobe is read-only and never renders media."""
    episode_path = Path(episode_path).resolve()
    base = episode_path.parent
    cfg = load_json(episode_path)
    timeline_path = base / 'timeline.json'
    timeline = load_json(timeline_path) if timeline_path.is_file() else None
    video_path = base / 'out' / 'video.mp4'
    measured, probe_error = None, None
    video_source = {'path': str(video_path), 'status': 'NOT_GENERATED'}
    if video_path.is_file():
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'json', str(video_path)], capture_output=True, text=True,
                encoding='utf-8', errors='replace', check=True)
            measured = float(json.loads(result.stdout)['format']['duration'])
            if _seconds(measured) is None:
                raise ValueError('MP4 duration is not finite and nonnegative')
            video_source.update(status='MEASURED_BY_FFPROBE', sha256=sha256_file(video_path))
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            measured = None
            probe_error = 'Final MP4 duration unavailable: ' + str(exc)
            video_source['status'] = 'PROBE_FAILED'
    report = longform_timing_report(cfg, timeline, combat_ranges, measured)
    report['episode_script_hash'] = script_hash(cfg)
    report['video_source'] = video_source
    report['timeline_source'] = {'path': str(timeline_path), 'status': 'NOT_GENERATED'}
    if timeline is not None:
        report['timeline_source'].update(status='CURRENT_SCRIPT_CUE_FRAMES', sha256=sha256_file(timeline_path))
    if probe_error:
        report['warnings'].append(probe_error)
    return report
