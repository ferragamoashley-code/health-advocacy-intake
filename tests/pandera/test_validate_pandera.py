"""
Black-box unit tests for the Pandera atomic protocol (node 6: validate case
record against case schema).

Node 6 receives a one-row case DataFrame plus the per-category case schema
from config (fixtures/case_schema.yaml) and returns missing fields, invalid
values and urgency flags, or None if there is nothing to flag.

The thin wrapper below (build_schema / validate_case) only translates the
YAML config into a pandera.DataFrameSchema and sorts Pandera's failure table
into three lists. All validation is done by Pandera; the tests check
Pandera's documented behavior for this job:
  - non-nullable columns report blank values (check "not_nullable")
  - required columns report when absent (check "column_in_dataframe")
  - custom and built-in checks catch rule violations
  - lazy validation reports every failure in one pass (SchemaErrors)

No API key, no network, no cost. Runs in seconds.

Pandera 0.35.0, pandas 3.1.1
"""

import copy
import json
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest
import yaml

F = Path(__file__).parent / "fixtures"
SCHEMA_PATH = F / "case_schema.yaml"


# ---------------------------------------------------------------------------
# Wrapper (node 6)
# ---------------------------------------------------------------------------

def load_config(path=SCHEMA_PATH):
    with open(path) as fh:
        return yaml.safe_load(fh)


def load_record(name):
    with open(F / name) as fh:
        return json.load(fh)


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


def as_df(record):
    return pd.DataFrame([record])


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture
def billing():
    return load_record("billing_complete.json")


@pytest.fixture
def appeal():
    return load_record("appeal_complete.json")


@pytest.fixture
def eligibility():
    return load_record("eligibility_complete.json")


# ---------------------------------------------------------------------------
# Typical
# ---------------------------------------------------------------------------

# Row 1-3
@pytest.mark.parametrize("record_name", ["billing_complete.json",
                                         "appeal_complete.json",
                                         "eligibility_complete.json"])
def test_complete_record_passes(config, record_name):
    assert validate_case(as_df(load_record(record_name)), config) is None


# Row 4
def test_blank_required_core_field_reported(config, billing):
    billing["zip_code"] = None
    result = validate_case(as_df(billing), config)
    assert result["missing"] == ["zip_code"]
    assert result["invalid"] == [] and result["urgent"] == []


# Row 5
def test_blank_optional_fields_pass(config, billing):
    billing["email"] = None
    billing["third_party_admin"] = None
    billing["narrative"] = None
    assert validate_case(as_df(billing), config) is None


# Row 6
def test_care_gap_yes_flags_urgent(config, eligibility):
    eligibility["care_gap"] = "yes"
    result = validate_case(as_df(eligibility), config)
    assert result["urgent"] == ["care_gap"]
    assert result["missing"] == [] and result["invalid"] == []


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------

# Row 7
def test_lazy_validation_reports_all_failures_together(config, appeal):
    appeal["zip_code"] = None                 # missing
    appeal["appeal_level"] = "fair hearing"   # invalid (not in list)
    appeal["care_gap"] = "yes"                # urgent
    result = validate_case(as_df(appeal), config)
    assert result["missing"] == ["zip_code"]
    assert result["invalid"] == ["appeal_level"]
    assert result["urgent"] == ["care_gap"]


# Row 8
@pytest.mark.parametrize("due_date,urgent", [
    ("2026-11-16", True),    # exactly 14 days after 2026-11-02
    ("2026-11-17", False),   # 15 days
])
def test_appeal_deadline_boundary(config, appeal, due_date, urgent):
    appeal["appeal_due_date"] = due_date
    result = validate_case(as_df(appeal), config)
    if urgent:
        assert result["urgent"] == ["appeal_deadline_within_14_days"]
        assert result["missing"] == [] and result["invalid"] == []
    else:
        assert result is None


