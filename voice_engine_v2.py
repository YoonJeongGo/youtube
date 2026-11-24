"""Cue/group Edge TTS and measured, frame-aligned PCM timing.

Use .venv/Scripts/python make_voice.py episode.json. Failed voice generation
never becomes a silent video. --silent is reserved for an explicit silent request.
"""
from __future__ import annotations

import argparse
from array import array
import asyncio
from datetime import datetime, timezone
import hashlib
import html
import importlib.metadata
import inspect
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import wave

from episode_core import (SETUP_PROFILE, STRUCTURED_PROFILES, atomic_json, duration_limits, load_json, narration_groups,
                          object_hash, script_hash, sha256_file, speech_segments, is_longform)

FPS, SAMPLE_RATE, SAMPLE_WIDTH = 30, 48000, 2
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
MAX_GENERATIONS = 3  # Initial generation plus two corrections, persisted per sentence.
VOICE = "ko-KR-InJoonNeural"
TRIM_POLICY = {"version": 1, "method": "exact digital zero only", "absolute_sample_threshold": 0,
               "retain_head_samples": 2400, "retain_tail_samples": 4800, "sample_rate": SAMPLE_RATE}
STORY_TRIM_POLICY = {"version": 1, "method": "absolute low-level outer padding with word guards",
                     "threshold_dbfs": -65, "absolute_sample_threshold": 18,
                     "retain_head_samples": 7200, "retain_tail_samples": 7200,
                     "sample_rate": SAMPLE_RATE, "internal_audio_removed": False}
SETTINGS = ("voice", "rate", "pitch", "volume", "pause_before", "pause_after", "pronunciation", "pronunciation_notes")
GROUP_SETTINGS = ("voice", "rate", "pitch", "volume", "pause_before", "pause_after")


class VoiceError(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def audio_name(segment_id):
    return "v_" + re.sub(r"[^a-zA-Z0-9_-]", "_", segment_id) + ".mp3"


def engine_info():
    try:
        import edge_tts
        version = importlib.metadata.version("edge-tts")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise VoiceError("edge-tts가 없어요. 프로젝트 .venv의 Python과 requirements.txt를 확인해 주세요.") from exc
    if not {"text", "voice", "rate", "pitch", "volume", "boundary"}.issubset(inspect.signature(edge_tts.Communicate).parameters):
        raise VoiceError("설치된 edge-tts가 필수 음성 설정과 경계 정보를 지원하지 않아요.")
    dependencies = {name: importlib.metadata.version(name) for name in ("aiohttp", "certifi")}
    return {"name": "edge-tts", "version": version, "dependencies": dependencies,
            "model": "Microsoft Edge online TTS; remote model revision is not exposed",
            "capabilities": ["voice", "rate", "pitch", "volume", "WordBoundary", "SentenceBoundary"],
            "source_sha256": sha256_file(inspect.getfile(edge_tts.Communicate))}


def decoder_info():
    exe = shutil.which("ffmpeg")
    if not exe:
        raise VoiceError("ffmpeg를 찾을 수 없어 음성을 디코딩할 수 없어요.")
    result = subprocess.run([exe, "-version"], capture_output=True, text=True, check=True, timeout=15)
    return {"name": "ffmpeg", "version": result.stdout.splitlines()[0]}


def defaults(kind):
    rates = {"hook": "+5%", "intro": "+9%", "clash": "+3%", "edge": "+7%", "reason": "+6%", "question": "+3%", "cta": "+6%", "story": "+6%"}
    return {"voice": VOICE, "rate": rates[kind], "pitch": "+1Hz" if kind in ("hook", "question") else "+0Hz",
            "volume": "+2%" if kind == "edge" else "+0%", "pause_before": 0.08 if kind == "clash" else 0.0,
            "pause_after": 0.22 if kind == "cta" else 0.10, "pronunciation": {}, "pronunciation_notes": ""}


def check_entry(entry):
    for field, pattern in (("rate", r"[+-]\d+%"), ("pitch", r"[+-]\d+Hz"), ("volume", r"[+-]\d+%")):
        if not isinstance(entry[field], str) or not re.fullmatch(pattern, entry[field]):
            raise VoiceError(f"{entry['id']}: {field} 설정 형식이 잘못됐어요.")
    for field in ("pause_before", "pause_after"):
        value = entry[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 2:
            raise VoiceError(f"{entry['id']}: {field}는 0~2초여야 해요.")
    if not isinstance(entry["voice"], str) or not entry["voice"].strip():
        raise VoiceError(f"{entry['id']}: 목소리가 비어 있어요.")
    if not isinstance(entry["spoken_text"], str) or not entry["spoken_text"].strip():
        raise VoiceError(f"{entry['id']}: 낭독 문장이 비어 있어요.")
    if re.search(r"\[[^\]]*\]|<[^>]*>", entry["spoken_text"]):
        raise VoiceError(f"{entry['id']}: 낭독문에 연출 지시나 SSML을 넣을 수 없어요.")


def build_plan(cfg, base, *, silent=False):
    """Retain voice choices but clear spoken overrides when source text changes.

    Edit settings, pronunciation mappings or spoken_override in voice_plan.json.
    Direct cue spoken_text edits are also captured as explicit overrides. In
    grouped mode, narration_units owns prosody; segments owns cue wording.
    """
    path = Path(base) / "voice_plan.json"
    old = load_json(path) if path.exists() else {}
    previous = {entry["id"]: entry for entry in old.get("segments", [])}
    if len(previous) != len(old.get("segments", [])):
        raise VoiceError("voice_plan.json에 중복 문장 ID가 있어요.")
    entries = []
    for source in speech_segments(cfg):
        kind = source['kind']
        if is_longform(cfg) and kind not in ('hook', 'intro', 'clash', 'edge', 'reason', 'question', 'cta', 'story'):
            kind = 'story'
        entry = {"id": source["id"], **defaults(kind)}
        prior = previous.get(source["id"], {})
        entry.update({key: prior[key] for key in SETTINGS if key in prior})
        same_source = (prior.get("source_spoken_text") == source["spoken_text"] and
                       (is_longform(cfg) or prior.get("source_display_text") == source["display_text"]))
        override = prior.get("spoken_override") if same_source else None
        if same_source and prior.get("spoken_text") != prior.get("_generated_spoken_text", prior.get("spoken_text")):
            override = prior["spoken_text"]
        text = override if override is not None else source["spoken_text"]
        mapping = entry["pronunciation"]
        if not isinstance(mapping, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in mapping.items()):
            raise VoiceError(f"{source['id']}: pronunciation은 원문→발음 문자열 사전이어야 해요.")
        for written, spoken in sorted(mapping.items(), key=lambda pair: len(pair[0]), reverse=True):
            text = text.replace(written, spoken)
        entry.update(display_text=source["display_text"], source_display_text=source["display_text"],
                     source_spoken_text=source["spoken_text"], spoken_text=text, spoken_override=override,
                     _generated_spoken_text=text)
        check_entry(entry)
        entries.append(entry)
    plan = {"schema_version": 2, "pipeline_version": 2, "producer_sha256": sha256_file(__file__),
            "script_hash": script_hash(cfg), "audio_mode": "silent" if silent else "voice", "fps": FPS,
            "pcm_format": {"sample_rate": SAMPLE_RATE, "channels": 1, "sample_width": SAMPLE_WIDTH},
            "pcm_trim_policy": STORY_TRIM_POLICY if ('story_beats' in cfg or cfg.get('production_profile') in STRUCTURED_PROFILES) else TRIM_POLICY,
            "engine": None if silent else engine_info(), "decoder": None if silent else decoder_info(), "segments": entries}
    if 'story_beats' in cfg or 'narration_groups' in cfg or cfg.get('production_profile') in STRUCTURED_PROFILES:
        plan['narration_units'] = build_narration_units(cfg, entries, old)
        plan['timing_policy'] = 'WordBoundary exact sequential text match; measured gap midpoint; nearest 30fps frame; continuous PCM'
        plan['settings_scope'] = ('narration_units owns voice/rate/pitch/volume and outer pauses; '
                                  'segments owns cue pronunciation and spoken_override; no per-cue pauses are inserted')
        if is_longform(cfg):
            plan['format'] = 'longform'
            plan['timing_policy'] = ('WordBoundary exact sequential text match; global PCM sample positions; '
                                     'one global 30fps frame grid; final alignment padding only')
    return plan


def build_narration_units(cfg, entries, old):
    """Groups are generation units, while segments remain display/voice cues."""
    positions = {entry['id']: i for i, entry in enumerate(entries)}
    specs = narration_groups(cfg)
    if not isinstance(specs, list):
        raise VoiceError('narration_groups는 목록이어야 해요.')
    grouped, used, group_ids, previous_end = {}, set(), set(), -1
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get('id'), str) or not spec['id'].strip():
            raise VoiceError('낭독 그룹 ID가 비어 있거나 형식이 잘못됐어요.')
        members = spec.get('segment_ids')
        if not isinstance(members, list) or not members or any(not isinstance(key, str) or key not in positions for key in members):
            raise VoiceError('낭독 그룹에 없는 cue ID 또는 빈 목록이 있어요.')
        indexes = [positions[key] for key in members]
        if indexes != list(range(indexes[0], indexes[0] + len(indexes))) or indexes[0] <= previous_end:
            raise VoiceError('낭독 그룹은 대본 순서대로 연속된 cue여야 해요.')
        if used.intersection(members) or spec['id'] in group_ids:
            raise VoiceError('낭독 그룹 또는 구성 cue가 중복돼요.')
        group_ids.add(spec['id'])
        used.update(members)
        previous_end = indexes[-1]
        grouped[indexes[0]] = {'id': 'group.' + spec['id'], 'members': members, 'group_id': spec['id']}
    previous = {entry['id']: entry for entry in old.get('narration_units', [])}
    if len(previous) != len(old.get('narration_units', [])):
        raise VoiceError('voice_plan 낭독 단위 ID가 중복돼요.')
    units, index = [], 0
    while index < len(entries):
        spec = grouped.get(index)
        count = len(spec['members']) if spec else 1
        members = entries[index:index + count]
        first = members[0]
        if spec:
            unit = {key: first[key] for key in GROUP_SETTINGS}
            unit.update({key: previous.get(spec['id'], {})[key] for key in GROUP_SETTINGS
                         if key in previous.get(spec['id'], {})})
            unit.update(id=spec['id'], group_id=spec['group_id'], explicit_group=True,
                        pronunciation={}, pronunciation_notes='Cue pronunciation mappings applied before joining')
        else:
            unit = dict(first, explicit_group=False)
        unit.update(segment_ids=[member['id'] for member in members],
                    members=[{'id': member['id'], 'spoken_text': member['spoken_text'],
                              'display_text': member['display_text'],
                              'source_spoken_text': member['source_spoken_text']} for member in members],
                    spoken_text=' '.join(member['spoken_text'] for member in members),
                    display_text=' '.join(member['display_text'] for member in members))
        check_entry(unit)
        units.append(unit)
        index += count
    names = [audio_name(unit['id']) for unit in units]
    if len(names) != len(set(names)) or len({unit['id'] for unit in units}) != len(units):
        raise VoiceError('낭독 단위의 ID 또는 음성 파일 이름이 충돌해요.')
    return units


def generation_units(plan):
    return plan.get('narration_units', plan['segments'])


def generation_settings(plan, entry):
    if plan.get('format') != 'longform':
        return entry
    # Subtitle spelling and image metadata do not change the spoken request.
    result = {key: value for key, value in entry.items()
              if key not in ('display_text', 'source_display_text', 'source_spoken_text',
                             '_generated_spoken_text', 'pronunciation_notes', 'members')}
    if 'members' in entry:
        result['members'] = [{'id': member['id'], 'spoken_text': member['spoken_text']}
                             for member in entry['members']]
    return result


def sentence_hash(plan, entry):
    return object_hash({**{key: plan[key] for key in ("pipeline_version", "producer_sha256", "engine", "decoder", "pcm_format", "pcm_trim_policy")},
                        "sentence": generation_settings(plan, entry)})


def input_hash(plan):
    return object_hash(plan)


def decode_pcm(path):
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-vn", "-ac", "1",
                             "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"], capture_output=True, timeout=60)
    if result.returncode or not result.stdout or len(result.stdout) % SAMPLE_WIDTH:
        raise VoiceError(f"음성 PCM 디코딩 실패: {path}\n{result.stderr.decode('utf-8', errors='replace')[-1200:]}")
    return result.stdout


