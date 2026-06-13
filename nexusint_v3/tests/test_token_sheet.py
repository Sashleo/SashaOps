"""Tests for the value tokeniser (core/sanitiser.py TokenSheet)."""

from core.sanitiser import TokenSheet, tokenise_financials


def test_add_returns_consistent_token_for_same_value():
    ts = TokenSheet()
    t1 = ts.add(1.42, "CR")
    t2 = ts.add(1.42, "CR")
    assert t1 == t2


def test_restore_replaces_all_tokens():
    ts = TokenSheet()
    cr = ts.add(1.42, "CR")
    roe = ts.add(0.18, "ROE")
    text = f"Current ratio {cr} and ROE {roe}."
    restored = ts.restore(text)
    assert "1.42" in restored
    assert "0.18" in restored
    assert cr not in restored
    assert roe not in restored


def test_tokenise_financials_tokens_for_every_non_none_key():
    financials = {"revenue": 1000, "net_income": None, "roe": 0.18, "ebit": 200}
    sheet, tokens = tokenise_financials(financials)
    # Every non-None key gets a token; None-valued keys are skipped
    assert set(tokens.keys()) == {"revenue", "roe", "ebit"}
    assert "net_income" not in tokens
    # The produced tokens round-trip back to the original values
    joined = " ".join(tokens.values())
    restored = sheet.restore(joined)
    assert "1000" in restored
    assert "200" in restored
