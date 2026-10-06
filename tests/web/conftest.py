"""Web-app tests never touch the real app data folder (server state,
autosaves, templates): every test gets its own empty SCS_DATA_DIR."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SCS_DATA_DIR", str(tmp_path / "data"))
