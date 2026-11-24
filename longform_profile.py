"""Longform contracts and the pre-production approval gate; never runs media tools.

Semantic story/source/image approval remains an actual review, not a schema PASS.
"""
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

STORY_PLAN_FIELDS = ('central_event', 'goals', 'motivation', 'information', 'setting',
                     'abilities', 'progression', 'outcome', 'visualization')
SCENE_FIELDS = ('objective', 'obstacle', 'action', 'result', 'next_reason', 'location')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
STORY_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _present(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value) and any(_present(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and any(_present(v) for v in value)
    return False


def _asset(value):
    if not _text(value):
        return False
    unix, windows = PurePosixPath(value.replace('\\', '/')), PureWindowsPath(value)
    return (not unix.is_absolute() and not windows.is_absolute() and not windows.drive
            and '..' not in unix.parts and unix.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'))


def _records(cfg, key, errors):
    value = cfg.get(key)
    if not isinstance(value, list) or not value or any(not isinstance(row, dict) for row in value):
        errors.append(f'longform {key}: nonempty object list required')
        return []
    ids = [row.get('id') for row in value]
    if any(not isinstance(key_id, str) or not _ID.fullmatch(key_id) for key_id in ids):
        errors.append(f'longform {key}: stable safe IDs required')
    elif len(set(ids)) != len(ids):
        errors.append(f'longform {key}: duplicate IDs')
    return value


def longform_structure_errors(cfg):
    """Validate reusable scene/cut/cue/group mappings, without semantic claims."""
    from episode_core import is_longform
    errors = []
    try:
        if not is_longform(cfg):
            return ['longform profile required']
    except (ValueError, TypeError, AttributeError) as exc:
        return [str(exc)]
    if cfg.get('schema_version') != 2:
        errors.append('longform schema_version must be 2')
    if cfg.get('cta_presentation') != 'comment_spoken':
        errors.append('longform cta_presentation must be comment_spoken')
    if 'cta' in cfg:
        errors.append('CTA is shared content and cannot be supplied by an episode')
    if not _asset(cfg.get('thumb', 'thumb.png')):
        errors.append('longform thumb: safe relative image required')
    for who in ('a', 'b'):
        info = cfg.get(who)
        if not isinstance(info, dict) or not _text(info.get('name')):
            errors.append(f'longform {who}: named central character required')
        elif not _asset(info.get('image', who + '.png')):
            errors.append(f'longform {who}.image: safe relative image required')
    scenes = _records(cfg, 'scenes', errors)
    cuts = _records(cfg, 'cuts', errors)
    beats = _records(cfg, 'story_beats', errors)
    groups = _records(cfg, 'narration_groups', errors)
    # Returning here avoids misleading cascades and unhashable malformed IDs.
    if errors:
        return errors
    scene_ids = {row['id'] for row in scenes}
    cut_map = {row['id']: row for row in cuts}
    cue_ids = [row['id'] for row in beats]
    used_cuts, used_scenes = set(), set()
    for cut in cuts:
        if not isinstance(cut.get('scene_id'), str) or cut['scene_id'] not in scene_ids:
            errors.append(f"longform cut {cut['id']}: unknown scene_id")
        if not _asset(cut.get('image')) or not _text(cut.get('moment')):
            errors.append(f"longform cut {cut['id']}: safe image and illustrated moment required")
        participants = cut.get('participants', ['a', 'b'])
        if (not isinstance(participants, list) or any(who not in ('a', 'b') for who in participants)
                or len(set(participants)) != len(participants)):
            errors.append(f"longform cut {cut['id']}: participants must be distinct central-character IDs")
        if cut.get('transition', 'crossfade') not in ('crossfade', 'hardcut'):
            errors.append(f"longform cut {cut['id']}: transition must be crossfade or hardcut")
    for beat in beats:
        key = beat['id']
        if key == 'cta':
            errors.append('longform story_beats: cta is appended once by the shared engine')
        if any(not _text(beat.get(field)) for field in ('display_text', 'spoken_text')):
            errors.append(f'longform cue {key}: display_text and spoken_text required')
        if beat.get('kind', 'story') not in ('hook', 'intro', 'story', 'clash', 'edge', 'reason', 'question'):
            errors.append(f'longform cue {key}: unsupported kind')
        if beat.get('condition') not in (None, 'a', 'b') or not isinstance(beat.get('header', ''), str):
            errors.append(f'longform cue {key}: invalid condition or header')
        cut_id = beat.get('cut_id')
        cut = cut_map.get(cut_id) if isinstance(cut_id, str) else None
        if cut is None:
            errors.append(f'longform cue {key}: unknown cut_id')
            continue
        if beat.get('scene_id') != cut.get('scene_id'):
            errors.append(f'longform cue {key}: scene_id disagrees with cut')
        if 'image' in beat and beat['image'] != cut.get('image'):
            errors.append(f'longform cue {key}: image disagrees with cut')
        used_cuts.add(cut_id)
        if isinstance(cut.get('scene_id'), str):
            used_scenes.add(cut['scene_id'])
    if used_cuts != set(cut_map):
        errors.append('longform cuts: every declared cut must map to actual cues')
    if used_scenes != scene_ids:
        errors.append('longform scenes: every declared scene must map to actual cuts')
    # A cut/scene is one continuing moment/sequence; returning later gets a new ID.
    for field in ('scene_id', 'cut_id'):
        runs = []
        for beat in beats:
            value = beat.get(field)
            if not runs or runs[-1] != value:
                runs.append(value)
        if all(isinstance(value, str) for value in runs) and len(runs) != len(set(runs)):
            errors.append('longform ' + field + ': noncontiguous reuse requires a new stable ID')
    flattened = []
    for group in groups:
        segment_ids = group.get('segment_ids')
        if (not isinstance(segment_ids, list) or not segment_ids
                or any(not isinstance(value, str) for value in segment_ids)):
            errors.append('longform narration groups require nonempty segment_ids')
        else:
            flattened.extend(segment_ids)
    if flattened != cue_ids + ['cta']:
        errors.append('longform narration groups must cover all cues and shared cta once in order')
    if len(groups) < 2:
        errors.append('longform narration must use multiple paragraph groups, not one whole-video request')
    return errors


def require_longform_structure(cfg):
    errors = longform_structure_errors(cfg)
    if errors:
        raise ValueError('; '.join(errors))


def longform_text_errors(cfg):
    errors = longform_structure_errors(cfg)
    if errors:
        return errors
    if not isinstance(cfg.get('story_id'), str) or not STORY_ID_PATTERN.fullmatch(cfg['story_id']):
        errors.append('longform story_id: stable safe narrative identity required')
    plan = cfg.get('story_plan', {})
    for key in STORY_PLAN_FIELDS:
        if not isinstance(plan, dict) or not _present(plan.get(key)):
            errors.append('longform story_plan.' + key + ': planning evidence required')
    for scene in cfg['scenes']:
        for key in SCENE_FIELDS:
            if not _present(scene.get(key)):
                errors.append(f"longform scene {scene['id']}.{key}: causal scene map required")
    resolution = cfg.get('resolution')
    if not isinstance(resolution, dict) or not _text(resolution.get('outcome')):
        errors.append('longform resolution: event outcome required')
    elif resolution.get('objective_resolved') is not True:
        exception = resolution.get('open_ending_request', {})
        if (resolution.get('objective_resolved') is not False or not isinstance(exception, dict)
                or not _text(exception.get('user_statement'))
                or not _text(resolution.get('resolved_part'))
                or not _text(resolution.get('remaining_question'))):
            errors.append('longform ending must resolve the event; an explicit series/open-ending request must state resolved and remaining parts')
    return errors


def longform_production_errors(cfg, base):
    """Check the current script/premises approval before any TTS/render mutation.

Only an agent recording a real user approval may create this record. Presence of
this schema is not evidence that the user approved; preserve their actual text.
    """
    from episode_core import load_json, script_hash
    errors = longform_text_errors(cfg)
    if errors:
        return errors
    path = Path(base) / 'production_authorization.json'
    try:
        record = load_json(path)
        if (not isinstance(record, dict) or record.get('approved') is not True
                or record.get('approval_scope') != 'full_script_and_premises'
                or not _text(record.get('user_statement'))
                or record.get('script_hash') != script_hash(cfg)):
            errors.append('LONGFORM_SCRIPT_APPROVAL_REQUIRED: current full script and premises are not approved')
    except (OSError, ValueError, TypeError):
        errors.append('LONGFORM_SCRIPT_APPROVAL_REQUIRED: no valid current approval record')
    return errors