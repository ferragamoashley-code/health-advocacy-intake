"""
Black-box unit tests for the Presidio atomic protocol (node 9: de-identify
the review log for the audit/evaluation log).

Input: free text only (the continuous transcript text from AssemblyAI and the
narrative field). Output: the same text with identifiers replaced by type
labels, e.g. "Dana Whitlock called" -> "<PERSON> called".

Configuration decided for this project:
  - spaCy model: en_core_web_lg (Presidio's default; best name detection)
  - Built-in entities ON: PERSON, PHONE_NUMBER, EMAIL_ADDRESS, LOCATION, US_SSN
  - OFF: DATE_TIME (general dates), NRP (nationality/religious/political
    group), and everything else (credit card, bank, IBAN, crypto, IP, URL...)
  - Custom recognizers:
      DATE_OF_BIRTH - a date with a year, counted only when birth context
                      words ("date of birth", "DOB", "born", "birthday")
                      come just before it
      MEDICAID_CIN  - NY Medicaid ID: two letters, five digits, one letter
      ZIP_CODE      - five digits, counted only after "zip" / "zip code" /
                      "postal code"; masked to three digits (11201 -> 112**)
  - Replacement: type labels (Presidio's default "replace" operator), except
    ZIP_CODE, which uses Presidio's "mask" operator.

Context-only recognizers use Presidio's documented context enhancement: the
pattern alone scores below the threshold, and a context word within the
preceding words raises it above.

No API key, no network once the model is installed, no cost.

presidio-analyzer 2.2.366, presidio-anonymizer 2.2.366, spaCy 3.8.18,
en_core_web_lg 3.8.0
"""

import json
import time
from pathlib import Path

import pytest
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

F = Path(__file__).parent / "fixtures"

ENTITIES = ["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION", "US_SSN",
            "DATE_OF_BIRTH", "MEDICAID_CIN", "ZIP_CODE"]

# Context-only patterns score 0.01 on their own; Presidio's context
# enhancement adds 0.35 when a context word precedes them, which clears the
# threshold. Everything else Presidio finds scores well above 0.3.
SCORE_THRESHOLD = 0.3
CONTEXT_ONLY_SCORE = 0.01

# Presidio looks for context words among the 5 words before a match by
# default, but it skips stopwords ("and", "the", "is") when counting, so the
# window can reach back across a clause. With 5, "birth" in "My date of birth
# is March 3, 1961, and the appeal is due November 16, 2026" also counted for
# the appeal deadline. Three content words keeps context local.
CONTEXT_PREFIX_WORDS = 3

MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|"
          r"july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
          r"dec(?:ember)?)")


def dob_recognizer():
    patterns = [
        # March 3rd, 1961 / March 3 1961 / Mar. 3, 1961
        Pattern("dob_month_name",
                rf"\b{MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
                CONTEXT_ONLY_SCORE),
        # 03/03/1961 / 3/3/61 / 3-3-1961
        Pattern("dob_numeric", r"\b\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})\b",
                CONTEXT_ONLY_SCORE),
    ]
    return PatternRecognizer(
        supported_entity="DATE_OF_BIRTH", name="DateOfBirthRecognizer",
        patterns=patterns,
        context=["birth", "dob", "born", "birthday"],
    )


def cin_recognizer():
    return PatternRecognizer(
        supported_entity="MEDICAID_CIN", name="MedicaidCinRecognizer",
        patterns=[Pattern("ny_cin", r"\b[A-Z]{2}\d{5}[A-Z]\b", 0.6)],
    )


def zip_recognizer():
    return PatternRecognizer(
        supported_entity="ZIP_CODE", name="ZipCodeRecognizer",
        patterns=[Pattern("us_zip5", r"(?<![$\d,.])\b\d{5}\b(?![,.]\d)",
                          CONTEXT_ONLY_SCORE)],
        context=["zip", "postal"],
    )


def build_engines():
    nlp_engine = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
    }).create_engine()
    enhancer = LemmaContextAwareEnhancer(context_prefix_count=CONTEXT_PREFIX_WORDS,
                                         context_suffix_count=0)
    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"],
                              default_score_threshold=SCORE_THRESHOLD,
                              context_aware_enhancer=enhancer)
    for rec in (dob_recognizer(), cin_recognizer(), zip_recognizer()):
        analyzer.registry.add_recognizer(rec)
    return analyzer, AnonymizerEngine()


OPERATORS = {
    "ZIP_CODE": OperatorConfig("mask", {"masking_char": "*",
                                        "chars_to_mask": 2,
                                        "from_end": True}),
}


def deidentify(text, analyzer, anonymizer):
    """Node 9 wrapper: free text in, de-identified text out."""
    if not isinstance(text, str):
        raise TypeError(f"Node 9 expects text, got {type(text).__name__}")
    if text == "":
        return ""
    results = analyzer.analyze(text=text, language="en", entities=ENTITIES)
    return anonymizer.anonymize(text=text, analyzer_results=results,
                                operators=OPERATORS).text


@pytest.fixture(scope="module")
def engines():
    return build_engines()


@pytest.fixture(scope="module")
def deid(engines):
    analyzer, anonymizer = engines
    return lambda text: deidentify(text, analyzer, anonymizer)


@pytest.fixture(scope="module")
def transcript():
    with open(F / "intake_en_identifiers_transcript.json") as fh:
        return json.load(fh)["text"]


# ---------------------------------------------------------------------------
# Typical
# ---------------------------------------------------------------------------

