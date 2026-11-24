"""Shared data contracts. No generation, review or upload is performed implicitly."""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import uuid
from pathlib import Path

SCHEMA_VERSION = 2
FPS = 30
IMAGE_NAMES = ('thumb.png', 'a.png', 'a_1.png', 'a_2.png', 'a_atk.png',
               'b.png', 'b_1.png', 'b_2.png', 'b_atk.png', 'b_react.png', 'a_react.png')
SETUP_PROFILE = 'setup_story_v3'
SHOWDOWN_PROFILE = 'showdown_story_v4'
DETAIL_PROFILE = 'showdown_detail_v5'
LONGFORM_PROFILE = 'showdown_story_long_v1'
# Only creation code should use this default. Reading an existing episode never
# fills in or replaces its saved production_profile.
DEFAULT_PRODUCTION_PROFILE = DETAIL_PROFILE
STRUCTURED_PROFILES = (SETUP_PROFILE, SHOWDOWN_PROFILE, DETAIL_PROFILE)
SETUP_IMAGE_NAMES = ('thumb.png', 'scene.png', 'a.png', 'a_1.png', 'a_2.png', 'a_atk.png',
                     'b.png', 'b_1.png', 'b_2.png', 'b_atk.png')
ADDITIONAL_EDGE_IMAGE_NAMES = ('a_atk_2.png', 'b_atk_2.png')
USER_VERBATIM_IMAGE_NAMES = ('thumb.png', 'a.png', 'b.png', 'scene.png', 'a_2.png',
                             'a_atk.png', 'b_atk.png', 'b_2.png', 'cliffhanger.png')


def is_user_verbatim(cfg):
    # Explicit episode opt-in only. Existing v5 contracts remain unchanged.
    return (cfg.get('production_profile') == DETAIL_PROFILE
            and isinstance(cfg.get('user_verbatim_script'), str)
            and bool(cfg['user_verbatim_script'].strip()))




def is_longform(cfg):
    """Select an explicit format; mismatches must never use the legacy renderer."""
    media_format = cfg.get('format')
    profile = cfg.get('production_profile')
    if media_format == 'longform' or profile == LONGFORM_PROFILE:
        if media_format != 'longform' or profile != LONGFORM_PROFILE:
            raise ValueError('longform requires format=longform and production_profile=' + LONGFORM_PROFILE)
        return True
    if media_format not in (None, 'shorts'):
        raise ValueError('Unsupported episode format: ' + str(media_format))
    return False


def video_spec(cfg):
    lo, hi = duration_limits(cfg)
    longform = is_longform(cfg)
    return dict(width=1920 if longform else 1080,
                height=1080 if longform else 1920,
                fps=FPS, duration_min=lo, duration_max=hi)


def validate_detail_options(cfg):
    """Reject unsupported v5 combinations before any shared helper can ignore them."""
    if cfg.get('production_profile') != DETAIL_PROFILE:
        return
    if cfg.get('cta_presentation') != 'comment_spoken':
        raise ValueError(DETAIL_PROFILE + ': cta_presentation must explicitly be comment_spoken')
    verbatim = is_user_verbatim(cfg)
    if 'user_verbatim_script' in cfg and not verbatim:
        raise ValueError('user_verbatim_script must contain the complete approved script')
    if verbatim and ('story_beats' not in cfg or 'narration_groups' not in cfg):
        raise ValueError('user_verbatim_script requires explicit story_beats and narration_groups')
    for field in ('story_beats', 'clash', 'additional_edges', 'reaction_image'):
        if field in cfg and not (verbatim and field == 'story_beats'):
            raise ValueError(DETAIL_PROFILE + ': ' + field + ' is not supported; keep the basic two exchanges')
    for who in ('a', 'b'):
        info = cfg.get(who)
        edge = info.get('edge') if isinstance(info, dict) else None
        if isinstance(edge, dict) and 'reaction_image' in edge:
            raise ValueError(DETAIL_PROFILE + ': ' + who + '.edge.reaction_image is not supported')


