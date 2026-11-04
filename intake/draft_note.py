"""Node 8: draft the case note from verified facts only.

Sections built straight from the reviewed record (client and plan, case type,
key facts, missing information, urgency) are filled by code, so nothing
unverified can appear there. Claude writes only the issue summary and the
suggested next steps, from the verified facts and the reviewed narrative,
not from the transcript.
"""

import anthropic

from .config import load_note_template
from .extract import call_tool

LABELS = {"client_name": "Client", "plan_name": "Plan", "plan_type": "Plan type",
          "contact_number": "Phone", "language_at_home": "Language at home"}

NOTE_SYSTEM = """You draft intake case notes for volunteer advocates at a \
health advocacy nonprofit. Use only the verified facts and narrative you are \
given; never add facts. Write plainly and briefly. Next steps are suggestions \
for the case handler, such as documents to request or deadlines to act on; \
the volunteer will review and edit them."""


def _fmt(value):
    return "—" if value is None else str(value)


def _bullets(items):
    return "\n".join(f"- {i}" for i in items) if items else "- None"


def _llm_sections(facts, narrative, categories, missing, urgent, settings, client):
    tool = {
        "name": "write_note_sections",
        "description": "Write the narrative sections of the case note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_summary": {"type": "string",
                                  "description": "2-4 sentences."},
                "next_steps": {"type": "array", "items": {"type": "string"},
                               "description": "2-5 short suggested next steps."},
            },
            "required": ["issue_summary", "next_steps"],
        },
    }
    verified = "\n".join(f"- {k}: {v['value']}" for k, v in facts.items()
                         if v.get("value") is not None)
    user = (f"Case categories: {', '.join(categories)}\n"
            f"Urgent: {', '.join(urgent) or 'none'}\n"
            f"Still missing: {', '.join(missing) or 'none'}\n\n"
            f"Verified facts:\n{verified}\n\nNarrative:\n{narrative}")
    return call_tool(tool, NOTE_SYSTEM, user, settings, client)


URGENCY_TEXT = {
    "appeal_deadline_within_14_days": "Appeal deadline is 14 days or fewer after the call",
    "care_gap": "Client is going without needed care or medication",
}


def draft_note(facts, narrative, categories, missing, urgent, settings, client=None):
    """facts: verified facts {field: {"value": ...}}; urgent: approved flags."""
    llm = _llm_sections(facts, narrative, categories, missing, urgent, settings, client)
    client_lines = [f"{LABELS[k]}: {_fmt(facts.get(k, {}).get('value'))}"
                    for k in ("client_name", "contact_number", "language_at_home",
                              "plan_name", "plan_type")]
    skip = set(LABELS) | {"narrative"}
    key_facts = [f"{k.replace('_', ' ').capitalize()}: {v['value']}"
                 for k, v in facts.items() if k not in skip and v.get("value") is not None]
    return load_note_template().format(
        client_and_plan="\n".join(client_lines),
        case_type=", ".join(c.capitalize() for c in categories) or "—",
        issue_summary=llm["issue_summary"],
        key_facts=_bullets(key_facts),
        missing_information=_bullets([m.replace("_", " ") for m in missing]),
        urgency=_bullets([URGENCY_TEXT.get(u, u) for u in urgent]),
        next_steps=_bullets(llm["next_steps"]),
    )
