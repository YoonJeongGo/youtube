"""Opt-in, waveform-guarded longform pause editing with exact reconstruction.

The immutable voice_engine_v2 module continues to own TTS and source cache hashes.
A separate policy and this processor's hash govern only deterministic PCM editing.
Every removed source sample is checked against the explicit conservative peak
threshold (no greater than 48/32768, -56.68 dBFS), inside a full quiet interval.
"""
from array import array
from bisect import bisect_right
import hashlib
import json
import math
from pathlib import Path
import re
import wave

SR = 48000
WIDTH = 2
SPF = 1600
FPS = 30
POLICY_FILE = 'voice_pacing_plan.json'


def checked_policy(value):
    p = dict(value)
    if p.get('schema_version') != 1 or p.get('enabled') is not True:
        raise ValueError('Pacing requires an explicit enabled version-1 policy.')
    for key in ('sentence_early_seconds', 'sentence_late_seconds'):
        v = p.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not .3 <= v <= .5:
            raise ValueError(key + ' must stay inside the authorized .3-.5 second range.')
    if p.get('paragraph_seconds') != .7 or p.get('chapter_seconds') != 1.2:
        raise ValueError('Paragraph/chapter targets must be .7/1.2 seconds.')
    if p.get('early_scene_count') != 7:
        raise ValueError('This policy switches sentence pace after chapter 7.')
    if p.get('quiet_rms_dbfs') != -45 or p.get('max_removed_abs_sample') not in (18, 32, 48):
        raise ValueError('Acoustic thresholds may not silently change.')
    if p.get('quiet_edge_guard_seconds') != .05:
        raise ValueError('Keep the approved 50ms guard at both detected quiet edges.')
    return p


def pcm_array(raw):
    data = array('h')
    data.frombytes(raw)
    return data


def quiet_runs(samples, threshold=32768 * 10 ** (-45 / 20), step=480):
    """Full contiguous 10ms RMS quiet intervals, not clipped to metadata gaps."""
    runs, begin = [], None
    threshold_square = threshold * threshold
    for offset in range(0, len(samples), step):
        frame = samples[offset:offset + step]
        quiet = sum(x * x for x in frame) <= threshold_square * len(frame)
        if quiet and begin is None:
            begin = offset
        if not quiet and begin is not None:
            if offset - begin >= 4 * step:
                runs.append((begin, offset))
            begin = None
    if begin is not None and len(samples) - begin >= 4 * step:
        runs.append((begin, len(samples)))
    return runs


def longest_safe_core(samples, start, end, limit=18):
    """Return a contiguous, strictly very-low-amplitude source interval."""
    best, begin = (start, start), start
    for i in range(start, end):
        if abs(samples[i]) > limit:
            if i - begin > best[1] - best[0]:
                best = begin, i
            begin = i + 1
    if end - begin > best[1] - best[0]:
        best = begin, end
    return best