def additional_edge_specs(cfg):
    """Optional second A/B exchange; older episodes keep their exact sequence."""
    if 'additional_edges' not in cfg:
        return []
    if cfg.get('production_profile') != SHOWDOWN_PROFILE:
        raise ValueError('additional_edges is only supported by showdown_story_v4')
    edges = cfg['additional_edges']
    if not isinstance(edges, list) or len(edges) != 2:
        raise ValueError('additional_edges must contain exactly two condition objects, a then b')
    for index, (who, image) in enumerate(zip(('a', 'b'), ADDITIONAL_EDGE_IMAGE_NAMES)):
        edge = edges[index]
        where = f'additional_edges.{index}'
        if not isinstance(edge, dict) or edge.get('condition') != who:
            raise ValueError(where + f': condition must be {who}; keep a then b order')
        if edge.get('image') != image:
            raise ValueError(where + f'.image: {image} required')
        if not isinstance(edge.get('title'), str) or not edge['title'].strip():
            raise ValueError(where + '.title: nonempty title required')
        lines = edge.get('lines')
        if (not isinstance(lines, list) or len(lines) != 2
                or any(not isinstance(line, str) or not line.strip() for line in lines)):
            raise ValueError(where + '.lines: exactly two nonempty cues required')
    return edges


def image_names(cfg):
    if is_longform(cfg):
        from longform_profile import require_longform_structure
        require_longform_structure(cfg)
        names = [cfg.get('thumb', 'thumb.png')]
        names.extend(cfg[who].get('image', who + '.png') for who in ('a', 'b'))
        names.extend(cut['image'] for cut in cfg['cuts'])
        return tuple(dict.fromkeys(names))
    validate_detail_options(cfg)
    if is_user_verbatim(cfg):
        return USER_VERBATIM_IMAGE_NAMES
    if cfg.get('production_profile') in (SHOWDOWN_PROFILE, DETAIL_PROFILE):
        # Portrait references may also be the solo introduction shot. Require
        # every actual scene, but never unused legacy reaction/intro assets.
        names = ['thumb.png', 'a.png', 'b.png']
        for who in ('a', 'b'):
            names.extend(cfg[who]['intro_images'])
        names.append(cfg['setup']['image'])
        if 'clash' in cfg:
            names.append('sword_clash.png')
        names.extend([cfg['a']['edge']['image'], cfg['b']['edge']['image']])
        names.extend(edge['image'] for edge in additional_edge_specs(cfg))
        names.append(cfg['cliffhanger']['image'])
        return tuple(dict.fromkeys(names))
    return SETUP_IMAGE_NAMES if cfg.get('production_profile') == SETUP_PROFILE else IMAGE_NAMES


def duration_limits(cfg):
    """Seconds, inclusive. Existing episode profiles retain their former limits."""
    if is_longform(cfg):
        return (540, 900)
    if is_user_verbatim(cfg):
        override = cfg.get('episode_specific_override', {})
        if 'duration_limit_seconds' in override:
            maximum = override['duration_limit_seconds']
            if (type(maximum) is not int or not 60 <= maximum <= 180
                    or not isinstance(override.get('duration_basis'), str)
                    or not override['duration_basis'].strip()):
                raise ValueError('A verbatim duration exception requires a 60..180 second limit and explicit user basis')
            return (30, maximum)
    return (30, 60) if cfg.get('production_profile') in STRUCTURED_PROFILES else (20, 45)


