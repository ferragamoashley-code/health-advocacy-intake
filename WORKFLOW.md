# WORKFLOW.md — instructions for regenerating the glue code

This file describes the intake workflow in enough detail for a coding agent to rebuild the glue code and disposable components from scratch. The wire diagram in the [README](README.md#wire-diagram) shows the same workflow; node numbers here match the diagram and the code.

## 1. Purpose and scope

A volunteer at a health advocacy nonprofit uploads a recorded intake call. The workflow transcribes it, has Claude extract a structured case record, validates the record, lets the volunteer review and correct everything, drafts a case note, and saves the note plus a de-identified audit log.

- **Input:** one call recording (.wav, .mp3 or .m4a), or a saved transcript JSON.
- **Output:** `outputs/<case_id>/case_note.md` and `outputs/<case_id>/audit_log.json` (de-identified).
- **Scope:** synthetic test calls only; English only; three case categories (billing, appeal, eligibility).
- **Principle:** every AI output is reviewed by the volunteer before it is used.

## 2. What to keep and what to regenerate

**Keep as is (do not regenerate):**

| Path | What it is |
|---|---|
| `intake/transcribe.py` | Node 2 wrapper for AssemblyAI (atomic protocol, tested in `tests/assemblyai/`) |
| `intake/validate.py` | Node 6 wrapper for Pandera (atomic protocol, tested in `tests/pandera/`) |
| `intake/deidentify.py` | Node 9 wrapper for Presidio (atomic protocol, tested in `tests/presidio/`) |
| `config/case_schema.yaml` | Fields, categories, fields per category, urgency rules |
| `config/settings.yaml` | Claude model, AssemblyAI options, outputs folder |
| `config/note_template.md` | Case note template with `{placeholders}` |
| `tests/`, `.github/workflows/tests.yml`, `requirements.txt`, `.gitignore` | Tests, CI, pinned dependencies |

**Regenerate from this file:**

| Path | Node | What it does |
|---|---|---|
| `intake/__init__.py` | — | Package docstring listing nodes and files |
| `intake/config.py` | — | Loads the three config files; creates the outputs folder |
| `intake/extract.py` | 3, 4, 5 | Claude extraction and categorization |
| `intake/frame.py` | glue (5 → 6) | Builds the one-row DataFrame Pandera validates |
| `intake/draft_note.py` | 8 | Drafts the case note |
| `app.py` | 7 + orchestration | Streamlit app that runs the whole workflow |

## 3. Environment

- Python 3.12. Install with `pip install -r requirements.txt` (versions are pinned; includes `anthropic`, `streamlit`, `pandera[pandas]`, `presidio-analyzer`, `presidio-anonymizer`, `spacy` with `en_core_web_lg`, `httpx`, `pyyaml`).
- Environment variables: `ANTHROPIC_API_KEY` (required), `ASSEMBLYAI_API_KEY` (only to transcribe a new recording). Never commit keys; `.env` is git-ignored.
- Run from the repo root: `streamlit run app.py`.
- `outputs/` is git-ignored. Never commit real call data or outputs.

## 4. Interfaces of the kept files

Regenerated code must call these exactly as written:

- `transcribe.transcribe(audio_bytes, settings) -> transcript` — raises `transcribe.TranscriptionError` on an empty file, missing key, API error or timeout.
- `validate.merge_fields(schema, categories) -> dict` — core fields plus the fields of every assigned category; a shared field is required if any assigned category requires it.
- `validate.validate_case(df, schema) -> None | {"missing", "invalid", "urgent", "failure_cases"}` — expects exactly one row; returns `None` when everything passes.
- `deidentify.deidentify_log(log) -> dict` — returns a de-identified copy of the review log.

## 5. Data contracts

**Transcript** (from node 2, or loaded from a saved JSON):
```
{"text": str, "utterances": [{"speaker": str, "text": str}], "language_code": str,
 "language_confidence": float | None, "is_english": bool}
```
A saved transcript JSON has `text` and optionally `utterances`; treat it as English.

**Fact** (one per field, from nodes 3 and 5):
```
{"value": str | number | None, "quote": str | None, "status": str, "source": str}
```
- `status`: `stated`, `client_unknown`, `declined` or `not_discussed`.
- `source`: `ai` when extracted; on review becomes `volunteer_edited`, `volunteer_added` (AI value was empty) or `rejected`.
- Facts are stored as a dict keyed by field name: `{field: fact}`.

**Categories result** (node 4): `{"categories": [str], "rationale": str, "quote": str | None}`.

**Validation result** (node 6): `missing`, `invalid` and `urgent` are sorted lists of strings. Urgent flag names come from the schema's `urgency` rules: `appeal_deadline_within_14_days`, `care_gap`.

**Audit log** (built at save, then de-identified by node 9) has exactly these keys:
`case_id`, `call_date`, `model`, `transcript`, `categories_ai`, `categories_final`, `category_reextractions` (list of `{"from": [...], "to": [...]}`), `facts_ai`, `facts_final`, `narrative_ai`, `narrative_final`, `validation` (`missing`, `invalid`, `urgent` only), `urgent_approved`, `urgent_denied`, `case_note`.

**Outputs:** `case_id` is the save time as `YYYYMMDD-HHMMSS`. Each case gets its own folder `outputs/<case_id>/` with `case_note.md` (plain text of the final note) and `audit_log.json` (de-identified log, 2-space indent, non-JSON values written as strings).

## 6. Calling Claude (shared by nodes 3, 4, 5 and 8)

- Use the `anthropic` SDK, `client.messages.create`, with `model` and `max_tokens` from `settings.yaml` (`llm.model`, `llm.max_tokens`).
- Claude must always answer through one tool whose `input_schema` defines the expected JSON, so replies are always structured.
- **Do not force the tool.** The configured model (Sonnet 5.5) returns a 400 error for `tool_choice` set to `"tool"` or `"any"`. Leave `tool_choice` at its default and append to the user message: *"Answer only by calling the `<tool name>` tool once. Do not reply in text."*
- If the reply contains no matching `tool_use` block, retry once (two attempts total), then raise a `RuntimeError`.
- Put this in one shared function, `extract.call_tool(tool, system, user_text, settings, client=None, attempts=2)`, returning the tool's input dict. `draft_note.py` imports it.

## 7. Nodes

### Node 1 — Record call (deployment only)
Not built. In deployment, Vonage Contact Center records the call after the client consents. In the prototype the volunteer uploads a recording. A required consent checkbox is planned but not built.

### Node 2 — Transcribe call (kept: `transcribe.py`)
Called by the app with the uploaded file's bytes. If `is_english` is false (language not English, or confidence below `min_english_confidence`), stop and tell the volunteer to handle the intake manually.

### Nodes 3–5 — `extract.py`

**System prompt for extraction** (nodes 3–5). It must say that a volunteer reviews everything, and give these rules:
- Use only what was said in the transcript; never guess or fill in typical values.
- For every fact, copy the exact supporting words into `quote`. If nothing supports it, `value` and `quote` are null and `status` is `not_discussed`.
- If the client said they don't know, `status` is `client_unknown`, using the field's "unknown" value if it has one. If they declined, `status` is `declined`, using "Declined" if the field allows it.
- Fields with a fixed list of values use one of the listed values exactly.
- Dates are YYYY-MM-DD; resolve dates without a year using the call date.
- Numbers are plain, with no $ sign or commas.
- Phone numbers are 10 digits with no punctuation; zip codes are 5 digits.

**Fact tool** — build it from the schema with `fact_tool(name, fields, extra_properties=None)`:
- Each field becomes an object property with `value`, `quote` (string or null) and `status` (enum of the four statuses), all required.
- `value` type: number or null for `number` and `integer` fields; string or null otherwise; for `choice` fields add an enum of the allowed values plus null.
- Each property's description: the field name with underscores as spaces; plus "one of: …" for choice fields, "date, YYYY-MM-DD" for dates, "number" for numbers.
- For `care_gap`, add this definition: "yes" only if the client is at immediate risk because they are without care they currently depend on — out of, or about to run out of, a medication; a treatment, home care hours or a procedure cut, cancelled or missed; or no access to a provider they are being treated by. A new item or service that is still being requested, reviewed or appealed (for example, equipment not yet approved) is "no" unless one of those also applies.

**Cleaning** — `clean_facts(raw, fields)` runs on every fact-tool reply:
- Keep only fields in `fields`; a missing field becomes `not_discussed`.
- An unknown status becomes `not_discussed`.
- `not_discussed` forces `value` to null.
- `client_unknown` with a null value takes the field's "unknown" option (case-insensitive) if one exists.
- `declined` with a null value takes "Declined" if the field allows it.
- Set `source` to `ai`.

**User message** for every node: `Call date: <date>`, a blank line, any extra context, then the transcript inside `<transcript>…</transcript>` tags.

**Node 3 — `extract_core_facts(transcript_text, call_date, schema, settings, client=None)`**
Fields: every `core` field except `call_date` and `narrative` (these two are `NOT_EXTRACTED`: call date comes from the app; the narrative is a separate property). Tool `record_core_facts`, with an extra required string property `narrative`: 3–5 plain-language sentences giving the client's problem in their own terms, what has happened so far and what they want, with no facts not in the transcript. Returns `(facts, narrative)`.

**Node 4 — `categorize(transcript_text, core_facts, schema, settings, client=None)`**
Tool `record_categories` with `categories` (array, at least 1 item, enum from `categories_field.allowed`), `rationale` (string) and `quote` (string or null). Extra context in the message:
- category definitions: billing = a bill the client received; appeal = a denial the client wants to challenge; eligibility = getting or keeping coverage; a case can have more than one;
- the core facts already found, one `- field: value` line per non-null value.

Leave the call date blank. Drop any category not in the allowed list; return categories sorted and de-duplicated.

**Node 5 — `extract_category_facts(transcript_text, call_date, categories, schema, settings, client=None)`**
Return `{}` if there are no categories. Fields: `merge_fields(schema, categories)` minus every core field. Tool `record_case_facts`; extra context `Case categories: <comma-separated>`. Returns cleaned facts.

### Glue — `frame.py`
`build_record(call_date, categories, facts, schema) -> pandas.DataFrame` with one row:
- `call_date` from the app;
- `case_categories` as the categories joined with `;`, or None if empty;
- one column per field in `merge_fields(schema, categories)` (except `call_date`), holding `facts[field]["value"]` or None;
- for `number` and `integer` fields, convert text values to float after removing `$` and commas; if that fails, leave the text so Pandera reports it as invalid;
- convert whole-number floats to int for `integer` fields.

Review form values are strings, so this conversion is what makes edited numbers valid.

### Node 6 — Validate record (kept: `validate.py`)
Call `validate_case(build_record(...), schema)` and treat `None` as `{"missing": [], "invalid": [], "urgent": []}`.

### Node 7 — Volunteer review (`app.py`)
Streamlit app, wide layout, titled "Intake call review", captioned as a prototype for synthetic test calls only, with every AI output reviewed by the volunteer. Load the schema and settings once at startup; keep all state in `st.session_state`.

**Step 1 — Call (sidebar):**
- Choose "Upload a recording" or "Use a saved transcript", and a call date (default today, ISO format).
- Upload: wav, mp3 or m4a, then "Transcribe and process" runs node 2. Show transcription errors. Stop on non-English, showing the detected language and confidence.
- Saved transcript: pick from `tests/*/fixtures/*transcript*.json`, then "Process".
- Processing runs nodes 3, 4 and 5 in order, showing progress for each. Then store:
  - the transcript and call date;
  - `facts_ai` (core and category facts merged);
  - `narrative_ai` and an editable `narrative`;
  - the categories result;
  - `categories_ai`, `categories`, and the multiselect value;
  - an empty re-extraction list;
  - no note, and no saved path.
  - Initialize one value widget per fact (empty string for null) and one reject checkbox (unchecked).
- Until a call is processed, show a prompt to start and stop.

**Step 2 — Review:**
- A collapsible transcript showing each utterance as **speaker** + text (or the full text if there are no utterances).
- **Case category:** a multiselect of allowed categories, with a caption showing the AI's suggestion and rationale. When the selection differs from the current categories, show "Update category and re-extract facts" and a warning to re-extract before approving.
- **Re-extract (loop to node 5):**
  - run node 5 with the new categories;
  - keep all core facts;
  - for each new category fact: if the field already exists and its form value is not empty, keep the existing fact; otherwise use the new fact and reset its widgets;
  - drop facts for fields no longer in any assigned category;
  - append `{"from": old, "to": new}` to the re-extraction list;
  - set the new categories and clear any drafted note.
- **Current facts:** rebuilt on every run from `facts_ai` plus the widgets:
  - a rejected fact or an empty value → null;
  - `source` set as in section 5.
- **Validation (node 6):** run on every page run, so it validates the AI's facts on load and again after every edit.
  - Show missing fields as a warning, invalid fields as an error, or a success message when there are none.
  - Each urgent flag gets an Approve/Deny radio (default Approve), labeled with its plain-language text (see node 8).
- **Facts form:** one row per field in `merge_fields` that is in `facts_ai` (skipping `NOT_EXTRACTED`).
  - Choice fields use a dropdown (blank plus the allowed values, plus the current value if it is not in the list); other fields use a text input.
  - Mark missing or invalid fields with 🔴.
  - Beside each, show the quote, or the status in words if there is no quote, and a Reject checkbox.
- A text area for the narrative.

### Node 8 — Draft case note (`draft_note.py`, triggered from `app.py`)
"Approve record and draft case note" is disabled while a category change is pending. It calls `draft_note(facts, narrative, categories, missing, urgent, settings, client=None)`, where:
- `facts` are the current reviewed facts;
- `narrative` is the reviewed narrative;
- `missing` comes from node 6;
- `urgent` lists only the approved flags.

Code fills every section of `note_template.md` except two:
- **Client and plan:** Client, Phone, Language at home, Plan, Plan type, from `client_name`, `contact_number`, `language_at_home`, `plan_name` and `plan_type`; "—" for null.
- **Case type:** categories capitalized, comma-separated, or "—".
- **Key facts:** every other non-null fact, as `Field name: value` bullets. Exclude the five client-and-plan fields and the narrative.
- **Missing information:** bullets of missing field names with spaces for underscores, or "- None".
- **Urgency:** bullets of approved flags in plain language, or "- None". Text per flag:
  - `appeal_deadline_within_14_days` → "Appeal deadline is 14 days or fewer after the call";
  - `care_gap` → *(update to match the narrowed care-gap definition; the current code still says "Client is going without needed care or medication")*.

Claude writes only **Issue summary** (2–4 sentences) and **Next steps** (2–5 short suggestions for the case handler, such as documents to request or deadlines to act on), through tool `write_note_sections`.
- Claude receives only the categories, urgent flags, still-missing fields, verified facts (non-null values) and narrative — never the transcript.
- The system prompt tells it to use only what it is given, never add facts, write plainly and briefly, and treat next steps as suggestions the volunteer will review.

The draft appears in an editable text area. Changing categories clears it.

### Node 9 — De-identify and save (kept: `deidentify.py`, triggered from `app.py`)
"Save case" does three things:
1. creates `outputs/<case_id>/`;
2. writes the edited note to `case_note.md`;
3. builds the audit log (section 5), passes it through `deidentify_log`, and writes the result to `audit_log.json`.

`urgent_denied` is every urgent flag not approved. After saving, show the saved path and the de-identified log in an expander.

## 8. Rules that must always hold

- Never commit API keys, real call data, or anything in `outputs/`.
- Claude never chooses note content outside Issue summary and Next steps; all other sections come from reviewed facts.
- The audit log is de-identified before it is written to disk. `case_note.md` is not de-identified: it is for staff handling the case.
- Every AI output (facts, categories, narrative, urgency, note) is shown to the volunteer and can be changed before saving.
- No CI test calls Claude (the Anthropic key stays local).

## 9. How to check a rebuild

1. `python -m pytest tests/pandera tests/presidio -v` passes (AssemblyAI tests need the key: `python -m pytest tests/assemblyai -v --timeout=720`).
2. `streamlit run app.py` → "Use a saved transcript" → pick `intake_en_identifiers_transcript.json` → Process. Nodes 3–5 run, facts appear with quotes, and validation flags show.
3. Edit a fact, reject a fact and change a category: validation updates after each change, and drafting stays disabled until re-extraction.
4. Draft and save. `outputs/<case_id>/` contains `case_note.md` and `audit_log.json`. In the log, name, phone and email are `<REMOVED>`, zip is 3 digits plus `**`, and the transcript is redacted.
