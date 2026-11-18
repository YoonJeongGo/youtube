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
from episode_core import DETAIL_PROFILE, IMAGE_NAMES, SCHEMA_VERSION, SHOWDOWN_PROFILE, STRUCTURED_PROFILES, additional_edge_specs, duration_limits, image_names, load_json, narration_groups, nfc, object_hash, safe_child, script_hash, sha256_file, speech_segments, validate_detail_options
from subtitle_checks import validate_subtitles
from audio_review_checks import validate_audio_model_review

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
    if name == "thumbnail.png" and (Path(base) / "upload_thumbnail.png").is_file():
        review = load_json(Path(base) / "upload_thumbnail_review.json")
        if (review.get("status") != "PASS"
                or review.get("sha256") != sha256_file(Path(base) / "upload_thumbnail.png")
                or review.get("source_sha256") != sha256_file(Path(base) / "thumb.png")
                or not review.get("observations")):
            raise ValueError("Upload thumbnail needs a current visual review")
        return "upload_thumbnail.png"
    return DELIVERY_SOURCES[name]


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
    allowed_images = set(IMAGE_NAMES)
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
                f"{where} image must be one of the fixed 11 images or null")
        require(isinstance(beat.get("header", ""), str), f"{where} header must be a string")
        for field in ("display_text", "spoken_text"):
            text = beat.get(field)
            require(_nonempty(text) and re.search(r"[^\W_]", text, re.UNICODE) is not None,
                    f"{where} {field} must contain actual text")
    if beats:
        require(isinstance(beats[0], dict) and beats[0].get("kind") == "hook", "the first cue must be the hook")
        require(isinstance(beats[-1], dict) and beats[-1].get("kind") == "question", "the last story cue must be the question")
    for name, limit in (("hook", 30), ("reason", 30), ("question", 34)):
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
            require(bool(re.search(r"[.?!]$", joined)),
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
    require(cfg.get("schema_version") == SCHEMA_VERSION, "schema_version: 새 제작은 반드시 2여야 해요")
    require(cfg.get("winner") == "none", 'winner: 명시적으로 "none"이어야 해요')
    require("stats" not in cfg and "cta" not in cfg, "stats/cta 필드는 사용할 수 없어요")
    require(cfg.get("thumb") == "thumb.png", "thumb: thumb.png여야 해요")
    profile = cfg.get("production_profile")
    require(profile is None or profile in STRUCTURED_PROFILES, "Unknown production_profile")
    require('additional_edges' not in cfg or profile == SHOWDOWN_PROFILE,
            'additional_edges is only supported by showdown_story_v4')
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
                if image.size != expected:
                    errors.append(f"{name}: 실제 크기 {image.size}, 필수 크기 {expected}")
                pixels = image.convert("RGBA")
                digest = hashlib.sha256(str(pixels.size).encode() + pixels.tobytes()).hexdigest()
                if digest in seen:
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
    root = next((p for p in base.parents if (p / "matchup_ideas.json").is_file()), None)
    if root is None:
        errors.append("matchup_ideas.json cannot be found in the project ancestors")
        return
    pool = _read(root, "matchup_ideas.json", errors)
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
    if planning.get("conditions") != idea.get("conditions"):
        errors.append("planning: conditions changed without synchronizing the idea")
    if cfg.get("hook") != idea.get("hook"):
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
        for field in ("work", "locator", "url", "checked_at", "summary"):
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
        if review.get("status") != "PASS":
            errors.append(f"{name}: visual review is {review.get('status', 'UNREVIEWED')}")
        if not review.get("full_observations") or not review.get("detail_observations") or not review.get("reviewed_at"):
            errors.append(f"{name}: actual full-size and detail observations required")
        if review.get("issues") != []:
            errors.append(f"{name}: unresolved image issues or missing issues list")
        if cfg and cfg.get('production_profile') in (SHOWDOWN_PROFILE, DETAIL_PROFILE) and name == 'cliffhanger.png':
            if not _nonempty(review.get('cliffhanger_observations')):
                errors.append('cliffhanger.png: observations of both strengths and the unresolved outcome required')
        version = review.get("generation_version")
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 4:
            errors.append(f"{name}: generation_version must respect initial + 3 revisions")
        _hash_matches(base, name, review.get("sha256"), errors)
        _hash_matches(base, "character_bible.json", review.get("character_bible_sha256"), errors, name + " character reference")
        _hash_matches(base, "storyboard.json", review.get("storyboard_sha256"), errors, name + " storyboard")
        required = []
        if name not in ("a.png", "b.png"):
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
        if (video.get("width"), video.get("height"), video.get("codec_name"), video.get("pix_fmt")) != (1080, 1920, "h264", "yuv420p"):
            errors.append("Final video must be 1080x1920 H.264 yuv420p")
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
    validate_subtitles(base, timeline, errors)
    audio_review = _read(base, "audio_review.json", errors)
    try:
        from make_voice import validate_voice
        errors.extend("voice: " + str(e) for e in validate_voice(cfg, base))
    except (OSError, ValueError, RuntimeError, KeyError, ImportError) as exc:
        errors.append("voice validation failed: " + str(exc))
    silent = timeline.get("audio_mode") == "silent"
    if silent:
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
        if technical.get("settings_hash") != object_hash(make_vs.settings_for()):
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
        expected_frames = {item["frame"] for item in review_samples(timeline)}
        actual_frames = {item.get("frame") for item in review_frames.get("frames", [])}
        if not expected_frames.issubset(actual_frames):
            errors.append("Extracted review frames do not cover every required transition/caption/extreme")
    except (KeyError, ValueError, TypeError) as exc:
        errors.append("Cannot verify review frame coverage: " + str(exc))
    for frame in review_frames.get("frames", []):
        _hash_matches(base / "out", frame.get("file"), frame.get("sha256"), errors, "extracted frame")
        try:
            with Image.open(safe_child(base / "out", frame.get("file"))) as image:
                if image.size != (1080, 1920):
                    errors.append("Review frame must preserve native 1080x1920 resolution: " + frame["file"])
        except (OSError, ValueError, TypeError) as exc:
            errors.append("Cannot inspect review frame: " + str(exc))
    if not (base / "qa_report.md").is_file():
        errors.append("qa_report.md missing")
    validate_video_specs(base / "out/video.mp4", timeline.get("audio_mode"), errors, cfg)
    return errors


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
    desktop_layout = (delivery.get('layout') == 'desktop_title'
                      and target.parent == Path(delivery.get('desktop_root', ''))
                      and target.name == (base / 'title.txt').read_text(encoding='utf-8-sig').strip())
    if not target.is_absolute() or (target.parent.name != "완성본" and not desktop_layout):
        errors.append("COMPLETE requires the resolved 완성본/<title> delivery folder")
        return errors
    delivered = _read(target, "manifest.json", errors)
    if not manifest.get("job_id") or delivered.get("job_id") != manifest.get("job_id"):
        errors.append("Delivered job ID differs from working job")
    _hash_matches(target, "manifest.json", delivery.get("manifest_sha256"), errors, "delivery manifest")
    files = delivered.get("files", {})
    if delivered.get("status") != "COMPLETE" or delivered.get("working_episode") != str(episode_path):
        errors.append("Delivered manifest is not COMPLETE for this working episode")
    for name in DELIVERY_FILES:
        _hash_matches(target, name, files.get(name), errors, "delivered " + name)
        current = delivery_source(base, name)
        _hash_matches(base, current, files.get(name), errors, "delivery current source " + name)
    for name, current in DELIVERY_OPTIONAL_SOURCES.items():
        if name in files:
            _hash_matches(target, name, files[name], errors, "delivered optional " + name)
            _hash_matches(base, current, files[name], errors, "delivery current optional " + name)
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
    args = parser.parse_args()
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
