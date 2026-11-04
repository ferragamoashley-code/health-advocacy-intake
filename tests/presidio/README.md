# Presidio — unit tests (node 9: de-identify the review log)

**Status:** Complete — 31 passed, 0 skipped (Nov 4, 2026)

## Protocol

| | |
|---|---|
| Tool | [Microsoft Presidio](https://microsoft.github.io/presidio/), open-source PII detection (analyzer) and de-identification (anonymizer) |
| Version | `presidio-analyzer==2.2.366`, `presidio-anonymizer==2.2.366`, `spacy==3.8.18`, `en_core_web_lg` 3.8.0 (pinned by URL in requirements.txt) |
| Cost / keys | Free, local, no API key, no network once the model is installed. Model download about 400 MB; suite runs in about 4 seconds. |

## What node 9 does

Input: free text from the review log — the continuous transcript text from AssemblyAI (node 2) and the narrative field. Structured fact fields are not run through Presidio; their identifying columns are known and can be dropped directly.
Output: the same text with identifiers replaced by type labels, for the audit/evaluation log. Example: "Dana Whitlock called" → "\<PERSON\> called".

## Configuration

- **spaCy model:** `en_core_web_lg` (Presidio's default), chosen over the small model because a missed name is a privacy failure.
- **Built-in entities on:** PERSON, PHONE_NUMBER, EMAIL_ADDRESS, LOCATION, US_SSN.
- **Off:** DATE_TIME (general dates — appeal deadlines and call dates are needed for evaluation); NRP (nationality, religious or political group — kept so the log can show whether the workflow performs equally across groups; not a HIPAA Safe Harbor identifier); credit card (never collected at intake); bank numbers (would flag claim and reference numbers); crypto, IP, URL, IBAN and others.
- **Custom recognizers:**
  - `DATE_OF_BIRTH` — a date with a year, counted only when "date of birth", "DOB", "born" or "birthday" comes just before it.
  - `MEDICAID_CIN` — NY Medicaid ID: two letters, five digits, one letter (e.g. AB12345C).
  - `ZIP_CODE` — five digits, counted only after "zip" or "postal"; masked to three digits per Safe Harbor (11201 → 112\*\*). Dollar amounts are excluded.
- **Context-only recognizers** use Presidio's context enhancement: the pattern alone scores 0.01, below the 0.3 threshold; a context word among the 3 preceding words adds 0.35.
- **Replacement:** type labels (Presidio's `replace` operator); `mask` for ZIP_CODE.

## Fixtures

| File | Contents |
|---|---|
| `intake_en_identifiers_16k.wav` | ~1-minute synthetic intake call (advocate Jordan Pike, client Dana Whitlock) with fictional DOB, phone (555-01xx), zip, CIN, appeal deadline, "Spanish" and an income amount |
| `intake_en_identifiers_transcript.json` | AssemblyAI transcript of the recording; tests use the continuous `text` field |
| `intake_en_identifiers_transcript.txt` | Same transcript with speaker labels, for reading |
| `../save_transcript.py` | One-time helper that produced the transcript files |

All other test inputs are short sentences in the test file. No real client data.

## Test plan

| # | Category | Input | Expected | Result |
|---|---|---|---|---|
| 1 | Typical | Full transcript fixture | Names, DOB, phone, CIN labeled; zip → 112\*\*; deadline, "Spanish", "$54,000" kept | ✅ |
| 2 | Typical | Sentence with a full name | \<PERSON\> | ✅ |
| 3 | Typical | 555-555-0142, (555) 555-0142 | \<PHONE_NUMBER\> | ✅ |
| 4 | Typical | Email address | \<EMAIL_ADDRESS\> | ✅ |
| 5 | Typical | Fictional SSN | \<US_SSN\> | ✅ |
| 6 | Typical | Narrative with a name and a CIN | Both replaced | ✅ |
| 7 | Typical | Text with no identifiers | Unchanged | ✅ |
| 8 | Edge | DOB and appeal deadline in one sentence | Only DOB removed | ✅ (after narrowing context window) |
| 9 | Edge | "March 3rd, 1961", "03/03/1961", "3/3/61" | All removed | ✅ |
| 10 | Edge | Date with no birth context | Kept | ✅ |
| 11 | Edge | "zip code 11201", "54000 dollars", "$54,000" | Only the zip masked | ✅ |
| 12 | Edge | Bare "11201", no context word | To verify → **kept** | ✅ |
| 13 | Edge | CIN lowercase / spelled out with spaces | To verify → lowercase caught; **spaced missed** | ✅ |
| 14 | Edge | "Jordan", "Sam" alone | To verify → both caught | ✅ |
| 15 | Edge | "Brooklyn Anesthesia Associates" | Planned \<LOCATION\> → **kept whole** | ✅ |
| 16 | Edge | "Haitian", "Spanish", "Hispanic" | Kept | ✅ |
| 17 | Edge | Spanish sentence with a name | To verify → name caught | ✅ |
| 18 | Invalid | Empty string | Empty string back | ✅ |
| 19 | Invalid | None, a number, bytes | TypeError | ✅ |
| 20 | Invalid | ~60-minute call (~9,000 words) | Within time budget | ✅ |

## Findings

- **Deliberate-failure check:** with the CIN recognizer removed, 3 tests failed (full transcript, narrative, lowercase CIN); 28 passed.
- **Context window skips stopwords.** Presidio counts the 5 words before a match by default but skips words like "and", "the" and "is", so "birth" early in a sentence also boosted the appeal deadline later in it. Narrowed to 3 words.
- **Bare zip codes are not masked** (row 12), by design, so five-digit amounts are not masked either.
- **Spelled-out CINs are missed** (row 13). Presidio matches patterns ignoring case, so lowercase is caught.
- **Organization names keep their places** (row 15). The model labels "Brooklyn Anesthesia Associates" as an organization, which is not a de-identified entity.
- **Known public SSNs are rejected.** Presidio invalidates 078-05-1120 (a famous public example) as an SSN and labels it a phone number instead; the test uses a different fictional number.
- **Transcription split the DOB across speakers** ("March 3rd," / "1961."). Using AssemblyAI's continuous `text` field rather than speaker-labeled lines keeps the date whole.
- **NumPy downgrade:** installing spaCy downgraded NumPy from 2.5.4 to 2.4.6; Pandera tests still pass.

## Running

```
source .venv/bin/activate
python -m spacy download en_core_web_lg   # first time only
python -m pytest tests/presidio -v
```
