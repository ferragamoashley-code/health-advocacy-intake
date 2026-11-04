"""Glue (5 -> 6): build the one-row case DataFrame Pandera validates."""

import pandas as pd

from .validate import merge_fields


def build_record(call_date, categories, facts, schema):
    """facts: {field: {"value": ...}} for core and category fields combined."""
    fields = merge_fields(schema, categories)
    row = {"call_date": call_date,
           schema["categories_field"]["name"]: ";".join(categories) or None}
    for field, spec in fields.items():
        if field == "call_date":
            continue
        value = facts.get(field, {}).get("value")
        if isinstance(value, str) and spec["type"] in ("number", "integer"):
            try:
                value = float(value.replace("$", "").replace(",", ""))
            except ValueError:
                pass   # left as text; Pandera reports it as invalid
        if spec["type"] == "integer" and isinstance(value, float) and value.is_integer():
            value = int(value)
        row[field] = value
    return pd.DataFrame([row])
