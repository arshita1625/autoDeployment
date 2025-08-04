# ──────────────────────────────────────────────────────────────────────────────
# nlp_extractor.py
#
#  A lightweight three-layer NLP parser for deployment instructions.
#  1.  regex / keyword look-ups          →     micro-seconds
#  2.  spaCy PhraseMatcher (rule-based)  →     sub-millisecond
#  3.  keyword-similarity fallback       →     only if the first two fail
#
#  Tested on Windows 10 / Python 3.11.
# ──────────────────────────────────────────────────────────────────────────────

import os
import re
import warnings
from pathlib import Path
from typing import Dict, List, Optional

# ──────────────────────────────────────────────────
# 0.  House-keeping: keep the console clean
# ──────────────────────────────────────────────────
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"  # silence HF symlink msg
warnings.filterwarnings("ignore", category=UserWarning)  # hide other UserWarnings

# ──────────────────────────────────────────────────
# 1.  Keyword tables (add to them at will)
# ──────────────────────────────────────────────────
CLOUD_WORDS: Dict[str, str] = {
    "aws": "AWS", "amazon web services": "AWS",
    "gcp": "GCP", "google cloud": "GCP",
    "azure": "Azure"
}
FRAMEWORK_WORDS: Dict[str, str] = {
    "flask": "Flask", "django": "Django",
    "fastapi": "FastAPI", "node": "Node.js",
    "express": "Express"
}
DB_WORDS: Dict[str, str] = {
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "mysql": "MySQL", "mongodb": "MongoDB",
    "dynamo": "DynamoDB", "dynamodb": "DynamoDB"
}


# ──────────────────────────────────────────────────
# 2.  Super-cheap regex look-up
# ──────────────────────────────────────────────────
def simple_lookup(txt: str, table: Dict[str, str]) -> Optional[str]:
    """Return the first matching normalized value, else None."""
    lower = txt.lower()
    for key, norm in table.items():
        if re.search(rf"\b{re.escape(key)}\b", lower):
            return norm
    return None


# ──────────────────────────────────────────────────
# 3.  spaCy PhraseMatcher for plural / hyphen variants
# ──────────────────────────────────────────────────
import spacy
from spacy.matcher import PhraseMatcher


def _check_and_load_spacy_model():
    """Check if spaCy model exists, download if not, then load it."""
    model_name = "en_core_web_sm"

    try:
        # Try to load the model first
        nlp = spacy.load(model_name, disable=["parser", "ner", "tagger"])
        return nlp
    except OSError:
        # Model not found, download it
        print(f"Downloading spaCy model '{model_name}'... (this will happen once)")
        spacy.cli.download(model_name)
        # Now load it
        nlp = spacy.load(model_name, disable=["parser", "ner", "tagger"])
        return nlp


nlp = _check_and_load_spacy_model()
matcher = PhraseMatcher(nlp.vocab, attr="LOWER")


def _add_patterns(table: Dict[str, str]) -> None:
    """Add phrase patterns to the matcher."""
    for key in table:
        matcher.add(key, [nlp.make_doc(key)])


_add_patterns(CLOUD_WORDS)
_add_patterns(FRAMEWORK_WORDS)
_add_patterns(DB_WORDS)


def spacy_match(txt: str, table: Dict[str, str]) -> Optional[str]:
    """Return normalized value using spaCy PhraseMatcher."""
    doc = nlp(txt)
    for match_id, _, _ in matcher(doc):
        raw = nlp.vocab.strings[match_id]
        if raw in table:
            return table[raw]
    return None


# ──────────────────────────────────────────────────
# 4.  Lightweight fallback classifier (no big models)
# ──────────────────────────────────────────────────
def keyword_fallback(txt: str, labels: List[str]) -> Optional[str]:
    """
    Crude similarity: pick the label whose words appear most often
    in the lower-cased sentence.  Good enough for rare edge cases
    where the first two layers fail.
    """
    txt_lower = txt.lower()
    best_label, best_score = None, 0
    for label in labels:
        words = label.lower().split()
        if not words:  # Handle empty label
            continue
        score = sum(w in txt_lower for w in words) / len(words)
        if score > best_score:
            best_label, best_score = label, score
    return best_label if best_score > 0 else None


# ──────────────────────────────────────────────────
# 5.  Master extractor
# ──────────────────────────────────────────────────
def extract(context: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns   (cloud_provider, framework, database)
    Each element is either a normalized string or None.
    """
    provider = (simple_lookup(context, CLOUD_WORDS)
                or spacy_match(context, CLOUD_WORDS)
                or keyword_fallback(context, list(set(CLOUD_WORDS.values()))))

    framework = (simple_lookup(context, FRAMEWORK_WORDS)
                 or spacy_match(context, FRAMEWORK_WORDS)
                 or keyword_fallback(context, list(set(FRAMEWORK_WORDS.values()))))

    database = (simple_lookup(context, DB_WORDS)
                or spacy_match(context, DB_WORDS)
                or keyword_fallback(context, list(set(DB_WORDS.values()))))

    return provider, framework, database


# ──────────────────────────────────────────────────
# 6.  Quick CLI test
# ──────────────────────────────────────────────────
# if __name__ == "__main__":
#     samples = [
#         "Deploy this Flask application on AWS with a Postgres database.",
#         "Spin up my Django project on Azure and use MySQL.",
#         "Host this Node.js API on Google Cloud; MongoDB backend please.",
#         "Please get this FastAPI demo running in Amazon Web Services with Postgre-SQL.",
#         "Run the Express app on G-Cloud and wire it to Dynamo.",
#         "Launch this static site on AWS CloudFront."  # no DB or framework
#     ]
#
#     bar = "─" * 80
#     for s in samples:
#         prov, fw, db = extract(s)
#         print(bar)
#         print(f"Sentence : {s}")
#         print(f"Provider : {prov}")
#         print(f"Framework: {fw}")
#         print(f"Database : {db}")
#     print(bar)
