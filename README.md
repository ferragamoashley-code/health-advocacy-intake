# Health Advocacy Intake

[![Protocol unit tests](https://github.com/ferragamoashley-code/health-advocacy-intake/actions/workflows/tests.yml/badge.svg)](https://github.com/ferragamoashley-code/health-advocacy-intake/actions/workflows/tests.yml)

Black-box unit tests for the atomic protocols in an AI-assisted intake workflow for health advocacy nonprofits.

See the [wire diagram](#wire-diagram) for the full workflow.

## Wire diagram

The full intake workflow, from the incoming call to the reviewed case note and the de-identified audit log. Numbers match the node numbers in the code (`intake/` and `app.py`).

- **Atomic protocol** (dark): an externally maintained tool, covered by the black-box unit tests in `tests/` (Record call is not tested; see `tests/vonage/README.md`).
- **Disposable component** (light): code written for this workflow that can be regenerated.
- **Cylinders**: organization-specific config. Editing `config/` adapts the workflow to another organization.
- **Dotted arrows**: config inputs (from a cylinder), or data the app keeps in memory and carries forward to review.

```mermaid
flowchart TD
    %% Inputs and config
    CALL(["Phone call · client consents → recording begins<br/><i>consent checkbox: planned</i>"])
    SCHEMA[("config/case_schema.yaml<br/>core fields · categories · fields per category · urgency rules")]
    TEMPLATE[("config/note_template.md")]
    SETTINGS[("config/settings.yaml<br/>Claude model for nodes 3, 4, 5 and 8 ·<br/>AssemblyAI options for node 2 · outputs folder")]

    N1["<b>ATOMIC PROTOCOL</b><br/><b>1 · Record call</b><br/><i>Deployment only.</i> Vonage Contact Center records the call<br/><i>Prototype: recording uploaded in the app</i>"]
    N2["<b>ATOMIC PROTOCOL</b><br/><b>2 · Transcribe call</b><br/>AssemblyAI speech-to-text with speaker labels<br/>and an English-only check · transcribe.py"]
    STOP(["Not English → stop:<br/>volunteer handles the intake manually"])
    N3["<b>DISPOSABLE COMPONENT</b><br/><b>3 · Extract core facts</b><br/>Claude fills every core field, including care gap,<br/>with value, source quote and status, plus a narrative · extract.py"]
    N4["<b>DISPOSABLE COMPONENT</b><br/><b>4 · Categorize case</b><br/>Claude assigns one or more categories<br/>(billing, appeal, eligibility) with rationale and quote · extract.py"]
    N5["<b>DISPOSABLE COMPONENT</b><br/><b>5 · Extract category-specific facts</b><br/>Claude fills the fields the assigned categories need,<br/>with value, source quote and status · extract.py"]
    N6["<b>ATOMIC PROTOCOL</b><br/><b>6 · Validate record</b><br/>Pandera checks the one-row case record against the schema:<br/>required fields present · values in valid format ·<br/>urgency rules: appeal due ≤ 14 days after the call; care gap = yes<br/>validate.py (record built by frame.py)"]
    N7["<b>DISPOSABLE COMPONENT</b><br/><b>7 · Volunteer review</b><br/>Streamlit form shows each fact with its source quote;<br/>volunteer edits, fills or rejects facts, edits the narrative,<br/>changes categories and approves or denies urgency flags · app.py"]
    N8["<b>DISPOSABLE COMPONENT</b><br/><b>8 · Draft case note</b><br/>Code fills client and plan, case type, key facts, missing information<br/>and urgency; Claude writes only the issue summary and next steps;<br/>volunteer edits the draft and saves · draft_note.py"]
    N9["<b>ATOMIC PROTOCOL</b><br/><b>9 · De-identify</b><br/>Drops name, phone and email; zip → 3 digits;<br/>Microsoft Presidio redacts the transcript, narratives, quotes and case note · deidentify.py"]
    NOTE(["outputs/&lt;case_id&gt;/case_note.md<br/>→ staff review and case decision"])
    AUDIT(["outputs/&lt;case_id&gt;/audit_log.json<br/>de-identified, for evaluation"])

    CALL --> N1
    N1 -->|"call recording (.wav / .mp3)"| N2
    SETTINGS -.->|"AssemblyAI options"| N2
    N2 -->|"not detected as English (or confidence < 0.5)"| STOP
    N2 -->|"transcript (text + utterances)"| N3
    SCHEMA -.->|"core fields"| N3
    N3 -->|"transcript + core facts (dict)"| N4
    SCHEMA -.->|"allowed categories"| N4
    N4 -->|"transcript + call date + categories (list[str])"| N5
    SCHEMA -.->|"fields per category"| N5
    N5 -->|"core + category facts + categories → one-row DataFrame (frame.py)"| N6
    SCHEMA -.->|"required fields, formats, urgency rules"| N6
    N6 -->|"missing (list[str]) · invalid (list[str]) · urgent (list[str])"| N7
    N3 -.->|"carried forward: core facts + narrative"| N7
    N4 -.->|"carried forward: categories + rationale + quote"| N7
    N5 -.->|"carried forward: category facts"| N7
    N7 -->|"each edit → re-validate: current facts + categories (one-row DataFrame)"| N6
    N7 -->|"category change → re-extract: categories (list[str])"| N5
    N7 -->|"approve: verified facts + narrative + categories + missing + approved urgency"| N8
    TEMPLATE -.->|"note template (str)"| N8
    N8 -->|"case note (markdown)"| NOTE
    N8 -->|"review log (dict): AI vs. final facts, categories and narrative, validation, urgency decisions, case note"| N9
    N9 -->|"redacted log (JSON)"| AUDIT

    classDef atomic fill:#1f3864,stroke:#1f3864,color:#ffffff
    classDef disposable fill:#dbe8f8,stroke:#3d6fc4,color:#1a1a1a
    classDef io fill:#ffffff,stroke:#888888,color:#333333
    classDef config fill:#f4f4f4,stroke:#888888,color:#333333
    class N1,N2,N6,N9 atomic
    class N3,N4,N5,N7,N8 disposable
    class CALL,STOP,NOTE,AUDIT io
    class SCHEMA,TEMPLATE,SETTINGS config
```

## Protocols

| Protocol | Status | Details |
|---|---|---|
| AssemblyAI (transcription) | Complete (46 passed, 5 skipped) | [tests/assemblyai](tests/assemblyai/README.md) |
| Pandera (schema validation) | Complete (26 passed) | [tests/pandera](tests/pandera/README.md) |
   | Presidio (de-identification) | Complete (32 passed) | [tests/presidio](tests/presidio/README.md) |
| Vonage (call recording) | Not tested (no license) | [tests/vonage](tests/vonage/README.md) |

## Prototype

`app.py` is a working Streamlit prototype of the full intake workflow. A recorded call goes in; a reviewed case note and a de-identified audit log come out.

| Node | Step | File |
|---|---|---|
| 1 | Record call (Vonage) | not included, no license; upload a recording instead |
| 2 | Transcribe (AssemblyAI) | `intake/transcribe.py` |
| 3 | Extract core facts and narrative (Claude) | `intake/extract.py` |
| 4 | Categorize case (Claude) | `intake/extract.py` |
| 5 | Extract category-specific facts (Claude) | `intake/extract.py` |
| — | Build case data frame | `intake/frame.py` |
| 6 | Validate against case schema (Pandera) | `intake/validate.py` |
| 7 | Volunteer review | `app.py` |
| 8 | Draft case note (Claude + template) | `intake/draft_note.py` |
| 9 | De-identify audit log (Presidio) | `intake/deidentify.py` |

Claude extracts every fact with the quote it came from, and the volunteer reviews everything before use: they can edit, fill in or reject facts, change the case category (which re-runs node 5), and approve or deny urgency flags. Nodes 6 and 9 use the same configuration as the tested protocols in `tests/`. The prototype uses Claude Sonnet 5.5 (`config/settings.yaml`) and is for synthetic test calls only.

## Setup

### Requirements

- Python 3.12
- An [AssemblyAI](https://www.assemblyai.com/) API key (transcription tests and new recordings)
- An [Anthropic](https://console.anthropic.com/) API key (the prototype app)
- Pandera and Presidio tests need no keys and run offline once installed

### Install

```
git clone https://github.com/ferragamoashley-code/health-advocacy-intake-repo.git
cd health-advocacy-intake-repo
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` includes the spaCy `en_core_web_lg` model (about 400 MB) that Presidio uses.

### API keys

Add both keys to your shell profile (`~/.zshrc` on a Mac), then run `source ~/.zshrc`:

```
export ASSEMBLYAI_API_KEY="your-key"
export ANTHROPIC_API_KEY="your-key"
```

### Run the tests

```
python -m pytest tests/pandera -v
python -m pytest tests/presidio -v
python -m pytest tests/assemblyai -v --timeout=720
```

The 60-minute AssemblyAI test runs only on request:

```
RUN_MANUAL=1 python -m pytest tests/assemblyai -k long_call_60min --timeout=2400
```

On GitHub, `.github/workflows/tests.yml` runs the Pandera and Presidio tests on every push, and the AssemblyAI tests every Monday or by hand from the Actions tab. The AssemblyAI job needs a repository secret named `ASSEMBLYAI_API_KEY` (Settings → Secrets and variables → Actions).

### Run the prototype

```
streamlit run app.py
```

The app opens in your browser. Upload a recording, or choose **Use a saved transcript** to skip transcription. Saved cases go to `outputs/`, which is git-ignored, because case notes contain client details. **Use synthetic test calls only.**

Settings, the case schema and the case note template are in `config/`.