async def fetch_tts(entry, path):
    import edge_tts
    communicator = edge_tts.Communicate(entry["spoken_text"], entry["voice"], rate=entry["rate"], pitch=entry["pitch"],
                                        volume=entry["volume"], boundary="WordBoundary", connect_timeout=10, receive_timeout=30)
    boundaries, audio_bytes = [], 0
    with Path(path).open("wb") as output:
        async for chunk in communicator.stream():
            if chunk["type"] == "audio":
                output.write(chunk["data"])
                audio_bytes += len(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                boundaries.append({"type": chunk["type"], "offset_ticks": chunk["offset"], "duration_ticks": chunk["duration"], "text": chunk["text"]})
        output.flush()
        os.fsync(output.fileno())
    if not audio_bytes:
        raise VoiceError("TTS 서비스가 음성을 반환하지 않았어요.")
    return boundaries


async def verify_voices(plan, base):
    import edge_tts
    voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=30)
    supported = {voice["ShortName"] for voice in voices}
    requested = {entry["voice"] for entry in generation_units(plan)}
    if requested - supported:
        raise VoiceError("지원하지 않는 목소리: " + ", ".join(sorted(requested - supported)))
    atomic_json(Path(base) / "voice_capabilities.json", {"checked_at": now(), "engine": plan["engine"],
                "requested_voices": sorted(requested), "supported_requested_voices": sorted(requested & supported),
                "parameter_support_verified_from_installed_signature": True,
                "unsupported_features": ["custom emotion tags", "arbitrary SSML", "pinned remote model revision"]})


