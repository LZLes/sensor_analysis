"""Real-browser tests of the local web app (Playwright + Chromium).

Skipped unless Playwright is installed with a Chromium build:
    pip install playwright && python -m playwright install chromium
Each test module starts the app on a free port, with its own data folder,
and every test fails if the page logs a console error or throws.
"""
import os
import socket
import threading
import time
import urllib.request

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    os.environ["SCS_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    import uvicorn

    from web_app.main import app

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{url}/api/app/info", timeout=0.5)
            break
        except OSError:
            time.sleep(0.05)
    yield url
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — browsers not installed
            pytest.skip(f"Chromium not available for Playwright: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    pg.goto(server)
    pg.wait_for_selector(".mode-btn")
    pg.wait_for_function("Object.keys(MODE_HANDLERS).length === Object.keys(MODES).length")
    pg.expected_errors = []   # a test can list substrings of errors it provokes on purpose
    yield pg
    unexpected = [e for e in errors if not any(x in e for x in pg.expected_errors)]
    ctx.close()
    assert not unexpected, "\n".join(unexpected)

