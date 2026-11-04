"""Intake call workflow prototype (EPID 695 final project).

Run from the repo folder:
    streamlit run app.py

Needs ANTHROPIC_API_KEY (and ASSEMBLYAI_API_KEY to transcribe a new
recording). Synthetic test data only: do not use real client calls.
"""

import datetime as dt
import json
from pathlib import Path

import streamlit as st

from intake import extract
from intake.config import ROOT, load_schema, load_settings, outputs_dir
from intake.deidentify import deidentify_log
from intake.draft_note import URGENCY_TEXT, draft_note
from intake.frame import build_record
from intake.transcribe import TranscriptionError, transcribe
from intake.validate import merge_fields, validate_case

st.set_page_config(page_title="Intake call review", layout="wide")
SCHEMA = load_schema()
SETTINGS = load_settings()
S = st.session_state
LABEL = lambda f: f.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def set_fact_widgets(facts):
    for field, fact in facts.items():
        S[f"val_{field}"] = "" if fact["value"] is None else str(fact["value"])
        S[f"rej_{field}"] = False


def current_facts():
    """Facts as the volunteer has them now (edits and rejections applied)."""
    out = {}
    for field, ai in S.facts_ai.items():
        text = S.get(f"val_{field}", "").strip()
        rejected = S.get(f"rej_{field}", False)
        value = None if rejected or text == "" else text
        ai_value = "" if ai["value"] is None else str(ai["value"])
        if rejected:
            source = "rejected"
        elif text != ai_value:
            source = "volunteer_added" if ai_value == "" else "volunteer_edited"
        else:
            source = "ai"
        out[field] = {**ai, "value": value, "source": source}
    return out


def process(transcript, call_date):
    with st.status("Processing call…", expanded=True) as status:
        st.write("Node 3: extracting core facts…")
        core, narrative = extract.extract_core_facts(
            transcript["text"], call_date, SCHEMA, SETTINGS)
        st.write("Node 4: categorizing case…")
        cat = extract.categorize(transcript["text"], core, SCHEMA, SETTINGS)
        st.write("Node 5: extracting category facts…")
        case = extract.extract_category_facts(
            transcript["text"], call_date, cat["categories"], SCHEMA, SETTINGS)
        status.update(label="Ready for review", state="complete")
    S.transcript = transcript
    S.call_date = call_date
    S.facts_ai = {**core, **case}
    S.narrative_ai = narrative
    S.narrative = narrative
    S.category_info = cat
    S.categories_ai = cat["categories"]
    S.categories = cat["categories"]
    S.category_pick = cat["categories"]
    S.reextractions = []
    S.note = None
    S.saved_to = None
    set_fact_widgets(S.facts_ai)


def rerun_category_facts():
    """Loop back to node 5 with the volunteer's categories."""
    new = sorted(S.category_pick)
    case = extract.extract_category_facts(
        S.transcript["text"], S.call_date, new, SCHEMA, SETTINGS)
    keep = {f: v for f, v in S.facts_ai.items() if f in SCHEMA["core"]}
    for field, fact in case.items():
        if field in S.facts_ai and S.get(f"val_{field}", "") != "":
            keep[field] = S.facts_ai[field]          # keep shared fields
        else:
            keep[field] = fact
            S[f"val_{field}"] = "" if fact["value"] is None else str(fact["value"])
            S[f"rej_{field}"] = False
    S.facts_ai = keep
    S.reextractions.append({"from": S.categories, "to": new})
    S.categories = new
    S.note = None


# ---------------------------------------------------------------------------
# Step 1: input
# ---------------------------------------------------------------------------

st.title("Intake call review")
st.caption("Prototype · synthetic test calls only · every AI output is "
           "reviewed by the volunteer before use")

with st.sidebar:
    st.header("1. Call")
    source = st.radio("Input", ["Upload a recording", "Use a saved transcript"])
    call_date = st.date_input("Call date", dt.date.today()).isoformat()
    transcript = None
    if source == "Upload a recording":
        audio = st.file_uploader("Recording", type=["wav", "mp3", "m4a"])
        go = st.button("Transcribe and process", disabled=audio is None,
                       type="primary")
        if go:
            try:
                with st.spinner("Node 2: transcribing with AssemblyAI…"):
                    transcript = transcribe(audio.getvalue(), SETTINGS)
            except TranscriptionError as err:
                st.error(f"Transcription failed: {err}")
            if transcript and not transcript["is_english"]:
                st.warning("This call was not detected as English "
                           f"({transcript['language_code']}, confidence "
                           f"{transcript['language_confidence']}). English-only "
                           "scope: handle this intake manually.")
                transcript = None
    else:
        saved = sorted(ROOT.glob("tests/*/fixtures/*transcript*.json"))
        pick = st.selectbox("Transcript", saved,
                            format_func=lambda p: p.name) if saved else None
        go = st.button("Process", disabled=pick is None, type="primary")
        if go:
            data = json.loads(Path(pick).read_text())
            transcript = {"text": data["text"],
                          "utterances": data.get("utterances", []),
                          "is_english": True}
    if transcript:
        process(transcript, call_date)

