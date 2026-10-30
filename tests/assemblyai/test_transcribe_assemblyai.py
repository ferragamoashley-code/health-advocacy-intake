"""Unit tests for the atomic protocol "Transcribe: AssemblyAI".

Protocol
    Transcribe: AssemblyAI. Pre-recorded speech-to-text with speaker diarization
    and automatic language detection, called through AssemblyAI's documented
    REST API v2 (POST /v2/upload, POST /v2/transcript, GET /v2/transcript/{id}).
    The tests treat AssemblyAI as a black box and use only that interface.

Citable source
    AssemblyAI. Speaker Diarization. AssemblyAI documentation.
    https://www.assemblyai.com/docs/pre-recorded-audio/label-speakers
    Version: API v2 (/v2/transcript), speech models universal-3-5-pro with
    universal-2 fallback, accessed Oct 23, 2026.
    Supporting pages, same access date: Automatic Language Detection, Model
    selection, Common errors and solutions, and the FAQs on supported file
    types and on file size and duration limits.

Input -> output contract
    call recording (.wav, mono) -> transcript (JSON). The JSON is AssemblyAI's
    raw GET /v2/transcript/{id} response: status, error, speech_model_used,
    language_code, language_confidence, audio_duration, text, metadata, and
    utterances[] (speaker, text, start, end, confidence, speaker_confidence,
    words[]).

Configuration under test (CONFIG below)
    speech_models=["universal-3-5-pro", "universal-2"], speaker_labels=True,
    speaker_options={"min_speakers_expected": 1, "max_speakers_expected": 4,
                     "include_speaker_confidence": True},
    language_detection=True,
    language_detection_options={"expected_languages": ["en", "es"],
                                "on_no_speech_detected": "fallback",
                                "fallback_language": "en"}

Why REST instead of the assemblyai SDK
    assemblyai 1.6.5 (latest on Oct 23, 2026) silently drops
    include_speaker_confidence and on_no_speech_detected from requests and
    strips speaker_confidence from responses, so it cannot exercise the
    configured behavior. The REST API is AssemblyAI's documented interface.

Written for
    Python 3.13.17, httpx 0.28.1, pytest 9.1.1, pytest-timeout 2.4.0
    (exact pins, including dependencies, are in requirements.txt).

Running
    Set ASSEMBLYAI_API_KEY (a free-tier key is enough; in GitHub Actions,
    store it as a repository secret).
        pytest test_transcribe_assemblyai.py --timeout=720
    The 60-minute long call runs only when RUN_MANUAL=1 is set:
        RUN_MANUAL=1 pytest test_transcribe_assemblyai.py -k long_call_60min --timeout=2400
    Each AssemblyAI job runs once per session and is shared by every test that
    reads it. All jobs the selected tests need start together, at most five at
    a time, with the longest call first.

Fixtures (tests/fixtures/, mono, fictional people only: volunteer "Jordan
Pike", client "Dana Whitlock", caregiver "Sam")
    intake_en_2spk_16k.wav, intake_en_2spk_8k.wav, intake_en_2spk.mp3,
    intake_en_accented_16k.wav, intake_es_2spk_16k.wav,
    intake_en_es_mixed_16k.wav, intake_zh_2spk_16k.wav,
    callback_en_20s_16k.wav, intake_en_3spk_16k.wav, voicemail_en_1spk_16k.wav.
    Silence clips and the long calls are generated at test time.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import io
import json
import math
import os
import tempfile
import time
import unicodedata
import wave
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple, Union

import httpx
import pytest

# ---------------------------------------------------------------------------
# Inputs and configuration (from the test plan)
# ---------------------------------------------------------------------------

F = Path(__file__).resolve().parent / "fixtures"
EN_16K = str(F / "intake_en_2spk_16k.wav")
EN_8K = str(F / "intake_en_2spk_8k.wav")
EN_MP3 = str(F / "intake_en_2spk.mp3")
ES = str(F / "intake_es_2spk_16k.wav")
MIXED = str(F / "intake_en_es_mixed_16k.wav")
ACCENTED = str(F / "intake_en_accented_16k.wav")
ZH = str(F / "intake_zh_2spk_16k.wav")
SHORT = str(F / "callback_en_20s_16k.wav")
THREE = str(F / "intake_en_3spk_16k.wav")
VOICEMAIL = str(F / "voicemail_en_1spk_16k.wav")

CONFIG = {
    "speech_models": ["universal-3-5-pro", "universal-2"],
    "speaker_labels": True,
    "speaker_options": {
        "min_speakers_expected": 1,
        "max_speakers_expected": 4,
        "include_speaker_confidence": True,
    },
    "language_detection": True,
    "language_detection_options": {
        "expected_languages": ["en", "es"],
        "on_no_speech_detected": "fallback",
        "fallback_language": "en",
    },
}


def with_speakers(min_n: int, max_n: int) -> dict:
    """CONFIG with a different speaker range (speaker confidence kept on)."""
    return {
        **CONFIG,
        "speaker_options": {
            "min_speakers_expected": min_n,
            "max_speakers_expected": max_n,
            "include_speaker_confidence": True,
        },
    }


KEY_EN = ["medicaid", "prior authorization", "fair hearing", "medical necessity", "appeal"]
KEY_ES = ["medicaid", "autorizacion previa", "audiencia imparcial", "necesidad medica", "apelacion"]

BAD_KEY = "invalid-key-0000"
NON_AUDIO = b"This is not audio. " * 50
LONG_12_S = 720
LONG_60_S = 3600
RUN_MANUAL = os.environ.get("RUN_MANUAL") == "1"

BASE_URL = "https://api.assemblyai.com"
MAX_CONCURRENCY = 5
POLL_INTERVAL_S = 3
POLL_TIMEOUT_S = 600          # weekly jobs, including the 12-minute call
POLL_TIMEOUT_MANUAL_S = 2100  # the 60-minute call


# ---------------------------------------------------------------------------
# Generated audio
# ---------------------------------------------------------------------------


def silence_wav(seconds: float, rate: int = 16000) -> bytes:
    """Mono 16-bit WAV of digital silence."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def wav_seconds(path: str) -> float:
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def repeats_for(target_s: float) -> int:
    """How many copies of the English fixture reach at least `target_s`."""
    return math.ceil(target_s / wav_seconds(EN_16K))


