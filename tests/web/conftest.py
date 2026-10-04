"""Fixtures for the web interface tests."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import TYPE_CHECKING

import pytest
from starlette.testclient import TestClient

from cgt_calc.web.app import create_app
from cgt_calc.web.config import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

TOKEN = "test-token"
BASE_URL = "http://127.0.0.1:8765"
FAKE_CLI = (sys.executable, str(Path(__file__).with_name("fake_cli.py")))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings keeping every run below the test's temporary folder."""
    return Settings(token=TOKEN, work_root=tmp_path / "runs")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Provide a signed-in client for an application running the fake tool."""
    app = create_app(settings, program=FAKE_CLI)
    # Entering the client runs the app's lifespan and keeps one event loop
    # alive, which the background runs need.
    with TestClient(app, base_url=BASE_URL) as test_client:
        response = test_client.get(
            "/auth", params={"token": TOKEN}, follow_redirects=False
        )
        assert response.status_code == 303
        yield test_client
