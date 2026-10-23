"""
Report reliability tests — superseded by the V2 pipeline plan.

_report_is_reliable, the OCR reconciliation gate, and the wholesale/partial-merge
branches of fetch_psx_doc_financials were all part of the old PDF-enrichment step.
That step was deleted under the V2 pipeline plan (M1 cleanup, committed alongside
the pipeline/contract.py + store.py scaffolding).

Until M7 publishes verified figures via the Gold table, psx_financials must carry
only PSX summary data. The static guard in test_psx_financials_basis.py enforces
this rule.  No tests are needed here until M7 re-introduces a merge step with
full pipeline provenance.
"""