def narration_groups(cfg):
    if is_longform(cfg):
        from longform_profile import require_longform_structure
        require_longform_structure(cfg)
        return cfg['narration_groups']
    validate_detail_options(cfg)
    if 'narration_groups' in cfg:
        groups = cfg['narration_groups']
        if cfg.get('production_profile') == DETAIL_PROFILE:
            # make_voice also calls this helper directly. Do not let an empty
            # or partial explicit grouping silently become single-cue TTS.
            if (not isinstance(groups, list) or any(
                    not isinstance(group, dict) or not isinstance(group.get('segment_ids'), list)
                    or not group['segment_ids'] for group in groups)):
                raise ValueError(DETAIL_PROFILE + ': narration groups require nonempty segment_ids')
            ids = [cue['id'] for cue in speech_segments(cfg)
                   if not is_user_verbatim(cfg) or cue['id'] != 'cta']
            if [key for group in groups for key in group['segment_ids']] != ids:
                raise ValueError(DETAIL_PROFILE + ': all cues must belong to continuous narration groups')
            for who in (() if is_user_verbatim(cfg) else ('a', 'b')):
                if not any(f'{who}.intro.0' in group['segment_ids'] and
                           f'{who}.intro.1' in group['segment_ids'] for group in groups):
                    raise ValueError(DETAIL_PROFILE + ': ' + who +
                                     ' introduction and feat must be spoken in one narration group')
        return groups
    if cfg.get('production_profile') not in STRUCTURED_PROFILES:
        return []
    groups = [
        {'id': 'opening', 'segment_ids': ['hook']},
        {'id': 'intro_a', 'segment_ids': ['a.intro.0', 'a.intro.1']},
        {'id': 'intro_b', 'segment_ids': ['b.intro.0', 'b.intro.1']},
        {'id': 'setup', 'segment_ids': ['setup.0', 'setup.1']},
        {'id': 'edge_a', 'segment_ids': ['a.edge.0', 'a.edge.1']},
        {'id': 'edge_b', 'segment_ids': ['b.edge.0', 'b.edge.1']},
        {'id': 'ending', 'segment_ids': ['reason', 'question']},
        {'id': 'cta', 'segment_ids': ['cta']},
    ]
    if cfg.get('production_profile') == SHOWDOWN_PROFILE and 'clash' in cfg:
        groups.insert(4, {'id': 'sword_clash', 'segment_ids': ['sword_clash']})
    if cfg.get('production_profile') == SHOWDOWN_PROFILE:
        extra_groups = [{'id': 'edge2_' + edge['condition'],
                         'segment_ids': [f"{edge['condition']}.edge2.{i}" for i in range(2)]}
                        for edge in additional_edge_specs(cfg)]
        ending_index = next(i for i, group in enumerate(groups) if group['id'] == 'ending')
        groups[ending_index:ending_index] = extra_groups
    return [g for g in groups if g['id'] != 'cta' or cfg.get('cta_presentation') != 'overlay']


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def object_hash(obj):
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def nfc(text):
    return unicodedata.normalize('NFC', text)