def cached_audio(base, entry, fingerprint, plan=None):
    path = Path(base) / audio_name(entry["id"])
    metadata_path = path.with_suffix(".json")
    if not path.exists() or not metadata_path.exists():
        return None
    try:
        metadata = load_json(metadata_path)
        if metadata.get("audio_sha256") != sha256_file(path):
            return None
        reprocess = (metadata.get("input_hash") != fingerprint or
                     (plan and plan.get('format') == 'longform' and metadata.get('settings') != entry))
        # A changed PCM processor must invalidate the master/timeline, but need
        # not send the same text to TTS again. Verify the raw source's exact
        # generation inputs and bytes before re-decoding it.
        if reprocess and (plan is None or
                          generation_settings(plan, metadata.get('settings', {})) != generation_settings(plan, entry)
                          or metadata.get("engine") != plan["engine"]):
            return None
        pcm = decode_pcm(path)
        decoded_hash = hashlib.sha256(pcm).hexdigest()
        if not reprocess and metadata.get("decoded_pcm_sha256") != decoded_hash:
            return None
        if reprocess:
            archive = Path(base) / "revisions" / "voice" / "metadata"
            archive.mkdir(parents=True, exist_ok=True)
            revision_id = metadata_path.stem + "_" + uuid.uuid4().hex
            shutil.copy2(metadata_path, archive / (revision_id + ".json"))
            raw_name = path.stem + '_decoded.wav'
            if metadata.get('raw_pcm_file') == raw_name and (Path(base) / raw_name).exists():
                shutil.copy2(Path(base) / raw_name, archive / (revision_id + '_decoded.wav'))
            metadata.setdefault("processing_history", []).append({"previous_input_hash": metadata["input_hash"],
                "new_input_hash": fingerprint, "processed_at": now(), "reason": "Same verified raw TTS; PCM processor/dependencies changed; no TTS retry consumed"})
            metadata.update(input_hash=fingerprint, decoded_pcm_sha256=decoded_hash,
                            sample_count=len(pcm) // SAMPLE_WIDTH, pcm_trim_policy=plan["pcm_trim_policy"], decoder=plan["decoder"],
                            settings=entry)
            atomic_json(metadata_path, metadata)
        return pcm, metadata
    except (OSError, ValueError, VoiceError):
        return None


def archive_previous(base, path, attempt):
    if path.exists():
        target = Path(base) / "revisions" / "voice" / path.stem / f"before_{attempt}_{uuid.uuid4().hex[:8]}"
        target.mkdir(parents=True)
        shutil.copy2(path, target / path.name)
        if path.with_suffix(".json").exists():
            shutil.copy2(path.with_suffix(".json"), target / path.with_suffix(".json").name)
            prior = load_json(path.with_suffix('.json'))
            raw_name = path.stem + '_decoded.wav'
            if prior.get('raw_pcm_file') == raw_name and (Path(base) / raw_name).exists():
                shutil.copy2(Path(base) / raw_name, target / raw_name)
        atomic_json(target / "revision.json", {"reason": "Changed dependencies, invalid audio, or requested sentence regeneration", "recorded_at": now()})


async def generate_sentence(base, entry, plan, state, *, force=False):
    base = Path(base)
    fingerprint = sentence_hash(plan, entry)
    cached = None if force else cached_audio(base, entry, fingerprint, plan)
    if cached is not None:
        return cached
    group = entry.get('explicit_group', False)
    record = state.setdefault("groups" if group else "sentences", {}).setdefault(entry["id"], {"attempts": 0, "history": []})
    member_records = [state.setdefault('sentences', {}).setdefault(key, {'attempts': 0, 'history': []})
                      for key in entry.get('segment_ids', [])] if group else []
    limit = MAX_GENERATIONS
    authorization_path = base / 'production_revision_authorization.json'
    if not authorization_path.exists():
        authorization_path = base / 'user_release_acceptance.json'
    if authorization_path.exists():
        authorization = load_json(authorization_path)
        cfg = load_json(base / 'episode.json')
        extra = authorization.get('authorized_extra_voice_generations', 0)
        if (type(extra) is int and extra >= 0 and authorization.get('user_statement')
                and authorization.get('job_id') == cfg.get('job_id')
                and authorization.get('revised_script_hash') == script_hash(cfg)):
            limit += extra
    if any(item['attempts'] >= limit for item in [record, *member_records]):
        raise VoiceError(f"{entry['id']}: 최초 생성과 수정 2회 한도에 도달했어요. 검수 대기로 남겨야 해요.")
    record["attempts"] += 1
    event = {"attempt": record["attempts"], "input_hash": fingerprint, "started_at": now(), "status": "IN_PROGRESS", "authorized_limit": limit}
    record["history"].append(event)
    for member in member_records:
        member['attempts'] += 1
        member['history'].append({'attempt': member['attempts'], 'group_id': entry['id'],
                                  'group_attempt': record['attempts'], 'input_hash': fingerprint,
                                  'started_at': event['started_at'], 'status': 'IN_PROGRESS'})
    atomic_json(base / "voice_state.json", state)
    path = base / audio_name(entry["id"])
    temp = base / (path.name + "." + uuid.uuid4().hex + ".tmp.mp3")
    try:
        boundaries = await asyncio.wait_for(fetch_tts(entry, temp), timeout=60)
        pcm = decode_pcm(temp)
        metadata = {"schema_version": 2, "id": entry["id"], "input_hash": fingerprint, "audio_file": path.name,
                    "audio_sha256": sha256_file(temp), "decoded_pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                    "sample_count": len(pcm) // SAMPLE_WIDTH, "sample_rate": SAMPLE_RATE, "boundaries": boundaries,
                    "boundary_time_unit": "100 nanoseconds", "generated_at": now(), "attempt": record["attempts"],
                    "settings": entry, "engine": plan["engine"], "decoder": plan["decoder"],
                    "pcm_trim_policy": plan["pcm_trim_policy"]}
        archive_previous(base, path, record["attempts"])
        os.replace(temp, path)
        atomic_json(path.with_suffix(".json"), metadata)
        event.update(status="GENERATED", audio_sha256=metadata["audio_sha256"], ended_at=now())
        for member in member_records:
            member['history'][-1].update(status='GENERATED', audio_sha256=metadata['audio_sha256'], ended_at=event['ended_at'])
        atomic_json(base / "voice_state.json", state)
        return pcm, metadata
    except Exception as exc:
        event.update(status="FAILED", error=f"{type(exc).__name__}: {exc}", ended_at=now())
        for member in member_records:
            member['history'][-1].update(status='FAILED', error=event['error'], ended_at=event['ended_at'])
        atomic_json(base / "voice_state.json", state)
        raise VoiceError(f"{entry['id']} 음성 생성 실패: {exc}") from exc
    finally:
        temp.unlink(missing_ok=True)


def trim_digital_silence(pcm):
    """Remove only measured all-zero service padding; preserve every nonzero sample.

    Keep >=100 ms after the last nonzero sample and >=50 ms before the first,
    where available. This never uses word metadata to cut a spoken ending.
    """
    samples = array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    first = next((i for i, sample in enumerate(samples) if sample != 0), None)
    if first is None:
        raise VoiceError("TTS 음성이 전부 디지털 무음이에요. 정상 음성으로 처리할 수 없어요.")
    last = next(i for i in range(len(samples) - 1, -1, -1) if samples[i] != 0)
    start = max(0, first - TRIM_POLICY["retain_head_samples"])
    end = min(len(samples), last + 1 + TRIM_POLICY["retain_tail_samples"])
    return pcm[start * SAMPLE_WIDTH:end * SAMPLE_WIDTH], {
        "policy": TRIM_POLICY, "raw_sample_count": len(samples), "kept_sample_count": end - start,
        "leading_trim_samples": start, "trailing_trim_samples": len(samples) - end,
        "raw_first_nonzero_sample": first, "raw_last_nonzero_sample": last,
        "retained_head_samples": first - start, "retained_tail_samples": end - last - 1,
        "removed_samples_verified_equal_zero": True}


