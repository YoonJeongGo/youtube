"""Schema-2 structural checks and hash-bound factual, visual and listening gates.

No function creates a PASS review. Structural success alone is never COMPLETE.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from PIL import Image
from episode_core import DETAIL_PROFILE, IMAGE_NAMES, SCHEMA_VERSION, SHOWDOWN_PROFILE, STRUCTURED_PROFILES, USER_VERBATIM_IMAGE_NAMES, is_user_verbatim, additional_edge_specs, duration_limits, image_names, load_json, narration_groups, nfc, object_hash, safe_child, script_hash, sha256_file, speech_segments, validate_detail_options, is_longform, video_spec
from subtitle_checks import validate_subtitles
from audio_review_checks import validate_audio_model_review
from image_review_contract import contract_errors, generation_records, latest_generation

LIMITS = {"hook": 30, "tag": 14, "intro": 32, "edge_title": 14, "edge_line": 32, "reason": 30, "question": 34}
METADATA_FILES = ("title.txt", "description.txt", "pinned_comment.txt")
DELIVERY_SOURCES = {
    "video.mp4": "out/video.mp4", "thumbnail.png": "thumb.png",
    **{name: name for name in (*METADATA_FILES, "sources.md", "qa_report.md")},
    "captions.srt": "out/captions.srt",
    "review/subtitle_review.json": "subtitle_review.json", "review/timeline.json": "timeline.json",
    "review/technical_qa.json": "out/technical_qa.json", "review/qa_signoffs.json": "qa_signoffs.json",
    "review/audio_review.json": "audio_review.json", "review/image_review.json": "image_review.json",
    "review/source_records.json": "source_records.json",
}
DELIVERY_FILES = tuple(DELIVERY_SOURCES)
# ASR/signal diagnostics alone never satisfy perceptual review. Local raw-audio
# model evidence is accepted only through validate_audio_model_review, with
# its current file hash and complete input coverage; it is not human listening.
DELIVERY_OPTIONAL_SOURCES = {
    "review/upload_thumbnail_review.json": "upload_thumbnail_review.json",
    "review/upload_thumbnail_prompt.txt": "upload_thumbnail_prompt.txt",
    "review/user_release_acceptance.json": "user_release_acceptance.json",
    "review/audio_analysis_final_small.json": "audio_analysis_final_small.json",
    "review/audio_analysis_voice_story.json": "audio_analysis_voice_story.json",
    "review/audio_analysis_voice_story_medium.json": "audio_analysis_voice_story_medium.json",
    "review/audio_group_timing_diagnostics.json": "audio_group_timing_diagnostics.json",
    "review/voice_processing_refresh.json": "voice_processing_refresh.json",
    "review/audio_analysis.json": "audio_analysis.json",
    "review/audio_analysis_medium.json": "audio_analysis_medium.json",
    "review/audio_capability_probe.json": "audio_capability_probe.json",
    "review/audio_model_observation.json": "audio_model_observation.json",
    "review/audio_signal_diagnostics.json": "audio_signal_diagnostics.json",
    "review/audio_quality_komos.json": "audio_quality_komos.json",
    "review/audio_prosody_diagnostics.json": "audio_prosody_diagnostics.json",
    "review/audio_understanding_online.json": "audio_understanding_online.json",
    "review/audio_understanding_local.json": "audio_understanding_local.json",
    "review/audio_understanding_native.json": "audio_understanding_native.json",
}


def delivery_source(base, name):
    cfg_path = Path(base) / "episode.json"
    cfg = load_json(cfg_path) if cfg_path.is_file() else {}
    thumb = cfg.get("thumb", "thumb.png") if is_longform(cfg) else "thumb.png"
    if name == "thumbnail.png" and (Path(base) / "upload_thumbnail.png").is_file():
        review = load_json(Path(base) / "upload_thumbnail_review.json")
        if (review.get("status") != "PASS"
                or review.get("sha256") != sha256_file(Path(base) / "upload_thumbnail.png")
                or review.get("source_sha256") != sha256_file(safe_child(Path(base), thumb))
                or not review.get("observations")):
            raise ValueError("Upload thumbnail needs a current visual review")
        if is_longform(cfg) and (not review.get("full_observations") or not review.get("detail_observations")
                                or not review.get("small_size_observations") or review.get("issues") != []):
            raise ValueError("Longform upload thumbnail needs actual full/detail/small-list observations without unresolved issues")
        return "upload_thumbnail.png"
    return thumb if name == "thumbnail.png" else DELIVERY_SOURCES[name]


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


DETAIL_ABSTRACT_PATTERNS = (
    ('몰아붙인다', r'몰아붙[가-힣]*'),
    ('빈틈을 노린다', r'빈틈을\s*노[가-힣]*'),
    ('압도한다', r'압도[가-힣]*'),
    ('맞선다', r'맞(?:선[가-힣]*|서[가-힣]*|섭[가-힣]*)'),
    ('격돌한다', r'격돌[가-힣]*'),
    ('공격을 퍼붓는다', r'공격을\s*퍼(?:붓|부)[가-힣]*'),
    ('강력한 힘으로', r'강력한\s*힘으로'),
    ('치열한 싸움', r'치열한\s*싸움'),
    ('밀리지 않는다', r'밀리지\s*않[가-힣]*'),
    ('반격한다', r'반격[가-힣]*'),
)


def text_warnings(cfg):
    """Limited v5 phrase hints, never semantic failures or content approval.

    Some inflections are matched; similar wording and causal errors can remain
    undetected. The surrounding exchange must be reviewed in existing records.
    """
    if not isinstance(cfg, dict) or cfg.get('production_profile') != DETAIL_PROFILE:
        return []
    warnings = []
    for who in ('a', 'b'):
        info = cfg.get(who)
        edge = info.get('edge') if isinstance(info, dict) else None
        lines = edge.get('lines') if isinstance(edge, dict) else None
        if not isinstance(lines, list):
            continue
        for index, line in enumerate(lines):
            if not isinstance(line, str):
                continue
            for expression, pattern in DETAIL_ABSTRACT_PATTERNS:
                match = re.search(pattern, nfc(line))
                if match:
                    warnings.append(f"{who}.edge.{index}: '{match.group(0)}' (주의 표현: {expression}) — "
                                    "앞뒤 cue에 구체적 행동·대응·직접 결과가 있는지 내용 검토 필요")
    return warnings


def _check_setup_text(cfg, errors):
    """The setup profile changes structure, never the independent evidence gates."""
    showdown = cfg.get('production_profile') == SHOWDOWN_PROFILE
    detail = cfg.get('production_profile') == DETAIL_PROFILE
    modern = showdown or detail
    prefix = cfg.get('production_profile', 'setup_story_v3')
    def require(ok, message):
        if not ok:
            errors.append(prefix + ": " + message)

    # Per-episode dialogue opt-in: keep the default single-sentence contract.
    dialogue_cues = set()
    if 'dialogue_cues' in cfg:
        require(detail, 'dialogue_cues is supported only by showdown_detail_v5')
        selected = cfg['dialogue_cues']
        require(isinstance(selected, list), 'dialogue_cues: list of cue IDs required')
        if isinstance(selected, list):
            require(all(isinstance(cue, str) for cue in selected),
                    'dialogue_cues: every cue ID must be a string')
            if all(isinstance(cue, str) for cue in selected):
                known = {'hook', 'reason', 'question', 'cta', 'setup.0', 'setup.1'}
                known.update(f'{who}.{part}.{index}' for who in ('a', 'b')
                             for part in ('intro', 'edge') for index in (0, 1))
                require(len(selected) == len(set(selected)), 'dialogue_cues: duplicate cue IDs')
                require(set(selected) <= known, 'dialogue_cues: unknown cue ID')
                if detail:
                    dialogue_cues = set(selected) & known

    def sentence(value, where, limit, question=False, clause=False):
        if not _nonempty(value):
            require(False, where + ": nonempty sentence required")
            return
        value = nfc(value)
        require(bool(re.search(r"[^\W_]", value)), where + ": actual sentence text required")
        require(len(value) <= limit, f"{where}: at most {limit} NFC characters")
        stripped = value.rstrip()
        if not clause:
            require(bool(re.search(r"[.?!]$", stripped)), where + ": sentence must end with .?!")
        if where in dialogue_cues:
            require(len(re.findall(r"[.?!]\s*(?=\S)", stripped[:-1])) <= 1,
                    where + ": use at most two sentences for dialogue")
        else:
            require(not re.search(r"[.?!]\s*\S", stripped[:-1]), where + ": use one sentence")
        if question:
            require(stripped.endswith("?"), where + ": question must end with ?")

    require("story_beats" not in cfg, "story_beats cannot be mixed with this structured profile")
    if detail:
        try:
            validate_detail_options(cfg)
        except ValueError as exc:
            errors.append(str(exc))
    sentence(cfg.get("hook"), "hook", 40 if modern else 30, True)
    sentence(cfg.get("reason"), "reason", 48 if detail else 40 if showdown else 30, clause=modern)
    sentence(cfg.get("question"), "question", 36, True)
    authored = [cfg.get("hook")]
    edges = []
    for who in ("a", "b"):
        info = cfg.get(who)
        if not isinstance(info, dict):
            require(False, f"{who}: person object required")
            continue
        require(_nonempty(info.get("name")), f"{who}.name: name required")
        tag = info.get("tag")
        require(_nonempty(tag) and len(nfc(tag)) <= 14, f"{who}.tag: 1~14 NFC characters")
        require(info.get("image") == f"{who}.png", f"{who}.image: fixed portrait required")
        allowed_intros = [[f"{who}_1.png", f"{who}_2.png"]]
        if modern:
            allowed_intros.append([f"{who}.png", f"{who}_2.png"])
        require(info.get("intro_images") in allowed_intros, f"{who}.intro_images: solo portrait then dedicated feat image required")
        intro = info.get("intro")
        require(isinstance(intro, list) and len(intro) == 2, f"{who}.intro: exactly 2 sentences required")
        if isinstance(intro, list):
            for index, line in enumerate(intro):
                sentence(line, f"{who}.intro.{index}", 48, clause=modern and index == 0)
            authored.extend(intro)
        if modern:
            feat = info.get('featured_feat')
            require(isinstance(feat, dict), f'{who}.featured_feat: sourced representative feat required')
            if isinstance(feat, dict):
                for field in ('summary', 'challenge', 'outcome', 'relevance', 'selection_reason'):
                    require(_nonempty(feat.get(field)), f'{who}.featured_feat.{field}: reason required')
                require(feat.get('version') == info.get('version') and _nonempty(info.get('version')),
                        f'{who}.featured_feat.version: must use the selected character version')
                source = feat.get('source', {})
                require(isinstance(source, dict) and _nonempty(source.get('url')) and _nonempty(source.get('locator')),
                        f'{who}.featured_feat.source: URL and passage required')
        edge = info.get("edge")
        if not isinstance(edge, dict):
            require(False, f"{who}.edge: condition object required")
            continue
        title = edge.get("title")
        require(_nonempty(title) and len(nfc(title)) <= 16, f"{who}.edge.title: 1~16 NFC characters")
        require(edge.get("image") == f"{who}_atk.png", f"{who}.edge.image: fixed attack image required")
        require("reaction_image" not in edge, f"{who}.edge.reaction_image: legacy reaction image is not used by this profile")
        lines = edge.get("lines")
        require(isinstance(lines, list) and len(lines) == 2, f"{who}.edge.lines: exactly 2 sentences required")
        if isinstance(lines, list):
            for index, line in enumerate(lines):
                sentence(line, f"{who}.edge.{index}", 48 if detail else 44, clause=modern and index == 0)
            edges.extend(lines)
    setup = cfg.get("setup")
    if not isinstance(setup, dict):
        require(False, "setup: concrete hypothetical situation required")
    else:
        title = setup.get("title")
        require(_nonempty(title) and len(nfc(title)) <= 22, "setup.title: 1~22 NFC characters")
        require(setup.get("image") == "scene.png", "setup.image: scene.png required")
        lines = setup.get("lines")
        require(isinstance(lines, list) and len(lines) == 2, "setup.lines: exactly 2 sentences required")
        if isinstance(lines, list):
            for index, line in enumerate(lines):
                sentence(line, f"setup.{index}", 48, question=index == 1 and not modern,
                         clause=modern and index == 0)
            authored.extend(lines)
    if showdown and 'clash' in cfg:
        clash = cfg['clash']
        require(isinstance(clash, dict), 'clash: optional sword-clash object required')
        if isinstance(clash, dict):
            title = clash.get('title')
            require(_nonempty(title) and len(nfc(title)) <= 22, 'clash.title: 1~22 NFC characters')
            require(clash.get('image') == 'sword_clash.png', 'clash.image: sword_clash.png required')
            sentence(clash.get('text'), 'clash.text', 48)
            authored.append(clash.get('text'))
    if showdown:
        require(cfg.get('cta_presentation') in ('overlay', 'comment_spoken'),
                'CTA likes/subscription must be screen-only; use comment_spoken or legacy overlay')
        require(isinstance(cfg.get('hook'), str) and cfg['hook'].endswith('누가 이길까요?'),
                'hook: contrast the strengths and finish with 누가 이길까요?')
    if detail:
        require(re.sub(r'\s+', '', str(cfg.get('hook', ''))) != '누가이길까요?',
                'hook: use a concrete crisis/action question, not only 누가 이길까요?')
    if modern:
        cliff = cfg.get('cliffhanger')
        require(isinstance(cliff, dict), 'cliffhanger: new unresolved confrontation scene required')
        if isinstance(cliff, dict):
            require(cliff.get('image') == 'cliffhanger.png', 'cliffhanger.image: cliffhanger.png required')
            require(cliff.get('participants') == ['a', 'b'], 'cliffhanger: both participants required')
            require(cliff.get('winner_revealed') is False, 'cliffhanger: do not reveal a winner')
            for field in ('moment', 'unresolved_action'):
                require(_nonempty(cliff.get(field)), 'cliffhanger.' + field + ': concrete action required')
    if showdown:
        try:
            for index, edge in enumerate(additional_edge_specs(cfg)):
                where = f'additional_edges.{index}'
                require(len(nfc(edge['title'])) <= 16, where + '.title: 1~16 NFC characters')
                require('reaction_image' not in edge, where + ': no legacy reaction image')
                for cue_index, line in enumerate(edge['lines']):
                    sentence(line, f'{where}.lines.{cue_index}', 44, clause=cue_index == 0)
                edges.extend(edge['lines'])
        except ValueError as exc:
            require(False, str(exc))
    authored.extend(edges)
    authored.extend([cfg.get("reason"), cfg.get("question")])
    # Count authored spoken sentences in playback order, including reason.
    # Shared CTA is outside the author's wording and is deliberately excluded.
    texts = [nfc(text).rstrip().rstrip(".?!").rstrip() for text in authored if _nonempty(text)]
    yo_count = sum(text.endswith("요") for text in texts)
    require(yo_count * 2 <= len(texts), f"요 ending ratio {yo_count}/{len(texts)} exceeds one half")
    # These are observable suffix classes, not a morphological/semantic judge.
    # Keep 죠 distinct from 요 and longer 요 endings distinct from each other.
    endings = []
    pattern = re.compile(r"(니까요|는데요|거든요|잖아요|잖죠|까요|네요|군요|이에요|예요|어요|아요|해요|지요|니다|죠|요|다|까)$")
    for text in texts:
        match = pattern.search(text)
        endings.append(match.group(1) if match else text.split()[-1] if text.split() else "")
    for index in range(2, len(endings)):
        require(not endings[index] == endings[index - 1] == endings[index - 2],
                f"same ending repeated 3 times at spoken sentence {index + 1}: {endings[index]}")
    _check_setup_groups(cfg, errors)
    if modern:
        try:
            groups = narration_groups(cfg)
            ids = [s['id'] for s in speech_segments(cfg)]
            members = [key for g in groups for key in g['segment_ids']]
            require(members == ids, 'all cues must belong to continuous narration groups')
            for who in ('a','b'):
                require(any(f'{who}.intro.0' in g['segment_ids'] and f'{who}.intro.1' in g['segment_ids'] for g in groups),
                        who + ': solo introduction and feat must be spoken in one narration group')
            if detail:
                require(len(ids) == 14, 'the basic two exchanges require exactly 14 cues')
                require(len({s['image'] for s in speech_segments(cfg)}) == 9,
                        'the basic sequence requires exactly nine distinct screen images')
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            require(False, 'cannot resolve continuous intro groups')


def _check_setup_groups(cfg, errors):
    """Validate optional/default groups against the exact core cue sequence."""
    prefix = DETAIL_PROFILE if cfg.get('production_profile') == DETAIL_PROFILE else 'setup_story_v3'
    try:
        ids = [entry["id"] for entry in speech_segments(cfg)]
        groups = narration_groups(cfg)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        errors.append(prefix + ": cannot resolve narration groups: " + str(exc))
        return
    if not isinstance(groups, list):
        errors.append(prefix + ": narration_groups must be a list")
        return
    used, names, previous_end = set(), set(), -1
    for group in groups:
        if not isinstance(group, dict):
            errors.append(prefix + ": narration group must be an object")
            continue
        key, members = group.get("id"), group.get("segment_ids")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", key):
            errors.append(prefix + ": safe narration group ID required")
        else:
            audio_key = re.sub(r"[^a-zA-Z0-9_-]", "_", "group." + key).casefold()
            if audio_key in names:
                errors.append(prefix + ": duplicate narration group ID/audio filename")
            names.add(audio_key)
        if not isinstance(members, list) or not members or any(not isinstance(x, str) or x not in ids for x in members):
            errors.append(prefix + ": narration group contains empty/unknown segment_ids")
            continue
        indexes = [ids.index(x) for x in members]
        if indexes != list(range(indexes[0], indexes[0] + len(indexes))) or indexes[0] <= previous_end or used.intersection(members):
            errors.append(prefix + ": narration groups must contain consecutive unique cues in script order")
        used.update(members)
        previous_end = indexes[-1]


def _check_story_text(cfg, errors):
    """Story cues may be clauses; complete spoken groups own punctuation.

    This is structural validation, not a claim that joined prose is factual or
    grammatical. Sources and actual subtitle/audio review remain separate gates.
    """
    def require(ok, message):
        if not ok:
            errors.append("story: " + message)

    def safe_id(value):
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is not None

    def audio_key(value):
        # Match make_voice.audio_name and Windows's case-insensitive filenames.
        return ("v_" + re.sub(r"[^a-zA-Z0-9_-]", "_", value) + ".mp3").casefold()

    require(cfg.get("fictional_matchup") is True, "fictional_matchup must explicitly be true")
    require(_nonempty(cfg.get("fictional_premise")), "fictional_premise must explain the imagined scenario")
    beats = cfg.get("story_beats")
    if not isinstance(beats, list):
        errors.append("story: story_beats must be a list of 6~20 cues")
        return
    require(6 <= len(beats) <= 20, "story_beats must contain 6~20 cues")
    allowed_kinds = {"hook", "intro", "clash", "edge", "reason", "question", "story"}
    verbatim = is_user_verbatim(cfg)
    allowed_images = set(USER_VERBATIM_IMAGE_NAMES if verbatim else IMAGE_NAMES)
    indexed, ids, audio_names = {}, [], {audio_key("cta")}
    kinds = {name: [] for name in allowed_kinds}
    for position, beat in enumerate(beats):
        where = f"cue {position + 1}"
        if not isinstance(beat, dict):
            errors.append(f"story: {where} must be an object")
            continue
        require("cta" not in beat and "stats" not in beat, f"{where} cannot define cta/stats")
        key = beat.get("id")
        if not safe_id(key) or key.casefold() == "cta":
            errors.append(f"story: {where} needs a safe non-CTA cue ID")
        else:
            require(key not in indexed, f"duplicate cue ID: {key}")
            require(audio_key(key) not in audio_names, f"cue audio filename collision: {key}")
            audio_names.add(audio_key(key))
            indexed[key] = beat
            ids.append(key)
        kind = beat.get("kind")
        if not isinstance(kind, str) or kind not in allowed_kinds:
            errors.append(f"story: {where} has an invalid kind")
        else:
            kinds[kind].append(beat)
        require(beat.get("condition") in (None, "a", "b"), f"{where} condition must be null/a/b")
        picture = beat.get("image")
        require(picture is None or (isinstance(picture, str) and picture in allowed_images),
                (f"{where} image must use one of the fixed nine verbatim images" if verbatim
                 else f"{where} image must be one of the fixed 11 images or null"))
        require(isinstance(beat.get("header", ""), str), f"{where} header must be a string")
        for field in ("display_text", "spoken_text"):
            text = beat.get(field)
            require(_nonempty(text) and re.search(r"[^\W_]", text, re.UNICODE) is not None,
                    f"{where} {field} must contain actual text")
    if beats:
        require(isinstance(beats[0], dict) and beats[0].get("kind") == "hook", "the first cue must be the hook")
        require(isinstance(beats[-1], dict) and beats[-1].get("kind") == "question", "the last story cue must be the question")
    text_limits = (("hook", 40), ("reason", 48), ("question", 40)) if verbatim else (("hook", 30), ("reason", 30), ("question", 34))
    for name, limit in text_limits:
        value = cfg.get(name)
        require(_nonempty(value), f"top-level {name} text is required")
        if _nonempty(value):
            require(len(nfc(value)) <= limit, f"{name} must be at most {limit} NFC characters")
            if name in ("hook", "question"):
                require(value.rstrip().endswith("?"), f"{name} must end with a question mark")
        require(len(kinds[name]) == 1, f"exactly one corresponding {name} cue is required")
        if len(kinds[name]) == 1 and _nonempty(value):
            for field in ("display_text", "spoken_text"):
                actual = kinds[name][0].get(field)
                require(isinstance(actual, str) and nfc(actual) == nfc(value), f"{name} cue {field} differs from top-level {name}")

    groups = cfg.get("narration_groups")
    if not isinstance(groups, list) or not groups:
        errors.append("story: narration_groups must explicitly cover every story cue")
        return
    positions = {key: index for index, key in enumerate(ids)}
    flattened, group_ids, unit_audio_names, previous_end = [], set(), {audio_key("cta")}, -1
    for position, group in enumerate(groups):
        where = f"narration group {position + 1}"
        if not isinstance(group, dict):
            errors.append(f"story: {where} must be an object")
            continue
        key = group.get("id")
        if not safe_id(key):
            errors.append(f"story: {where} needs a safe group ID")
        else:
            require(key not in group_ids, f"duplicate narration group ID: {key}")
            group_ids.add(key)
            filename = audio_key("group." + key)
            require(filename not in unit_audio_names, f"narration group audio filename collision: {key}")
            unit_audio_names.add(filename)
        members = group.get("segment_ids")
        if not isinstance(members, list) or not members or any(not isinstance(x, str) or x not in indexed for x in members):
            errors.append(f"story: {where} has empty/unknown/CTA segment_ids")
            continue
        flattened.extend(members)
        indexes = [positions[x] for x in members]
        require(indexes == list(range(indexes[0], indexes[0] + len(indexes))) and indexes[0] > previous_end,
                f"{where} must contain consecutive cues in script order")
        previous_end = indexes[-1]
        texts = [indexed[x].get("spoken_text") for x in members]
        if all(_nonempty(x) for x in texts):
            joined = " ".join(nfc(text) for text in texts).rstrip()
            terminator = r"[.?!][\"'”’]*$" if verbatim else r"[.?!]$"
            require(bool(re.search(terminator, joined)),
                    f"{where} must end a complete spoken passage with .?!; a comma is only internal")
    require(flattened == ids and len(ids) == len(beats),
            "narration_groups must include every story cue exactly once, consecutively and in order; shared CTA is automatic")


def check_text(cfg, errors):
    def require(ok, message):
        if not ok:
            errors.append(message)
    def sentence(value, where, limit, question=False):
        if not _nonempty(value):
            errors.append(f"{where}: 완전한 문장이 필요해요")
            return
        value = nfc(value)
        require(len(value) <= limit, f"{where}: 공백 포함 {len(value)}자예요 ({limit}자 이내)")
        require(bool(re.search(r"요[.?]$", value)), f"{where}: 해요체 문장을 마침표나 물음표로 끝내세요")
        require(not re.search(r"[.?!].+", value[:-1]), f"{where}: 한 문장만 사용하세요")
        if question:
            require(value.endswith("?"), f"{where}: 질문은 물음표로 끝내세요")
    if not isinstance(cfg, dict):
        errors.append("episode.json must be an object")
        return
    try:
        longform = is_longform(cfg)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if longform:
        from longform_profile import longform_text_errors
        errors.extend(longform_text_errors(cfg))
        return
    require(cfg.get("schema_version") == SCHEMA_VERSION, "schema_version: 새 제작은 반드시 2여야 해요")
    require(cfg.get("winner") == "none", 'winner: 명시적으로 "none"이어야 해요')
    require("stats" not in cfg and "cta" not in cfg, "stats/cta 필드는 사용할 수 없어요")
    require(cfg.get("thumb") == "thumb.png", "thumb: thumb.png여야 해요")
    profile = cfg.get("production_profile")
    require(profile is None or profile in STRUCTURED_PROFILES, "Unknown production_profile")
    require('additional_edges' not in cfg or profile == SHOWDOWN_PROFILE,
            'additional_edges is only supported by showdown_story_v4')
    require("user_verbatim_script" not in cfg or is_user_verbatim(cfg),
            "user_verbatim_script requires a nonempty script and the explicit v5 profile")
    if is_user_verbatim(cfg):
        _check_story_text(cfg, errors)
        try:
            cues = speech_segments(cfg)
            narration_groups(cfg)
            require({cue['image'] for cue in cues} == set(USER_VERBATIM_IMAGE_NAMES),
                    "user_verbatim_script requires the nine approved screen images")
        except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
            require(False, str(exc))
        for who in ('a', 'b'):
            info = cfg.get(who, {})
            require(isinstance(info, dict) and _nonempty(info.get('name')),
                    who + ': character name required')
            if isinstance(info, dict):
                require(_nonempty(info.get('tag')) and len(nfc(info['tag'])) <= 14,
                        who + '.tag: 1~14 NFC characters required')
                require(info.get('image') == who + '.png', who + '.image: fixed portrait filename required')
        cliff = cfg.get('cliffhanger', {})
        require(isinstance(cliff, dict) and cliff.get('image') == 'cliffhanger.png'
                and cliff.get('winner_revealed') is False,
                'cliffhanger: unresolved ending artwork required')
        return
    if profile in STRUCTURED_PROFILES:
        _check_setup_text(cfg, errors)
        return
    story_mode = "story_beats" in cfg
    if story_mode:
        _check_story_text(cfg, errors)
    else:
        sentence(cfg.get("hook"), "hook", 30, True)
        sentence(cfg.get("reason"), "reason", 30)
        sentence(cfg.get("question"), "question", 34, True)
        sentence(cfg.get("clash_say", "둘이 맞붙으면 어떨까요?"), "clash_say", 34)
    for who, opponent in (("a", "b"), ("b", "a")):
        info = cfg.get(who)
        if not isinstance(info, dict):
            errors.append(f"{who}: 인물 객체가 필요해요")
            continue
        require(_nonempty(info.get("name")), f"{who}.name: 이름이 필요해요")
        tag = info.get("tag")
        require(_nonempty(tag) and len(nfc(tag)) <= 14, f"{who}.tag: 1~14자여야 해요")
        require(info.get("image") == f"{who}.png", f"{who}.image: 고정 파일명을 사용하세요")
        require(info.get("intro_images") == [f"{who}_1.png", f"{who}_2.png"], f"{who}.intro_images: 소개 원본 두 장이 필요해요")
        if not story_mode:
            intro = info.get("intro")
            if not isinstance(intro, list) or len(intro) != 3:
                errors.append(f"{who}.intro: 정확히 3문장이어야 해요")
            else:
                for i, line in enumerate(intro):
                    sentence(line, f"{who}.intro.{i}", 32)
        edge = info.get("edge")
        if not isinstance(edge, dict):
            errors.append(f"{who}.edge: 조건 객체가 필요해요")
            continue
        title = edge.get("title")
        require(_nonempty(title) and len(nfc(title)) <= 14, f"{who}.edge.title: 1~14자여야 해요")
        require(edge.get("image") == f"{who}_atk.png", f"{who}.edge.image: 공격 이미지가 필요해요")
        require(edge.get("reaction_image") == f"{opponent}_react.png", f"{who}.edge.reaction_image: 상대 반응 이미지가 필요해요")
        if not story_mode:
            lines = edge.get("lines")
            if not isinstance(lines, list) or len(lines) != 2:
                errors.append(f"{who}.edge.lines: 정확히 2문장이어야 해요")
            else:
                for i, line in enumerate(lines):
                    sentence(line, f"{who}.edge.{i}", 32)


def check_images(cfg, base_dir, errors):
    base, seen = Path(base_dir), {}
    try:
        required_images = image_names(cfg)
    except (ValueError, TypeError, AttributeError) as exc:
        errors.append("Cannot select profile image names: " + str(exc))
        return
    for name in required_images:
        path = base / name
        if not path.is_file():
            errors.append(f"그림 누락: {name}")
            continue
        try:
            with Image.open(path) as image:
                image.load()
                expected = (1024, 1024) if name in ("a.png", "b.png") else (1024, 1536)
                if is_longform(cfg):
                    reference_images = {cfg.get(who, {}).get("image") for who in ("a", "b")}
                    if name not in reference_images and image.width <= image.height:
                        errors.append(f"{name}: longform source must use a landscape composition without stretching")
                elif image.size != expected:
                    errors.append(f"{name}: 실제 크기 {image.size}, 필수 크기 {expected}")
                pixels = image.convert("RGBA")
                digest = hashlib.sha256(str(pixels.size).encode() + pixels.tobytes()).hexdigest()
                if digest in seen and not is_longform(cfg):
                    errors.append(f"동일 픽셀 복제: {name} = {seen[digest]}")
                seen[digest] = name
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: 이미지를 읽을 수 없어요: {exc}")


def validate_episode(episode_path, text_only=False):
    errors = []
    try:
        cfg = load_json(episode_path)
    except (OSError, ValueError) as exc:
        return [f"episode.json 읽기 실패: {exc}"]
    check_text(cfg, errors)
    if not text_only:
        check_images(cfg, Path(episode_path).parent, errors)
    return errors


def _read(base, name, errors):
    try:
        value = load_json(base / name)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object")
        return value
    except (OSError, ValueError) as exc:
        errors.append(f"{name}: missing/invalid: {exc}")
        return {}


def _hash_matches(base, name, expected, errors, label=None):
    try:
        actual = sha256_file(safe_child(base, name))
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"{label or name}: missing/unsafe file: {exc}")
        return False
    if actual != expected:
        errors.append(f"{label or name}: stale or missing review hash")
        return False
    return True


def _observed(review, key, errors):
    if review.get("status") != "PASS":
        errors.append(f"{key}: actual review has not passed ({review.get('status', 'UNREVIEWED')})")
    if not review.get("observations"):
        errors.append(f"{key}: specific review observations are required")


def validate_planning(cfg, base, errors):
    planning = cfg.get("planning", {})
    if not isinstance(planning, dict) or not planning.get("idea_id"):
        errors.append("planning.idea_id is required")
        return
    pool_name = "matchup_ideas_longform.json" if is_longform(cfg) else "matchup_ideas.json"
    root = next((p for p in base.parents if (p / pool_name).is_file()), None)
    if root is None:
        errors.append(pool_name + " cannot be found in the project ancestors")
        return
    pool = _read(root, pool_name, errors)
    matches = [x for x in pool.get("candidates", []) if x.get("id") == planning["idea_id"]]
    if len(matches) != 1:
        errors.append("planning.idea_id must match exactly one candidate")
        return
    idea = matches[0]
    from plan_matchups import candidate_errors
    errors.extend("planning: " + e for e in candidate_errors(idea))
    for who in ("a", "b"):
        actual = cfg[who]
        for field, alternate in (("id", "person_ids"), ("version", "versions")):
            value = actual.get(field) or planning.get(alternate, {}).get(who)
            if value != idea[who][field]:
                errors.append(f"planning: {who}.{field} differs from selected source version/identity")
        if actual.get("name") != idea[who]["name"]:
            errors.append(f"planning: {who}.name differs from selected plan")
    if is_longform(cfg):
        if cfg.get("story_id") != idea.get("story_id") or cfg.get("story_plan") != idea.get("story_plan"):
            errors.append("planning: story identity or reviewed narrative changed without synchronizing the idea")
    elif planning.get("conditions") != idea.get("conditions"):
        errors.append("planning: conditions changed without synchronizing the idea")
    if not is_longform(cfg) and cfg.get("hook") != idea.get("hook"):
        errors.append("planning: hook changed without synchronizing the idea")
    for filename, field in (("title.txt", "title"), ("pinned_comment.txt", "pinned_comment")):
        path = base / filename
        if not path.is_file() or path.read_text(encoding="utf-8-sig").strip() != idea.get(field, "").strip():
            errors.append(f"planning: {filename} differs from current idea")


def validate_sources(cfg, base, signoffs, errors):
    source_data = _read(base, "source_records.json", errors)
    try:
        source_text = (base / "sources.md").read_text(encoding="utf-8-sig")
    except OSError:
        source_text = ""
        errors.append("sources.md is missing")
    records = source_data.get("records", [])
    if not isinstance(records, list):
        errors.append("source_records.records must be a list")
        records = []
    indexed = {}
    for record in records:
        if not isinstance(record, dict):
            errors.append("Invalid source record")
            continue
        key = record.get("id")
        if key in indexed:
            errors.append(f"Duplicate source record: {key}")
        indexed[key] = record
    for segment in speech_segments(cfg):
        if segment["id"] == "cta":
            continue
        record = indexed.get(segment["id"], {})
        if record.get("text") != segment["spoken_text"]:
            errors.append(f"source record missing/stale: {segment['id']}")
        if record.get("kind") not in {"history", "narrative", "hypothetical"}:
            errors.append(f"source kind must distinguish fact/fiction/condition: {segment['id']}")
        fields = ("summary",) if is_longform(cfg) and record.get("kind") == "hypothetical" else ("work", "locator", "url", "checked_at", "summary")
        for field in fields:
            if not _nonempty(record.get(field)):
                errors.append(f"{segment['id']}: source {field} required")
        for item in (segment["id"], segment["spoken_text"], record.get("url", "")):
            if item and item not in source_text:
                errors.append(f"sources.md does not contain current {segment['id']} evidence")
                break
    review = signoffs.get("script", {})
    _observed(review, "script", errors)
    if review.get("script_hash") != script_hash(cfg):
        errors.append("script: stale script review")
    _hash_matches(base, "sources.md", review.get("sources_sha256"), errors, "script sources")
    _hash_matches(base, "source_records.json", review.get("source_records_sha256"), errors, "script source records")


def validate_image_reviews(base, errors, cfg=None):
    try:
        generations = generation_records(base)
    except (OSError, ValueError, TypeError) as exc:
        generations = []
        errors.append("Invalid image generation evidence: " + str(exc))
    document = _read(base, "image_review.json", errors)
    _read(base, "character_bible.json", errors)
    _read(base, "storyboard.json", errors)
    records = document.get("images", [])
    if not isinstance(records, list):
        records = []
        errors.append("image_review.images must be a list")
    indexed = {}
    for record in records:
        if not isinstance(record, dict):
            errors.append("Invalid image review record")
            continue
        if record.get("file") in indexed:
            errors.append(f"Duplicate review entry: {record.get('file')}")
        indexed[record.get("file")] = record
    for name in image_names(cfg or {}):
        review = indexed.get(name, {})
        errors.extend(contract_errors(base, name, review, latest_generation(generations, name)))
        if review.get("status") != "PASS":
            errors.append(f"{name}: visual review is {review.get('status', 'UNREVIEWED')}")
        if not review.get("full_observations") or not review.get("detail_observations") or not review.get("reviewed_at"):
            errors.append(f"{name}: actual full-size and detail observations required")
        if review.get("issues") != []:
            errors.append(f"{name}: unresolved image issues or missing issues list")
        if cfg and cfg.get('production_profile') in (SHOWDOWN_PROFILE, DETAIL_PROFILE) and name == 'cliffhanger.png':
            if not _nonempty(review.get('cliffhanger_observations')):
                errors.append('cliffhanger.png: observations of both strengths and the unresolved outcome required')
        if cfg and is_longform(cfg) and name == cfg.get("thumb"):
            if not _nonempty(review.get("small_size_observations")):
                errors.append(name + ": actual small-list thumbnail legibility observations required")
        version = review.get("generation_version")
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 4:
            errors.append(f"{name}: generation_version must respect initial + 3 revisions")
        _hash_matches(base, name, review.get("sha256"), errors)
        _hash_matches(base, "character_bible.json", review.get("character_bible_sha256"), errors, name + " character reference")
        _hash_matches(base, "storyboard.json", review.get("storyboard_sha256"), errors, name + " storyboard")
        required = []
        if cfg and is_longform(cfg):
            portraits = {cfg.get(who, {}).get("image") for who in ("a", "b")}
            participants = set()
            if name not in portraits:
                if name == cfg.get("thumb"):
                    participants.update(("a", "b"))
                for cut in cfg.get("cuts", []):
                    if cut.get("image") == name:
                        participants.update(cut.get("participants", ["a", "b"]))
                        if cut.get("participants") == [] and not cut.get("reference_reason"):
                            errors.append(name + ": context/prop-only cut needs reference_reason")
                required = [cfg.get(who, {}).get("image") for who in participants if cfg.get(who, {}).get("image")]
        elif name not in ("a.png", "b.png"):
            if name in ("a_1.png", "a_2.png"):
                required = ["a.png"]
            elif name in ("b_1.png", "b_2.png"):
                required = ["b.png"]
            else:
                required = ["a.png", "b.png"]
            if name == "b_react.png":
                required.append("a_atk.png")
            elif name == "a_react.png":
                required.append("b_atk.png")
        references = review.get("reference_hashes", {})
        for ref in required:
            _hash_matches(base, ref, references.get(ref), errors, name + " reference " + ref)


def validate_video_specs(path, audio_mode, errors, cfg=None):
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        probe = json.loads(result.stdout)
        video = next(x for x in probe["streams"] if x["codec_type"] == "video")
        spec = video_spec(cfg or {})
        if (video.get("width"), video.get("height"), video.get("codec_name"), video.get("pix_fmt")) != (spec["width"], spec["height"], "h264", "yuv420p"):
            errors.append(f"Final video must be {spec['width']}x{spec['height']} H.264 yuv420p")
        if Fraction(video.get("avg_frame_rate", "0/1")) != 30:
            errors.append("Final video must be 30 fps")
        duration = float(probe["format"]["duration"])
        minimum, maximum = duration_limits(cfg or {})
        if not minimum <= duration <= maximum:
            errors.append(f"Final duration outside {minimum}~{maximum} seconds: {duration}")
        audio = [x for x in probe["streams"] if x["codec_type"] == "audio"]
        if audio_mode == "silent":
            if audio:
                errors.append("Explicit silent output must contain no audio stream")
        elif not audio or audio[0].get("codec_name") != "aac":
            errors.append("Voice output requires AAC audio")
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError, ZeroDivisionError) as exc:
        errors.append("Final MP4 technical probe failed: " + str(exc))


def validate_quality(episode_path):
    episode_path = Path(episode_path).resolve()
    base = episode_path.parent
    errors = validate_episode(episode_path)
    if errors:
        return errors
    cfg = load_json(episode_path)
    signoffs = _read(base, "qa_signoffs.json", errors)
    validate_planning(cfg, base, errors)
    validate_sources(cfg, base, signoffs, errors)
    validate_image_reviews(base, errors, cfg)
    metadata = signoffs.get("metadata", {})
    _observed(metadata, "metadata", errors)
    for name in METADATA_FILES:
        _hash_matches(base, name, metadata.get("hashes", {}).get(name), errors, "metadata " + name)
    timeline = _read(base, "timeline.json", errors)
    if is_longform(cfg):
        validate_longform_metadata(cfg, base, timeline, errors)
    validate_subtitles(base, timeline, errors)
    audio_review = _read(base, "audio_review.json", errors)
    try:
        from make_voice import validate_voice
        errors.extend("voice: " + str(e) for e in validate_voice(cfg, base))
    except (OSError, ValueError, RuntimeError, KeyError, ImportError) as exc:
        errors.append("voice validation failed: " + str(exc))
    silent = timeline.get("audio_mode") == "silent"
    if is_longform(cfg):
        validate_longform_audio(base, cfg, timeline, audio_review, signoffs, errors)
    elif silent:
        if audio_review.get("status") != "N/A":
            errors.append("Explicit silent mode requires N/A audio review")
    elif audio_review.get("reviewer_kind") == "local_audio_model":
        errors.extend(validate_audio_model_review(base, timeline, audio_review))
    else:
        if audio_review.get("status") != "PASS":
            errors.append("AUDIO_REVIEW_REQUIRED: actual pronunciation/intonation listening has not passed")
        if audio_review.get("listened") is not True or not audio_review.get("observations"):
            errors.append("Actual listening and specific audio observations are required")
    if audio_review.get("input_hash") != timeline.get("input_hash"):
        errors.append("audio review: stale input hash")
    if not silent:
        _hash_matches(base, timeline.get("pcm_path", "voice.wav"), audio_review.get("pcm_sha256"), errors, "audio review PCM")
    try:
        from make_vs import validate_timeline
        errors.extend("timeline: " + str(e) for e in validate_timeline(cfg, timeline, base, silent=silent))
    except (OSError, ValueError, RuntimeError, KeyError, ImportError, TypeError) as exc:
        errors.append("timeline validation failed: " + str(exc))
    technical = _read(base, "out/technical_qa.json", errors)
    if technical.get("status") != "PASS" or technical.get("passed") is not True:
        errors.append("Final video technical QA has not passed")
    _hash_matches(base, "out/video.mp4", technical.get("video_sha256"), errors, "technical video")
    _hash_matches(base, "timeline.json", technical.get("timeline_sha256"), errors, "technical timeline")
    if technical.get("script_hash") != script_hash(cfg):
        errors.append("technical video: stale script hash")
    try:
        import make_vs
        if technical.get("renderer_sha256") != sha256_file(make_vs.__file__):
            errors.append("technical video: renderer changed since render")
        if technical.get("settings_hash") != object_hash(make_vs.settings_for(base / "render_settings.json" if (base / "render_settings.json").is_file() else make_vs.SETTINGS_PATH, cfg=cfg)):
            errors.append("technical video: render settings changed since render")
    except (OSError, ValueError) as exc:
        errors.append("Cannot verify renderer/settings: " + str(exc))
    if technical.get("voice_input_hash") != timeline.get("input_hash") or technical.get("pcm_sha256") != timeline.get("pcm_sha256"):
        errors.append("technical video: current voice input does not match rendered audio")
    if cfg.get("bgm") and not silent:
        rights = cfg.get("bgm_rights", {})
        if rights.get("verified") is not True or not rights.get("source") or not rights.get("license"):
            errors.append("Current BGM rights have not been verified")
        _hash_matches(base, cfg["bgm"], technical.get("bgm_sha256"), errors, "rendered BGM")
    elif technical.get("bgm_sha256"):
        errors.append("technical video: rendered BGM differs from the current no-BGM mode")
    for name in image_names(cfg):
        _hash_matches(base, name, technical.get("image_hashes", {}).get(name), errors, "rendered image " + name)
    if not technical.get("checks", {}).get("full_decode"):
        errors.append("Full final MP4 decode verification missing")
    video_review = signoffs.get("video", {})
    _observed(video_review, "video", errors)
    _hash_matches(base, "out/video.mp4", video_review.get("video_sha256"), errors, "visual video")
    _hash_matches(base, "timeline.json", video_review.get("timeline_sha256"), errors, "visual timeline")
    _hash_matches(base, "image_review.json", video_review.get("image_review_sha256"), errors, "visual source reviews")
    review_frames = _read(base, "out/review_frames.json", errors)
    _hash_matches(base, "out/review_frames.json", technical.get("review_frames_sha256"), errors, "technical review frame index")
    _hash_matches(base, "out/video.mp4", review_frames.get("video_sha256"), errors, "review frames video")
    _hash_matches(base, "out/_review.png", review_frames.get("contact_sheet_sha256"), errors, "review contact sheet")
    if not review_frames.get("frames"):
        errors.append("Full-resolution extracted MP4 review frames missing")
    try:
        from make_vs import review_samples
        expected_frames = {item["frame"] for item in review_samples(timeline, make_vs.settings_for(base / "render_settings.json" if (base / "render_settings.json").is_file() else make_vs.SETTINGS_PATH, cfg=cfg))}
        actual_frames = {item.get("frame") for item in review_frames.get("frames", [])}
        if not expected_frames.issubset(actual_frames):
            errors.append("Extracted review frames do not cover every required transition/caption/extreme")
    except (KeyError, ValueError, TypeError) as exc:
        errors.append("Cannot verify review frame coverage: " + str(exc))
    for frame in review_frames.get("frames", []):
        _hash_matches(base / "out", frame.get("file"), frame.get("sha256"), errors, "extracted frame")
        try:
            with Image.open(safe_child(base / "out", frame.get("file"))) as image:
                spec = video_spec(cfg)
                if image.size != (spec["width"], spec["height"]):
                    errors.append(f"Review frame must preserve native {spec['width']}x{spec['height']} resolution: " + frame["file"])
        except (OSError, ValueError, TypeError) as exc:
            errors.append("Cannot inspect review frame: " + str(exc))
    if is_longform(cfg):
        validate_longform_frames(cfg, base, timeline, video_review, review_frames, errors)
    if not (base / "qa_report.md").is_file():
        errors.append("qa_report.md missing")
    validate_video_specs(base / "out/video.mp4", timeline.get("audio_mode"), errors, cfg)
    return errors




def longform_chapters(cfg, timeline):
    """Optional chapter anchors allow many scenes per chapter; all times are measured."""
    segments = timeline.get("segments", [])
    configured = cfg.get("chapters")
    anchors = []
    if configured is not None:
        if not isinstance(configured, list) or not configured:
            raise ValueError("chapters must be a nonempty list when configured")
        for chapter in configured:
            keys = [key for key in ("cue_id", "cut_id", "scene_id") if chapter.get(key)]
            if len(keys) != 1 or not _nonempty(chapter.get("title")):
                raise ValueError("Each chapter needs a meaningful title and exactly one cue/cut/scene anchor")
            key = keys[0]
            field = "id" if key == "cue_id" else key
            match = next((segment for segment in segments if segment.get(field) == chapter[key]), None)
            if match is None:
                raise ValueError("Chapter anchor is absent from actual final timeline")
            anchors.append((chapter["title"], match.get("start_frame")))
    else:
        scenes = {scene["id"]: scene for scene in cfg.get("scenes", [])}
        previous = None
        for segment in segments:
            scene_id = segment.get("scene_id")
            if scene_id == previous or segment.get("id") == "cta":
                continue
            previous = scene_id
            scene = scenes.get(scene_id, {})
            title = scene.get("title") or scene.get("objective")
            if not _nonempty(title):
                raise ValueError("Chapter needs a meaningful scene title or objective")
            anchors.append((title, segment.get("start_frame")))
    rows, previous_second = [], -1
    for title, frame in anchors:
        if type(frame) is not int or frame < 0:
            raise ValueError("Chapter requires an actual nonnegative global start_frame")
        seconds = frame // 30
        if seconds <= previous_second or "\n" in title or "\r" in title:
            raise ValueError("Chapters need ordered distinct actual timestamps and one-line titles")
        previous_second = seconds
        rows.append(f"{seconds // 60:02d}:{seconds % 60:02d} {title}")
    return rows


def linked_short_comment(cfg):
    link = cfg.get("planning", {}).get("related_short")
    if not link:
        return None
    relationship = link.get("relationship")
    if relationship == "continuation":
        return "이 대결의 전체 이야기는 롱폼에서 확인하세요. [롱폼 영상 링크]"
    if relationship == "new_event":
        return "이 두 인물의 새로운 이야기는 롱폼에서 확인하세요. [롱폼 영상 링크]"
    raise ValueError("Linked Shorts relationship must be continuation or new_event")



def linked_short_comment_document(cfg):
    comment = linked_short_comment(cfg)
    if comment is None:
        return None
    linked_id = cfg["planning"]["related_short"]["job_id"]
    return "DRAFT — 게시/고정 미실행\nShorts job_id: " + linked_id + "\n\n" + comment + "\n"


def validate_longform_metadata(cfg, base, timeline, errors):
    try:
        expected = longform_chapters(cfg, timeline)
        description = (base / "description.txt").read_text(encoding="utf-8-sig")
        actual = [line.strip() for line in description.splitlines() if re.match(r"^\d{2,}:\d{2}\s", line.strip())]
        if not expected or actual != expected:
            errors.append("longform metadata: chapters must use actual final scene boundaries in timeline order")
    except (OSError, ValueError, KeyError) as exc:
        errors.append("longform chapter validation failed: " + str(exc))
    link = cfg.get("planning", {}).get("related_short")
    if not link:
        return
    try:
        linked_short_comment(cfg)
        if not link.get("job_id") or not link.get("compatibility_notes") or not link.get("question_decision"):
            raise ValueError("related_short needs job_id, edition/continuity review and question decision")
        root = next(p for p in base.parents if (p / "plan_matchups.py").is_file())
        short_path = safe_child(root, link.get("episode_path"))
        short_cfg = load_json(short_path)
        if is_longform(short_cfg) or short_cfg.get("job_id") != link["job_id"]:
            raise ValueError("related_short must identify an actual Shorts job")
        if {short_cfg.get(who, {}).get("name") for who in ("a", "b")} != {cfg.get(who, {}).get("name") for who in ("a", "b")}:
            raise ValueError("related_short must use the same character pair")
        for filename in ("episode.json", "source_records.json", "character_bible.json"):
            _hash_matches(short_path.parent, filename, link.get("reference_hashes", {}).get(filename), errors, "related Shorts reference " + filename)
    except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
        errors.append("longform related Shorts record invalid: " + str(exc))


def audio_timing_hash(timeline):
    """Identity of spoken content and its time placement, excluding visual data."""
    keys = ("id", "group_id", "spoken_text", "tts_text", "start", "end", "start_frame", "end_frame",
            "speech_start", "speech_end", "sample_start", "sample_end", "start_sample", "end_sample")
    return object_hash({"audio_mode": timeline.get("audio_mode"), "sample_rate": timeline.get("sample_rate"),
                        "duration": timeline.get("duration"), "total_frames": timeline.get("total_frames"),
                        "segments": [{key: segment.get(key) for key in keys} for segment in timeline.get("segments", [])]})


def final_audio_sha256(path):
    """Hash actual final audio decoded to canonical stereo 48 kHz signed PCM."""
    import tempfile
    # stderr must not block a long decode; tempfile stays on configured project TEMP.
    with tempfile.TemporaryFile() as error_log:
        process = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                                    "-vn", "-ac", "2", "-ar", "48000", "-f", "s16le", "pipe:1"],
                                   stdout=subprocess.PIPE, stderr=error_log)
        digest, size = hashlib.sha256(), 0
        try:
            while data := process.stdout.read(1024 * 1024):
                digest.update(data)
                size += len(data)
            if process.wait() or not size:
                raise ValueError("Cannot decode complete final audio")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    return digest.hexdigest()


LONGFORM_AUDIO_CHECKS = ("asr_alignment", "starts_and_ends", "group_boundaries", "volume_clipping_silence",
                         "audio_specs", "subtitle_srt_alignment")


def validate_longform_audio(base, cfg, timeline, review, signoffs, errors):
    """Technical evidence and explicit user full listening are independent gates."""
    if timeline.get("audio_mode") == "silent":
        errors.append("longform: narration cannot be bypassed with silent mode")
        return
    technical = review.get("technical", {})
    if technical.get("status") != "PASS":
        errors.append("LONGFORM_AUDIO_TECHNICAL_REQUIRED: required technical checks have not passed")
    _hash_matches(base, "out/video.mp4", technical.get("video_sha256"), errors, "longform audio technical video")
    _hash_matches(base, "timeline.json", technical.get("timeline_sha256"), errors, "longform audio technical timeline")
    actual_timing = audio_timing_hash(timeline)
    if technical.get("audio_timing_hash") != actual_timing:
        errors.append("longform audio: technical timing identity is stale")
    try:
        actual_audio = final_audio_sha256(base / "out/video.mp4")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        errors.append("longform audio: final PCM verification failed: " + str(exc))
        actual_audio = None
    if not actual_audio or technical.get("final_audio_sha256") != actual_audio:
        errors.append("longform audio: technical final audio identity is stale")
    for key in LONGFORM_AUDIO_CHECKS:
        item = technical.get("checks", {}).get(key, {})
        if item.get("status") != "PASS" or not item.get("observations") or item.get("issues") != []:
            errors.append("longform audio technical check not reviewed/passed: " + key)
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list) or not evidence:
            errors.append("longform audio technical evidence missing: " + key)
        else:
            for entry in evidence:
                if not isinstance(entry, dict):
                    errors.append("longform audio technical evidence invalid: " + key)
                    continue
                _hash_matches(base, entry.get("file"), entry.get("sha256"), errors, "audio diagnostic " + key)
    # ASR must cover the actual final MP4 and all current cues in sequence.
    asr_file = technical.get("asr_file", "audio_analysis.json")
    analysis = _read(base, asr_file, errors)
    _hash_matches(base, asr_file, technical.get("asr_sha256"), errors, "longform ASR")
    direct_asr = (analysis.get("video_sha256") == technical.get("video_sha256")
                  and analysis.get("timeline_sha256") == technical.get("timeline_sha256"))
    if not direct_asr:
        # Keep the actual old ASR output unchanged. Visual-only reuse proves the
        # old source audio and time placement, rather than relabeling its hashes.
        reuse = technical.get("asr_reuse", {})
        valid_reuse = (actual_audio is not None and reuse.get("final_audio_sha256") == actual_audio
                       and reuse.get("audio_timing_hash") == actual_timing
                       and reuse.get("source_video_sha256") == analysis.get("video_sha256"))
        source_video_ok = _hash_matches(base, reuse.get("source_video_path"), analysis.get("video_sha256"), errors, "ASR predecessor MP4")
        source_timeline_ok = _hash_matches(base, reuse.get("source_timeline_path"), analysis.get("timeline_sha256"), errors, "ASR predecessor timeline")
        if source_video_ok and source_timeline_ok:
            try:
                valid_reuse = (valid_reuse and final_audio_sha256(safe_child(base, reuse["source_video_path"])) == actual_audio
                               and audio_timing_hash(load_json(safe_child(base, reuse["source_timeline_path"]))) == actual_timing)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                errors.append("longform ASR predecessor verification failed: " + str(exc))
                valid_reuse = False
        else:
            valid_reuse = False
        if not valid_reuse:
            errors.append("longform ASR: current analysis or verified identical final audio/timing reuse required")
    if (analysis.get("status") != "ASR_ANALYSIS_COMPLETE_AUDIO_REVIEW_REQUIRED"
            or not analysis.get("full_transcript") or not analysis.get("full_segments")
            or not analysis.get("acoustic_diagnostics")):
        errors.append("longform ASR: complete actual final audio analysis required")
    comparisons = analysis.get("sentence_comparisons", [])
    segments = timeline.get("segments", [])
    if [x.get("id") for x in comparisons] != [x.get("id") for x in segments]:
        errors.append("longform ASR: missing/duplicated/reordered cue comparisons")
    resolutions = technical.get("asr_discrepancy_reviews", {})
    for expected, comparison in zip(segments, comparisons):
        if comparison.get("expected_for_comparison_only") != (expected.get("tts_text") or expected.get("spoken_text")):
            errors.append("longform ASR: stale approved text for " + str(expected.get("id")))
        if comparison.get("normalized_edit_distance") != 0:
            decision = resolutions.get(comparison.get("id"), {})
            if decision.get("status") != "RESOLVED" or not decision.get("observations") or not decision.get("evidence"):
                errors.append("longform ASR: unresolved suspected omission/misreading " + str(comparison.get("id")))
    approval = review.get("user_approval", {})
    valid = (approval.get("status") == "APPROVED" and approval.get("reviewer_kind") == "user"
             and approval.get("scope") == "full_final_mp4" and approval.get("full_listened") is True
             and bool(approval.get("statement")) and bool(approval.get("approved_at"))
             and bool(re.fullmatch(r"[0-9a-f]{64}", str(approval.get("video_sha256", "")))))
    if not valid:
        errors.append("LONGFORM_LISTENING_PENDING: user approval after listening to the full final MP4 is required")
        return
    if not actual_audio or approval.get("final_audio_sha256") != actual_audio or approval.get("audio_timing_hash") != actual_timing:
        errors.append("LONGFORM_LISTENING_STALE: final sound or spoken time placement changed after approval")
        return
    current_video = technical.get("video_sha256")
    if approval.get("video_sha256") != current_video:
        # Preserve the approved predecessor as existing revision evidence.
        previous = approval.get("approved_video_path")
        if _hash_matches(base, previous, approval.get("video_sha256"), errors, "approved predecessor MP4"):
            try:
                if final_audio_sha256(safe_child(base, previous)) != actual_audio:
                    errors.append("LONGFORM_LISTENING_STALE: approved predecessor audio differs")
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                errors.append("Cannot verify approved predecessor audio: " + str(exc))
    binding = signoffs.get("audio", {})
    if (binding.get("user_approval_sha256") != object_hash(approval)
            or binding.get("video_sha256") != current_video
            or binding.get("final_audio_sha256") != actual_audio
            or binding.get("audio_timing_hash") != actual_timing):
        errors.append("longform audio: current qa_signoffs.audio approval/version binding required")


def validate_longform_frames(cfg, base, timeline, video_review, index, errors):
    """Actual full-size observations cover cuts and transition samples, not a montage."""
    frames = {item.get("frame"): item for item in index.get("frames", [])}
    observations = video_review.get("frame_observations", [])
    if not isinstance(observations, list):
        errors.append("longform video: frame_observations must be a list")
        return
    observed = {}
    for item in observations:
        if not isinstance(item, dict):
            errors.append("longform video: invalid frame observation")
            continue
        number = item.get("frame")
        if number in observed:
            errors.append("longform video: duplicate frame observation")
        observed[number] = item
        actual = frames.get(number, {})
        if (item.get("status") != "PASS" or item.get("issues") != [] or not item.get("observations")
                or item.get("full_resolution_viewed") is not True
                or item.get("sha256") != actual.get("sha256") or not actual.get("sha256")):
            errors.append("longform video: unreviewed/stale/unresolved actual frame " + str(number))
    for cut in cfg.get("cuts", []):
        cut_id = cut.get("id", cut.get("cut_id"))
        matching = [seg for seg in timeline.get("segments", []) if seg.get("cut_id") == cut_id]
        if not matching or not any(seg.get("start_frame", 0) <= number < seg.get("end_frame", 0)
                                   for seg in matching for number in observed):
            errors.append("longform video: no actual composed frame observation for cut " + str(cut_id))
    for number, item in frames.items():
        if any("사진 교차전환" in label for label in item.get("labels", [])) and number not in observed:
            errors.append("longform video: transition sample not visually reviewed " + str(number))


def validate_completion(episode_path):
    episode_path = Path(episode_path).resolve()
    from release_checks import release_errors
    errors = release_errors(episode_path, validate_quality(episode_path))
    base = episode_path.parent
    manifest = _read(base, "manifest.json", errors)
    delivery = manifest.get("delivery", {})
    if delivery.get("verified") is not True or delivery.get("classification") != "COMPLETE":
        errors.append("Final delivery has not been verified as COMPLETE")
        return errors
    target = Path(delivery.get("path", ""))
    root_key = {'desktop_title': 'desktop_root', 'title_root': 'output_root'}.get(delivery.get('layout'))
    title_layout = (root_key is not None
                    and target.parent == Path(delivery.get(root_key) or '')
                    and target.name == (base / 'title.txt').read_text(encoding='utf-8-sig').strip())
    if not target.is_absolute() or (target.parent.name != "완성본" and not title_layout):
        errors.append("COMPLETE requires a resolved category or exact-title delivery folder")
        return errors
    delivered = _read(target, "manifest.json", errors)
    cfg = load_json(episode_path)
    if is_longform(cfg) and (delivered.get("format") != "longform" or delivered.get("story_id") != cfg.get("story_id")):
        errors.append("Delivered format/story identity differs from working longform")
    if not manifest.get("job_id") or delivered.get("job_id") != manifest.get("job_id"):
        errors.append("Delivered job ID differs from working job")
    _hash_matches(target, "manifest.json", delivery.get("manifest_sha256"), errors, "delivery manifest")
    files = delivered.get("files", {})
    recorded_episode = delivered.get("working_episode")
    try:
        same_episode = (isinstance(recorded_episode, str) and bool(recorded_episode.strip())
                        and Path(recorded_episode).is_absolute() and Path(recorded_episode).is_file()
                        and Path(recorded_episode).resolve(strict=True) == episode_path.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        same_episode = False
    if delivered.get("status") != "COMPLETE" or not same_episode:
        errors.append("Delivered manifest is not COMPLETE for this working episode")
    for name in DELIVERY_FILES:
        _hash_matches(target, name, files.get(name), errors, "delivered " + name)
        current = delivery_source(base, name)
        _hash_matches(base, current, files.get(name), errors, "delivery current source " + name)
    for name, current in DELIVERY_OPTIONAL_SOURCES.items():
        if name in files:
            _hash_matches(target, name, files[name], errors, "delivered optional " + name)
            _hash_matches(base, current, files[name], errors, "delivery current optional " + name)
    if is_longform(cfg):
        expected_comment = linked_short_comment_document(cfg)
        if expected_comment is not None:
            _hash_matches(target, "linked_short_comment.txt", files.get("linked_short_comment.txt"), errors, "delivered linked Shorts draft")
            _hash_matches(base, "linked_short_comment.txt", files.get("linked_short_comment.txt"), errors, "current linked Shorts draft")
            if not (base / "linked_short_comment.txt").is_file() or (base / "linked_short_comment.txt").read_text(encoding="utf-8-sig") != expected_comment:
                errors.append("Linked Shorts comment draft does not match the current relationship/job")
        elif "linked_short_comment.txt" in files:
            errors.append("Stale unrelated Shorts comment must not be included in the current delivery")
    review_files = [name for name in files if name.startswith("review/") and name.endswith(".png")]
    if not any(name != "review/_review.png" for name in review_files):
        errors.append("Delivered full-resolution review frames missing")
    for name in review_files:
        _hash_matches(target, name, files[name], errors, "delivered " + name)
        current = "out/_review.png" if name == "review/_review.png" else "out/" + name[len("review/"):]
        _hash_matches(base, current, files[name], errors, "delivery current review " + name)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--text-only", action="store_true")
    mode.add_argument("--quality", action="store_true")
    mode.add_argument("--completion", action="store_true")
    mode.add_argument("--subtitles", action="store_true", help="SRT와 현재 시간표·실제 영상 자막 검수 증거 확인")
    mode.add_argument("--longform-timing-report", action="store_true", help="기존 시간표와 최종 MP4의 시간만 보고; 품질 PASS 아님")
    parser.add_argument("--combat-range", action="append", default=[], metavar="START_CUE:END_CUE",
                        help="검토자가 선택한 전투 cue 포괄 구간; 여러 번 지정 가능, 시간 보고 모드 전용")
    args = parser.parse_args()
    if args.combat_range and not args.longform_timing_report:
        parser.error("--combat-range requires --longform-timing-report")
    if args.longform_timing_report:
        from longform_reporting import timing_report_from_episode
        try:
            report = timing_report_from_episode(args.episode, args.combat_range)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.subtitles:
        base = Path(args.episode).resolve().parent
        errors = []
        timeline = _read(base, "timeline.json", errors)
        validate_subtitles(base, timeline, errors)
    elif args.completion:
        errors = validate_completion(args.episode)
    elif args.quality:
        errors = validate_quality(args.episode)
    else:
        errors = validate_episode(args.episode, args.text_only)
    try:
        cfg = load_json(args.episode)
    except (OSError, ValueError):
        cfg = None
    for warning in text_warnings(cfg):
        print('경고 (내용 검토):', warning)
    if isinstance(cfg, dict) and cfg.get('format') == 'longform':
        from longform_reporting import longform_guidance
        for key, document in longform_guidance(Path(__file__).parent)['documents'].items():
            print('롱폼 내용 검토 지침:', document['path'], '' if document['available'] else '(파일 없음)')
        print('전체·전투 예상 시간과 인과·원전 검토는 해당 지침으로 확인합니다. 약 3분은 고정 합격 기준이 아닙니다.')
    if isinstance(cfg, dict) and cfg.get('production_profile') == DETAIL_PROFILE:
        print('추상 표현 검사는 제한된 문자열 경고입니다. 경고 유무는 내용의 합격·불합격 판정이 아닙니다.')
    if errors:
        print(f"고칠 곳 {len(errors)}개:")
        for error in errors:
            print("  -", error)
        raise SystemExit(1)
    if args.subtitles:
        print("통과 (SRT 시간표 + 현재 영상 자막 검수 기록)")
        return
    print("통과 (대본만 검사)" if args.text_only else "통과 (대본 + 그림)")
    if args.quality or args.completion:
        if args.completion and (Path(args.episode).parent / 'user_release_acceptance.json').exists():
            print("사용자 수락 범위 + 수정본 기술·자막 검사 및 전달 검증 통과 (자체 청취 PASS와 구분)")
        else:
            print("필수 검수 기록·현재 파일 해시·출력 검사 통과" + (" 및 전달 검증 통과" if args.completion else ""))
    else:
        print("형식 검사이며 사실성·실제 시청각 검수를 대신하지 않습니다.")


if __name__ == "__main__":
    main()
