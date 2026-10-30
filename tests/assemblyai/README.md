# AssemblyAI transcription — unit tests

Black-box unit tests for the AssemblyAI transcription step, one of the atomic protocols in the health advocacy intake workflow. The tests check what the service returns for realistic intake-call audio. They do not test AssemblyAI's internals.

## Status

**46 passed, 5 skipped** (as of October 30, 2026).

Passing: offline config check; all 10 English-call rows; 8 kHz telephony audio; .mp3 input; single speaker (natural voicemail); speaker floor; three speakers separated; maximum speaker cap merges extra speakers; 20-second callback; silence fallback; 12-minute long call; too-short clip; Mandarin (revised expectation); invalid API key; empty bytes; non-audio bytes; `speakers_expected` combined with a speaker range; min speakers above max; unknown model.

Skipped:

| Test(s) | Skipped tests | Fixture needed | Reason |
|---|---|---|---|
| `test_spanish_call` | 1 | `intake_es_2spk_16k.wav` (two Spanish speakers; at least 4 of the 5 Spanish key terms) | Out of scope: the wrapper is English-only for now |
| `test_mixed_english_spanish_both_kept` | 2 | `intake_en_es_mixed_16k.wav` (both "appeal" and "apelación") | Out of scope: the wrapper is English-only for now |
| `test_accented_english_not_translated` | 1 | `intake_en_accented_16k.wav` (English with a strong non-native accent) | In scope; fixture not yet recorded |
| `long_call_60min` | 1 | — | Manual only; see "Running the tests" |

The first three use `pytest.mark.skipif` on the fixture file, so each test runs automatically once its recording is added to `fixtures/`.

## Configuration

The tests call AssemblyAI's REST API v2 directly with `httpx` rather than through the `assemblyai` Python SDK. SDK version 1.6.5 drops the `include_speaker_confidence` and `on_no_speech_detected` options and strips `speaker_confidence` from responses.

Request configuration:

- `speech_models`: `["universal-3-5-pro", "universal-2"]`
- `speaker_labels`: `True`
- `speaker_options`: minimum 1 speaker, maximum 4, `include_speaker_confidence: True`
- `language_detection`: `True`
- `language_detection_options`: `expected_languages: ["en", "es"]`, `on_no_speech_detected: "fallback"`, `fallback_language: "en"`

## Fixtures

All fixtures are in `fixtures/`. Recordings were made with Voice Memos and converted to 16 kHz mono WAV:

```
afconvert -f WAVE -d LEI16@16000 -c 1 input.m4a output.wav
```

| File | Used for |
|---|---|
| `intake_en_2spk_16k.wav` | English two-speaker intake call (main English rows) |
| `intake_en_2spk_8k.wav` | 8 kHz telephony audio |
| `intake_en_2spk.mp3` | .mp3 input |
| `intake_en_3spk_16k.wav` | Three speakers (advocate, client, caregiver "Sam") |
| `voicemail_en_1spk_16k.wav` | Single speaker (natural voicemail) |
| `callback_en_20s_16k.wav` | 20-second callback |
| `intake_zh_2spk_16k.wav` | Mandarin, a language outside `expected_languages` |

[Ashley: add how the silence, too-short, and 12-minute long-call audio are produced.]

## Running the tests

From the top of the repository:

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ASSEMBLYAI_API_KEY="your-key-here"
python -m pytest tests/assemblyai --timeout=720
```

To run selected tests, add `-k "<test names>"`. To see why tests were skipped, add `-rs`.

The 60-minute long-call test runs only when requested:

```
RUN_MANUAL=1 python -m pytest tests/assemblyai -k long_call_60min --timeout=2400
```

The 12-minute long call is intended to run weekly; the 60-minute run is manual only.

## Findings

- **Deliberate-failure check.** Pointing the English tests at a one-speaker voicemail that mentions both names made `test_advocate_and_client_distinct` fail (`'A' != 'A'`) while the other 21 tests passed. The test passed again after the two-speaker file was restored, confirming it detects the problem it is meant to catch.
- **Role-play is over-split.** A recording of one person reading both roles was labeled as two speakers (A and B). A natural voicemail received a single label.
- **Retired model name still accepted.** `universal-3-pro` is still accepted by the API although it no longer appears on the models page. That test row was replaced with `test_unknown_model_rejected`, which confirms that `"not-a-real-model"` is rejected.
- **Languages outside the expected list.** A Mandarin call was transcribed correctly in Chinese characters but labeled `en` (the fallback language) with a language confidence of 0.005. The test was revised to expect this documented behavior.
- **Edge cases.** On October 23, 2026, empty bytes, non-audio bytes, a too-short clip, `speakers_expected` combined with a speaker range, a minimum above the maximum, and an unknown model were all rejected or handled cleanly.

## Implications for the wrapper

These rules belong in the wrapper that calls AssemblyAI, not in these tests:

- Treat a call as English only if `language_code` starts with `en` **and** `language_confidence` is at least 0.5. This follows from the Mandarin finding.
- Scope is English-only for now.
- Downmix stereo audio to mono before sending.
- Use the placeholders `no_speech`, `unsupported_language`, and `single_speaker` for those outcomes.