def trim_story_padding(pcm, boundaries):
    """Only shorten measured outer low-level padding, never internal pauses.

    Keep 150 ms beyond both detected signal and supplied word bounds. A quiet
    word or consonant near the threshold therefore has two conservative guards.
    The complete decoded source and original MP3 remain available for review.
    """
    samples = array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    threshold = STORY_TRIM_POLICY['absolute_sample_threshold']
    first = next((i for i, value in enumerate(samples) if abs(value) > threshold), None)
    if first is None:
        raise VoiceError('이야기 음성에 -65 dBFS보다 큰 신호가 없어요. 정상 음성으로 처리하지 않아요.')
    last = next(i for i in range(len(samples) - 1, -1, -1) if abs(samples[i]) > threshold)
    head, tail = (STORY_TRIM_POLICY[key] for key in ('retain_head_samples', 'retain_tail_samples'))
    word_bounds = [b for b in boundaries if b.get('type') == 'WordBoundary']
    first_word = min((round(b['offset_ticks'] * SAMPLE_RATE / 10_000_000) for b in word_bounds), default=first)
    last_word = max((round((b['offset_ticks'] + b['duration_ticks']) * SAMPLE_RATE / 10_000_000) for b in word_bounds), default=last + 1)
    start = max(0, min(first, first_word) - head)
    end = min(len(samples), max(last + 1, last_word) + tail)
    return pcm[start * SAMPLE_WIDTH:end * SAMPLE_WIDTH], {
        'policy': STORY_TRIM_POLICY, 'raw_sample_count': len(samples), 'kept_sample_count': end - start,
        'leading_trim_samples': start, 'trailing_trim_samples': len(samples) - end,
        'raw_first_above_threshold_sample': first, 'raw_last_above_threshold_sample': last,
        'word_guard_first_sample': first_word, 'word_guard_last_sample': last_word,
        'retained_head_samples': first - start, 'retained_tail_samples': end - last - 1,
        'removed_head_peak_abs': max((abs(x) for x in samples[:start]), default=0),
        'removed_tail_peak_abs': max((abs(x) for x in samples[end:]), default=0),
        'internal_audio_removed': False}


def normalized_words(text):
    # Punctuation/spacing is not pronounced by Edge. No fuzzy matching, searching
    # ahead, or proportional timing is used; every retained character must match.
    return ''.join(character for character in html.unescape(text).casefold() if character.isalnum())


def align_group_words(unit, metadata, raw_samples):
    members = unit['members']
    texts = [normalized_words(member['spoken_text']) for member in members]
    if any(not text for text in texts):
        raise VoiceError(f"{unit['id']}: 단어 경계와 연결할 낭독문이 비어 있어요.")
    expected = ''.join(texts)
    words, cursor, previous_end = [], 0, -1
    for boundary in metadata.get('boundaries', []):
        if boundary.get('type') != 'WordBoundary':
            continue
        text = normalized_words(boundary.get('text', ''))
        if not text:
            continue
        offset, duration = boundary.get('offset_ticks'), boundary.get('duration_ticks')
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (offset, duration)) or offset < 0 or duration <= 0:
            raise VoiceError(f"{unit['id']}: 유효하지 않은 WordBoundary 시간이에요.")
        start = round(offset * SAMPLE_RATE / 10_000_000)
        end = round((offset + duration) * SAMPLE_RATE / 10_000_000)
        if start < previous_end or end > raw_samples or expected[cursor:cursor + len(text)] != text:
            raise VoiceError(f"{unit['id']}: WordBoundary와 실제 낭독문/PCM을 정확히 대응할 수 없어요.")
        words.append({**boundary, 'normalized_start': cursor, 'normalized_end': cursor + len(text),
                      'source_start_sample': start, 'source_end_sample': end})
        cursor += len(text)
        previous_end = end
    if cursor != len(expected):
        raise VoiceError(f"{unit['id']}: WordBoundary가 전체 낭독문을 덮지 못해요. 비율로 시간을 추정하지 않아요.")
    result, cursor = [], 0
    for member, text in zip(members, texts):
        end = cursor + len(text)
        selected = [word for word in words if cursor <= word['normalized_start'] and word['normalized_end'] <= end]
        if not selected or selected[0]['normalized_start'] != cursor or selected[-1]['normalized_end'] != end:
            raise VoiceError(f"{member['id']}: cue 경계가 한 WordBoundary 단어 안에 있어요. 단어 사이에서 나눠 주세요.")
        result.append(selected)
        cursor = end
    return result


