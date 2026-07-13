"""
ml/ — Local ML layer (CPU-only).

All modules lazy-load their models. If a model is absent or LOCAL_ML=False,
the function returns None gracefully — the pipeline continues without ML enrichment.

Enable via config/settings.py: LOCAL_ML = True (or env var LOCAL_ML=true).
"""
