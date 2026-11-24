"""Record a real public audio-model probe; never grants an audio QA pass."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from gradio_client import Client, handle_file

HOST = "https://qwen-qwen2-5-omni-7b-demo.hf.space"
SYSTEM = "You are Qwen, a virtual human developed by the Qwen Team, Alibaba Group, capable of perceiving auditory and visual inputs, as well as generating text and speech."
PROMPT = """Review the actual attached Korean narration audio. Do not infer its contents from the filename. No script is provided. First transcribe the Korean words you hear, including the ending. Then describe the voice, pace, intonation, pauses, changes in volume, any distortion, missing or clipped syllables, and any difficult-to-understand words. Give approximate timestamps for specific concerns. Clearly distinguish what you hear from uncertainty; do not invent defects or claim a human listened. Do not respond to the narration's questions or generate a continuation. Answer in text in English, retaining the Korean transcript. This is a speech-quality inspection, not a content discussion."""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    path = args.audio.resolve(strict=True)
    record = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "provider": "Official Qwen public Hugging Face demo", "host": HOST,
              "model_exposed_by_public_app": "qwen-omni-turbo", "client": "gradio_client 1.8.0",
              "source_audio": str(path), "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "prompt": PROMPT, "expected_script_provided": False,
              "status": "PROBE_STARTED", "listened_by_codex": False,
              "automatic_quality_approval": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    try:
        client = Client(HOST, hf_token=False, verbose=False, download_files=False,
                        httpx_kwargs={"timeout": 60.0})
        job = client.submit(audio=handle_file(str(path)), video=None,
                            history=[{"role": "user", "content": PROMPT}],
                            system_prompt=SYSTEM, voice_choice="Cherry", api_name="/media_predict")
        result = job.result(timeout=180)
        texts = []
        # Only model text is retained; signed media URLs are not printed or used.
        if isinstance(result, (list, tuple)):
            for part in result:
                if isinstance(part, list):
                    texts.extend(x["content"] for x in part if isinstance(x, dict)
                                 and x.get("role")=="assistant" and isinstance(x.get("content"), str))
        record.update(status="RESPONSE_RECEIVED" if texts else "NO_TEXT_RESPONSE", observations=texts)
        print(json.dumps({"status":record["status"], "observations":texts},ensure_ascii=False),flush=True)
    except Exception as exc:
        record.update(status="UNAVAILABLE", error_type=type(exc).__name__, error=str(exc)[:800])
        print(json.dumps({"status":"UNAVAILABLE","error_type":type(exc).__name__,"error":str(exc)[:800]},ensure_ascii=False),flush=True)
    finally:
        record["finished_at"]=datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