# Row 9
def test_two_categories_use_combined_required_fields(config, billing, appeal):
    combined = {**billing, **appeal}
    combined["case_categories"] = "appeal;billing"
    combined["date_of_service"] = "2026-09-14"
    combined["bill_amount"] = None   # optional for appeal, required for billing
    result = validate_case(as_df(combined), config)
    assert result["missing"] == ["bill_amount"]
    assert result["invalid"] == [] and result["urgent"] == []


def test_same_bill_blank_passes_for_appeal_only(config, appeal):
    # Control for row 9: appeal alone does not require bill_amount.
    appeal["bill_amount"] = None
    assert validate_case(as_df(appeal), config) is None


# Row 10
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_null_and_empty_string_both_reported_missing(config, billing, blank):
    billing["provider_name"] = blank
    result = validate_case(as_df(billing), config)
    assert result["missing"] == ["provider_name"]


# Row 11
def test_unknown_and_declined_values_pass(config, eligibility, appeal):
    eligibility["race"] = "Declined"
    eligibility["ethnicity"] = "Declined"
    eligibility["immigration_status"] = "Declined"
    eligibility["care_gap"] = "unknown"
    assert validate_case(as_df(eligibility), config) is None

    appeal["network_status"] = "unknown"
    appeal["provider_appealed"] = "unknown"
    appeal["funding_type"] = "unknown"
    assert validate_case(as_df(appeal), config) is None


# Row 12
def test_absent_column_vs_blank_value(config, billing):
    absent = {k: v for k, v in billing.items() if k != "plan_name"}
    r_absent = validate_case(as_df(absent), config)
    assert r_absent["missing"] == ["plan_name"]
    assert "column_in_dataframe" in set(r_absent["failure_cases"]["check"])

    billing["plan_name"] = None
    r_blank = validate_case(as_df(billing), config)
    assert r_blank["missing"] == ["plan_name"]
    assert "not_nullable" in set(r_blank["failure_cases"]["check"])


# Row 13
def test_pre_service_appeal_without_dos_passes(config, appeal):
    assert appeal["service_timing"] == "pre-service"
    assert appeal["date_of_service"] is None
    assert validate_case(as_df(appeal), config) is None


# ---------------------------------------------------------------------------
# Invalid
# ---------------------------------------------------------------------------

# Row 14
def test_text_in_date_field_rejected(config, billing):
    billing["call_date"] = "last Tuesday"
    result = validate_case(as_df(billing), config)
    assert result["invalid"] == ["call_date"]


# Row 15
def test_value_not_in_allowed_list_rejected(config, billing):
    billing["plan_type"] = "HMO"
    result = validate_case(as_df(billing), config)
    assert result["invalid"] == ["plan_type"]


# Row 16
@pytest.mark.parametrize("field,value", [
    ("bill_amount", -50.0),
    ("household_size", 0),
])
def test_out_of_range_numbers_rejected(config, billing, field, value):
    billing[field] = value
    result = validate_case(as_df(billing), config)
    assert result["invalid"] == [field]


# Row 17
@pytest.mark.parametrize("phone", ["555-555-0142", "(555) 555-0142", "555555014"])
def test_badly_formatted_phone_rejected(config, billing, phone):
    billing["contact_number"] = phone
    result = validate_case(as_df(billing), config)
    assert result["invalid"] == ["contact_number"]


# Row 18
def test_unknown_category_rejected(config, billing):
    billing["case_categories"] = "housing"
    result = validate_case(as_df(billing), config)
    assert "case_categories" in result["invalid"]


# Row 19
def test_empty_dataframe_pandera_behavior(config, billing):
    """Finding: with zero rows there are no values to fail, so Pandera passes
    an empty DataFrame (columns present, same types) without error. The
    wrapper therefore guards against it itself.

    Every column needs a typed value before slicing: a column that held only
    None has no text type, and on zero rows Pandera then reports
    WRONG_DATATYPE for it (it does not on a one-row record)."""
    billing["third_party_admin"] = "Example TPA"
    empty = as_df(billing).iloc[0:0]
    schema = build_schema(config, ["billing"])
    validated = schema.validate(empty, lazy=True)
    assert len(validated) == 0
    with pytest.raises(ValueError, match="exactly one case row"):
        validate_case(empty, config)
