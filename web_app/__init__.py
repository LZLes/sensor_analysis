"""Local web app (FastAPI + plain JS). See web_app/main.py."""

# Headless Matplotlib before anything imports pyplot: exports are drawn in
# the server's worker threads, and the default macOS backend can only draw
# on the main thread. (Streamlit used to set this as a side effect of being
# imported; the web app no longer imports it.)
import matplotlib

matplotlib.use("Agg")
