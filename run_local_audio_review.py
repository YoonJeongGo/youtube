"""Run the local Qwen2.5-Omni GGUF on real audio, retaining unedited evidence.

This helper never marks audio_review PASS or claims human/Codex-native listening.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import wave

from episode_core import load_json, atomic_json, sha256_file

ROOT = Path(__file__).resolve().parent
EXE = ROOT / '.audio-tools' / 'llama-b12428-cuda12.4' / 'llama-mtmd-cli.exe'
MODEL_DIR = ROOT / '.models' / 'Qwen2.5-Omni-7B-GGUF'
PROBE_PROMPT = """Analyze only the actual sound in the attached recording. No script is supplied.
First write the words you hear, in the spoken language. Then describe the speaker's voice,
speaking pace, pitch movements, pauses, articulation, sentence-ending intonation, and any
audible distortion or abrupt cut. Say whether the speech sounds spontaneous or read and
give specific audible evidence. If there is silence or you cannot understand the words,
say so; do not invent speech. Do not infer pictures or events outside the recording.
Do not give a numeric quality score. Acknowledge uncertainty. Answer concisely in English,
but preserve the actual heard words in Korean when applicable."""
FULL_PROMPT = """Inspect the actual audio throughout this recording. No transcript or expected words are supplied.
Write the spoken content in Korean, including the opening and the final words; mark any
uncertain words. Separately give an English auditory quality report with approximate time
ranges for specific observations: speaking pace, pitch/intonation variation, naturalness,
unintended pauses or repetitions, transitions between sentences, audibility of word endings,
volume changes, background sounds, clipping/distortion, and abrupt starts or ends.
Distinguish what you actually hear from guesses. If no specific defect is audible, say that
within the limits of this recording. Do not infer visual content and do not use numeric scores.
End by naming what remains uncertain. Base this report on audio, not common script patterns."""
PROSODY_PROMPT = """Listen to this recording and describe its SOUND in English, in no more than 180 words.
Do not transcribe the speech and do not produce timestamps or subtitles.
Describe the audible voice, speaking pace, pitch variation, sentence-ending pitch movement,
pauses and boundaries between utterances, articulation smoothness, word-ending audibility,
volume continuity, background sounds, and any distortion or abrupt cutoff.
Use concrete audible cues and distinguish observations from uncertainty. Explain whether the
delivery sounds fluid, choppy, expressive or flat, and what audible evidence supports that.
Pay particular attention to whether the final spoken sound is complete or cut off.
Do not infer visuals or events outside the audio. Do not give a numeric quality score.
If something cannot be judged from this clip, say so. If the input is silent, report silence."""


def make_probe(source, target, seconds=6.0, silent=False):
    with wave.open(str(source), 'rb') as f:
        params = f.getparams()
        data = f.readframes(round(seconds * f.getframerate()))
    if silent:
        data = b'\0' * len(data)
    with wave.open(str(target), 'wb') as f:
        f.setparams(params)
        f.writeframes(data)


def make_chunks(source, folder, role, timeline):
    with wave.open(str(source), 'rb') as stream:
        params = stream.getparams()
        pcm = stream.readframes(params.nframes)
    frame_width = params.nchannels * params.sampwidth
    entries = []
    segments = timeline['segments']
    start_sample = 0
    for offset in range(0, len(segments), 3):
        group = segments[offset:offset + 3]
        last = offset + 3 >= len(segments)
        end_sample = params.nframes if last else round(group[-1]['end'] * params.framerate)
        destination = folder / f'{role}_chunk_{offset // 3 + 1:02d}.wav'
        with wave.open(str(destination), 'wb') as target:
            target.setparams(params)
            target.writeframes(pcm[start_sample * frame_width:end_sample * frame_width])
        entries.append((role, destination, PROSODY_PROMPT, {
            'target_kind': role, 'parent_input_path': str(source), 'parent_input_sha256': sha256_file(source),
            'clip_path': str(destination), 'clip_sha256': sha256_file(destination),
            'start': start_sample / params.framerate, 'end': end_sample / params.framerate,
            'source_start_sample': start_sample, 'source_end_sample': end_sample,
            'source_sample_rate': params.framerate, 'source_total_samples': params.nframes,
            'processed_audio': {'path': str(destination), 'sha256': sha256_file(destination),
                                'sample_rate': params.framerate, 'frames': end_sample - start_sample,
                                'start_sample': start_sample, 'end_sample': end_sample},
            'segment_ids': [item['id'] for item in group], 'review_mode': 'prosody_only_sentence_chunks',
        }))
        start_sample = end_sample
    if start_sample != params.nframes:
        raise RuntimeError('Chunk input coverage is incomplete')
    return entries


def make_speed_control(base, folder):
    source = folder / 'original_voice_pcm_chunk_01.wav'
    target = folder / 'control_speed_1p6.wav'
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg or not source.exists():
        raise RuntimeError('Existing source chunk and ffmpeg are required for the speed control')
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
               '-af', 'atempo=1.6', '-c:a', 'pcm_s16le', str(target)]
    subprocess.run(command, check=True, timeout=60, capture_output=True)
    with wave.open(str(source), 'rb') as stream:
        source_duration = stream.getnframes() / stream.getframerate()
    with wave.open(str(target), 'rb') as stream:
        target_duration = stream.getnframes() / stream.getframerate()
    return [('control_speed_1p6', target, PROSODY_PROMPT, {
        'target_kind': 'control_speed_1p6', 'is_control': True,
        'clip_path': str(target), 'clip_sha256': sha256_file(target),
        'control_source_path': str(source), 'control_source_sha256': sha256_file(source),
        'control_master_sha256': sha256_file(base / 'voice.wav'),
        'control_source_duration': source_duration, 'control_duration': target_duration,
        'observed_duration_ratio': source_duration / target_duration,
        'requested_tempo_factor': 1.6, 'preserve_pitch': True,
        'transform_command': command, 'review_mode': 'blind_speed_sensitivity_control',
        'alteration_disclosed_in_prompt': False,
    })]


def run(episode, mode='probe', gpu_layers=20):
    base = Path(episode).resolve().parent
    output = base / 'audio_understanding_local.json'
    folder = base / 'out' / 'audio_audit' / 'local_model'
    folder.mkdir(parents=True, exist_ok=True)
    video = base / 'out' / 'video.mp4'
    decoded = base / 'out' / 'audio_analysis' / 'final_audio_16k.wav'
    previous_asr = load_json(base / 'audio_analysis.json')
    if sha256_file(video) != previous_asr['video_sha256'] or sha256_file(decoded) != previous_asr['decoded_audio_sha256']:
        raise RuntimeError('Final video or decoded audio changed; re-extract before review.')
    if mode == 'probe':
        timeline = load_json(base / 'timeline.json')
        probe_seconds = next(item['end'] for item in timeline['segments'] if item['id'] == 'a.intro.0')
        real = folder / 'probe_opening_sentences.wav'
        silence = folder / 'control_silence.wav'
        make_probe(decoded, real, seconds=probe_seconds)
        make_probe(decoded, silence, seconds=probe_seconds, silent=True)
        targets = [('probe_real_audio', real, PROBE_PROMPT, {}), ('control_digital_silence', silence, PROBE_PROMPT, {})]
    elif mode == 'prosody':
        timeline = load_json(base / 'timeline.json')
        targets = make_chunks(base / 'voice.wav', folder, 'original_voice_pcm', timeline)
        targets += make_chunks(decoded, folder, 'final_video_decoded_audio', timeline)
    elif mode == 'speed_control':
        targets = make_speed_control(base, folder)
    else:
        targets = [('original_voice_pcm', base / 'voice.wav', FULL_PROMPT, {}), ('final_video_decoded_audio', decoded, FULL_PROMPT, {})]
    model_source = load_json(MODEL_DIR / 'model_source.json')
    actual_model_files = []
    for metadata in model_source['files']:
        if not metadata['name'].endswith('.gguf'):
            continue
        model_file = MODEL_DIR / metadata['name']
        current_hash = sha256_file(model_file)
        if current_hash != metadata['sha256'] or model_file.stat().st_size != metadata['bytes']:
            raise RuntimeError(f'Current model differs from verified source: {model_file.name}')
        actual_model_files.append({'name': model_file.name, 'sha256': current_hash,
                                   'bytes': model_file.stat().st_size})
    runtime_source = load_json(EXE.parent / 'runtime_source.json')
    timeline = load_json(base / 'timeline.json')
    evidence = load_json(output) if output.exists() else {'schema_version': 1, 'runs': []}
    runner_hash = sha256_file(Path(__file__))
    runner_snapshot = folder / ('runner_' + runner_hash + '.py')
    runner_snapshot.write_bytes(Path(__file__).read_bytes())
    evidence.update(status='IN_PROGRESS', reviewer_kind='local_audio_model', listened=False, human_listened=False,
                    codex_native_listened=False, audio_perception_verified=False, source_video_sha256=sha256_file(video),
                    original_voice_sha256=sha256_file(base / 'voice.wav'), final_decoded_sha256=sha256_file(decoded),
                    model_source=model_source, runtime_source=runtime_source, runtime_executable_sha256=sha256_file(EXE),
                    current_model_files_verified=actual_model_files, review_runner_sha256=runner_hash,
                    timeline_sha256=sha256_file(base / 'timeline.json'), input_hash=timeline['input_hash'],
                    external_audio_upload=False, note='Raw model observations require assessment. This is not a human listening claim.')
    if (MODEL_DIR / 'license_review.json').exists():
        evidence['license_review'] = load_json(MODEL_DIR / 'license_review.json')
    for role, audio, prompt, extra in targets:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:6]
        with wave.open(str(audio), 'rb') as f:
            duration = f.getnframes() / f.getframerate()
        command = [str(EXE), '-m', str(MODEL_DIR / 'Qwen2.5-Omni-7B-Q4_K_M.gguf'),
                   '--mmproj', str(MODEL_DIR / 'mmproj-Qwen2.5-Omni-7B-f16.gguf'),
                   '--audio', str(audio), '--no-mmproj-offload', '-ngl', str(gpu_layers),
                   '--offline', '--seed', '0',
                   '-c', '4096', '-n', '1536' if mode == 'full' else '512' if mode == 'prosody' else '768', '--temp', '0', '-t', '8', '-tb', '8',
                   '-p', prompt]
        item = {'run_id': run_id, 'input_role': role, 'input_path': str(audio), 'input_sha256': sha256_file(audio),
                'review_runner_sha256': runner_hash, 'review_runner_source': str(runner_snapshot),
                'input_span_seconds': [0, duration], 'coverage_verified': False,
                'prompt': prompt, 'script_or_expected_words_provided': False,
                'command': command, 'status': 'IN_PROGRESS', 'started_at': datetime.now(timezone.utc).isoformat()}
        item.update(extra)
        if 'parent_input_sha256' in extra:
            item['input_sha256'] = extra['parent_input_sha256']
        evidence['runs'].append(item)
        stdout_path = folder / (run_id + '.stdout.txt')
        stderr_path = folder / (run_id + '.stderr.txt')
        item.update(stdout_path=str(stdout_path), stderr_path=str(stderr_path))
        atomic_json(output, evidence)
        started = time.monotonic()
        try:
            with stdout_path.open('wb') as stdout_stream, stderr_path.open('wb') as stderr_stream:
                result = subprocess.run(command, stdout=stdout_stream, stderr=stderr_stream,
                                        timeout=900, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            item.update(returncode=result.returncode,
                        stdout=stdout_path.read_text(encoding='utf-8', errors='replace'),
                        stderr=stderr_path.read_text(encoding='utf-8', errors='replace'),
                        elapsed_seconds=time.monotonic() - started, status='MODEL_RETURNED' if result.returncode == 0 else 'FAILED')
        except Exception as exc:
            item.update(status='FAILED', error=f'{type(exc).__name__}: {exc}', elapsed_seconds=time.monotonic() - started)
            for name, path in [('stdout', stdout_path), ('stderr', stderr_path)]:
                if path.exists():
                    item[name] = path.read_text(encoding='utf-8', errors='replace')
            evidence['status'] = 'FAILED'
            atomic_json(output, evidence)
            raise
        atomic_json(output, evidence)
        print(json.dumps({'role': role, 'status': item['status'], 'seconds': item['elapsed_seconds'], 'stdout': item.get('stdout', '')}, ensure_ascii=True), flush=True)
        if item['status'] == 'FAILED':
            evidence['status'] = 'FAILED'
            atomic_json(output, evidence)
            raise RuntimeError('Local audio model failed; full stderr retained in evidence.')
    evidence['status'] = 'MODEL_OUTPUT_REQUIRES_ASSESSMENT'
    atomic_json(output, evidence)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('episode')
    parser.add_argument('--mode', choices=('probe', 'full', 'prosody', 'speed_control'), default='probe')
    parser.add_argument('--gpu-layers', type=int, default=20)
    args = parser.parse_args()
    run(args.episode, args.mode, args.gpu_layers)
