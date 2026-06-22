import pytest

from freshsky_common.privacy import (
    SensitiveDataError,
    detect_education_pii,
    detect_sensitive_data,
    enforce_deidentified_education_input,
    enforce_deidentified_public_input,
)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Student: Maya Johnson", "labeled_name"),
        ("Contact family@example.org", "email"),
        ("DOB: 04/12/2014", "date_of_birth"),
        ("Student ID: A123456", "student_id"),
        ("Call (202) 555-0142", "phone"),
        ("SSN 123-45-6789", "ssn"),
        ("Lives at 125 Main Street", "street_address"),
    ],
)
def test_detects_likely_student_pii(text, category):
    assert category in detect_education_pii(text)


def test_allows_deidentified_education_context():
    text = (
        "An anonymized seventh-grade student has difficulty with transitions. "
        "Use aggregate scores and no names or account identifiers."
    )
    assert detect_education_pii(text) == []
    enforce_deidentified_education_input(text)


def test_exception_exposes_categories_not_source_text():
    with pytest.raises(SensitiveDataError) as exc:
        enforce_deidentified_education_input("Student: Maya Johnson")
    assert exc.value.categories == ("labeled_name",)
    assert "Maya" not in str(exc.value)
    assert "student identifiers" in str(exc.value)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Name: Maya Johnson", "labeled_name"),
        ("Account number: AB1234567", "account_number"),
        ("Card 4242 4242 4242 4242", "payment_card"),
    ],
)
def test_public_profile_detects_general_identifiers(text, category):
    assert category in detect_sensitive_data(text)


def test_public_profile_uses_general_error_without_source_text():
    with pytest.raises(SensitiveDataError) as exc:
        enforce_deidentified_public_input("Name: Maya Johnson")
    assert exc.value.categories == ("labeled_name",)
    assert "personal identifiers" in str(exc.value)
    assert "Maya" not in str(exc.value)
