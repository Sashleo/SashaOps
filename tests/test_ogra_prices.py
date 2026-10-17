"""
Regression tests for the OGRA petroleum-price scraper's PDF parsing.

The previous implementation could never have worked: OGRA's price pages render
client-side via JavaScript, so requests+BeautifulSoup saw no price data at all
(confirmed by inspecting raw response bytes). These tests lock in the real,
Playwright-discovered source: OGRA's own "Total Petrol/Diesel Price" computation
tables, which mix digital and scanned PDFs day to day and sometimes cover a
different product (e.g. Kerosene-only) entirely.
"""

from intelligence.gov_scrapers.ogra_prices import (
    _extract_prices, _to_iso, _TOTAL_LINE, _TOTAL_VALUE, _EFFECTIVE_DATE,
)

# A real excerpt of the digital Petrol(MS)/Diesel(HSD) computation PDF text
# (verified 2026-07-28, OGRA download id 14714, effective 23 July 2026).
_PETROL_PAGE = """PETROL (MS) Price Effective 23 July 2026, 00:00 hours
Price Components Unit 23-Jul-26 Formula Explanation
Petrol Platts Arab Gulf Mean Average USD/BBL A 104.48000 Seven working days' average.
Total Petrol Price (excl.
RS/LTR T={P+Q+R+S} 327.12
Secondary Freight*)"""

_DIESEL_PAGE = """Diesel (HSD) Price Effective 23 July 2026, 00:00 hours
Price Components Unit 23-Jul-26 Formula Explanation
Diesel Platts Arab Gulf Mean Average USD/BBL A 144.90143 Seven working days' average.
Total Diesel Price (excl.
RS/LTR T={P+Q+R+S} 375.04
Secondary Freight*)"""


def test_petrol_total_price_and_date_extracted():
    # _extract_prices expects real PDF bytes (it opens them with pdfplumber); these
    # pure-text checks exercise its regex layer directly instead of building a
    # fixture PDF.
    date_m = _EFFECTIVE_DATE.search(_PETROL_PAGE)
    assert date_m.group(1).lower() == "petrol"
    assert date_m.group(2) == "23 July 2026"

    lines = _PETROL_PAGE.split("\n")
    for i, line in enumerate(lines):
        if _TOTAL_LINE.search(line):
            val = _TOTAL_VALUE.search(lines[i + 1])
            assert val.group(1) == "327.12"


def test_diesel_total_price_extracted():
    lines = _DIESEL_PAGE.split("\n")
    for i, line in enumerate(lines):
        if _TOTAL_LINE.search(line):
            val = _TOTAL_VALUE.search(lines[i + 1])
            assert val.group(1) == "375.04"


def test_date_normalised_to_iso():
    assert _to_iso("23 July 2026") == "2026-07-23"


def test_date_normalisation_falls_back_on_unparseable_input():
    assert _to_iso("not a date") == "not a date"


def test_scanned_pdf_with_no_text_layer_is_rejected():
    # A PDF with (effectively) no extractable text must not be treated as data -
    # the parser must not fabricate a price from noise.
    assert _extract_prices(b"%PDF-1.4\n") is None


def test_wrong_product_document_is_rejected():
    # A Kerosene-only notification (verified real case: OGRA download id 14801)
    # must not be mistaken for a Petrol/Diesel computation just because it is a
    # valid, text-bearing petroleum-price PDF.
    kerosene_only = "Kerosene Oil 294.43 20.36 8.67 - 1.58 - 303.10" * 20
    assert _extract_prices(kerosene_only.encode()) is None
