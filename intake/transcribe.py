"""Node 2: transcribe a call recording with AssemblyAI (REST API via httpx).

Same approach and settings as tests/assemblyai. Returns the continuous
transcript text (what Presidio and the extraction steps read), the
speaker-labeled utterances (for display), and the English-only check.
"""

import os
import time

import httpx

BASE = "https://api.assemblyai.com/v2"


class TranscriptionError(RuntimeError):
    pass


def transcribe(audio_bytes, settings, poll_seconds=3, timeout_seconds=900):
    if not audio_bytes:
        raise TranscriptionError("The recording is empty.")
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is not set.")
    cfg = settings["assemblyai"]
    request = {
        "speech_models": cfg["speech_models"],
        "speaker_labels": cfg["speaker_labels"],
        "language_detection": cfg["language_detection"],
        "language_detection_options": {
            "expected_languages": cfg["expected_languages"],
            "on_no_speech_detected": "fallback",
            "fallback_language": cfg["fallback_language"],
        },
    }
    with httpx.Client(headers={"authorization": key}, timeout=120) as client:
        upload = client.post(f"{BASE}/upload", content=audio_bytes)
        upload.raise_for_status()
        job = client.post(f"{BASE}/transcript",
                          json={"audio_url": upload.json()["upload_url"], **request})
        if job.status_code >= 400:
            raise TranscriptionError(job.text)
        tid = job.json()["id"]
        start = time.time()
        while True:
            result = client.get(f"{BASE}/transcript/{tid}").json()
            if result["status"] == "completed":
                break
            if result["status"] == "error":
                raise TranscriptionError(result.get("error", "unknown error"))
            if time.time() - start > timeout_seconds:
                raise TranscriptionError("Transcription timed out.")
            time.sleep(poll_seconds)
    return to_transcript(result, settings)


def to_transcript(result, settings):
    """Reduce an AssemblyAI response to what the workflow needs."""
    code = result.get("language_code") or ""
    conf = result.get("language_confidence")
    min_conf = settings["assemblyai"]["min_english_confidence"]
    is_english = code.startswith("en") and conf is not None and conf >= min_conf
    utterances = [{"speaker": u["speaker"], "text": u["text"]}
                  for u in result.get("utterances") or []]
    return {
        "text": result.get("text") or "",
        "utterances": utterances,
        "language_code": code,
        "language_confidence": conf,
        "is_english": is_english,
    }