def build_edits(raw, boundaries, policy):
    samples = pcm_array(raw)
    runs = quiet_runs(samples)
    edits, observations, previous_end = [], [], 0
    guard = round(policy['quiet_edge_guard_seconds'] * SR)
    for boundary in boundaries:
        left, right = boundary['left_end_sample'], boundary['right_start_sample']
        candidates = [(max(0, min(b, right) - max(a, left)), b - a, a, b)
                      for a, b in runs if a < right and b > left]
        candidates = [item for item in candidates if item[0] > 0]
        record = dict(boundary)
        target = (policy['sentence_early_seconds'] if boundary['early'] else policy['sentence_late_seconds']) if boundary['kind'] == 'sentence' else policy[boundary['kind'] + '_seconds']
        record['target_seconds'] = target
        if not candidates:
            record.update(status='NO_SAFE_QUIET_RUN_RETAINED', actual_quiet_seconds=0)
            observations.append(record)
            continue
        _, _, quiet_begin, quiet_end = max(candidates)
        record.update(source_quiet_start_sample=quiet_begin, source_quiet_end_sample=quiet_end,
                      source_quiet_seconds=(quiet_end - quiet_begin) / SR)
        a, b = longest_safe_core(samples, quiet_begin + guard, quiet_end - guard,
                                policy['max_removed_abs_sample'])
        if b - a < 480 or a < previous_end:
            record.update(status='NO_SAFE_CORE_RETAINED', actual_quiet_seconds=(quiet_end - quiet_begin) / SR)
            observations.append(record)
            continue
        outside = quiet_end - quiet_begin - (b - a)
        # Leave at least one frame of replacement silence. Metadata inside this
        # discarded, acoustically empty core maps to its respective retained edge.
        replacement = max(SPF, round(target * SR) - outside)
        actual = (outside + replacement) / SR
        edit = {**record, 'source_start_sample': a, 'source_end_sample': b,
                'permitted_removed_abs_sample': policy['max_removed_abs_sample'],
                'replacement_samples': replacement, 'removed_samples': b - a,
                'delta_samples': replacement - (b - a),
                'removed_pcm_sha256': hashlib.sha256(raw[a * WIDTH:b * WIDTH]).hexdigest(),
                'removed_max_abs_sample': max(abs(x) for x in samples[a:b]),
                'actual_quiet_seconds': actual,
                'status': 'APPLIED' if abs(actual - target) <= 1 / SR else 'SAFE_CORE_LIMIT_TARGET_EXTENDED'}
        edits.append(edit)
        observations.append(edit)
        previous_end = b
    return edits, observations


def apply_edits(raw, edits):
    parts, cursor = [], 0
    samples = pcm_array(raw)
    for edit in edits:
        a, b, length = (edit[k] for k in ('source_start_sample', 'source_end_sample', 'replacement_samples'))
        if not 0 <= cursor <= a < b <= len(samples) or length < 0:
            raise ValueError('Overlapping or invalid pacing edit.')
        cut = raw[a * WIDTH:b * WIDTH]
        limit = edit.get('permitted_removed_abs_sample', 18)
        if limit not in (18, 32, 48) or hashlib.sha256(cut).hexdigest() != edit['removed_pcm_sha256'] or max(abs(x) for x in samples[a:b]) > limit:
            raise ValueError('Pacing edit would remove an unverified audible sample.')
        parts.extend((raw[cursor * WIDTH:a * WIDTH], b'\0' * length * WIDTH))
        cursor = b
    parts.append(raw[cursor * WIDTH:])
    return b''.join(parts)


class SampleMap:
    def __init__(self, edits):
        self.edits = edits
        self.ends = [e['source_end_sample'] for e in edits]
        self.shifts = [0]
        for e in edits:
            self.shifts.append(self.shifts[-1] + e['delta_samples'])

    def at(self, sample, role='point'):
        i = bisect_right(self.ends, sample)
        shift = self.shifts[i]
        if i < len(self.edits):
            e = self.edits[i]
            a, b, n = e['source_start_sample'], e['source_end_sample'], e['replacement_samples']
            if a < sample < b:
                if role == 'end':
                    return a + shift
                if role == 'start':
                    return a + shift + n
                return a + shift + round((sample - a) * n / (b - a))
        return sample + shift