def repeat_wav(src: str, times: int) -> str:
    """Write `src` repeated `times` times to a temp file and return its path."""
    with wave.open(src) as r:
        params, frames = r.getparams(), r.readframes(r.getnframes())
    dst = os.path.join(tempfile.gettempdir(), f"long_call_x{times}.wav")
    with wave.open(dst, "wb") as w:
        w.setparams(params)
        w.writeframes(frames * times)
    return dst


# ---------------------------------------------------------------------------
# AssemblyAI REST API v2 (documented interface; no logic reimplemented)
# ---------------------------------------------------------------------------


class APIError(Exception):
    """A non-200 HTTP response from AssemblyAI."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


def api_client(api_key: str, transport: Optional[httpx.BaseTransport] = None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"authorization": api_key},
        timeout=httpx.Timeout(120.0),
        transport=transport,
    )


def _ok(response: httpx.Response) -> dict:
    if response.status_code != 200:
        raise APIError(response.status_code, response.text)
    return response.json()


def upload(client: httpx.Client, data: bytes) -> str:
    """POST /v2/upload -> upload_url."""
    return _ok(client.post("/v2/upload", content=data))["upload_url"]


def submit(client: httpx.Client, audio_url: str, config: dict) -> dict:
    """POST /v2/transcript with the configuration as the request body."""
    return _ok(client.post("/v2/transcript", json={"audio_url": audio_url, **config}))


def poll(client: httpx.Client, transcript_id: str, timeout_s: float) -> dict:
    """GET /v2/transcript/{id} until status is completed or error."""
    deadline = time.monotonic() + timeout_s
    while True:
        js = _ok(client.get(f"/v2/transcript/{transcript_id}"))
        if js["status"] in ("completed", "error"):
            return js
        if time.monotonic() > deadline:
            raise TimeoutError(f"Transcript {transcript_id} still {js['status']!r} after {timeout_s} s")
        time.sleep(POLL_INTERVAL_S)


def transcribe(audio: Union[str, bytes], config: dict, api_key: str, timeout_s: float) -> dict:
    """Upload, submit, and poll. Returns AssemblyAI's raw transcript JSON."""
    data = Path(audio).read_bytes() if isinstance(audio, str) else audio
    with api_client(api_key) as client:
        audio_url = upload(client, data)
        created = submit(client, audio_url, config)
        return poll(client, created["id"], timeout_s)


