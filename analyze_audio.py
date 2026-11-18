"""Offline ASR/PCM diagnostics of the FINAL MP4; never a listening approval.

Requires the separate .asr-venv and a previously downloaded local small model.
No remote model loading, audio upload, script prompt, or audio_review mutation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata
import wave

from episode_core import atomic_json, load_json, sha256_file


def normalized(text):
    return re.sub(r"[^가-힣A-Za-z0-9]", "", unicodedata.normalize("NFC", text)).lower()


def distance(left, right):
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        previous, row = row, [i]
        for j, b in enumerate(right, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
    return row[-1]


def diagnostics(audio, sample_rate, timeline, video):
    import numpy as np
    values = np.asarray(audio, dtype=np.float64)
    def db(value):
        return 20 * math.log10(value) if value > 0 else None
    sentence_levels = []
    for segment in timeline["segments"]:
        start = round(segment["speech_start"] * sample_rate)
        end = round(segment["speech_end"] * sample_rate)
        part = values[start:end]
        sentence_levels.append({"id": segment["id"], "duration": len(part) / sample_rate,
                                "peak_dbfs": db(float(np.max(np.abs(part)))),
                                "rms_dbfs": db(float(np.sqrt(np.mean(part * part))))})
    command = ["ffmpeg", "-hide_banner", "-i", str(video), "-map", "0:a:0", "-vn",
               "-af", "ebur128=peak=true", "-f", "null", "NUL" if os.name == "nt" else "/dev/null"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=True)
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    def metric(pattern):
        match = re.search(pattern, summary)
        return float(match.group(1)) if match else None
    levels = [item["rms_dbfs"] for item in sentence_levels if item["rms_dbfs"] is not None]
    return {"basis": "decoded audio from final MP4; not TTS text metadata",
            "peak_dbfs_16k_pcm": db(float(np.max(np.abs(values)))),
            "rms_dbfs_16k_pcm": db(float(np.sqrt(np.mean(values * values)))),
            "dc_offset": float(np.mean(values)), "full_scale_samples": int(np.count_nonzero(np.abs(values) >= 32767 / 32768)),
            "integrated_lufs": metric(r"Integrated loudness:\s+I:\s+(-?[\d.]+)"),
            "loudness_range_lu": metric(r"Loudness range:\s+LRA:\s+(-?[\d.]+)"),
            "true_peak_dbfs": metric(r"True peak:\s+Peak:\s+(-?[\d.]+)"),
            "sentence_rms_range_db": max(levels) - min(levels), "sentence_levels": sentence_levels,
            "limitations": "No clipping flags or consistent level metrics do not prove intelligibility, pronunciation, or natural intonation."}


def analyze(episode_path, model_dir=None):
    import numpy as np
    root = Path(__file__).resolve().parent
    base = Path(episode_path).resolve().parent
    model_dir = Path(model_dir or root / ".models" / "faster-whisper-small").resolve()
    if not (model_dir / "model.bin").is_file():
        raise RuntimeError("Local ASR model is missing. This script never downloads it implicitly.")
    # These flags are set before importing the HF-dependent package.
    os.environ["HF_HOME"] = str(root / ".models" / "hf-home")
    os.environ["HF_HUB_CACHE"] = str(root / ".models" / "hub")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from faster_whisper import WhisperModel
    video = base / "out" / "video.mp4"
    folder = base / "out" / "audio_analysis"
    folder.mkdir(parents=True, exist_ok=True)
    audio_file = folder / "final_audio_16k.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0:a:0", "-vn",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio_file)], check=True, timeout=60)
    with wave.open(str(audio_file), "rb") as wav:
        sample_rate = wav.getframerate()
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    options = {"language": "ko", "task": "transcribe", "beam_size": 5, "temperature": 0.0,
               "condition_on_previous_text": False, "word_timestamps": True, "vad_filter": False,
               "initial_prompt": None, "prefix": None, "hotwords": None}
    started = time.monotonic()
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=min(8, os.cpu_count() or 1),
                         local_files_only=True)
    generated, info = model.transcribe(audio, **options)
    full_segments = [asdict(segment) for segment in generated]
    full_text = " ".join(segment["text"].strip() for segment in full_segments)
    source = load_json(model_dir / "model_source.json")
    analysis = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "ASR_ANALYSIS_IN_PROGRESS", "listened": False,
                "audio_review_modified": False, "audio_uploaded": False,
                "source_video": str(video), "video_sha256": sha256_file(video),
                "decoded_audio": str(audio_file), "decoded_audio_sha256": sha256_file(audio_file),
                "decoded_duration": len(audio) / sample_rate, "sample_rate": sample_rate,
                "versions": {name: importlib.metadata.version(name) for name in ("faster-whisper", "ctranslate2", "av", "numpy")},
                "python": sys.version, "model": source, "device": "cpu", "compute_type": "int8",
                "asr_options": options, "full_transcript": full_text, "full_segments": full_segments,
                "full_pass_seconds": time.monotonic() - started,
                "limitations": ["No script, character names, or expected words were supplied as ASR prompts.",
                                "ASR may confuse names, homophones, spacing, and inflections.",
                                "Independent isolated passes use the same model, not a separate recognizer.",
                                "ASR agreement is not a naturalness/intonation or actual listening PASS."]}
    # Save the independent full recording result BEFORE loading the expected script.
    atomic_json(base / "audio_analysis.json", analysis)
    print(json.dumps({"stage": "full_transcript", "text": full_text, "seconds": analysis["full_pass_seconds"]}, ensure_ascii=False), flush=True)
    timeline = load_json(base / "timeline.json")
    analysis["timeline_sha256"] = sha256_file(base / "timeline.json")
    words = [word for segment in full_segments for word in segment.get("words", [])]
    comparisons = []
    for segment in timeline["segments"]:
        expected = segment.get("tts_text") or segment["spoken_text"]
        assigned = [word for word in words if segment["start"] <= (word["start"] + word["end"]) / 2 < segment["end"]]
        recognized = "".join(word["word"] for word in assigned).strip()
        norm_expected, norm_recognized = normalized(expected), normalized(recognized)
        errors = distance(norm_expected, norm_recognized)
        comparison = {"id": segment["id"], "start": segment["start"], "end": segment["end"],
                      "expected_for_comparison_only": expected, "full_pass_recognized": recognized,
                      "normalized_edit_distance": errors, "character_error_rate": errors / max(1, len(norm_expected)),
                      "status": "ASR_TEXT_AGREEMENT" if not errors else "ASR_DISCREPANCY_REQUIRES_REVIEW"}
        if errors:
            chunk = audio[round(segment["start"] * sample_rate):round(segment["end"] * sample_rate)]
            generated, _ = model.transcribe(chunk, **options)
            isolated = [asdict(item) for item in generated]
            text = " ".join(item["text"].strip() for item in isolated)
            comparison.update(isolated_pass_recognized=text, isolated_segments=isolated,
                              isolated_edit_distance=distance(norm_expected, normalized(text)),
                              isolated_pass_context="Only the current sentence's final-video audio, no text prompt or preceding transcript")
            comparison["assessment"] = ("Isolated ASR agrees; full-pass mismatch may be alignment/context or recognition error."
                                         if normalized(text) == norm_expected else
                                         "Discrepancy persists in the same model. This does not distinguish mispronunciation from ASR error.")
            print(json.dumps({"stage": "suspect_sentence", "id": segment["id"], "expected": expected,
                              "full": recognized, "isolated": text}, ensure_ascii=False), flush=True)
        comparisons.append(comparison)
        analysis["sentence_comparisons"] = comparisons
        atomic_json(base / "audio_analysis.json", analysis)
    analysis["acoustic_diagnostics"] = diagnostics(audio, sample_rate, timeline, video)
    analysis["total_analysis_seconds"] = time.monotonic() - started
    analysis["suspected_sentence_ids"] = [item["id"] for item in comparisons if item["normalized_edit_distance"]]
    analysis["persistent_discrepancy_ids"] = [item["id"] for item in comparisons if item.get("isolated_edit_distance", 0)]
    analysis["independent_text_agreement_count"] = sum(not item["normalized_edit_distance"] or item.get("isolated_edit_distance") == 0 for item in comparisons)
    analysis["status"] = "ASR_ANALYSIS_COMPLETE_AUDIO_REVIEW_REQUIRED"
    atomic_json(base / "audio_analysis.json", analysis)
    print(json.dumps({"stage": "done", "report": str(base / "audio_analysis.json"),
                      "persistent_discrepancies": analysis["persistent_discrepancy_ids"],
                      "agreement_count": analysis["independent_text_agreement_count"], "seconds": analysis["total_analysis_seconds"]}, ensure_ascii=False), flush=True)
    return analysis


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode")
    parser.add_argument("--model-dir")
    args = parser.parse_args()
    analyze(args.episode, args.model_dir)