def speech_segments(cfg):
    longform = is_longform(cfg)
    if longform:
        from longform_profile import require_longform_structure
        require_longform_structure(cfg)
    validate_detail_options(cfg)
    # One owner for CTA. Import lazily so importing this utility has no media side effects.
    from make_vs import CTA_SAY, CTA_SHOW, CTA_COMMENT_SAY
    mode = cfg.get('cta_presentation', 'spoken')
    if mode not in ('spoken', 'overlay', 'comment_spoken'):
        raise ValueError('cta_presentation must be spoken, overlay or comment_spoken')
    def finish(result):
        if is_user_verbatim(cfg):
            # The user supplied the complete visible/spoken script, including
            # the shared CTA. Do not add legacy subscription overlay wording.
            for cue in result:
                if cue['id'] == 'cta':
                    cue.update(image=cfg['cliffhanger']['image'],
                               spoken_text=CTA_COMMENT_SAY, display_text=CTA_COMMENT_SAY,
                               caption_in_overlay=False)
            return result
        if mode in ('overlay', 'comment_spoken'):
            if mode == 'overlay':
                result = [s for s in result if s['id'] != 'cta']
            for s in result:
                if s['kind'] in ('reason', 'question', 'cta'):
                    s['cta_overlay'] = '\n'.join(CTA_SHOW)
                if s['id'] == 'cta':
                    # Continue the unresolved final artwork. The spoken line
                    # is already visible in the overlay; do not print it twice.
                    s.update(image=cfg.get('cliffhanger', {}).get('image') or
                             next((p['image'] for p in reversed(result[:-1]) if p.get('image')), None),
                             spoken_text=CTA_COMMENT_SAY, display_text=CTA_SHOW[0],
                             caption_in_overlay=True)
        return result
    def part(key, kind, image, text, spoken=None, condition=None):
        return dict(id=key, scene_id=key, kind=kind, image=image,
                    display_text=nfc(text), spoken_text=nfc(spoken if spoken is not None else text),
                    condition=condition)
    if longform:
        cuts = {cut['id']: cut for cut in cfg['cuts']}
        result = []
        for beat in cfg['story_beats']:
            cut = cuts[beat['cut_id']]
            result.append({**part(beat['id'], beat.get('kind', 'story'), cut['image'],
                                 beat['display_text'], beat['spoken_text'], beat.get('condition')),
                           'scene_id': beat['scene_id'], 'cut_id': cut['id'],
                           'header': nfc(beat.get('header', '')),
                           'transition': cut.get('transition', 'crossfade')})
        last = result[-1]
        result.append({**part('cta', 'cta', last['image'], CTA_SHOW[0], CTA_COMMENT_SAY),
                       'scene_id': last['scene_id'], 'cut_id': last['cut_id'],
                       'transition': 'hold', 'cta_overlay': '\n'.join(CTA_SHOW),
                       'caption_in_overlay': True})
        # Only the actual ending has CTA graphics; intermediate questions do not.
        return result
    if cfg.get('production_profile') in STRUCTURED_PROFILES and not is_user_verbatim(cfg):
        if 'story_beats' in cfg or 'cta' in cfg:
            raise ValueError('setup_story_v3 uses structured fields and one shared CTA')
        result = [part('hook', 'hook', cfg.get('thumb', 'thumb.png'), cfg['hook'])]
        for who in ('a', 'b'):
            info = cfg[who]
            if len(info['intro']) != 2:
                raise ValueError('setup_story_v3 requires two intro sentences per character')
            for i, text in enumerate(info['intro']):
                result.append(part(f'{who}.intro.{i}', 'intro', info['intro_images'][i], text))
        setup = cfg['setup']
        if len(setup['lines']) != 2:
            raise ValueError('setup_story_v3 requires two setup sentences')
        for i, text in enumerate(setup['lines']):
            result.append({**part(f'setup.{i}', 'story', setup.get('image', 'scene.png'), text),
                           'header': nfc(setup['title'])})
        if cfg.get('production_profile') == SHOWDOWN_PROFILE and 'clash' in cfg:
            clash = cfg['clash']
            if not isinstance(clash, dict):
                raise ValueError('showdown_story_v4 clash must be an object')
            result.append({**part('sword_clash', 'story', clash['image'], clash['text']),
                           'header': nfc(clash['title'])})
        for who in ('a', 'b'):
            edge = cfg[who]['edge']
            if len(edge['lines']) != 2:
                raise ValueError('setup_story_v3 requires two edge sentences per character')
            for i, text in enumerate(edge['lines']):
                result.append(part(f'{who}.edge.{i}', 'edge', edge['image'], text, condition=who))
        if cfg.get('production_profile') == SHOWDOWN_PROFILE:
            for edge in additional_edge_specs(cfg):
                who = edge['condition']
                for i, text in enumerate(edge['lines']):
                    result.append({**part(f'{who}.edge2.{i}', 'edge', edge['image'], text, condition=who),
                                   'header': nfc(edge['title'])})
        ending_image = cfg['cliffhanger']['image'] if cfg.get('production_profile') in (SHOWDOWN_PROFILE, DETAIL_PROFILE) else None
        result.extend([part('reason', 'reason', ending_image, cfg['reason']),
                       part('question', 'question', ending_image, cfg['question']),
                       part('cta', 'cta', None, '\n'.join(CTA_SHOW), spoken=CTA_SAY)])
        return finish(result)
    if 'story_beats' in cfg:
        if 'cta' in cfg:
            raise ValueError('CTA is shared renderer content and cannot be supplied by an episode')
        beats = cfg['story_beats']
        if not isinstance(beats, list) or not beats:
            raise ValueError('story_beats must be a nonempty list')
        result, ids = [], set()
        kinds = {'hook', 'intro', 'clash', 'edge', 'reason', 'question', 'story'}
        for beat in beats:
            if not isinstance(beat, dict):
                raise ValueError('Each story beat must be an object')
            key = beat.get('id')
            if not isinstance(key, str) or not key.strip() or key == 'cta' or key in ids:
                raise ValueError('Story cue IDs must be unique, nonempty, and not cta')
            if beat.get('kind') not in kinds or beat.get('condition') not in (None, 'a', 'b'):
                raise ValueError('Invalid story cue kind or condition')
            if any(not isinstance(beat.get(field), str) or not beat[field].strip()
                   for field in ('display_text', 'spoken_text')):
                raise ValueError('Story display_text and spoken_text must be nonempty strings')
            if beat.get('image') is not None and not isinstance(beat['image'], str):
                raise ValueError('Story image must be a filename or null')
            if not isinstance(beat.get('header', ''), str):
                raise ValueError('Story header must be a string')
            ids.add(key)
            result.append({**part(key, beat['kind'], beat.get('image'), beat['display_text'],
                                  beat['spoken_text'], beat.get('condition')),
                           'header': nfc(beat.get('header', ''))})
        result.append(part('cta', 'cta', None, '\n'.join(CTA_SHOW), spoken=CTA_SAY))
        result = finish(result)
        if is_user_verbatim(cfg):
            normalize = lambda text: ' '.join(nfc(text).split())
            if normalize(' '.join(cue['spoken_text'] for cue in result)) != normalize(cfg['user_verbatim_script']):
                raise ValueError('user_verbatim_script: spoken cues must preserve every approved word and its order')
            if any(normalize(cue['display_text']) != normalize(cue['spoken_text']) for cue in result):
                raise ValueError('user_verbatim_script: captions must preserve the approved spoken text')
        return result
    result = [part('hook', 'hook', cfg.get('thumb', 'thumb.png'), cfg['hook'])]
    for who in ('a', 'b'):
        info = cfg[who]
        for i, line in enumerate(info['intro']):
            result.append(part(f'{who}.intro.{i}', 'intro', info['intro_images'][0 if i < 2 else 1], line))
    result.append(part('clash', 'clash', None, cfg.get('clash_text', '둘이 붙는다면?'),
                       cfg.get('clash_say', '둘이 맞붙으면 어떨까요?')))
    for who in ('a', 'b'):
        edge = cfg[who]['edge']
        for i, line in enumerate(edge['lines']):
            result.append(part(f'{who}.edge.{i}', 'edge', edge['image' if i == 0 else 'reaction_image'], line, condition=who))
    result.extend([part('reason', 'reason', None, cfg['reason']),
                   part('question', 'question', None, cfg['question']),
                   part('cta', 'cta', None, '\n'.join(CTA_SHOW), spoken=CTA_SAY)])
    return finish(result)


def script_hash(cfg):
    # Includes all visible/spoken content, portrait cards, conditions and source-version choices.
    return object_hash({'schema_version': cfg.get('schema_version'), 'episode': cfg,
                        'segments': speech_segments(cfg)})


def safe_child(base, relative):
    base = Path(base).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Path escapes episode directory: ' + str(relative))
    return path