# ---------------------------------------------------------------------------
# Reading the transcript JSON
# ---------------------------------------------------------------------------


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def text_of(js: dict) -> str:
    """Lowercased transcript text, falling back to the utterances."""
    text = js.get("text") or " ".join(u["text"] for u in (js.get("utterances") or []))
    return text.lower()


def count_terms(text: str, terms: Iterable[str]) -> int:
    return sum(1 for term in terms if term in text)


def labels(js: dict) -> set:
    return {u["speaker"] for u in (js.get("utterances") or [])}


def utt(js: dict, needle: str) -> dict:
    """First utterance whose lowercased text contains `needle`."""
    for u in js.get("utterances") or []:
        if needle in u["text"].lower():
            return u
    pytest.fail(f"No utterance contains {needle!r}; check the fixture recording and transcript.")


def cjk_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if "一" <= c <= "鿿") / len(chars)


# ---------------------------------------------------------------------------
# Shared jobs: each unique (audio, config, key) runs once per session
# ---------------------------------------------------------------------------

AudioSpec = Union[str, bytes, Callable[[], Union[str, bytes]]]

# name -> (audio, config, api_key override, poll timeout)
JOBS: Dict[str, Tuple[AudioSpec, dict, Optional[str], float]] = {
    "en": (EN_16K, CONFIG, None, POLL_TIMEOUT_S),
    "en_8k": (EN_8K, CONFIG, None, POLL_TIMEOUT_S),
    "en_mp3": (EN_MP3, CONFIG, None, POLL_TIMEOUT_S),
    "es": (ES, CONFIG, None, POLL_TIMEOUT_S),
    "three": (THREE, CONFIG, None, POLL_TIMEOUT_S),
    "three_cap2": (THREE, with_speakers(1, 2), None, POLL_TIMEOUT_S),
    "voicemail": (VOICEMAIL, CONFIG, None, POLL_TIMEOUT_S),
    "voicemail_floor2": (VOICEMAIL, with_speakers(2, 4), None, POLL_TIMEOUT_S),
    "short": (SHORT, CONFIG, None, POLL_TIMEOUT_S),
    "mixed": (MIXED, CONFIG, None, POLL_TIMEOUT_S),
    "accented": (ACCENTED, CONFIG, None, POLL_TIMEOUT_S),
    "zh": (ZH, CONFIG, None, POLL_TIMEOUT_S),
    "silence": (lambda: silence_wav(30.0), CONFIG, None, POLL_TIMEOUT_S),
    "too_short": (lambda: silence_wav(0.1), CONFIG, None, POLL_TIMEOUT_S),
    "long_12": (lambda: repeat_wav(EN_16K, repeats_for(LONG_12_S)), CONFIG, None, POLL_TIMEOUT_S),
    "long_60": (lambda: repeat_wav(EN_16K, repeats_for(LONG_60_S)), CONFIG, None, POLL_TIMEOUT_MANUAL_S),
    "bad_key": (SHORT, CONFIG, BAD_KEY, POLL_TIMEOUT_S),
    "empty": (b"", CONFIG, None, POLL_TIMEOUT_S),
    "non_audio": (NON_AUDIO, CONFIG, None, POLL_TIMEOUT_S),
    "combo": (SHORT, {**CONFIG, "speakers_expected": 2}, None, POLL_TIMEOUT_S),
    "min_above_max": (SHORT, with_speakers(4, 2), None, POLL_TIMEOUT_S),
    "unknown_model": (SHORT, {**CONFIG, "speech_models": ["not-a-real-model"]}, None, POLL_TIMEOUT_S),
}
MANUAL_JOBS = {"long_60"}
LONG_FIRST = ("long_60", "long_12")


@dataclasses.dataclass
class Outcome:
    """What one AssemblyAI job produced: raw JSON or the APIError raised."""

    json: Optional[dict] = None
    error: Optional[APIError] = None

    def unwrap(self) -> dict:
        if self.error is not None:
            raise self.error
        assert self.json is not None
        return self.json


