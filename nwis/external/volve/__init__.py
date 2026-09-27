"""Phase 8: Equinor Volve real-data validation (SIH26121, research/07 "Volve DDR
access recipe", IMPLEMENTATION_PLAN.md §6 A12).

Modules:
    download.py      -- fetch the HuggingFace Volve DDR parquet + Sodir FactPages CSVs
    wells.py          -- build Well/SurveyStation objects, load into data/volve.sqlite
    prepare_docs.py   -- DDR parquet rows -> data/external/volve/docs/*.txt (+ documents.json)
    labels.py         -- frozen weak-label regex set -> data/external/volve/weak_truth.json

Everything here writes to data/external/volve/ and data/volve.sqlite -- NEVER to
data/synthetic/ or data/nwis.sqlite. See data/external/volve/README.md for the data
licence once download.py has run.
"""
