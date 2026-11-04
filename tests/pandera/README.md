# Pandera — unit tests (node 6: validate case record against case schema)

**Status:** Complete — 26 passed, 0 skipped (Nov 4, 2026)

## Protocol

| | |
|---|---|
| Tool | [Pandera](https://pandera.readthedocs.io/), open-source schema validation for pandas DataFrames |
| Citation | Bantilan, N. (2020). pandera: Statistical Data Validation of Pandas Dataframes. *Proceedings of the 19th Python in Science Conference (SciPy 2020)*, 116–124. |
| Version | `pandera[pandas]==0.35.0` (pandas 3.1.1), `pyyaml==6.0.3` |
| Cost / keys | Free, local, no API key, no network. Suite runs in about 1–2 seconds. |

## What node 6 does

Input: a one-row case DataFrame (assembled from extracted and volunteer-reviewed facts) and the case schema in `fixtures/case_schema.yaml`.
Output: lists of **missing** fields, **invalid** values and **urgency** flags, or `None` if there is nothing to flag. The volunteer sees these at review (node 7); any fact edit re-runs node 6.

The thin wrapper in the test file only translates the YAML config into a `pandera.DataFrameSchema` and sorts Pandera's failure table into those three lists. All validation is done by Pandera.

## Schema (`fixtures/case_schema.yaml`)

- **Core fields (every case):** call date, client name, contact number (10 digits), email (optional), zip code (5 digits), race, ethnicity, language at home, plan name, plan type, care gap, narrative (optional).
- **Case categories:** billing, appeal, eligibility, from CHA's intake checklist. A case can have several (`"appeal;billing"`). Fields are combined; a field is required if **any** assigned category requires it.
- **Allowed values:** fixed lists for plan type, denial type and reason, appeal level (1st, 2nd, external), timing, network status, funding type, MAGI/non-MAGI, immigration status and others. Race and ethnicity follow the 1997 OMB (SPD 15) categories plus "Other" and "Declined".
- **"unknown" and "Declined"** are valid answers, so Pandera only flags fields nobody asked about, not ones the client couldn't or wouldn't answer.
- **Urgency rules** (written so a *failed* check means *urgent*): appeal due date 14 days or fewer after the call; `care_gap = yes`.
- **Scope simplification:** conditionally required fields (e.g. third-party administrator "if applicable", fully vs. self-insured) are optional.

## Fixtures

Synthetic records typed by hand, using the fictional client Dana Whitlock. No real client data.

| File | Contents |
|---|---|
| `case_schema.yaml` | Schema config |
| `billing_complete.json` | Complete billing case (OON anesthesia bill) |
| `appeal_complete.json` | Complete pre-service appeal, deadline 30 days out |
| `eligibility_complete.json` | Complete eligibility case (lost Medicaid at renewal) |

Tests change one or more fields on a copy of these records.

## Test plan

| # | Category | Input | Expected | Result |
|---|---|---|---|---|
| 1–3 | Typical | Complete billing, appeal, eligibility records | Pass | ✅ |
| 4 | Typical | Required core field blank (`zip_code`) | `zip_code` missing | ✅ |
| 5 | Typical | Optional fields blank | Pass | ✅ |
| 6 | Typical | `care_gap = yes` | Urgent | ✅ |
| 7 | Edge | Missing + invalid + urgent in one record | All three reported together (lazy) | ✅ |
| 8 | Edge | Appeal deadline 14 vs. 15 days after call | 14 urgent; 15 not | ✅ |
| 9 | Edge | Appeal + billing, `bill_amount` blank (plus appeal-only control) | Missing for combined case; passes for appeal alone | ✅ |
| 10 | Edge | Required text null, `""`, or spaces | All reported missing | ✅ |
| 11 | Edge | "unknown" and "Declined" values | Pass | ✅ |
| 12 | Edge | Required column absent vs. present but blank | Both missing; different Pandera checks | ✅ |
| 13 | Edge | Pre-service appeal with no date of service | Pass | ✅ |
| 14 | Invalid | `call_date = "last Tuesday"` | Invalid | ✅ |
| 15 | Invalid | `plan_type = "HMO"` | Invalid | ✅ |
| 16 | Invalid | Negative bill; household size 0 | Invalid | ✅ |
| 17 | Invalid | Phone with dashes, parentheses, or 9 digits | Invalid | ✅ |
| 18 | Invalid | `case_categories = "housing"` | Invalid | ✅ |
| 19 | Invalid | Empty DataFrame (no rows) | Unverified before running; see findings | ✅ |

## Findings

- **Deliberate-failure check:** pointing the "missing zip code" test at a complete record made it fail; it passed again once restored.
- **Empty DataFrame (row 19):** Pandera passes a table with zero rows, since there are no values to fail. The wrapper rejects anything other than exactly one row itself.
- **Column types on empty tables:** a column that held only blanks has no text type. On a one-row record Pandera accepts it; on a zero-row table it reports `WRONG_DATATYPE`.
- **Absent vs. blank:** an absent required column is reported by Pandera's `column_in_dataframe` check; a blank value by `not_nullable`. Both become "missing" for the volunteer.
- **Empty strings:** Pandera treats `""` as a value, so required text fields need an added "not empty" check.
- **Table-level checks report every field:** a failed urgency rule lists every cell in the row, so the wrapper reduces it to one flag per rule.
- **YAML gotcha:** unquoted `yes`/`no` in YAML are read as true/false, which would have rejected `care_gap: "yes"`. The values are quoted in the config.

## Running

```
source .venv/bin/activate
python -m pytest tests/pandera -v
```
