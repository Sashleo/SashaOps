"""Tests for the entity/PII sanitiser (security/sanitiser.py)."""

from security.sanitiser import build_sanitiser_for_client


def test_entity_names_are_stripped():
    s = build_sanitiser_for_client("Systems Limited", "pakistan", "IT Services")
    out = s.sanitise("Systems Limited is in Karachi")
    # None of the real tokens may survive in the sanitised prompt
    assert "Systems" not in out
    assert "Limited" not in out
    assert "Karachi" not in out


def test_restore_reverses_sanitise():
    s = build_sanitiser_for_client("Systems Limited", "pakistan", "IT Services")
    original = "Systems Limited is in Karachi"
    out = s.sanitise(original)
    restored = s.restore(out)
    assert "Systems Limited" in restored
    assert "Karachi" in restored


def test_pii_email_phone_cnic_stripped():
    s = build_sanitiser_for_client("Acme Co", "pakistan", "Manufacturing")
    text = (
        "Contact ali@acme.com or call +92-300-1234567. "
        "CNIC 35202-1234567-8 on file."
    )
    out = s.sanitise(text)
    assert "ali@acme.com" not in out
    assert "1234567" not in out          # phone digits scrubbed
    assert "35202-1234567-8" not in out  # CNIC scrubbed