class JobRunner:
    """Starts every needed job at once and hands out the shared results."""

    def __init__(self, names: Iterable[str], api_key: str) -> None:
        self._api_key = api_key
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_CONCURRENCY, thread_name_prefix="aai-job"
        )
        order = sorted(set(names), key=lambda n: (n not in LONG_FIRST, n))
        self._futures = {name: self._pool.submit(self._run, name) for name in order}

    def _run(self, name: str) -> Outcome:
        audio, config, key_override, timeout_s = JOBS[name]
        data = audio() if callable(audio) else audio
        try:
            return Outcome(json=transcribe(data, config, key_override or self._api_key, timeout_s))
        except APIError as error:
            return Outcome(error=error)

    def get(self, name: str) -> Outcome:
        if name not in self._futures:
            raise KeyError(f"Job {name!r} was not declared with @uses on this test")
        return self._futures[name].result()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def uses(*names: str):
    """Declares which shared jobs a test reads, so they can start up front."""
    unknown = set(names) - set(JOBS)
    if unknown:
        raise ValueError(f"Unknown jobs: {sorted(unknown)}")

    def mark(fn):
        fn.aai_jobs = names
        return fn

    return mark


def require_api_key() -> str:
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        pytest.fail("Set ASSEMBLYAI_API_KEY to run tests that call AssemblyAI.")
    return key


@pytest.fixture(scope="session")
def aai_jobs(request):
    api_key = require_api_key()
    needed = set()
    for item in request.session.items:
        needed.update(getattr(getattr(item, "function", None), "aai_jobs", ()))
    if not RUN_MANUAL:
        needed -= MANUAL_JOBS
    runner = JobRunner(needed, api_key)
    yield runner
    runner.close()


def completed(outcome: Outcome) -> dict:
    js = outcome.unwrap()
    assert js["status"] == "completed", f"status={js['status']!r}, error={js.get('error')!r}"
    return js


def assert_rejected(outcome: Outcome) -> None:
    """Rejected at upload or submit (HTTP 4xx) or failed during processing."""
    if outcome.error is not None:
        assert 400 <= outcome.error.status_code < 500, str(outcome.error)
        return
    assert outcome.json["status"] == "error", f"Expected a rejection, got status={outcome.json['status']!r}"
    assert isinstance(outcome.json["error"], str) and outcome.json["error"]


def check_long_call(js: dict, expected_s: float) -> None:
    assert js["status"] == "completed", js.get("error")
    assert js["audio_duration"] == pytest.approx(expected_s, abs=2.0)
    last_end_s = max(u["end"] for u in js["utterances"]) / 1000
    assert last_end_s == pytest.approx(expected_s, abs=5.0)
    assert 2 <= len(labels(js)) <= 4


# ---------------------------------------------------------------------------
# Typical inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(CONFIG))
def test_config_payload_matches_workflow(key):
    sent = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "offline", "status": "queued"})

    with api_client("offline-key", transport=httpx.MockTransport(capture)) as client:
        submit(client, "https://example.invalid/audio.wav", CONFIG)

    assert len(sent) == 1, "Exactly one request, and no real network call"
    body = sent[0]
    assert "speakers_expected" not in body
    assert body[key] == CONFIG[key]


@uses("en")
def test_english_call_completes(aai_jobs):
    js = completed(aai_jobs.get("en"))
    assert js.get("error") is None
    assert js["speech_model_used"] == "universal-3-5-pro"


@uses("en")
def test_english_language_detected(aai_jobs):
    js = completed(aai_jobs.get("en"))
    assert js["language_code"].startswith("en")
    assert 0.5 <= js["language_confidence"] <= 1


@uses("en")
def test_english_key_terms(aai_jobs):
    js = completed(aai_jobs.get("en"))
    assert count_terms(text_of(js), KEY_EN) >= 4


@uses("en")
@pytest.mark.parametrize(
    "field, kind",
    [("speaker", str), ("text", str), ("start", int), ("end", int), ("confidence", float), ("words", list)],
)
def test_utterance_schema(aai_jobs, field, kind):
    js = completed(aai_jobs.get("en"))
    utterances = js["utterances"]
    assert isinstance(utterances, list) and utterances
    for u in utterances:
        assert isinstance(u[field], kind), f"{field}={u[field]!r}"
        if kind in (str, list):
            assert u[field], f"{field} is empty"
        if field == "confidence":
            assert 0 <= u[field] <= 1