def source_clock(cfg, plan, decoded, engine):
    sources = {s['id']: s for s in engine.speech_segments(cfg)}
    plan_cues = {s['id']: s for s in plan['segments']}
    scene_order = {s['id']: i + 1 for i, s in enumerate(cfg['scenes'])}
    pending, groups, parts, cursor, boundaries = [], [], [], 0, []
    norm = engine.normalized_words
    for unit in engine.generation_units(plan):
        raw, metadata = decoded[unit['id']]
        aligned = engine.align_group_words(unit, metadata, len(raw) // WIDTH)
        pcm, trim = engine.trim_story_padding(raw, metadata['boundaries'])
        before, after = (round(unit[k] * SR) for k in ('pause_before', 'pause_after'))
        content_start, lead = cursor + before, trim['leading_trim_samples']
        content_end = content_start + len(pcm) // WIDTH
        group = {'id': unit['id'], 'segment_ids': unit['segment_ids'],
                 'audio_file': metadata['audio_file'], 'audio_sha256': metadata['audio_sha256'],
                 'input_hash': metadata['input_hash'], 'spoken_text': unit['spoken_text'],
                 'decoded_pcm_sha256': metadata['decoded_pcm_sha256'],
                 'raw_pcm_file': metadata['raw_pcm_file'], 'raw_pcm_sha256': metadata['raw_pcm_sha256'],
                 'raw_sample_count': len(raw) // WIDTH, 'pcm_trim': trim,
                 'source_start_sample': cursor, 'source_end_sample': content_end + after,
                 'source_content_start_sample': content_start, 'source_content_end_sample': content_end,
                 'source_pause_before_samples': before, 'source_pause_after_samples': after,
                 'scene_id': sources[unit['segment_ids'][0]].get('scene_id')}
        flat = []
        for key, words in zip(unit['segment_ids'], aligned):
            bounds = [{**word, 'source_global_start_sample': content_start - lead + word['source_start_sample'],
                       'source_global_end_sample': content_start - lead + word['source_end_sample']}
                      for word in words]
            flat.extend(bounds)
            pending.append({'key': key, 'unit': unit, 'raw': raw, 'metadata': metadata,
                            'group': group, 'bounds': bounds})
        group['words'] = flat
        source_text = ' '.join(plan_cues[k]['source_spoken_text'] for k in unit['segment_ids'])
        if norm(source_text) != norm(unit['spoken_text']):
            raise ValueError('Pause punctuation source differs from actual spoken words: ' + unit['id'])
        ends = {w['normalized_end']: i for i, w in enumerate(flat)}
        for marker in re.finditer(r'[.!?]+', source_text):
            offset = len(norm(source_text[:marker.start()]))
            if offset == len(norm(source_text)):
                continue
            i = ends.get(offset)
            if i is None or i + 1 >= len(flat):
                raise ValueError('A sentence boundary lies inside a WordBoundary: ' + unit['id'])
            left, right = flat[i:i + 2]
            boundaries.append({'kind': 'sentence', 'scene_id': group['scene_id'],
                               'early': scene_order.get(group['scene_id'], 13) <= 7,
                               'group_before': unit['id'], 'group_after': unit['id'],
                               'left_end_sample': left['source_global_end_sample'],
                               'right_start_sample': right['source_global_start_sample'],
                               'before': left['text'], 'after': right['text']})
        groups.append(group)
        parts.extend((b'\0' * before * WIDTH, pcm, b'\0' * after * WIDTH))
        cursor = content_end + after
    for g, h in zip(groups, groups[1:]):
        left, right = g['words'][-1], h['words'][0]
        # The final CTA belongs to the closing chapter; it does not add a chapter pause.
        change = g['scene_id'] != h['scene_id'] and h['scene_id'] is not None
        boundaries.append({'kind': 'chapter' if change else 'paragraph',
                           'scene_id': g['scene_id'], 'next_scene_id': h['scene_id'], 'early': False,
                           'group_before': g['id'], 'group_after': h['id'],
                           'left_end_sample': left['source_global_end_sample'],
                           'right_start_sample': right['source_global_start_sample'],
                           'before': left['text'], 'after': right['text']})
    boundaries.sort(key=lambda e: e['left_end_sample'])
    if [p['key'] for p in pending] != list(sources):
        raise ValueError('Narration source sequence is incomplete.')
    return b''.join(parts), pending, groups, boundaries


def assemble_paced_pcm(cfg, plan, decoded, engine):
    policy = checked_policy(plan['pacing']['policy'])
    source_pcm, pending, source_groups, boundaries = source_clock(cfg, plan, decoded, engine)
    edits, observations = build_edits(source_pcm, boundaries, policy)
    pcm = apply_edits(source_pcm, edits)
    mapping = SampleMap(edits)
    mapped_pending = []
    for item in pending:
        bounds = []
        for word in item['bounds']:
            start = mapping.at(word['source_global_start_sample'], 'start')
            end = mapping.at(word['source_global_end_sample'], 'end')
            if end <= start:
                raise ValueError('Pacing collapsed a spoken word: ' + word['text'])
            bounds.append({**word, 'start': start / SR, 'end': end / SR,
                           'paced_start_sample': start, 'paced_end_sample': end,
                           'timing_transform': 'retained source samples; silence-only replacement maps word-end/start to retained edges'})
        mapped_pending.append({**item, 'bounds': bounds})
    unrounded = len(pcm) // WIDTH
    total_frames = math.ceil(unrounded / SPF)
    edges, measured = [0], []
    for left, right in zip(mapped_pending, mapped_pending[1:]):
        word_end, word_start = left['bounds'][-1]['paced_end_sample'], right['bounds'][0]['paced_start_sample']
        lower, upper = math.ceil(word_end / SPF), math.floor(word_start / SPF)
        midpoint = (word_end + word_start) / 2
        frame = max(lower, min(round(midpoint / SPF), upper))
        if lower > upper or not edges[-1] < frame < total_frames:
            raise engine.VoiceError(f"{right['key']}: paced WordBoundary gap has no safe 30fps cue edge ({word_end}, {word_start}). Merge adjacent caption cues without changing narration.")
        edges.append(frame)
        measured.append({'after_cue': left['key'], 'before_cue': right['key'],
                         'basis': 'silence-edit sample map of measured WordBoundary gap; nearest safe global frame',
                         'previous_word_end_sample': word_end, 'next_word_start_sample': word_start,
                         'global_gap_midpoint_sample': midpoint, 'global_frame': frame,
                         'alignment_error_samples': frame * SPF - midpoint})
    edges.append(total_frames)
    sources = {s['id']: s for s in engine.speech_segments(cfg)}
    entries = {e['id']: e for e in plan['segments']}
    segments = []
    for i, item in enumerate(mapped_pending):
        key, unit, meta, bounds, group = item['key'], item['unit'], item['metadata'], item['bounds'], item['group']
        begin, end = edges[i] * SPF, edges[i + 1] * SPF
        ws, we = bounds[0]['paced_start_sample'], bounds[-1]['paced_end_sample']
        if not begin <= ws < we <= end:
            raise ValueError('Paced word lies outside caption: ' + key)
        speech_begin = max(begin, min(ws, mapping.at(group['source_content_start_sample'])))
        speech_end = min(end, max(we, mapping.at(group['source_content_end_sample'])))
        raw_begin, raw_end = bounds[0]['source_start_sample'], bounds[-1]['source_end_sample']
        segments.append({**sources[key], 'tts_text': entries[key]['spoken_text'],
                         'narration_group_id': unit['id'], 'group_tts_text': unit['spoken_text'],
                         'start_frame': edges[i], 'end_frame': edges[i + 1],
                         'start': edges[i] / FPS, 'end': edges[i + 1] / FPS,
                         'start_sample': begin, 'end_sample': end,
                         'speech_start_sample': speech_begin, 'speech_end_sample': speech_end,
                         'speech_start': speech_begin / SR, 'speech_end': speech_end / SR,
                         'speech_range_basis': 'retained source speech after explicitly verified very-low-level pause editing',
                         'decoded_sample_count': raw_end - raw_begin,
                         'word_speech_start': ws / SR, 'word_speech_end': we / SR,
                         'audio_file': meta['audio_file'], 'audio_sha256': meta['audio_sha256'],
                         'sentence_input_hash': meta['input_hash'], 'boundaries': bounds,
                         'parent_audio': {'id': unit['id'], 'raw_pcm_file': meta['raw_pcm_file'],
                                          'raw_pcm_sha256': meta['raw_pcm_sha256'],
                                          'decoded_pcm_sha256': meta['decoded_pcm_sha256'],
                                          'sample_rate': SR, 'source_start_sample': raw_begin, 'source_end_sample': raw_end,
                                          'slice_pcm_sha256': hashlib.sha256(item['raw'][raw_begin * WIDTH:raw_end * WIDTH]).hexdigest(),
                                          'slice_role': 'unmodified parent word-envelope provenance; not a claim of contiguous final output',
                                          'pacing_edits_applied': True}})
    by_cue = {s['id']: s for s in segments}
    groups = []
    for source in source_groups:
        group = {k: v for k, v in source.items() if k != 'words'}
        for key in ('start_sample', 'end_sample', 'content_start_sample', 'content_end_sample'):
            group[key] = mapping.at(source['source_' + key])
        group.update(pause_before_samples=group['content_start_sample'] - group['start_sample'],
                     pause_after_samples=group['end_sample'] - group['content_end_sample'],
                     alignment_tail_samples=0,
                     internal_inserted_silence_samples=sum(max(0, e['delta_samples']) for e in edits
                         if e['group_before'] == source['id'] and e['group_after'] == source['id']),
                     silence_replacement_samples=sum(e['replacement_samples'] for e in edits
                         if e['group_before'] == source['id']),
                     pacing_edit_count=sum(e['group_before'] == source['id'] for e in edits))
        group['cue_global_frame_edges'] = [by_cue[k]['start_frame'] for k in group['segment_ids']] + [by_cue[group['segment_ids'][-1]]['end_frame']]
        group['start_frame'], group['end_frame'] = group['cue_global_frame_edges'][0], group['cue_global_frame_edges'][-1]
        groups.append(group)
    tail = total_frames * SPF - unrounded
    pcm += b'\0' * tail * WIDTH
    manifest = {'schema_version': 1, 'policy': policy, 'processor_sha256': plan['pacing']['processor_sha256'],
                'source_clock_pcm_sha256': hashlib.sha256(source_pcm).hexdigest(),
                'source_clock_samples': len(source_pcm) // WIDTH,
                'paced_unpadded_pcm_sha256': hashlib.sha256(pcm[:unrounded * WIDTH]).hexdigest(),
                'source_audio_preserved': True, 'speed_change_applied': False,
                'source_words_preserved': True, 'edits': edits, 'observations': observations,
                'removed_total_samples': sum(e['removed_samples'] for e in edits),
                'replacement_total_samples': sum(e['replacement_samples'] for e in edits),
                'maximum_removed_abs_sample': max((e['removed_max_abs_sample'] for e in edits), default=0),
                'unmodified_sample_spans_retained_byte_identically': True,
                'timestamp_contract': 'Original ticks and source samples retained; output times map retained samples and silent replacement edges, never text-length interpolation.'}
    return pcm, {'schema_version': 2, 'fps': FPS, 'audio_mode': 'voice', 'format': 'longform',
                 'script_hash': engine.script_hash(cfg), 'input_hash': engine.input_hash(plan),
                 'total_frames': total_frames, 'duration': total_frames / FPS, 'sample_rate': SR,
                 'total_samples': total_frames * SPF, 'unrounded_total_samples': unrounded,
                 'final_alignment_tail_samples': tail, 'pcm_path': 'voice.wav',
                 'segments': segments, 'narration_groups': groups, 'measured_cue_boundaries': measured,
                 'timing_policy': 'global sample clock; approved waveform-guarded pause edits; final frame tail only',
                 'pacing_applied': True, 'pacing_manifest': manifest,
                 'pacing_manifest_file': 'voice_pacing_manifest.json',
                 'pacing_manifest_sha256': hashlib.sha256((json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')).hexdigest()}


def load_retained_parents(engine, cfg, base, plan):
    decoded = {}
    for unit in engine.generation_units(plan):
        filename = engine.audio_name(unit['id'])
        meta = engine.load_json((base / filename).with_suffix('.json'))
        raw_name = Path(filename).stem + '_decoded.wav'
        if meta.get('raw_pcm_file') != raw_name or meta.get('settings') != unit:
            raise engine.VoiceError(unit['id'] + ': retained parent settings differ.')
        path = base / raw_name
        if engine.sha256_file(path) != meta.get('raw_pcm_sha256'):
            raise engine.VoiceError(unit['id'] + ': retained parent WAV hash differs.')
        with wave.open(str(path), 'rb') as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SR, 1, WIDTH):
                raise engine.VoiceError('Retained parent PCM format differs.')
            raw = wav.readframes(wav.getnframes())
        if (hashlib.sha256(raw).hexdigest() != meta.get('decoded_pcm_sha256') or
                len(raw) // WIDTH != meta.get('sample_count') or
                meta.get('input_hash') != engine.sentence_hash(plan, unit) or meta.get('engine') != plan['engine']):
            raise engine.VoiceError(unit['id'] + ': retained parent generation provenance differs.')
        decoded[unit['id']] = raw, meta
    return decoded


def install(engine):
    """Install opt-in hooks on the actual engine module, preserving patch targets."""
    if getattr(engine, '_pacing_hooks_installed', False):
        return
    legacy_plan = engine.build_plan
    legacy_assemble = engine.assemble_grouped_pcm
    legacy_provenance = engine.validate_group_provenance
    legacy_build = engine._build

    def build_plan(cfg, base, *, silent=False):
        plan = legacy_plan(cfg, base, silent=silent)
        policy_path = Path(base) / POLICY_FILE
        if engine.is_longform(cfg) and policy_path.exists() and not silent:
            policy = checked_policy(engine.load_json(policy_path))
            plan['pacing'] = {'policy': policy, 'policy_file': POLICY_FILE,
                              'policy_file_sha256': engine.sha256_file(policy_path),
                              'processor_sha256': engine.sha256_file(__file__)}
        return plan

    def assemble(cfg, plan, decoded):
        if 'pacing' in plan:
            return assemble_paced_pcm(cfg, plan, decoded, engine)
        return legacy_assemble(cfg, plan, decoded)

    def validate_provenance(cfg, base, plan, timeline, actual_pcm, errors):
        if 'pacing' not in plan:
            return legacy_provenance(cfg, base, plan, timeline, actual_pcm, errors)
        decoded = load_retained_parents(engine, cfg, base, plan)
        pcm, expected = assemble_paced_pcm(cfg, plan, decoded, engine)
        for s in expected['segments']:
            s['audio_metadata_sha256'] = engine.sha256_file((base / s['audio_file']).with_suffix('.json'))
        if pcm != actual_pcm:
            errors.append('Paced master differs from exact retained-parent and safe-edit reconstruction.')
        for key in ('segments', 'narration_groups', 'unrounded_total_samples', 'final_alignment_tail_samples',
                    'measured_cue_boundaries', 'timing_policy', 'pacing_applied', 'pacing_manifest', 'pacing_manifest_file', 'pacing_manifest_sha256'):
            if expected.get(key) != timeline.get(key):
                errors.append('Paced timeline/reconstruction differs: ' + key)
        path = base / expected['pacing_manifest_file']
        if not path.is_file() or engine.sha256_file(path) != expected['pacing_manifest_sha256']:
            errors.append('Pacing manifest file is missing or differs from reconstruction.')

    async def build(cfg, base, *, silent=False, regenerate=()):
        timeline = await legacy_build(cfg, base, silent=silent, regenerate=regenerate)
        if timeline.get('pacing_applied'):
            engine.atomic_json(Path(base) / timeline['pacing_manifest_file'], timeline['pacing_manifest'])
        return timeline

    engine.build_plan = build_plan
    engine.assemble_grouped_pcm = assemble
    engine.validate_group_provenance = validate_provenance
    engine._build = build
    engine._pacing_hooks_installed = True
