"""Run local DNSMOS waveform diagnostics; never approve listening or pronunciation.

The P.835 polynomial calibration is from microsoft/DNS-Challenge/dnsmos_local.py.
Model provenance and the original implementation are retained beside the model.
Only a pre-downloaded ONNX model is accepted; no network calls are made here.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import subprocess
import wave

import numpy as np
import onnxruntime as ort

from episode_core import atomic_json, sha256_file


def evaluate(episode, model_dir):
    base = Path(episode).resolve().parent
    model_dir = Path(model_dir).resolve()
    model = model_dir / "sig_bak_ovr.onnx"
    provenance = json.loads((model_dir / "source_manifest.json").read_text(encoding="utf-8"))
    if sha256_file(model) != provenance["files"][model.name]["sha256"]:
        raise ValueError("DNSMOS model does not match recorded source hash")
    video = base / "out/video.mp4"
    video_hash = sha256_file(video)
    folder = base / "out/audio_signal"
    folder.mkdir(parents=True, exist_ok=True)
    wav_path = folder / "final_audio_16k.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0:a:0", "-vn",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)], check=True)
    with wave.open(str(wav_path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError("Expected mono PCM16 at 16 kHz")
        values = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    length = 144160  # 9.01 seconds, upstream P.835 model input.
    if len(values) < length:
        raise ValueError("This diagnostic requires at least 9.01 seconds without looping audio")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    starts = list(range(0, len(values) - length + 1, 16000))
    # Include the actual ending as a separate measured window, not repeated/padded audio.
    if starts[-1] != len(values) - length:
        starts.append(len(values) - length)
    coefficients = {
        "SIG": [-0.08397278, 1.22083953, 0.0052439],
        "BAK": [-0.13166888, 1.60915514, -0.39604546],
        "OVRL": [-0.06766283, 1.11546468, 0.04602535],
    }
    windows = []
    for start in starts:
        raw = session.run(None, {session.get_inputs()[0].name: values[start:start + length][None, :]})[0][0]
        item = {"start": start / 16000, "end": (start + length) / 16000,
                "raw": dict(zip(coefficients, map(float, raw)))}
        item["scores"] = {name: float(np.polyval(coef, value))
                          for (name, coef), value in zip(coefficients.items(), raw)}
        windows.append(item)
    summary = {name: {"mean": float(np.mean([x["scores"][name] for x in windows])),
                      "min": float(min(x["scores"][name] for x in windows)),
                      "max": float(max(x["scores"][name] for x in windows))}
               for name in coefficients}
    if video_hash != sha256_file(video):
        raise RuntimeError("Video changed during audio analysis")
    record = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "status": "DIAGNOSTIC_COMPLETED_NOT_LISTENING_APPROVAL", "video_sha256": video_hash,
              "audio_path": str(wav_path.relative_to(base)), "audio_sha256": sha256_file(wav_path),
              "duration": len(values) / 16000, "model_provenance": provenance,
              "runtime": {"onnxruntime": importlib.metadata.version("onnxruntime"), "provider": "CPUExecutionProvider"},
              "summary": summary, "windows": windows,
              "limitations": ["These are model estimates, not human MOS measurements or a pass threshold.",
                              "DNSMOS was designed for noise-suppression evaluation; it does not validate Korean words, names, intonation, or TTS naturalness.",
                              "Overlapping windows are correlated. The final additional window ensures end coverage; its score is not the exact upstream aggregate."]}
    atomic_json(base / "audio_signal_diagnostics.json", record)
    return {"status": record["status"], "windows": len(windows), "summary": summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode")
    parser.add_argument("--model-dir", default=".models/dnsmos")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.episode, args.model_dir), ensure_ascii=False, indent=2))
