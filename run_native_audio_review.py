"""Offline Qwen Thinker audio controls/review using the official Transformers path.

No quality PASS or human-listening claim is assigned by this runner.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import wave
from importlib.metadata import version

from episode_core import load_json, atomic_json, sha256_file

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / '.models' / 'Qwen2.5-Omni-7B-native'
for key, value in {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                   'HF_HOME': str(ROOT / '.models' / 'native-hf-home')}.items():
    os.environ[key] = value

PROMPT = """Describe only what you can hear in this recording. Do not transcribe the speech.
Give concise numbered answers:
1. How many distinct groups of speech are separated by clear pauses? If unclear, say uncertain.
2. Are there audible pauses between the groups? Describe their relative length from the sound.
3. Is the speaking pace slow, moderate, or fast compared with ordinary conversation? Give an audible reason.
4. For each speech group you can distinguish, does the ending pitch rise, fall, or remain level?
Do not infer intonation from meaning or punctuation; say uncertain when you cannot hear it reliably.
5. Are word endings complete, or is there an abrupt cutoff, distortion, or interfering background sound?
Avoid generic compliments and numeric quality scores. If the input is silent, report that and stop.
Answer in English and state any uncertainty."""
COMPARE_PROMPT = """Two recordings labelled A and B are supplied. Compare their sound, without transcribing.
Which recording has faster speech, A or B, or is the difference uncertain? Explain the audible evidence.
Compare the pauses between speech groups as well. Do not assume that the order implies the answer.
Avoid quality scores and generic compliments. Answer concisely in English."""


def file_meta(path):
    return {'filename': path.name, 'bytes': path.stat().st_size, 'sha256': sha256_file(path)}


def json_values(value):
    if isinstance(value, dict):
        return {str(key): json_values(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [json_values(item) for item in sorted(value, key=repr)]
    if isinstance(value, (tuple, list)):
        return [json_values(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def verified_model():
    plan = load_json(MODEL_DIR / 'thinker_download_plan.json')
    receipt = load_json(MODEL_DIR / 'model_source.json')
    if receipt['repository'] != plan['repository'] or receipt['revision'] != plan['revision']:
        raise RuntimeError('Downloaded artifact receipt does not match the fixed model revision')
    received = {item['file']: item for item in receipt['files']}
    if len(received) != len(receipt['files']):
        raise RuntimeError('Duplicate artifact identities in the download receipt')
    result = {'format': 'safetensors', 'repository': plan['repository'], 'revision': plan['revision'],
              'weight_shards': [], 'processor_artifacts': [], 'source_plan_sha256': sha256_file(MODEL_DIR / 'thinker_download_plan.json')}
    result['source_manifest_sha256'] = sha256_file(MODEL_DIR / 'model_source.json')
    for expected in plan['selected_shards']:
        name = expected['file']
        if Path(name).name != name or '/' in name or '\\' in name:
            raise RuntimeError('Unsafe shard basename')
        observed = file_meta(MODEL_DIR / name)
        if observed['bytes'] != expected['bytes'] or observed['sha256'] != expected['sha256']:
            raise RuntimeError(f'Official shard hash mismatch: {name}')
        observed['official_sha256_verified'] = True
        result['weight_shards'].append(observed)
    original_index = MODEL_DIR / 'model.safetensors.index.original.json'
    if sha256_file(original_index) != next(x['sha256'] for x in plan['metadata_downloaded'] if x['file'] == 'model.safetensors.index.json'):
        raise RuntimeError('Original index hash mismatch')
    original = load_json(original_index)
    active = load_json(MODEL_DIR / 'model.safetensors.index.json')
    expected_map = {key: value for key, value in original['weight_map'].items() if key.startswith('thinker.')}
    if active['weight_map'] != expected_map or len(expected_map) != 1346:
        raise RuntimeError('Active index is not the complete original Thinker subset')
    result['active_index_sha256'] = sha256_file(MODEL_DIR / 'model.safetensors.index.json')
    result['original_index_sha256'] = sha256_file(original_index)
    result['thinker_tensor_count'] = len(expected_map)
    config = file_meta(MODEL_DIR / 'config.json')
    if config['sha256'] != next(x['sha256'] for x in plan['metadata_downloaded'] if x['file'] == 'config.json'):
        raise RuntimeError('Original configuration hash mismatch')
    result['config_sha256'] = config['sha256']
    for expected in plan['tokenizer_processor_candidates']:
        observed = file_meta(MODEL_DIR / expected['file'])
        source_receipt = received[expected['file']]
        if observed['bytes'] != expected['bytes'] or observed['sha256'] != source_receipt['sha256']:
            raise RuntimeError(f'Processor artifact does not match the official download receipt: {expected["file"]}')
        observed['download_verification'] = source_receipt['verification']
        observed['source_url'] = source_receipt['url']
        if expected['file'] == 'LICENSE':
            result['license'] = observed
        else:
            result['processor_artifacts'].append(observed)
    return result


def slice_inputs(base, folder):
    timeline = load_json(base / 'timeline.json')
    targets = []
    for role, source in [('original_pcm', base / 'voice.wav'),
                         ('final_mp4_audio', base / 'out' / 'audio_analysis' / 'final_audio_16k.wav')]:
        with wave.open(str(source), 'rb') as audio:
            params = audio.getparams()
            pcm = audio.readframes(params.nframes)
        width = params.nchannels * params.sampwidth
        start = 0
        for index in range(0, len(timeline['segments']), 3):
            group = timeline['segments'][index:index + 3]
            end = params.nframes if index + 3 >= len(timeline['segments']) else round(group[-1]['end'] * params.framerate)
            clip = folder / f'{role}_{index // 3 + 1:02d}.wav'
            with wave.open(str(clip), 'wb') as output:
                output.setparams(params)
                output.writeframes(pcm[start * width:end * width])
            targets.append({'target_kind': role, 'input_sha256': sha256_file(source),
                'parent_input_path': str(source), 'parent_input_sha256': sha256_file(source),
                'clip_path': str(clip), 'clip_sha256': sha256_file(clip),
                'start': start / params.framerate, 'end': end / params.framerate,
                'segment_ids': [segment['id'] for segment in group],
                'processed_audio': {'path': str(clip.relative_to(base)), 'sha256': sha256_file(clip),
                                    'sample_rate': params.framerate, 'frames': end - start,
                                    'start_sample': start, 'end_sample': end},
                'coverage_verified': False, 'quality_pass_assigned': False})
            start = end
    return targets


def controls(real, folder):
    source = Path(real['clip_path'])
    with wave.open(str(source), 'rb') as audio:
        params = audio.getparams()
        pcm = audio.readframes(params.nframes)
    silent = folder / 'control_silence.wav'
    with wave.open(str(silent), 'wb') as output:
        output.setparams(params)
        output.writeframes(b'\0' * len(pcm))
    fast = folder / 'control_speed_1p6.wav'
    command = [shutil.which('ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
               '-af', 'atempo=1.6', '-c:a', 'pcm_s16le', str(fast)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    with wave.open(str(fast), 'rb') as audio:
        fast_duration = audio.getnframes() / audio.getframerate()
    return [dict(real, control_role='real_opening'),
            {'target_kind': 'control_silence', 'control_role': 'digital_silence', 'is_control': True,
             'clip_path': str(silent), 'clip_sha256': sha256_file(silent), 'input_sha256': sha256_file(silent)},
            {'target_kind': 'control_speed_1p6', 'control_role': 'faster_speech', 'is_control': True,
             'clip_path': str(fast), 'clip_sha256': sha256_file(fast), 'input_sha256': sha256_file(fast),
             'control_source_sha256': real['clip_sha256'], 'requested_tempo': 1.6,
             'observed_duration_ratio': (params.nframes / params.framerate) / fast_duration,
             'transform_command': command, 'alteration_disclosed_in_prompt': False}]


def waveform_array(path, folder, identity):
    import numpy as np
    from scipy.signal import resample_poly
    with wave.open(str(path), 'rb') as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise RuntimeError('Expected original mono 16-bit PCM input')
        rate = source.getframerate()
        raw = source.readframes(source.getnframes())
    samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / np.float32(32768.0)
    divisor = math.gcd(rate, 16000)
    if rate != 16000:
        samples = resample_poly(samples, 16000 // divisor, rate // divisor,
                                window=('kaiser', 5.0), padtype='constant').astype(np.float32)
    samples = np.ascontiguousarray(samples, dtype='<f4')
    destination = folder / (identity + '_16k.npy')
    np.save(destination, samples, allow_pickle=False)
    metadata = {'source_wav_sha256': sha256_file(path), 'source_rate': rate, 'sample_rate': 16000,
                'dtype': str(samples.dtype), 'shape': list(samples.shape),
                'raw_array_sha256': hashlib.sha256(samples.tobytes(order='C')).hexdigest(),
                'array_file': str(destination), 'array_file_sha256': sha256_file(destination),
                'resampling': 'none' if rate == 16000 else 'scipy.signal.resample_poly',
                'up': 16000 // divisor, 'down': rate // divisor, 'window': ['kaiser', 5.0], 'padtype': 'constant'}
    return samples, metadata


def run(episode, stage):
    import numpy as np
    import torch
    import bitsandbytes as bnb
    import transformers.models.qwen2_5_omni.modeling_qwen2_5_omni as model_source
    import transformers.models.qwen2_5_omni.processing_qwen2_5_omni as processor_source
    from transformers import BitsAndBytesConfig, Qwen2_5OmniConfig, Qwen2_5OmniProcessor, Qwen2_5OmniThinkerForConditionalGeneration
    from accelerate.hooks import remove_hook_from_module, add_hook_to_module, AlignDevicesHook
    base = Path(episode).resolve().parent
    folder = base / 'out' / 'audio_audit' / 'native_model'
    folder.mkdir(parents=True, exist_ok=True)
    report = base / 'audio_understanding_native.json'
    evidence = load_json(report) if report.exists() else {'schema_version': 1, 'runs': [], 'loads': []}
    evidence.update(status='PREPARING', reviewer_kind='local_audio_model', listened=False,
                    human_listened=False, codex_native_listened=False, audio_perception_verified=False,
                    external_audio_upload=False, quality_pass_assigned=False,
                    source_video_sha256=sha256_file(base / 'out' / 'video.mp4'),
                    original_voice_sha256=sha256_file(base / 'voice.wav'),
                    final_decoded_sha256=sha256_file(base / 'out' / 'audio_analysis' / 'final_audio_16k.wav'))
    atomic_json(report, evidence)
    try:
        if shutil.disk_usage(ROOT).free < 10 * 2**30:
            raise RuntimeError('Less than 10 GiB free disk; stopping before model load')
        prior = load_json(base / 'audio_analysis.json')
        if evidence['source_video_sha256'] != prior['video_sha256'] or evidence['final_decoded_sha256'] != prior['decoded_audio_sha256']:
            raise RuntimeError('Current final video/audio no longer match the decoded-input provenance')
        evidence['model'] = verified_model()
        evidence['input_hash'] = load_json(base / 'timeline.json')['input_hash']
        evidence['runtime'] = {'python': sys.version, 'executable': sys.executable, 'executable_sha256': sha256_file(Path(sys.executable)),
                              'versions': {name: version(name) for name in ['torch', 'transformers', 'accelerate', 'bitsandbytes', 'safetensors', 'numpy', 'scipy']},
                              'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(0),
                              'source_files': [{'module': source.__name__, 'sha256': sha256_file(Path(inspect.getfile(source)))} for source in [model_source, processor_source]],
                              'runner_sha256': sha256_file(Path(__file__))}
        torch.manual_seed(0)
        torch.set_num_threads(8)
        config = Qwen2_5OmniConfig.from_pretrained(MODEL_DIR, local_files_only=True).thinker_config
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True,
                                  bnb_4bit_compute_dtype=torch.bfloat16,
                                  llm_int8_skip_modules=['model.audio_tower', 'model.visual', 'lm_head'],
                                  llm_int8_enable_fp32_cpu_offload=True)
        device_map = {'model.language_model': 'cuda:0', 'model.audio_tower': 'cpu', 'model.visual': 'cpu', 'lm_head': 'cuda:0'}
        load_record = {'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'IN_PROGRESS',
                       'device_map_requested': device_map, 'quantization_config': quant.to_dict(), 'dtype_requested': 'bfloat16'}
        evidence['loads'].append(load_record)
        evidence['status'] = 'LOADING'
        atomic_json(report, evidence)
        started = time.monotonic()
        model, loading_info = Qwen2_5OmniThinkerForConditionalGeneration.from_pretrained(
            MODEL_DIR, config=config, local_files_only=True, trust_remote_code=False,
            dtype=torch.bfloat16, quantization_config=quant, device_map=device_map,
            attn_implementation='eager', output_loading_info=True)
        loading_info = json_values(loading_info)
        load_record.update(elapsed_seconds=time.monotonic() - started, loading_info=loading_info,
                           actual_device_map=json_values(model.hf_device_map))
        atomic_json(report, evidence)
        if any(loading_info.get(name) for name in ['missing_keys', 'mismatched_keys', 'error_msgs']):
            raise RuntimeError('Incomplete/mismatched learned model weights; inference prohibited')
        unexplained = [key for key in loading_info.get('unexpected_keys', []) if not key.startswith(('talker.', 'token2wav.'))]
        if unexplained:
            raise RuntimeError('Unexplained checkpoint keys; inference prohibited')
        quantized = [name for name, module in model.named_modules() if isinstance(module, bnb.nn.Linear4bit)]
        if not quantized or any(not name.startswith('model.language_model.') for name in quantized):
            raise RuntimeError('Quantization did not stay inside the language model')
        meta_before = [name for name, parameter in model.named_parameters() if parameter.is_meta]
        load_record['offloaded_meta_before_binding'] = meta_before
        if any(not name.startswith(('model.audio_tower.', 'model.visual.')) for name in meta_before):
            raise RuntimeError('Unexpected unmaterialized language parameter; inference prohibited')
        for name in ['audio_tower', 'visual']:
            module = getattr(model.model, name)
            remove_hook_from_module(module, recurse=True)
            module.to(device='cpu', dtype=torch.float32 if name == 'audio_tower' else torch.bfloat16)
            add_hook_to_module(module, AlignDevicesHook(execution_device=torch.device('cpu'),
                               offload=False, io_same_device=False, place_submodules=True))
        if any(parameter.is_meta for parameter in model.parameters()):
            raise RuntimeError('Unmaterialized parameter after explicit CPU binding; inference prohibited')
        load_record.update(status='LOADED_VERIFIED', quantized_modules=quantized,
                           audio_encoder_dtype=str(next(model.model.audio_tower.parameters()).dtype),
                           audio_encoder_device=str(next(model.model.audio_tower.parameters()).device),
                           audio_n_window=model.model.audio_tower.n_window,
                           cuda_allocated_bytes=torch.cuda.memory_allocated(), cuda_peak_bytes=torch.cuda.max_memory_allocated())
        encoder_events = []
        def record_conv_input(module, args):
            encoder_events.append({'event': 'conv1_input', 'input_device': str(args[0].device),
                                   'input_dtype': str(args[0].dtype), 'parameter_device': str(module.weight.device),
                                   'parameter_dtype': str(module.weight.dtype), 'shape': list(args[0].shape)})
        def record_encoder_output(module, args, output):
            encoder_events.append({'event': 'encoder_output', 'device': str(output.last_hidden_state.device),
                                   'dtype': str(output.last_hidden_state.dtype), 'shape': list(output.last_hidden_state.shape)})
        model.model.audio_tower.conv1.register_forward_pre_hook(record_conv_input)
        model.model.audio_tower.register_forward_hook(record_encoder_output)
        load_record['explicit_execution_binding'] = {'audio_tower': 'CPU FP32 using non-offload AlignDevicesHook',
                                                     'visual': 'CPU BF16; not invoked for audio-only inputs'}
        processor = Qwen2_5OmniProcessor.from_pretrained(MODEL_DIR, local_files_only=True, trust_remote_code=False)
        model.eval()
        sources = slice_inputs(base, folder)
        probe_targets = controls(sources[0], folder) if stage in ['controls', 'compare'] else []
        if stage == 'controls':
            jobs = [(target, [target], PROMPT, None) for target in probe_targets]
        elif stage == 'compare':
            # Identity/order is retained in evidence but not disclosed to the model.
            pair = [probe_targets[2], probe_targets[0]]
            jobs = [({'target_kind': 'control_pair', 'is_control': True, 'control_role': 'blind_A_B_speed_comparison',
                      'label_assignment': {'A': 'faster_speech', 'B': 'real_opening'}}, pair, COMPARE_PROMPT, ['A', 'B'])]
        else:
            jobs = [(target, [target], PROMPT, None) for target in sources]
        for target, inputs, prompt, labels in jobs:
            identity = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:6]
            arrays, preprocess, content = [], [], []
            for index, item in enumerate(inputs):
                array, metadata = waveform_array(Path(item['clip_path']), folder, identity + '_' + str(index))
                arrays.append(array)
                preprocess.append(metadata)
                if labels:
                    content.append({'type': 'text', 'text': 'Recording ' + labels[index] + ':'})
                content.append({'type': 'audio', 'audio': item['clip_path']})
            content.append({'type': 'text', 'text': prompt})
            messages = [{'role': 'system', 'content': [{'type': 'text', 'text': 'Describe recordings from the sound itself and state uncertainty.'}]},
                        {'role': 'user', 'content': content}]
            rendered = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
            if any(Path(entry['clip_path']).name in rendered for entry in inputs):
                raise RuntimeError('Audio filename leaked into the model text prompt')
            features = processor(text=rendered, audio=arrays, sampling_rate=16000, return_tensors='pt', padding=True)
            feature_meta = {}
            saved_tensors = {}
            for name, tensor in features.items():
                if isinstance(tensor, torch.Tensor):
                    raw = tensor.detach().cpu().contiguous()
                    feature_meta[name] = {'shape': list(raw.shape), 'dtype': str(raw.dtype),
                                          'raw_sha256': hashlib.sha256(raw.view(torch.uint8).numpy().tobytes()).hexdigest()}
                    saved_tensors[name] = raw.numpy()
            feature_meta['feature_attention_mask_sums'] = features['feature_attention_mask'].sum(-1).tolist()
            features_path = folder / (identity + '_processor_inputs.npz')
            np.savez(features_path, **saved_tensors)
            feature_meta['saved_inputs'] = {'path': str(features_path), 'sha256': sha256_file(features_path)}
            item = dict(target, run_id=identity, stage=stage, status='IN_PROGRESS', prompt=prompt,
                        rendered_prompt=rendered, messages=messages, script_or_expected_words_provided=False,
                        preprocessing=preprocess, processor_tensors=feature_meta,
                        started_at=datetime.now(timezone.utc).isoformat(), actual_input_files=inputs)
            evidence['runs'].append(item)
            evidence['status'] = 'IN_PROGRESS'
            atomic_json(report, evidence)
            device_inputs = {key: value.to('cuda:0') if isinstance(value, torch.Tensor) else value for key, value in features.items()}
            model.model.rope_deltas = None
            encoder_events.clear()
            started = time.monotonic()
            with torch.inference_mode():
                tokens = model.generate(**device_inputs, do_sample=False, max_new_tokens=384, use_cache=True,
                                        eos_token_id=processor.tokenizer.eos_token_id,
                                        pad_token_id=processor.tokenizer.pad_token_id)
            torch.cuda.synchronize()
            generated = tokens[0, features['input_ids'].shape[1]:].detach().cpu()
            response = processor.tokenizer.decode(generated, skip_special_tokens=True)
            output_file = folder / (identity + '.response.txt')
            output_file.write_text(response, encoding='utf-8')
            item.update(status='MODEL_RETURNED', response=response, generated_token_ids=generated.tolist(),
                        response_file=str(output_file), response_sha256=sha256_file(output_file),
                        elapsed_seconds=time.monotonic() - started, cuda_peak_bytes=torch.cuda.max_memory_allocated(),
                        audio_encoder_execution_events=list(encoder_events))
            if not encoder_events or any(event.get('input_device', event.get('device')) != 'cpu' for event in encoder_events):
                raise RuntimeError('Actual audio encoder CPU execution was not verified')
            atomic_json(report, evidence)
            print(json.dumps({'stage': stage, 'target': target['target_kind'], 'seconds': item['elapsed_seconds'], 'response': response}, ensure_ascii=True), flush=True)
        evidence['status'] = 'MODEL_OUTPUT_REQUIRES_ASSESSMENT'
        atomic_json(report, evidence)
    except Exception as exc:
        evidence['status'] = 'FAILED'
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        for records in [evidence['loads'], evidence['runs']]:
            if records and records[-1]['status'] == 'IN_PROGRESS':
                records[-1].update(status='FAILED', error=evidence['error'])
        atomic_json(report, evidence)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('episode')
    parser.add_argument('--stage', choices=['controls', 'compare', 'full'], default='controls')
    args = parser.parse_args()
    run(args.episode, args.stage)
