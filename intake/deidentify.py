"""Node 9: de-identify the review log for the audit/evaluation log (Presidio).

Recognizers and settings are the ones tested in
tests/presidio/test_deidentify_presidio.py. Free text (transcript, narrative,
quotes, volunteer notes) goes through Presidio. Structured fields whose
column is known to identify the client are handled directly: dropped, or for
zip code, shortened to three digits.
"""

import copy

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

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


# Structured fields that identify the client by themselves.
DROP_FIELDS = {"client_name", "contact_number", "email"}
_ENGINES = None


def engines():
    global _ENGINES
    if _ENGINES is None:
        _ENGINES = build_engines()
    return _ENGINES


def scrub(text):
    if text is None:
        return None
    analyzer, anonymizer = engines()
    return deidentify(str(text), analyzer, anonymizer)


def deidentify_log(log):
    """Return a de-identified copy of the review log."""
    out = copy.deepcopy(log)
    out["transcript"] = scrub(log.get("transcript", ""))
    out.pop("utterances", None)
    for key in ("narrative_ai", "narrative_final"):
        if key in out:
            out[key] = scrub(out[key])
    for stage in ("facts_ai", "facts_final"):
        for field, fact in (out.get(stage) or {}).items():
            if field in DROP_FIELDS:
                fact["value"] = "<REMOVED>" if fact.get("value") is not None else None
                fact["quote"] = None
            elif field == "zip_code" and fact.get("value"):
                fact["value"] = str(fact["value"])[:3] + "**"
                fact["quote"] = None
            elif fact.get("quote"):
                fact["quote"] = scrub(fact["quote"])
    if "case_note" in out:
        out["case_note"] = scrub(out["case_note"])
    return out
