"""Nodes 3, 4 and 5: Claude reads the transcript and fills the case record.

  3  extract_core_facts      core fields (every case) + narrative
  4  categorize              case categories (one or more)
  5  extract_category_facts  fields for the assigned categories

Every fact comes back as {value, quote, status}:
  value   the answer in the schema's format, or null
  quote   the exact transcript words it came from, or null
  status  stated | client_unknown | declined | not_discussed

Fields asked for are built from config/case_schema.yaml, and Claude is made
to answer through a tool whose input schema lists them, so the reply is
always structured JSON.
"""

import anthropic

from .validate import merge_fields

STATUSES = ["stated", "client_unknown", "declined", "not_discussed"]

# Filled from call metadata or by node 4, not extracted as facts.
NOT_EXTRACTED = {"call_date", "narrative"}

SYSTEM = """You help volunteer advocates at a health advocacy nonprofit turn \
intake call transcripts into case records. A volunteer reviews everything you \
produce before it is used.

Rules:
- Use only what was said in the transcript. Never guess or fill in typical values.
- For every fact, copy the exact words from the transcript that support it \
into "quote". If nothing supports it, value and quote are null and status is \
"not_discussed".
- If the client said they don't know, use status "client_unknown" and the \
field's "unknown" value if it has one. If the client declined to answer, use \
status "declined" and "Declined" if the field allows it.
- For fields with a fixed list of values, use one of the listed values exactly.
- Dates are YYYY-MM-DD. Resolve dates without a year using the call date.
- Numbers are plain numbers with no $ sign or commas.
- Phone numbers are 10 digits with no punctuation; zip codes are 5 digits."""


def _client():
    return anthropic.Anthropic()


def _value_schema(spec):
    kind = spec["type"]
    if kind in ("number", "integer"):
        schema = {"type": ["number", "null"]}
    else:
        schema = {"type": ["string", "null"]}
    if kind == "choice":
        schema["enum"] = list(spec["allowed"]) + [None]
    return schema


def _describe(name, spec):
    parts = [name.replace("_", " ")]
    if spec["type"] == "choice":
        parts.append("one of: " + ", ".join(str(a) for a in spec["allowed"]))
    elif spec["type"] == "date":
        parts.append("date, YYYY-MM-DD")
    elif spec["type"] in ("number", "integer"):
        parts.append("number")
    if name == "care_gap":
        parts.append('"yes" only if the client is at immediate risk because '
                     "they are without care they currently depend on: out of, "
                     "or about to run out of, a medication; a treatment, home "
                     "care hours or a procedure cut, cancelled or missed; or "
                     "no access to a provider they are being treated by. A "
                     "new item or service that is still being requested, "
                     "reviewed or appealed (for example, equipment not yet "
                     'approved) is "no" unless one of those also applies')
    return "; ".join(parts)


def fact_tool(name, fields, extra_properties=None):
    props = {}
    for field, spec in fields.items():
        props[field] = {
            "type": "object",
            "description": _describe(field, spec),
            "properties": {
                "value": _value_schema(spec),
                "quote": {"type": ["string", "null"]},
                "status": {"type": "string", "enum": STATUSES},
            },
            "required": ["value", "quote", "status"],
        }
    props.update(extra_properties or {})
    return {
        "name": name,
        "description": "Record the facts found in the intake call transcript.",
        "input_schema": {"type": "object", "properties": props,
                         "required": list(props)},
    }


def call_tool(tool, system, user_text, settings, client=None, attempts=2):
    """Ask Claude to answer through one tool and return the tool's input.

    Sonnet 5.5 does not accept forcing a tool (tool_choice "tool" or "any"
    returns a 400), so tool_choice stays on its default ("auto") and the
    prompt tells Claude to call the tool. If it answers in text instead,
    the request is retried once.
    """
    client = client or _client()
    instruction = (f"\n\nAnswer only by calling the {tool['name']} tool once. "
                   "Do not reply in text.")
    for _ in range(attempts):
        response = client.messages.create(
            model=settings["llm"]["model"],
            max_tokens=settings["llm"]["max_tokens"],
            system=system,
            tools=[tool],
            messages=[{"role": "user", "content": user_text + instruction}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return block.input
    raise RuntimeError(f"Claude did not call {tool['name']} after {attempts} tries.")


def _call_tool(tool, user_text, settings, client=None):
    return call_tool(tool, SYSTEM, user_text, settings, client)


def clean_facts(raw, fields):
    """Keep only known fields and make value agree with status."""
    facts = {}
    for field, spec in fields.items():
        fact = raw.get(field) or {}
        value, status = fact.get("value"), fact.get("status", "not_discussed")
        if status not in STATUSES:
            status = "not_discussed"
        allowed = spec.get("allowed", [])
        if status == "not_discussed":
            value = None
        elif status == "client_unknown" and value is None:
            value = next((a for a in allowed if str(a).lower() == "unknown"), None)
        elif status == "declined" and value is None:
            value = "Declined" if "Declined" in allowed else None
        facts[field] = {"value": value, "quote": fact.get("quote"),
                        "status": status, "source": "ai"}
    return facts


def _prompt(transcript_text, call_date, extra=""):
    return (f"Call date: {call_date}\n\n{extra}"
            f"<transcript>\n{transcript_text}\n</transcript>")


# Node 3
def extract_core_facts(transcript_text, call_date, schema, settings, client=None):
    fields = {k: v for k, v in schema["core"].items() if k not in NOT_EXTRACTED}
    tool = fact_tool("record_core_facts", fields, {
        "narrative": {"type": "string",
                      "description": "3-5 sentences in plain language: the "
                                     "client's problem in their own terms, what "
                                     "has happened so far and what they want. "
                                     "No facts that are not in the transcript."},
    })
    raw = _call_tool(tool, _prompt(transcript_text, call_date), settings, client)
    return clean_facts(raw, fields), raw.get("narrative", "")


# Node 4
def categorize(transcript_text, core_facts, schema, settings, client=None):
    allowed = schema["categories_field"]["allowed"]
    tool = {
        "name": "record_categories",
        "description": "Record which case categories apply to this call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "categories": {"type": "array", "minItems": 1,
                               "items": {"type": "string", "enum": allowed}},
                "rationale": {"type": "string"},
                "quote": {"type": ["string", "null"]},
            },
            "required": ["categories", "rationale", "quote"],
        },
    }
    summary = "\n".join(f"- {k}: {v['value']}" for k, v in core_facts.items()
                        if v["value"] is not None)
    guide = ("Categories: billing (a bill the client received), appeal (a denial "
             "the client wants to challenge), eligibility (getting or keeping "
             "coverage). A case can have more than one.\n\n"
             f"Core facts already found:\n{summary}\n\n")
    raw = _call_tool(tool, _prompt(transcript_text, "", guide), settings, client)
    cats = [c for c in raw.get("categories", []) if c in allowed]
    return {"categories": sorted(set(cats)), "rationale": raw.get("rationale", ""),
            "quote": raw.get("quote")}


# Node 5
def extract_category_facts(transcript_text, call_date, categories, schema,
                           settings, client=None):
    if not categories:
        return {}
    fields = {k: v for k, v in merge_fields(schema, categories).items()
              if k not in schema["core"]}
    tool = fact_tool("record_case_facts", fields)
    extra = f"Case categories: {', '.join(categories)}\n\n"
    raw = _call_tool(tool, _prompt(transcript_text, call_date, extra), settings, client)
    return clean_facts(raw, fields)