@uses("en")
@pytest.mark.parametrize(
    "field, kind",
    [("text", str), ("start", int), ("end", int), ("confidence", float), ("speaker", str)],
)
def test_word_schema_matches_utterance(aai_jobs, field, kind):
    js = completed(aai_jobs.get("en"))
    for u in js["utterances"]:
        for w in u["words"]:
            assert isinstance(w[field], kind), f"{field}={w[field]!r}"
            if field == "confidence":
                assert 0 <= w[field] <= 1
            if field == "speaker":
                assert w["speaker"] == u["speaker"]


@uses("en")
def test_labels_sequential_letters(aai_jobs):
    js = completed(aai_jobs.get("en"))
    found = sorted(labels(js))
    assert found == [chr(ord("A") + i) for i in range(len(found))]


@uses("en")
def test_timestamps_ordered_and_bounded(aai_jobs):
    js = completed(aai_jobs.get("en"))
    utterances = js["utterances"]
    starts = [u["start"] for u in utterances]
    assert starts == sorted(starts)
    for u in utterances:
        assert u["start"] < u["end"]
    assert utterances[-1]["end"] <= wav_seconds(EN_16K) * 1000 + 500


@uses("en")
def test_audio_duration_matches_file(aai_jobs):
    js = completed(aai_jobs.get("en"))
    assert js["audio_duration"] == pytest.approx(wav_seconds(EN_16K), abs=1.0)


@uses("en")
def test_advocate_and_client_distinct(aai_jobs):
    js = completed(aai_jobs.get("en"))
    assert utt(js, "pike")["speaker"] != utt(js, "whitlock")["speaker"]


@uses("en")
@pytest.mark.parametrize("level", ["utterance", "word"])
def test_speaker_confidence_present_and_bounded(aai_jobs, level):
    js = completed(aai_jobs.get("en"))
    items = js["utterances"] if level == "utterance" else [w for u in js["utterances"] for w in u["words"]]
    assert items
    for item in items:
        assert "speaker_confidence" in item, f"{level} has no speaker_confidence"
        assert isinstance(item["speaker_confidence"], float)
        assert 0 <= item["speaker_confidence"] <= 1


@uses("es")
@pytest.mark.skipif(not (F / "intake_es_2spk_16k.wav").exists(),
                    reason="Fixture not recorded; Spanish is out of scope for the English-only wrapper")
def test_spanish_call(aai_jobs):
    js = completed(aai_jobs.get("es"))
    assert js["language_code"].startswith("es")
    assert count_terms(strip_accents(text_of(js)), KEY_ES) >= 4
    assert 2 <= len(labels(js)) <= 4


@uses("three")
@pytest.mark.parametrize("a, b", [("pike", "whitlock"), ("pike", "this is sam"), ("whitlock", "this is sam")])
def test_three_speakers_separated(aai_jobs, a, b):
    js = completed(aai_jobs.get("three"))
    assert 3 <= len(labels(js)) <= 4
    assert utt(js, a)["speaker"] != utt(js, b)["speaker"]


@uses("en_mp3")
def test_mp3_matches_wav(aai_jobs):
    js = completed(aai_jobs.get("en_mp3"))
    assert js["language_code"].startswith("en")
    assert "medicaid" in text_of(js)
    assert 2 <= len(labels(js)) <= 4


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@uses("en_8k")
def test_telephony_8khz(aai_jobs):
    js = completed(aai_jobs.get("en_8k"))
    assert js["language_code"].startswith("en")
    assert count_terms(text_of(js), KEY_EN) >= 3
    assert utt(js, "pike")["speaker"] != utt(js, "whitlock")["speaker"]


@uses("voicemail")
def test_single_speaker_one_label(aai_jobs):
    js = completed(aai_jobs.get("voicemail"))
    assert len(labels(js)) == 1


@uses("voicemail_floor2")
def test_min_floor_enforced(aai_jobs):
    js = completed(aai_jobs.get("voicemail_floor2"))
    assert len(labels(js)) >= 2


@uses("three_cap2")
@pytest.mark.parametrize("anchor", ["pike", "whitlock", "this is sam"])
def test_max_cap_merges_extra_speakers(aai_jobs, anchor):
    js = completed(aai_jobs.get("three_cap2"))
    assert len(labels(js)) <= 2
    assert anchor in text_of(js), f"{anchor!r} was dropped instead of merged"