def assemble_longform_pcm(cfg, plan, decoded):
    """Keep group PCM on one sample clock; quantize only global cue edges.

    No frame padding is added at a group boundary. The final tail is the only
    alignment silence, so hundreds of groups cannot accumulate rounding drift.
    All measured words must fit their cue; insufficient inter-word space is an
    actionable alignment error, never a reason to slice through a word.
    """
    sources = {source['id']: source for source in speech_segments(cfg)}
    entries = {entry['id']: entry for entry in plan['segments']}
    pending, groups, parts, cursor = [], [], [], 0
    units = generation_units(plan)
    for unit in units:
        raw, metadata = decoded[unit['id']]
        aligned = align_group_words(unit, metadata, len(raw) // SAMPLE_WIDTH)
        pcm, trim = (trim_story_padding(raw, metadata['boundaries']) if plan['pcm_trim_policy'] == STORY_TRIM_POLICY
                     else trim_digital_silence(raw))
        samples = len(pcm) // SAMPLE_WIDTH
        before, after = (round(unit[key] * SAMPLE_RATE) for key in ('pause_before', 'pause_after'))
        content_start = cursor + before
        content_end = content_start + samples
        group_end = content_end + after
        lead = trim['leading_trim_samples']
        group = {
            'id': unit['id'], 'segment_ids': unit['segment_ids'],
            'audio_file': metadata['audio_file'], 'audio_sha256': metadata['audio_sha256'],
            'input_hash': metadata['input_hash'], 'spoken_text': unit['spoken_text'],
            'decoded_pcm_sha256': metadata['decoded_pcm_sha256'],
            'raw_pcm_file': metadata['raw_pcm_file'], 'raw_pcm_sha256': metadata['raw_pcm_sha256'],
            'raw_sample_count': len(raw) // SAMPLE_WIDTH, 'pcm_trim': trim,
            'start_sample': cursor, 'end_sample': group_end,
            'content_start_sample': content_start, 'content_end_sample': content_end,
            'pause_before_samples': before, 'pause_after_samples': after,
            'alignment_tail_samples': 0, 'internal_inserted_silence_samples': 0,
        }
        groups.append(group)
        for key, words in zip(unit['segment_ids'], aligned):
            bounds = [{**word,
                       'start': (content_start - lead + word['source_start_sample']) / SAMPLE_RATE,
                       'end': (content_start - lead + word['source_end_sample']) / SAMPLE_RATE}
                      for word in words]
            pending.append({'key': key, 'unit': unit, 'raw': raw, 'metadata': metadata,
                            'group': group, 'bounds': bounds,
                            'word_start_sample': content_start - lead + words[0]['source_start_sample'],
                            'word_end_sample': content_start - lead + words[-1]['source_end_sample']})
        parts.extend((b'\0' * before * SAMPLE_WIDTH, pcm, b'\0' * after * SAMPLE_WIDTH))
        cursor = group_end
    if not pending:
        raise VoiceError('롱폼의 낭독 그룹과 실제 단어 경계가 비어 있어요.')
    if [item['key'] for item in pending] != list(sources):
        raise VoiceError('롱폼의 모든 cue와 CTA가 순서대로 낭독 그룹에 포함되어야 해요.')
    total_frames = math.ceil(cursor / SAMPLES_PER_FRAME)
    edges, measured = [0], []
    for left, right in zip(pending, pending[1:]):
        word_end, word_start = left['word_end_sample'], right['word_start_sample']
        midpoint = (word_end + word_start) / 2
        lower = math.ceil(word_end / SAMPLES_PER_FRAME)
        upper = math.floor(word_start / SAMPLES_PER_FRAME)
        nearest = round(midpoint / SAMPLES_PER_FRAME)
        frame = max(lower, min(nearest, upper))
        if lower > upper or frame <= edges[-1] or frame >= total_frames:
            raise VoiceError(f"{right['key']}: 단어를 자르지 않는 전역 30fps 경계가 없어요. 해당 cue 분할/낭독 그룹을 조정하세요.")
        edges.append(frame)
        measured.append({'after_cue': left['key'], 'before_cue': right['key'],
                         'basis': 'WordBoundary global sample gap midpoint',
                         'previous_word_end_sample': word_end, 'next_word_start_sample': word_start,
                         'global_gap_midpoint_sample': midpoint, 'global_frame': frame,
                         'alignment_error_samples': frame * SAMPLES_PER_FRAME - midpoint})
    edges.append(total_frames)
    segments = []
    for index, item in enumerate(pending):
        key, unit, group = item['key'], item['unit'], item['group']
        metadata, raw, bounds = item['metadata'], item['raw'], item['bounds']
        start_frame, end_frame = edges[index:index + 2]
        begin, end = start_frame * SAMPLES_PER_FRAME, end_frame * SAMPLES_PER_FRAME
        local_begin = max(begin, group['content_start_sample'])
        local_end = min(end, group['content_end_sample'])
        if not local_begin <= item['word_start_sample'] < item['word_end_sample'] <= local_end:
            raise VoiceError(f'{key}: 실제 단어가 할당한 부모 PCM 구간을 벗어나요.')
        raw_begin = group['pcm_trim']['leading_trim_samples'] + local_begin - group['content_start_sample']
        raw_end = group['pcm_trim']['leading_trim_samples'] + local_end - group['content_start_sample']
        segments.append({
            **sources[key], 'tts_text': entries[key]['spoken_text'], 'narration_group_id': unit['id'],
            'group_tts_text': unit['spoken_text'], 'start_frame': start_frame, 'end_frame': end_frame,
            'start': start_frame / FPS, 'end': end_frame / FPS,
            'start_sample': begin, 'end_sample': end,
            'speech_start_sample': local_begin, 'speech_end_sample': local_end,
            'speech_start': local_begin / SAMPLE_RATE, 'speech_end': local_end / SAMPLE_RATE,
            'speech_range_basis': 'Exact parent PCM slice on global frame grid; measured words retained intact',
            'decoded_sample_count': raw_end - raw_begin,
            'word_speech_start': bounds[0]['start'], 'word_speech_end': bounds[-1]['end'],
            'audio_file': metadata['audio_file'], 'audio_sha256': metadata['audio_sha256'],
            'sentence_input_hash': metadata['input_hash'], 'boundaries': bounds,
            'parent_audio': {'id': unit['id'], 'raw_pcm_file': metadata['raw_pcm_file'],
                             'raw_pcm_sha256': metadata['raw_pcm_sha256'],
                             'decoded_pcm_sha256': metadata['decoded_pcm_sha256'],
                             'sample_rate': SAMPLE_RATE, 'source_start_sample': raw_begin,
                             'source_end_sample': raw_end,
                             'slice_pcm_sha256': hashlib.sha256(raw[raw_begin * SAMPLE_WIDTH:raw_end * SAMPLE_WIDTH]).hexdigest()},
        })
    by_cue = {segment['id']: segment for segment in segments}
    for group in groups:
        group['cue_global_frame_edges'] = ([by_cue[key]['start_frame'] for key in group['segment_ids']]
                                            + [by_cue[group['segment_ids'][-1]]['end_frame']])
        group['start_frame'] = group['cue_global_frame_edges'][0]
        group['end_frame'] = group['cue_global_frame_edges'][-1]
    total_samples = total_frames * SAMPLES_PER_FRAME
    tail = total_samples - cursor
    parts.append(b'\0' * tail * SAMPLE_WIDTH)
    return b''.join(parts), {
        'schema_version': 2, 'fps': FPS, 'audio_mode': 'voice', 'format': 'longform',
        'script_hash': script_hash(cfg), 'input_hash': input_hash(plan),
        'total_frames': total_frames, 'duration': total_frames / FPS,
        'sample_rate': SAMPLE_RATE, 'total_samples': total_samples,
        'unrounded_total_samples': cursor, 'final_alignment_tail_samples': tail,
        'pcm_path': 'voice.wav', 'segments': segments, 'narration_groups': groups,
        'measured_cue_boundaries': measured,
        'timing_policy': 'global sample accumulation; only final frame tail padded',
    }


def assemble_grouped_pcm(cfg, plan, decoded):
    """Concatenate each parent exactly once, with no silence inserted per cue."""
    if is_longform(cfg):
        return assemble_longform_pcm(cfg, plan, decoded)
    sources = {source['id']: source for source in speech_segments(cfg)}
    entries = {entry['id']: entry for entry in plan['segments']}
    segments, groups, parts, cursor = [], [], [], 0
    for unit in generation_units(plan):
        raw, metadata = decoded[unit['id']]
        aligned = align_group_words(unit, metadata, len(raw) // SAMPLE_WIDTH)
        pcm, trim = (trim_story_padding(raw, metadata['boundaries']) if plan['pcm_trim_policy'] == STORY_TRIM_POLICY
                     else trim_digital_silence(raw))
        samples = len(pcm) // SAMPLE_WIDTH
        before, after = (round(unit[key] * SAMPLE_RATE) for key in ('pause_before', 'pause_after'))
        frames = math.ceil((before + samples + after) / SAMPLES_PER_FRAME)
        padded = frames * SAMPLES_PER_FRAME
        frame_edges, measured_edges = [0], []
        for left, right in zip(aligned, aligned[1:]):
            left_end, right_start = left[-1]['source_end_sample'], right[0]['source_start_sample']
            midpoint = (left_end + right_start) / 2
            actual = before + midpoint - trim['leading_trim_samples']
            frame = round(actual / SAMPLES_PER_FRAME)
            if frame <= frame_edges[-1] or frame >= frames:
                raise VoiceError(f"{unit['id']}: 측정 단어 경계로 양수 길이 cue 프레임을 만들 수 없어요.")
            frame_edges.append(frame)
            measured_edges.append({'basis': 'WordBoundary gap midpoint, not character-duration ratio',
                                   'previous_word_end_sample': left_end, 'next_word_start_sample': right_start,
                                   'parent_gap_midpoint_sample': midpoint, 'group_frame': frame,
                                   'alignment_error_samples': frame * SAMPLES_PER_FRAME - actual})
        frame_edges.append(frames)
        parent_start = trim['leading_trim_samples']
        parent_end = len(raw) // SAMPLE_WIDTH - trim['trailing_trim_samples']
        group_record = {'id': unit['id'], 'segment_ids': unit['segment_ids'],
                        'audio_file': metadata['audio_file'], 'audio_sha256': metadata['audio_sha256'],
                        'input_hash': metadata['input_hash'], 'spoken_text': unit['spoken_text'],
                        'decoded_pcm_sha256': metadata['decoded_pcm_sha256'],
                        'raw_pcm_file': metadata['raw_pcm_file'], 'raw_pcm_sha256': metadata['raw_pcm_sha256'],
                        'raw_sample_count': len(raw) // SAMPLE_WIDTH, 'pcm_trim': trim,
                        'start_sample': cursor, 'end_sample': cursor + padded,
                        'start_frame': cursor // SAMPLES_PER_FRAME, 'end_frame': (cursor + padded) // SAMPLES_PER_FRAME,
                        'pause_before_samples': before, 'pause_after_samples': after,
                        'alignment_tail_samples': padded - before - samples - after,
                        'cue_frame_edges': frame_edges, 'measured_cue_boundaries': measured_edges,
                        'internal_inserted_silence_samples': 0}
        groups.append(group_record)
        for index, (key, words) in enumerate(zip(unit['segment_ids'], aligned)):
            begin, end = (frame_edges[index] * SAMPLES_PER_FRAME, frame_edges[index + 1] * SAMPLES_PER_FRAME)
            local_begin, local_end = max(begin, before), min(end, before + samples)
            if local_begin >= local_end:
                raise VoiceError(f'{key}: cue에 연결된 실제 부모 PCM 구간이 없어요.')
            raw_begin = parent_start + local_begin - before
            raw_end = parent_start + local_end - before
            bounds = [{**word, 'start': (cursor + before - parent_start + word['source_start_sample']) / SAMPLE_RATE,
                       'end': (cursor + before - parent_start + word['source_end_sample']) / SAMPLE_RATE} for word in words]
            segment = {**sources[key], 'tts_text': entries[key]['spoken_text'], 'narration_group_id': unit['id'],
                       'group_tts_text': unit['spoken_text'], 'start_frame': (cursor + begin) // SAMPLES_PER_FRAME,
                       'end_frame': (cursor + end) // SAMPLES_PER_FRAME,
                       'start': (cursor + begin) / SAMPLE_RATE, 'end': (cursor + end) / SAMPLE_RATE,
                       'start_sample': cursor + begin, 'end_sample': cursor + end,
                       'speech_start_sample': cursor + local_begin, 'speech_end_sample': cursor + local_end,
                       'speech_start': (cursor + local_begin) / SAMPLE_RATE, 'speech_end': (cursor + local_end) / SAMPLE_RATE,
                       'speech_range_basis': 'Exact allocated parent PCM slice, including measured inter-word gap; word times are separate',
                       'decoded_sample_count': raw_end - raw_begin,
                       'word_speech_start': bounds[0]['start'], 'word_speech_end': bounds[-1]['end'],
                       'audio_file': metadata['audio_file'], 'audio_sha256': metadata['audio_sha256'],
                       'sentence_input_hash': metadata['input_hash'], 'boundaries': bounds,
                       'parent_audio': {'id': unit['id'], 'raw_pcm_file': metadata['raw_pcm_file'],
                                        'raw_pcm_sha256': metadata['raw_pcm_sha256'],
                                        'decoded_pcm_sha256': metadata['decoded_pcm_sha256'],
                                        'sample_rate': SAMPLE_RATE, 'source_start_sample': raw_begin,
                                        'source_end_sample': raw_end,
                                        'slice_pcm_sha256': hashlib.sha256(raw[raw_begin * SAMPLE_WIDTH:raw_end * SAMPLE_WIDTH]).hexdigest()}}
            segments.append(segment)
        parts.extend((b'\0' * before * SAMPLE_WIDTH, pcm, b'\0' * (padded - before - samples) * SAMPLE_WIDTH))
        cursor += padded
    return b''.join(parts), {'schema_version': 2, 'fps': FPS, 'audio_mode': 'voice', 'script_hash': script_hash(cfg),
                            'input_hash': input_hash(plan), 'total_frames': cursor // SAMPLES_PER_FRAME,
                            'duration': cursor / SAMPLE_RATE, 'sample_rate': SAMPLE_RATE, 'total_samples': cursor,
                            'pcm_path': 'voice.wav', 'segments': segments, 'narration_groups': groups}


def assemble_pcm(cfg, plan, decoded):
    if 'narration_units' in plan:
        return assemble_grouped_pcm(cfg, plan, decoded)
    entries = {entry["id"]: entry for entry in plan["segments"]}
    segments, parts, cursor = [], [], 0
    for source in speech_segments(cfg):
        entry = entries[source["id"]]
        raw_pcm, metadata = decoded[source["id"]]
        pcm, trim = trim_digital_silence(raw_pcm)
        samples = len(pcm) // SAMPLE_WIDTH
        before, after = (round(entry[key] * SAMPLE_RATE) for key in ("pause_before", "pause_after"))
        frames = math.ceil((before + samples + after) / SAMPLES_PER_FRAME)
        padded = frames * SAMPLES_PER_FRAME
        start, end = cursor // SAMPLES_PER_FRAME, cursor // SAMPLES_PER_FRAME + frames
        speech_start, speech_end = (cursor + before) / SAMPLE_RATE, (cursor + before + samples) / SAMPLE_RATE
        removed_lead = trim["leading_trim_samples"] / SAMPLE_RATE
        boundaries = [{**b, "start": speech_start + b["offset_ticks"] / 10_000_000 - removed_lead,
                       "end": speech_start + (b["offset_ticks"] + b["duration_ticks"]) / 10_000_000 - removed_lead}
                      for b in metadata.get("boundaries", [])]
        segments.append({**source, "tts_text": entry["spoken_text"], "start_frame": start, "end_frame": end,
                         "start": start / FPS, "end": end / FPS, "speech_start": speech_start, "speech_end": speech_end,
                         "start_sample": cursor, "end_sample": cursor + padded, "speech_start_sample": cursor + before,
                         "speech_end_sample": cursor + before + samples, "decoded_sample_count": samples, "pcm_trim": trim,
                         "pause_before_samples": before, "pause_after_samples": after,
                         "alignment_tail_samples": padded - before - samples - after, "audio_file": metadata["audio_file"],
                         "audio_sha256": metadata["audio_sha256"], "sentence_input_hash": metadata["input_hash"], "boundaries": boundaries})
        parts.extend((b"\0" * before * SAMPLE_WIDTH, pcm, b"\0" * (padded - before - samples) * SAMPLE_WIDTH))
        cursor += padded
    return b"".join(parts), {"schema_version": 2, "fps": FPS, "audio_mode": "voice", "script_hash": script_hash(cfg),
                             "input_hash": input_hash(plan), "total_frames": cursor // SAMPLES_PER_FRAME,
                             "duration": cursor / SAMPLE_RATE, "sample_rate": SAMPLE_RATE, "total_samples": cursor,
                             "pcm_path": "voice.wav", "segments": segments}


def silent_timeline(cfg, plan):
    cursor, segments = 0, []
    for source in speech_segments(cfg):
        # Explicit silent reading estimate; never presented as measured speech.
        frames = math.ceil(max(0.9, len(source["display_text"]) / 10.0 + 0.2) * FPS)
        segments.append({**source, "tts_text": None, "start_frame": cursor, "end_frame": cursor + frames,
                         "start": cursor / FPS, "end": (cursor + frames) / FPS, "speech_start": None,
                         "speech_end": None, "audio_file": None, "audio_sha256": None,
                         "timing_basis": "explicit silent-mode reading estimate"})
        cursor += frames
    return {"schema_version": 2, "fps": FPS, "audio_mode": "silent", "script_hash": script_hash(cfg),
            "input_hash": input_hash(plan), "total_frames": cursor, "duration": cursor / FPS,
            "pcm_path": None, "pcm_sha256": None, "segments": segments}


def write_wav(path, pcm):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with wave.open(str(temp), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(SAMPLE_WIDTH)
            output.setframerate(SAMPLE_RATE)
            output.writeframes(pcm)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def update_review(base, timeline, cfg=None):
    path = Path(base) / "audio_review.json"
    previous = load_json(path) if path.exists() else {}
    same = previous.get("input_hash") == timeline["input_hash"] and previous.get("pcm_sha256") == timeline.get("pcm_sha256")
    if same and previous.get("status") == "PASS" and timeline["audio_mode"] == "voice":
        return
    retained = {}
    if is_longform(cfg or {}):
        # A visual/script-hash change alone must not erase the user's actual
        # listening record. Final encoded sound and timing still decide validity.
        if previous:
            revisions = Path(base) / 'revisions'
            revisions.mkdir(exist_ok=True)
            archived = revisions / ('audio_review_' + object_hash(previous)[:16] + '.json')
            if not archived.exists():
                atomic_json(archived, previous)
        retained = {'user_approval': previous.get('user_approval', {}),
                    'technical': previous.get('technical', {}) if same else {'status': 'NOT_RUN'},
                    'approval_validation': 'Final MP4 audio identity and global spoken timing must match the retained approval.'}
    atomic_json(path, {"schema_version": 2, **retained, "status": "N/A" if timeline["audio_mode"] == "silent" else "AUDIO_REVIEW_REQUIRED",
                       "input_hash": timeline["input_hash"], "pcm_sha256": timeline.get("pcm_sha256"),
                       "script_hash": timeline["script_hash"], "updated_at": now(), "listened": False,
                       "notes": ["Explicit silent mode: no voice/BGM." if timeline["audio_mode"] == "silent" else
                                 "Decode and text checks do not establish pronunciation, intonation, or naturalness. Actual listening is required."]})


async def _build(cfg, base, *, silent=False, regenerate=()):
    base = Path(base)
    if is_longform(cfg):
        if silent:
            raise VoiceError('롱폼은 전체 낭독이 필요하며 --silent를 지원하지 않아요.')
        from longform_profile import longform_production_errors
        errors = longform_production_errors(cfg, base)
        if errors:
            raise VoiceError('\n'.join(errors))
    plan = build_plan(cfg, base, silent=silent)
    units = generation_units(plan)
    unknown = set(regenerate) - {entry['id'] for entry in [*plan['segments'], *units]}
    if unknown:
        raise VoiceError("알 수 없는 재생성 문장 ID: " + ", ".join(sorted(unknown)))
    atomic_json(base / "voice_plan.json", plan)
    build = {"schema_version": 2, "status": "IN_PROGRESS", "input_hash": input_hash(plan), "started_at": now()}
    atomic_json(base / "voice_build.json", build)
    try:
        if silent:
            timeline = silent_timeline(cfg, plan)
        else:
            path = base / "voice_state.json"
            state = load_json(path) if path.exists() else {"schema_version": 2, "sentences": {}}
            forced = {unit['id'] for unit in units if unit['id'] in regenerate or set(unit.get('segment_ids', [unit['id']])).intersection(regenerate)}
            if any(entry["id"] in forced or cached_audio(base, entry, sentence_hash(plan, entry), plan) is None for entry in units):
                await verify_voices(plan, base)  # One common service failure stops the run.
            decoded = {}
            for entry in units:
                raw, metadata = await generate_sentence(base, entry, plan, state, force=entry["id"] in forced)
                if 'narration_units' in plan:
                    raw_path = base / (Path(metadata['audio_file']).stem + '_decoded.wav')
                    write_wav(raw_path, raw)
                    metadata.update(raw_pcm_file=raw_path.name, raw_pcm_sha256=sha256_file(raw_path))
                    atomic_json((base / metadata['audio_file']).with_suffix('.json'), metadata)
                decoded[entry['id']] = raw, metadata
            pcm, timeline = assemble_pcm(cfg, plan, decoded)
            write_wav(base / "voice.wav", pcm)
            timeline["pcm_sha256"] = sha256_file(base / "voice.wav")
            for segment in timeline["segments"]:
                segment["audio_metadata_sha256"] = sha256_file((base / segment["audio_file"]).with_suffix(".json"))
        atomic_json(base / "timeline.json", timeline)
        update_review(base, timeline, cfg=cfg)
        build.update(status="COMPLETE", completed_at=now(), timeline_sha256=sha256_file(base / "timeline.json"))
        atomic_json(base / "voice_build.json", build)
        minimum, maximum = duration_limits(cfg)
        if not minimum <= timeline["duration"] <= maximum:
            raise VoiceError(f"실제 길이가 {timeline['duration']:.2f}초예요. {minimum}~{maximum}초가 되도록 대본과 호흡을 조정해 주세요. 시간표는 저장했어요.")
        return timeline
    except Exception as exc:
        build.update(status="FAILED", error=f"{type(exc).__name__}: {exc}", ended_at=now())
        atomic_json(base / "voice_build.json", build)
        raise


def main(json_path, *, silent=False, regenerate=()):
    json_path = Path(json_path).resolve()
    cfg = load_json(json_path)
    if is_longform(cfg):
        if silent:
            raise VoiceError('롱폼은 전체 낭독이 필요하며 --silent를 지원하지 않아요.')
        from longform_profile import longform_production_errors
        errors = longform_production_errors(cfg, json_path.parent)
        if errors:
            raise VoiceError('\n'.join(errors))
    lock = json_path.parent / ".voice.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise VoiceError("같은 편의 음성 작업 잠금이 있어요. 진행 중인 작업 또는 중단 기록을 확인해 주세요.") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(str(os.getpid()))
        return asyncio.run(_build(cfg, json_path.parent, silent=silent, regenerate=regenerate))
    finally:
        lock.unlink(missing_ok=True)


def validate_voice(cfg, base):
    """Read-only consistency check. Success is not a listening review."""
    base, errors = Path(base), []
    try:
        timeline = load_json(base / "timeline.json")
        if timeline.get("audio_mode") not in ("voice", "silent"):
            raise VoiceError("timeline audio_mode가 잘못됐어요.")
        silent = timeline["audio_mode"] == "silent"
        plan, build = build_plan(cfg, base, silent=silent), load_json(base / "voice_build.json")
        if build.get("status") != "COMPLETE":
            errors.append("음성/시간표 생성이 정상 완료된 상태가 아니에요.")
        if timeline.get("schema_version") != 2 or timeline.get("fps") != FPS:
            errors.append("시간표 버전 또는 프레임률이 맞지 않아요.")
        if timeline.get("script_hash") != script_hash(cfg):
            errors.append("대본/공통 CTA와 시간표가 달라요. 음성을 다시 생성해야 해요.")
        if timeline.get("input_hash") != input_hash(plan) or build.get("input_hash") != input_hash(plan):
            errors.append("음성 설정·대본·엔진 버전이 바뀌었어요. 예전 음성을 재사용할 수 없어요.")
        if build.get("timeline_sha256") != sha256_file(base / "timeline.json"):
            errors.append("완료 후 시간표가 변경됐어요.")
        sources, segments = speech_segments(cfg), timeline.get("segments", [])
        unit_by_cue = {key: unit for unit in generation_units(plan) for key in unit.get('segment_ids', [unit['id']])}
        if len(sources) != len(segments):
            errors.append("시간표의 문장 수가 대본과 달라요.")
        cursor = 0
        for source, segment, entry in zip(sources, segments, plan["segments"]):
            if any(segment.get(key) != value for key, value in source.items()):
                errors.append(f"{source['id']}: 자막/그림/문장 연결이 현재 대본과 달라요.")
            start, end = segment.get("start_frame"), segment.get("end_frame")
            if not isinstance(start, int) or not isinstance(end, int) or start != cursor or end <= start:
                errors.append(f"{source['id']}: 프레임 경계가 연속적이지 않아요.")
                continue
            if segment.get("start") != start / FPS or segment.get("end") != end / FPS:
                errors.append(f"{source['id']}: 시간과 프레임 경계가 맞지 않아요.")
            cursor = end
            if silent:
                if segment.get("audio_file") or segment.get("audio_sha256"):
                    errors.append("무음 시간표에 음성이 연결되어 있어요.")
                continue
            unit = unit_by_cue[source['id']]
            filename = audio_name(unit['id'])
            if segment.get("audio_file") != filename:
                errors.append(f"{source['id']}: 음성 파일 연결이 잘못됐어요.")
                continue
            path, metadata = base / filename, (base / filename).with_suffix(".json")
            if not path.exists() or sha256_file(path) != segment.get("audio_sha256"):
                errors.append(f"{source['id']}: 음성이 없거나 손상/변경됐어요.")
            if not metadata.exists() or sha256_file(metadata) != segment.get("audio_metadata_sha256"):
                errors.append(f"{source['id']}: 음성 메타데이터가 없거나 바뀌었어요.")
            if segment.get("sentence_input_hash") != sentence_hash(plan, unit) or segment.get("tts_text") != entry["spoken_text"]:
                errors.append(f"{source['id']}: 현재 낭독 조건과 생성 음성이 달라요.")
            if not start / FPS <= segment.get("speech_start", -1) < segment.get("speech_end", -1) <= end / FPS:
                errors.append(f"{source['id']}: 발화 경계가 문장 구간을 벗어나요.")
        if cursor != timeline.get("total_frames") or timeline.get("duration") != cursor / FPS:
            errors.append("전체 프레임 수와 재생 시간이 맞지 않아요.")
        minimum, maximum = duration_limits(cfg)
        if not minimum <= timeline.get("duration", 0) <= maximum:
            errors.append(f"영상 길이는 {minimum}~{maximum}초여야 해요.")
        if silent:
            if timeline.get("pcm_path") or timeline.get("pcm_sha256"):
                errors.append("무음 모드에 PCM 음성이 연결되어 있어요.")
        else:
            if timeline.get("pcm_path") != "voice.wav":
                errors.append("통합 PCM 파일 이름이 잘못됐어요.")
            path = base / "voice.wav"
            if not path.exists() or sha256_file(path) != timeline.get("pcm_sha256"):
                errors.append("최신 통합 PCM 음성이 없거나 변경됐어요.")
            else:
                with wave.open(str(path), "rb") as audio:
                    if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()) != (SAMPLE_RATE, 1, SAMPLE_WIDTH, cursor * SAMPLES_PER_FRAME):
                        errors.append("PCM 형식/샘플 수와 시간표 길이가 일치하지 않아요.")
                    actual_pcm = audio.readframes(audio.getnframes())
                if 'narration_units' in plan:
                    validate_group_provenance(cfg, base, plan, timeline, actual_pcm, errors)
    except (OSError, ValueError, KeyError, TypeError, wave.Error, VoiceError, subprocess.SubprocessError) as exc:
        errors.append(f"음성/시간표 검사 실패: {exc}")
    return errors


def validate_group_provenance(cfg, base, plan, timeline, actual_pcm, errors):
    """Reconstruct from retained parent PCM and boundaries, without network/writes."""
    decoded = {}
    for unit in generation_units(plan):
        filename = audio_name(unit['id'])
        metadata = load_json((base / filename).with_suffix('.json'))
        raw_name = Path(filename).stem + '_decoded.wav'
        if metadata.get('raw_pcm_file') != raw_name or metadata.get('settings') != unit:
            raise VoiceError(f"{unit['id']}: 부모 PCM 또는 그룹 설정 연결이 달라요.")
        raw_path = base / raw_name
        if sha256_file(raw_path) != metadata.get('raw_pcm_sha256'):
            raise VoiceError(f"{unit['id']}: 보존된 부모 PCM 파일이 달라요.")
        with wave.open(str(raw_path), 'rb') as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (SAMPLE_RATE, 1, SAMPLE_WIDTH):
                raise VoiceError('부모 PCM 형식이 잘못됐어요.')
            raw = audio.readframes(audio.getnframes())
        if (hashlib.sha256(raw).hexdigest() != metadata.get('decoded_pcm_sha256') or
                len(raw) // SAMPLE_WIDTH != metadata.get('sample_count') or
                metadata.get('input_hash') != sentence_hash(plan, unit) or metadata.get('engine') != plan['engine']):
            raise VoiceError(f"{unit['id']}: 부모 PCM 샘플/해시/생성 조건이 달라요.")
        decoded[unit['id']] = raw, metadata
    pcm, expected = assemble_grouped_pcm(cfg, plan, decoded)
    for segment in expected['segments']:
        segment['audio_metadata_sha256'] = sha256_file((base / segment['audio_file']).with_suffix('.json'))
    if pcm != actual_pcm:
        errors.append('통합 PCM이 보존된 부모 음성의 연속 연결과 달라요.')
    if expected['segments'] != timeline['segments'] or expected['narration_groups'] != timeline.get('narration_groups'):
        errors.append('cue 시간표/부모 샘플 범위가 실제 WordBoundary와 PCM 재구성 결과와 달라요.')
    if is_longform(cfg) and any(expected.get(key) != timeline.get(key) for key in
                               ('unrounded_total_samples', 'final_alignment_tail_samples',
                                'measured_cue_boundaries', 'timing_policy')):
        errors.append('롱폼 전역 샘플/프레임 정렬 근거가 보존된 부모 PCM과 달라요.')


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", nargs="+")
    parser.add_argument("--silent", action="store_true", help="명시적으로 요청한 무음 제작만 사용")
    parser.add_argument("--plan-only", action="store_true", help="계획만 저장; 네트워크/생성 없음")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--regenerate", action="append", default=[], metavar="CUE_OR_UNIT_ID",
                        help="cue ID 또는 group.<그룹ID>; cue 재생성은 해당 낭독 그룹 전체에 적용")
    args = parser.parse_args()
    for name in args.episodes:
        path = Path(name).resolve()
        try:
            if args.check:
                errors = validate_voice(load_json(path), path.parent)
                if errors:
                    raise VoiceError("\n".join(errors))
                print("통과 (음성/시간표 기술 검사; 청취 검수는 별도)")
            elif args.plan_only:
                atomic_json(path.parent / "voice_plan.json", build_plan(load_json(path), path.parent, silent=args.silent))
                print(f"음성 계획 저장: {path.parent / 'voice_plan.json'}")
            else:
                timeline = main(path, silent=args.silent, regenerate=args.regenerate)
                print(f"시간표 저장: {path.parent / 'timeline.json'} ({timeline['duration']:.2f}초)")
                print("N/A (명시적 무음)" if args.silent else "AUDIO_REVIEW_REQUIRED: 실제 청취 검수가 필요해요.")
        except Exception as exc:
            print(f"음성 작업 실패: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
