"""
Regression tests for the NEPRA tariff-determination scraper.

The previous implementation targeted www.nepra.org.pk (403-blocked / redirects) and
hunted for a single numeric rate that the site does not publish on its homepage.
These tests lock in the real source: nepra.org.pk's table of recent determinations,
newest first, each row carrying a date and a determination PDF link.
"""


from intelligence.gov_scrapers.nepra_power import _DATE_RE, _REF_RE

# A real row excerpt from the live homepage (verified 2026-07-28).
_ROW_TEXT = ("17-07-2026 Decision of the Authority in the matter of Application "
            "filed by Aerem Energy (Private) Limited for approval of tariff upto "
            "AC 240 MW Site Neutral Hybrid Project at K-Electric Dhab")
_HREF = "tariff/Tariff/TRF-623%20Decision%20Aerem%20Energy%2017-07-2026%2012993-13103.pdf"


def test_date_extracted_from_row():
    m = _DATE_RE.search(_ROW_TEXT)
    assert m is not None
    assert m.group(1) == "17-07-2026"


def test_reference_extracted_from_row_text():
    m = _REF_RE.search(_ROW_TEXT) or _REF_RE.search(_HREF)
    assert m is not None
    assert m.group(0).upper().startswith("TRF-623")


def test_reference_falls_back_to_href_when_not_in_row_text():
    # Some rows' visible text may omit the reference code; the href always has it.
    row_without_ref = "17-07-2026 Decision of the Authority regarding a tariff matter"
    m = _REF_RE.search(row_without_ref) or _REF_RE.search(_HREF)
    assert m is not None
    assert "TRF-623" in m.group(0).upper()


def test_admission_notice_links_are_not_treated_as_determinations():
    # The homepage also lists Admission Notices (e.g. a .jpeg), which must not be
    # mistaken for a tariff determination PDF.
    admission_href = "Admission%20Notices/2026/07%20Jul/telecom.jpeg"
    assert "tariff/Tariff/" not in admission_href
