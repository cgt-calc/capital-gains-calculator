"""End-to-end tests of the web application with a stand-in for the tool."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING
import zipfile

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.testclient import TestClient

    from cgt_calc.web.config import Settings

CSV = ("raw.csv", b"a,b\n1,2\n", "text/csv")


def _start_run(client: TestClient, data: dict[str, str] | None = None) -> str:
    response = client.post(
        "/runs",
        data=data or {"year": "2024"},
        files={"raw_file": CSV},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/runs/")
    return location.removeprefix("/runs/")


def _wait(client: TestClient, run_id: str) -> str:
    """Return the event stream of a run, which ends when the run does."""
    return client.get(f"/runs/{run_id}/events").text


def test_pages_need_the_token(settings: Settings) -> None:
    """Without the cookie nothing is served, not even the form."""
    from starlette.testclient import TestClient  # noqa: PLC0415

    from cgt_calc.web.app import create_app  # noqa: PLC0415

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as anon:
        assert anon.get("/").status_code == 401
        assert anon.get("/static/app.css").status_code == 401


def test_form_lists_the_options(client: TestClient) -> None:
    """The form is generated from the tool's own options."""
    page = client.get("/").text
    assert 'name="trading212_dir"' in page
    assert 'name="raw_file"' in page
    assert 'name="balance_check"' in page
    assert "--no-balance-check" in page
    assert 'action="/runs"' in page
    # Deprecated spellings and managed options are not offered.
    assert "--schwab<" not in page
    assert 'name="output"' not in page


def test_static_files_are_served(client: TestClient) -> None:
    """The stylesheet and script load for the signed-in browser."""
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/run.js").status_code == 200


def test_run_without_files_is_refused(client: TestClient) -> None:
    """The form is shown again with the reason and the typed values."""
    response = client.post("/runs", data={"year": "2023"})
    assert response.status_code == 422
    assert "Choose at least one input file" in response.text
    assert 'value="2023"' in response.text


def test_full_run(client: TestClient, settings: Settings) -> None:
    """Upload, run, read the result and download it."""
    run_id = _start_run(client, {"year": "2024", "verbose": "on"})
    events = _wait(client, run_id)
    assert "event: done" in events
    assert 'data: "succeeded"' in events

    page = client.get(f"/runs/{run_id}").text
    assert "cgt-calc --year 2024 --raw-file raw.csv --verbose" in page
    assert "succeeded" in page
    assert "Portfolio at the end of the tax year" in page
    assert "WARNING: a warning" in page

    pdf = client.get(f"/runs/{run_id}/files/out/calculations.pdf")
    assert pdf.content == b"%PDF-1.4 fake"
    assert pdf.headers["content-disposition"].startswith("attachment")
    command = client.get(f"/runs/{run_id}/files/command.txt")
    assert command.text == "cgt-calc --year 2024 --raw-file raw.csv --verbose\n"

    archive = zipfile.ZipFile(
        io.BytesIO(client.get(f"/runs/{run_id}/bundle.zip").content)
    )
    assert set(archive.namelist()) == {
        "command.txt",
        "log.txt",
        "report.txt",
        "out/calculations.pdf",
    }
    # The person's own uploads are not part of the download.
    assert not any("raw.csv" in name for name in archive.namelist())

    assert (
        client.post(f"/runs/{run_id}/delete", follow_redirects=False).status_code == 303
    )
    assert client.get(f"/runs/{run_id}").status_code == 404
    assert not list((settings.work_root).iterdir())


def test_uploads_are_saved_where_the_command_says(client: TestClient) -> None:
    """A directory option gets a folder, named as in the shown command."""
    response = client.post(
        "/runs",
        data={},
        files=[
            ("trading212_dir", ("a.csv", b"1", "text/csv")),
            ("trading212_dir", ("b.csv", b"2", "text/csv")),
        ],
        follow_redirects=False,
    )
    run_id = response.headers["location"].removeprefix("/runs/")
    _wait(client, run_id)
    page = client.get(f"/runs/{run_id}").text
    assert "cgt-calc --trading212-dir trading212" in page


def test_event_stream_can_resume(client: TestClient) -> None:
    """A page rendered with some log lines does not receive them again."""
    run_id = _start_run(client)
    _wait(client, run_id)
    full = [
        json.loads(line.removeprefix("data: "))
        for line in client.get(f"/runs/{run_id}/events").text.splitlines()
        if line.startswith("data: ") and not line.startswith('data: "s')
    ]
    resumed = client.get(f"/runs/{run_id}/events", params={"start": "1"}).text
    # Events carry the JSON encoding of each line.
    assert json.dumps(full[0]) not in resumed
    assert json.dumps(full[1]) in resumed


def test_unknown_run_and_paths(client: TestClient) -> None:
    """Made-up ids and paths outside the listing find nothing."""
    assert client.get("/runs/nope").status_code == 404
    run_id = _start_run(client)
    _wait(client, run_id)
    for name in ("../../workspace/raw.csv", "workspace/raw.csv", "/etc/passwd"):
        assert client.get(f"/runs/{run_id}/files/{name}").status_code == 404


def test_run_page_escapes_html(client: TestClient) -> None:
    """Text from the person's files or options is never treated as markup."""
    run_id = _start_run(client, {"interest_fund_tickers": "<script>x</script>"})
    _wait(client, run_id)
    page = client.get(f"/runs/{run_id}").text
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;x&lt;/script&gt;" in page


