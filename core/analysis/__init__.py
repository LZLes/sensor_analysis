"""Pure computation shared by both UIs and any future add-on: fits, parsers'
downstream maths, sample data, and Matplotlib export builders.

Nothing in this package may import Streamlit (tests/web/test_modularity.py
checks). The Streamlit modes in modes/*.py re-export these names, so
`from modes.amperometry import piecewise_fit` keeps working.
"""
