"""Tests for the entity/PII sanitiser."""
from security.sanitiser import build_sanitiser_for_client


def test_entity_names_are_stripped():
    s = build_sanitiser_for_client(
        "Systems Limited", "pakistan", "IT Services"
    )
    out = s.sanitise("Systems Limited is based in Karachi")
    assert "Systems" not in out
    assert "Limited" not in out
    assert "Karachi" not in out


def test_restore_reverses_sanitise():
    s = build_sanitiser_for_client(
        "Systems Limited", "pakistan", "IT Services"
    )
    original = "Systems Limited operates in Karachi"
    sanitised = s.sanitise(original)
    restored = s.restore(sanitised)
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
    assert "35202-1234567-8" not in out