def test_cross_site_post_is_refused(client: TestClient) -> None:
    """Even with the cookie, a form posted from another site is refused."""
    response = client.post(
        "/runs",
        files={"raw_file": CSV},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_oversized_upload_is_refused(tmp_path: Path) -> None:
    """The size limit applies to the whole request."""
    from starlette.testclient import TestClient  # noqa: PLC0415

    from cgt_calc.web.app import create_app  # noqa: PLC0415
    from cgt_calc.web.config import Settings  # noqa: PLC0415

    settings = Settings(token="t", work_root=tmp_path / "runs", max_upload_bytes=100)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as small:
        small.get("/auth", params={"token": "t"})
        response = small.post("/runs", files={"raw_file": ("big.csv", b"x" * 1000)})
        assert response.status_code == 413


def _log_lines(client: TestClient, run_id: str) -> str:
    """Return the log of a finished run as one text."""
    _wait(client, run_id)
    return client.get(f"/runs/{run_id}/files/log.txt").text


def _first_run_with_inputs(client: TestClient) -> str:
    """Start a run with a single file, a directory of two, and some options."""
    response = client.post(
        "/runs",
        data={"year": "2024", "no_pdflatex": "on"},
        files=[
            ("raw_file", ("raw.csv", b"raw-v1", "text/csv")),
            ("trading212_dir", ("a.csv", b"t1", "text/csv")),
            ("trading212_dir", ("b.csv", b"t2", "text/csv")),
        ],
        follow_redirects=False,
    )
    return response.headers["location"].removeprefix("/runs/")


def test_run_page_offers_run_again(client: TestClient) -> None:
    """Every finished run links to its re-run form."""
    run_id = _first_run_with_inputs(client)
    _wait(client, run_id)
    assert f'href="/runs/{run_id}/rerun"' in client.get(f"/runs/{run_id}").text


def test_rerun_form_is_filled_in(client: TestClient) -> None:
    """The form shows the earlier options and the files that will be reused."""
    run_id = _first_run_with_inputs(client)
    _wait(client, run_id)
    page = client.get(f"/runs/{run_id}/rerun").text
    assert "Run again" in page
    assert f'name="from_run" value="{run_id}"' in page
    assert 'value="2024"' in page
    assert 'name="no_pdflatex" checked' in page
    assert "<strong>raw.csv</strong>" in page
    assert "<strong>a.csv, b.csv</strong>" in page
    assert 'name="drop.raw_file"' in page


def test_run_again_reuses_inputs_with_other_options(
    client: TestClient, settings: Settings
) -> None:
    """A second run gets the same files without uploading them again."""
    first = _first_run_with_inputs(client)
    _wait(client, first)
    response = client.post(
        "/runs",
        data={"from_run": first, "year": "2024", "verbose": "on"},
        follow_redirects=False,
    )
    second = response.headers["location"].removeprefix("/runs/")
    log = _log_lines(client, second)
    assert "raw-v1" in log
    assert "trading212/a.csv:t1" in log
    assert "trading212/b.csv:t2" in log
    page = client.get(f"/runs/{second}").text
    assert (
        "cgt-calc --year 2024 --trading212-dir trading212 --raw-file raw.csv --verbose"
        in page
    )

    # The two runs are independent: deleting the first keeps the second's files.
    client.post(f"/runs/{first}/delete")
    assert (
        settings.work_root / second / "workspace" / "raw.csv"
    ).read_bytes() == b"raw-v1"


def test_run_again_can_replace_or_drop_an_input(client: TestClient) -> None:
    """A newly chosen file wins, and a dropped input is left out."""
    first = _first_run_with_inputs(client)
    _wait(client, first)
    response = client.post(
        "/runs",
        data={"from_run": first, "drop.trading212_dir": "on"},
        files={"raw_file": ("raw.csv", b"raw-v2", "text/csv")},
        follow_redirects=False,
    )
    second = response.headers["location"].removeprefix("/runs/")
    log = _log_lines(client, second)
    assert "raw-v2" in log
    assert "raw-v1" not in log
    assert "trading212" not in log
    assert "cgt-calc --raw-file raw.csv" in client.get(f"/runs/{second}").text


def test_run_again_with_everything_dropped_is_refused(client: TestClient) -> None:
    """Dropping every input leaves nothing to calculate."""
    first = _first_run_with_inputs(client)
    _wait(client, first)
    response = client.post(
        "/runs",
        data={
            "from_run": first,
            "drop.raw_file": "on",
            "drop.trading212_dir": "on",
        },
    )
    assert response.status_code == 422
    assert "Choose at least one input file" in response.text
    # The form still offers the earlier files, with the drop boxes ticked.
    assert 'name="drop.raw_file" checked' in response.text


def test_run_again_from_an_expired_run(client: TestClient) -> None:
    """A run that was deleted cannot be reused; the person is told to upload."""
    first = _first_run_with_inputs(client)
    _wait(client, first)
    client.post(f"/runs/{first}/delete")
    assert client.get(f"/runs/{first}/rerun").status_code == 404
    response = client.post("/runs", data={"from_run": first})
    assert response.status_code == 422
    assert "expired" in response.text


def test_earlier_runs_list_links_to_run_again(client: TestClient) -> None:
    """The history on the front page has the same shortcut."""
    run_id = _first_run_with_inputs(client)
    _wait(client, run_id)
    assert f'href="/runs/{run_id}/rerun"' in client.get("/").text