@uses("short")
def test_short_20s_clip(aai_jobs):
    js = completed(aai_jobs.get("short"))
    assert js["language_code"].startswith("en")
    assert "medicaid" in text_of(js)


@uses("mixed")
@pytest.mark.parametrize("term", ["appeal", "apelacion"])
@pytest.mark.skipif(not (F / "intake_en_es_mixed_16k.wav").exists(),
                    reason="Fixture not recorded; mixed English/Spanish is out of scope for the English-only wrapper")
def test_mixed_english_spanish_both_kept(aai_jobs, term):
    js = completed(aai_jobs.get("mixed"))
    assert js["language_code"][:2] in {"en", "es"}
    assert term in strip_accents(text_of(js)), f"{term!r} missing: one language may have been translated"


@uses("accented")
@pytest.mark.skipif(not (F / "intake_en_accented_16k.wav").exists(),
                    reason="Fixture not recorded yet")
def test_accented_english_not_translated(aai_jobs):
    js = completed(aai_jobs.get("accented"))
    text = strip_accents(text_of(js))
    assert js["language_code"].startswith("en")
    assert count_terms(text, KEY_EN) >= 3
    assert count_terms(text, [t for t in KEY_ES if t != "medicaid"]) == 0


@uses("zh")
def test_mandarin_outside_expected_languages(aai_jobs):
    js = completed(aai_jobs.get("zh"))
    # Outside expected_languages, the label is the fallback language, not the spoken one
    assert js["language_code"] == CONFIG["language_detection_options"]["fallback_language"]
    assert js["language_confidence"] < 0.5
    assert js["speech_model_used"] in {"universal-3-5-pro", "universal-2"}
    assert cjk_ratio(js.get("text") or "") >= 0.5


@uses("silence")
def test_silence_fallback_empty_transcript(aai_jobs):
    js = completed(aai_jobs.get("silence"))
    assert js["text"] == ""
    assert js["language_code"] == "en"
    assert (js.get("metadata") or {}).get("warnings"), "Expected a no-speech warning in metadata.warnings"


@uses("long_12")
@pytest.mark.timeout(POLL_TIMEOUT_S + 120)
def test_long_call_12min(aai_jobs):
    check_long_call(aai_jobs.get("long_12").unwrap(), repeats_for(LONG_12_S) * wav_seconds(EN_16K))


@uses("long_60")
@pytest.mark.skipif(not RUN_MANUAL, reason="Manual run only: set RUN_MANUAL=1")
@pytest.mark.timeout(POLL_TIMEOUT_MANUAL_S + 120)
def test_long_call_60min(aai_jobs):
    check_long_call(aai_jobs.get("long_60").unwrap(), repeats_for(LONG_60_S) * wav_seconds(EN_16K))


@uses("too_short")
def test_audio_too_short(aai_jobs):
    outcome = aai_jobs.get("too_short")
    if outcome.error is not None:  # rejected at upload/submit also counts as an error
        assert 400 <= outcome.error.status_code < 500
        return
    js = outcome.json
    if js["status"] == "completed":
        assert not js.get("text"), "Invented text from 0.1 s of silence"
    else:
        assert js["status"] == "error"


# ---------------------------------------------------------------------------
# Invalid inputs
# ---------------------------------------------------------------------------


@uses("bad_key")
def test_invalid_api_key(aai_jobs):
    with pytest.raises(APIError) as caught:
        aai_jobs.get("bad_key").unwrap()
    assert caught.value.status_code == 401 or "Authentication error" in caught.value.body


@uses("empty")
def test_empty_bytes_rejected(aai_jobs):
    assert_rejected(aai_jobs.get("empty"))


@uses("non_audio")
def test_non_audio_bytes_rejected(aai_jobs):
    js = aai_jobs.get("non_audio").unwrap()
    assert js["status"] == "error"
    assert isinstance(js["error"], str) and js["error"]


@uses("combo")
def test_speakers_expected_with_range_rejected(aai_jobs):
    assert_rejected(aai_jobs.get("combo"))


@uses("min_above_max")
def test_min_above_max_rejected(aai_jobs):
    assert_rejected(aai_jobs.get("min_above_max"))


@uses("unknown_model")
def test_unknown_model_rejected(aai_jobs):
    assert_rejected(aai_jobs.get("unknown_model"))