# Row 1
def test_full_transcript(deid, transcript):
    out = deid(transcript)
    for value in ["Jordan Pike", "Dana Whitlock", "March 3rd, 1961",
                  "555-555-0142", "11201", "AB12345C"]:
        assert value not in out, f"{value!r} was not removed"
    for label in ["<PERSON>", "<DATE_OF_BIRTH>", "<PHONE_NUMBER>",
                  "<MEDICAID_CIN>"]:
        assert label in out
    assert "112**" in out
    for kept in ["November 16th", "Spanish", "$54,000",
                 "Community Health Advocates", "power wheelchair"]:
        assert kept in out, f"{kept!r} should have been kept"


# Row 2
def test_full_name_replaced(deid):
    assert deid("I spoke with Dana Whitlock yesterday.") == \
        "I spoke with <PERSON> yesterday."


# Row 3
@pytest.mark.parametrize("phone", ["555-555-0142", "(555) 555-0142"])
def test_phone_numbers_replaced(deid, phone):
    out = deid(f"You can reach me at {phone} after five.")
    assert phone not in out and "<PHONE_NUMBER>" in out


# Row 4
def test_email_replaced(deid):
    assert deid("My email is dana.whitlock@example.com.") == \
        "My email is <EMAIL_ADDRESS>."


# Row 5
def test_ssn_replaced(deid):
    # 078-05-1120 is not used: Presidio rejects it as a known public example.
    out = deid("My social security number is 219-09-9999.")
    assert "219-09-9999" not in out and "<US_SSN>" in out


# Row 6
def test_narrative_field_name_and_cin(deid):
    narrative = ("Client Dana Whitlock, Medicaid ID AB12345C, was denied a "
                 "power wheelchair as not medically necessary.")
    out = deid(narrative)
    assert "<PERSON>" in out and "<MEDICAID_CIN>" in out
    assert "Dana Whitlock" not in out and "AB12345C" not in out
    assert "not medically necessary" in out


# Row 2b
def test_two_names_replaced(deid):
    out = deid("Jordan Pike transferred Dana Whitlock to a case handler.")
    assert out.count("<PERSON>") == 3   # wrong on purpose: there are two names


# Row 7
def test_no_identifiers_unchanged(deid):
    text = "The plan denied the request as not a covered benefit."
    assert deid(text) == text


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------

# Row 8
def test_dob_removed_appeal_deadline_kept(deid):
    out = deid("My date of birth is March 3, 1961, and the appeal is due "
               "November 16, 2026.")
    assert "March 3, 1961" not in out and "<DATE_OF_BIRTH>" in out
    assert "November 16, 2026" in out


# Row 9
@pytest.mark.parametrize("dob", ["March 3rd, 1961", "03/03/1961", "3/3/61"])
def test_dob_formats(deid, dob):
    out = deid(f"Date of birth {dob}.")
    assert dob not in out and "<DATE_OF_BIRTH>" in out


# Row 10
def test_date_without_birth_context_kept(deid):
    text = "She was seen on March 3rd, 2026 for a follow-up."
    assert deid(text) == text


# Row 11
@pytest.mark.parametrize("text,expected", [
    ("My zip code is 11201.", "My zip code is 112**."),
    ("We make about 54000 dollars a year.", "We make about 54000 dollars a year."),
    ("About $54,000 a year.", "About $54,000 a year."),
])
def test_zip_vs_amounts(deid, text, expected):
    assert deid(text) == expected


# Row 12 - finding
def test_bare_zip_without_context_kept(deid):
    """Finding: a zip code with no "zip" or "postal" word just before it is
    NOT masked, by design (otherwise five-digit amounts would be masked too).
    In the transcript fixture the zip follows the question "And your zip
    code?", which is close enough."""
    text = "I live at 11201."
    assert deid(text) == text


# Row 13 - finding
@pytest.mark.parametrize("cin,removed", [
    ("ab12345c", True),               # Presidio matches patterns ignoring case
    ("A B 1 2 3 4 5 C", False),       # spelled out with spaces: missed
])
def test_cin_variants(deid, cin, removed):
    out = deid(f"My Medicaid ID is {cin}.")
    assert (cin not in out) is removed


# Row 14 - finding
@pytest.mark.parametrize("text,name", [
    ("Jordan will call you back.", "Jordan"),
    ("Sam is my caregiver.", "Sam"),
])
def test_single_first_names(deid, text, name):
    out = deid(text)
    assert name not in out and "<PERSON>" in out


# Row 15 - finding
def test_provider_name_with_place_kept(deid):
    """Finding: the model labels the whole provider name as an organization,
    which is not a de-identified entity, so the place inside it is kept."""
    text = "The bill came from Brooklyn Anesthesia Associates."
    assert deid(text) == text


# Row 16
@pytest.mark.parametrize("term", ["Haitian", "Spanish", "Hispanic"])
def test_nrp_terms_kept(deid, term):
    text = f"The client is {term} and asked for help with the notice."
    assert term in deid(text)


# Row 17 - finding
def test_spanish_sentence_name(deid):
    out = deid("Hola, me llamo Dana Whitlock y vivo en el Bronx.")
    assert "Dana Whitlock" not in out and "<PERSON>" in out


# ---------------------------------------------------------------------------
# Invalid
# ---------------------------------------------------------------------------

# Row 18
def test_empty_string(deid):
    assert deid("") == ""


# Row 19
@pytest.mark.parametrize("bad", [None, 12345, b"Dana Whitlock"])
def test_non_text_rejected(deid, bad):
    with pytest.raises(TypeError):
        deid(bad)


# Row 20
def test_long_call_within_budget(deid, transcript):
    # About 60 minutes of speech: ~9,000 words at 150 words per minute.
    words = len(transcript.split())
    long_text = " ".join([transcript] * (9000 // words + 1))
    start = time.perf_counter()
    out = deid(long_text)
    elapsed = time.perf_counter() - start
    assert "Dana Whitlock" not in out
    assert elapsed < 120, f"took {elapsed:.1f} s"
