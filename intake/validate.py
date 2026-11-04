"""Node 6: validate the case record against the case schema (Pandera).

Same wrapper as tests/pandera/test_validate_pandera.py: translates
config/case_schema.yaml into a pandera.DataFrameSchema and sorts Pandera's
failure table into missing fields, invalid values and urgency flags.
"""

import copy

import pandas as pd
import pandera.pandas as pa


def parse_categories(value, allowed):
    """Split 'appeal;billing' into ['appeal', 'billing'], keeping only allowed ones."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    parts = [p.strip() for p in str(value).split(";") if p.strip()]
    return [p for p in parts if p in allowed]


def merge_fields(config, categories):
    """Core fields plus every assigned category's fields.
    A field is required if ANY assigned category requires it."""
    fields = copy.deepcopy(config["core"])
    for cat in categories:
        for name, spec in config["categories"][cat].items():
            if name in fields:
                fields[name]["required"] = fields[name]["required"] or spec["required"]
            else:
                fields[name] = copy.deepcopy(spec)
    return fields


NOT_EMPTY = pa.Check(lambda s: s.astype(str).str.strip().str.len() > 0, name="not_empty")


def build_column(spec):
    required = spec["required"]
    kind = spec["type"]
    checks = []
    if kind == "text":
        dtype, coerce = str, False
        if required:
            checks.append(NOT_EMPTY)
    elif kind == "choice":
        dtype, coerce = str, False
        checks.append(pa.Check.isin(spec["allowed"]))
    elif kind == "pattern":
        dtype, coerce = str, False
        checks.append(pa.Check.str_matches(spec["regex"]))
    elif kind == "date":
        dtype, coerce = pa.DateTime, True
    elif kind == "number":
        dtype, coerce = float, True
        checks.append(pa.Check.ge(0))
    elif kind == "integer":
        dtype, coerce = "Int64", True
        checks.append(pa.Check.ge(1))
    else:
        raise ValueError(f"Unknown field type in schema config: {kind}")
    return pa.Column(dtype, checks=checks, nullable=not required,
                     required=required, coerce=coerce)


def _dates(df, col):
    return pd.to_datetime(df.get(col, pd.Series(pd.NaT, index=df.index)), errors="coerce")


def appeal_deadline_check():
    # Passes when the deadline is MORE than 14 days after the call.
    # Failure = urgent. Missing or unreadable dates pass here; they are
    # already reported as missing or invalid by the column checks.
    def fn(df):
        days = (_dates(df, "appeal_due_date") - _dates(df, "call_date")).dt.days
        return days.isna() | (days > 14)
    return pa.Check(fn, name="urgent:appeal_deadline_within_14_days")


def care_gap_check():
    # Passes when care_gap is not "yes". Failure = urgent.
    def fn(df):
        return df.get("care_gap", pd.Series(None, index=df.index)) != "yes"
    return pa.Check(fn, name="urgent:care_gap")


URGENCY_RULES = {
    "appeal_deadline_within_14_days": appeal_deadline_check,
    "care_gap": care_gap_check,
}


def build_schema(config, categories):
    fields = merge_fields(config, categories)
    columns = {name: build_column(spec) for name, spec in fields.items()}

    cat_cfg = config["categories_field"]
    allowed = cat_cfg["allowed"]
    columns[cat_cfg["name"]] = pa.Column(
        str, nullable=False,
        checks=pa.Check(
            lambda s: s.apply(
                lambda v: len([p for p in str(v).split(";") if p.strip()]) >= cat_cfg["min_count"]
                and all(p.strip() in allowed for p in str(v).split(";") if p.strip())
            ),
            name="valid_categories",
        ),
    )

    checks = []
    for rule in config["urgency"]:
        if rule["name"] not in URGENCY_RULES:
            raise ValueError(f"Unknown urgency rule in schema config: {rule['name']}")
        if rule["applies_to"] == "all" or rule["applies_to"] in categories:
            checks.append(URGENCY_RULES[rule["name"]]())

    return pa.DataFrameSchema(columns, checks=checks)


MISSING_CHECKS = {"not_nullable", "column_in_dataframe", "not_empty"}


def validate_case(df, config):
    """Return {'missing': [...], 'invalid': [...], 'urgent': [...]} or None."""
    if len(df) != 1:
        raise ValueError(f"Node 6 expects exactly one case row, got {len(df)}")
    cat_name = config["categories_field"]["name"]
    raw = df[cat_name].iloc[0] if cat_name in df.columns else None
    categories = parse_categories(raw, config["categories_field"]["allowed"])
    schema = build_schema(config, categories)
    try:
        schema.validate(df, lazy=True)
        return None
    except pa.errors.SchemaErrors as err:
        fc = err.failure_cases
        missing, invalid, urgent = set(), set(), set()
        for _, row in fc.iterrows():
            check = str(row["check"])
            if check.startswith("urgent:"):
                urgent.add(check.split(":", 1)[1])
            elif check == "column_in_dataframe":
                missing.add(str(row["failure_case"]))
            elif check in MISSING_CHECKS:
                missing.add(str(row["column"]))
            else:
                invalid.add(str(row["column"]))
        return {"missing": sorted(missing), "invalid": sorted(invalid),
                "urgent": sorted(urgent), "failure_cases": fc}
