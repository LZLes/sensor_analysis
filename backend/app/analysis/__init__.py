"""
Framework-agnostic analysis functions ported from the original Streamlit app
(app.py at the repo root). Nothing in this package imports Streamlit or
touches any web-framework state — every function takes its inputs as
explicit parameters and returns plain data (numpy/pandas/dict/bytes),
so it can be called directly from FastAPI request handlers or unit tests.
"""
