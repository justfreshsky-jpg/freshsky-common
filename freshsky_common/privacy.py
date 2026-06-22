"""Privacy controls for education-facing AI requests."""
from __future__ import annotations

import re
from typing import Iterable


EDUCATION_PRIVACY_PROFILE = "education_deidentified"
US_PUBLIC_PRIVACY_PROFILE = "us_public"


class SensitiveDataError(ValueError):
    """Raised before an AI request when likely personal data is detected."""

    def __init__(self, categories: Iterable[str], *, education: bool = False):
        self.categories = tuple(sorted(set(categories)))
        subject = "student identifiers" if education else "personal identifiers"
        super().__init__(
            f"Remove {subject} before using this AI tool. "
            f"Detected categories: {', '.join(self.categories)}"
        )


_PATTERNS = {
    "email": re.compile(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        re.IGNORECASE,
    ),
    "ssn": re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)"),
    "phone": re.compile(
        r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})"
        r"[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
    ),
    "student_id": re.compile(
        r"\b(?:student|pupil|school)\s*(?:id|number|no\.?)\s*[:#=-]\s*"
        r"[A-Z0-9][A-Z0-9-]{3,}\b",
        re.IGNORECASE,
    ),
    "date_of_birth": re.compile(
        r"\b(?:date\s+of\s+birth|birth\s*date|dob)\s*[:=-]\s*"
        r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
        r"[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\b",
        re.IGNORECASE,
    ),
    "street_address": re.compile(
        r"\b\d{1,6}\s+(?:[A-Z0-9][A-Z0-9.'-]*\s+){0,5}"
        r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|"
        r"court|ct|circle|cir|parkway|pkwy|highway|hwy|way)\b",
        re.IGNORECASE,
    ),
    "labeled_name": re.compile(
        r"\b(?i:student(?:'s)?|child(?:'s)?|pupil(?:'s)?)"
        r"(?:\s+full)?\s+(?i:name)\s*(?i:is|[:=-])\s*"
        r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b|"
        r"\b(?i:student|child|pupil)\s*[:=-]\s*"
        r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b"
    ),
}

_PUBLIC_PATTERNS = {
    **_PATTERNS,
    "account_number": re.compile(
        r"\b(?:account|routing|claim|case)\s*(?:number|no\.?|#)\s*[:#=-]\s*"
        r"[A-Z0-9][A-Z0-9-]{5,}\b",
        re.IGNORECASE,
    ),
    "payment_card": re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    "labeled_name": re.compile(
        rf"(?:{_PATTERNS['labeled_name'].pattern})|"
        r"(?:\b(?:full\s+name|name)\s*(?:is|[:=-])\s*"
        r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b)",
        re.IGNORECASE,
    ),
}


def detect_education_pii(text: str) -> list[str]:
    """Return category names only; never return or log matched content."""
    if not isinstance(text, str) or not text:
        return []
    return sorted(name for name, pattern in _PATTERNS.items() if pattern.search(text))


def detect_sensitive_data(text: str) -> list[str]:
    """Return likely personal-data categories without returning matched text."""
    if not isinstance(text, str) or not text:
        return []
    return sorted(
        name for name, pattern in _PUBLIC_PATTERNS.items() if pattern.search(text)
    )


def enforce_deidentified_education_input(text: str) -> None:
    """Reject likely student PII before an external provider receives it."""
    categories = detect_education_pii(text)
    if categories:
        raise SensitiveDataError(categories, education=True)


def enforce_deidentified_public_input(text: str) -> None:
    """Reject likely personal identifiers before an external provider call."""
    categories = detect_sensitive_data(text)
    if categories:
        raise SensitiveDataError(categories)