if "facts_ai" not in S:
    st.info("Upload a recording or choose a saved transcript to start.")
    st.stop()


# ---------------------------------------------------------------------------
# Step 2: review (node 7), with live validation (node 6)
# ---------------------------------------------------------------------------

with st.expander("Transcript"):
    for u in S.transcript["utterances"] or [{"speaker": "", "text": S.transcript["text"]}]:
        st.markdown(f"**{u['speaker']}** {u['text']}")

st.header("2. Review")

c1, c2 = st.columns([2, 3])
with c1:
    st.subheader("Case category")
    st.multiselect("Categories", SCHEMA["categories_field"]["allowed"],
                   key="category_pick")
    st.caption(f"AI suggested: {', '.join(S.categories_ai)} — "
               f"{S.category_info['rationale']}")
    if sorted(S.category_pick) != S.categories:
        st.button("Update category and re-extract facts", type="primary",
                  on_click=rerun_category_facts, disabled=not S.category_pick)
        st.warning("Category changed: re-extract before approving.")

facts = current_facts()
record = build_record(S.call_date, S.categories, facts, SCHEMA)
result = validate_case(record, SCHEMA) or {"missing": [], "invalid": [], "urgent": []}

with c2:
    st.subheader("Validation")
    if result["missing"]:
        st.warning("Missing: " + ", ".join(LABEL(m) for m in result["missing"]))
    if result["invalid"]:
        st.error("Check format: " + ", ".join(LABEL(m) for m in result["invalid"]))
    if not (result["missing"] or result["invalid"]):
        st.success("All required fields present and valid.")
    approved_urgent = []
    for flag in result["urgent"]:
        choice = st.radio(f"Urgent: {URGENCY_TEXT.get(flag, flag)}",
                          ["Approve", "Deny"], key=f"urg_{flag}", horizontal=True)
        if choice == "Approve":
            approved_urgent.append(flag)
    if not result["urgent"]:
        st.caption("No urgency flags.")

st.subheader("Facts")
st.caption("Edit or fill any value, or reject it. Every change re-runs "
           "validation. Quotes show where the AI found each fact.")
fields = merge_fields(SCHEMA, S.categories)
for field in fields:
    if field in extract.NOT_EXTRACTED or field not in S.facts_ai:
        continue
    spec, ai = fields[field], S.facts_ai[field]
    a, b, c = st.columns([2, 3, 1])
    flag = "🔴 " if field in result["missing"] + result["invalid"] else ""
    if spec["type"] == "choice":
        options = [""] + [str(o) for o in spec["allowed"]]
        if S.get(f"val_{field}", "") not in options:
            options.append(S[f"val_{field}"])
        a.selectbox(flag + LABEL(field), options, key=f"val_{field}")
    else:
        a.text_input(flag + LABEL(field), key=f"val_{field}")
    quote = ai.get("quote")
    b.markdown(f"> {quote}" if quote else f"*{ai['status'].replace('_', ' ')}*")
    c.checkbox("Reject", key=f"rej_{field}")

st.text_area("Narrative / other information", key="narrative", height=120)


# ---------------------------------------------------------------------------
# Step 3: case note (node 8) and save with de-identified log (node 9)
# ---------------------------------------------------------------------------

st.header("3. Case note")
blocked = sorted(S.category_pick) != S.categories
if st.button("Approve record and draft case note", type="primary", disabled=blocked):
    with st.spinner("Node 8: drafting case note…"):
        S.note = draft_note(facts, S.narrative, S.categories, result["missing"],
                            approved_urgent, SETTINGS)
    S.saved_to = None

if S.get("note"):
    st.text_area("Draft (edit before saving)", key="note", height=420)
    if st.button("Save case"):
        case_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = outputs_dir(SETTINGS) / case_id
        folder.mkdir()
        (folder / "case_note.md").write_text(S.note)
        log = {
            "case_id": case_id, "call_date": S.call_date,
            "model": SETTINGS["llm"]["model"],
            "transcript": S.transcript["text"],
            "categories_ai": S.categories_ai, "categories_final": S.categories,
            "category_reextractions": S.reextractions,
            "facts_ai": S.facts_ai, "facts_final": facts,
            "narrative_ai": S.narrative_ai, "narrative_final": S.narrative,
            "validation": {k: result[k] for k in ("missing", "invalid", "urgent")},
            "urgent_approved": approved_urgent,
            "urgent_denied": [u for u in result["urgent"] if u not in approved_urgent],
            "case_note": S.note,
        }
        with st.spinner("Node 9: de-identifying the review log…"):
            clean = deidentify_log(log)
        (folder / "audit_log.json").write_text(json.dumps(clean, indent=2, default=str))
        S.saved_to = folder

if S.get("saved_to"):
    st.success(f"Saved to {S.saved_to.relative_to(ROOT)}: case_note.md and "
               "audit_log.json (de-identified)")
    with st.expander("De-identified audit log"):
        st.json(json.loads((S.saved_to / "audit_log.json").read_text()))
