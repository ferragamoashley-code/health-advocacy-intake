"""
One-time helper: transcribe the Presidio fixture recording with AssemblyAI
(same REST approach and model settings as tests/assemblyai) and save the
transcript as Presidio fixtures.

Run from the repo folder:
    python tests/presidio/save_transcript.py

Needs ASSEMBLYAI_API_KEY in your environment. Writes:
    tests/presidio/fixtures/intake_en_identifiers_transcript.json
    tests/presidio/fixtures/intake_en_identifiers_transcript.txt
"""

import json
import os
import time
from pathlib import Path

import httpx

F = Path(__file__).parent / "fixtures"
AUDIO = F / "intake_en_identifiers_16k.wav"
BASE = "https://api.assemblyai.com/v2"
HEADERS = {"authorization": os.environ["ASSEMBLYAI_API_KEY"]}

CONFIG = {
    "speech_models": ["universal-3-5-pro", "universal-2"],
    "speaker_labels": True,
    "language_code": "en",
}


def main():
    with httpx.Client(headers=HEADERS, timeout=120) as client:
        upload = client.post(f"{BASE}/upload", content=AUDIO.read_bytes())
        upload.raise_for_status()

        job = client.post(f"{BASE}/transcript",
                          json={"audio_url": upload.json()["upload_url"], **CONFIG})
        job.raise_for_status()
        tid = job.json()["id"]

        while True:
            result = client.get(f"{BASE}/transcript/{tid}").json()
            if result["status"] == "completed":
                break
            if result["status"] == "error":
                raise RuntimeError(result.get("error"))
            time.sleep(3)

    utterances = [{"speaker": u["speaker"], "text": u["text"]}
                  for u in result.get("utterances") or []]
    (F / "intake_en_identifiers_transcript.json").write_text(
        json.dumps({"text": result["text"], "utterances": utterances}, indent=2))
    lines = [f"Speaker {u['speaker']}: {u['text']}" for u in utterances]
    (F / "intake_en_identifiers_transcript.txt").write_text("\n".join(lines) + "\n")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
