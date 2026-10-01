"""Tests for saving uploaded files."""

from __future__ import annotations

import io
import shutil
from typing import TYPE_CHECKING

import pytest

from cgt_calc.web.forms import build_sections, flatten
from cgt_calc.web.uploads import (
    Upload,
    UploadError,
    reuse_inputs,
    safe_filename,
    save_uploads,
)

if TYPE_CHECKING:
    from pathlib import Path

FIELDS = {field.dest: field for field in flatten(build_sections(strict=True))}


def _upload(name: str, content: bytes = b"data") -> Upload:
    return Upload(name, io.BytesIO(content))


@pytest.mark.parametrize(
    ("claimed", "saved"),
    [
        ("report.csv", "report.csv"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\Documents\\trades.csv", "trades.csv"),
        (".bashrc", "bashrc"),
        ("a/b/../c.csv", "c.csv"),
        ("weird<>|name?.csv", "weird___name_.csv"),
        ("", "upload"),
        ("..", "upload"),
        ("x" * 300 + ".csv", "x" * 100),
    ],
)
def test_safe_filename(claimed: str, saved: str) -> None:
    """Only a plain file name survives."""
    assert safe_filename(claimed) == saved


def test_single_file_option(tmp_path: Path) -> None:
    """The saved path is relative to the folder the tool runs in."""
    saved = save_uploads(tmp_path, FIELDS, {"raw_file": [_upload("raw.csv", b"x")]})
    assert saved == {"raw_file": "raw.csv"}
    assert (tmp_path / "raw.csv").read_bytes() == b"x"


def test_directory_option(tmp_path: Path) -> None:
    """A directory option collects all its files in one folder."""
    saved = save_uploads(
        tmp_path,
        FIELDS,
        {"trading212_dir": [_upload("a.csv"), _upload("b.csv")]},
    )
    assert saved == {"trading212_dir": "trading212"}
    assert sorted(p.name for p in (tmp_path / "trading212").iterdir()) == [
        "a.csv",
        "b.csv",
    ]


def test_duplicate_names_are_kept_apart(tmp_path: Path) -> None:
    """Two files with one name are both stored."""
    save_uploads(
        tmp_path,
        FIELDS,
        {"trading212_dir": [_upload("a.csv", b"1"), _upload("a.csv", b"2")]},
    )
    contents = {p.name: p.read_bytes() for p in (tmp_path / "trading212").iterdir()}
    assert contents == {"a.csv": b"1", "a-2.csv": b"2"}


@pytest.mark.parametrize("name", ["out", "trading212"])
def test_uploads_cannot_take_a_folder_name(tmp_path: Path, name: str) -> None:
    """Folders the tool or the form needs are never replaced by a file."""
    saved = save_uploads(
        tmp_path,
        FIELDS,
        {"raw_file": [_upload(name)], "trading212_dir": [_upload("a.csv")]},
    )
    assert saved["raw_file"] == f"{name}-2"
    assert (tmp_path / "out").is_dir()
    assert (tmp_path / "trading212" / "a.csv").is_file()


def test_single_file_option_rejects_several_files(tmp_path: Path) -> None:
    """A second file for a one-file option is an error, not a silent drop."""
    with pytest.raises(UploadError, match="--raw-file"):
        save_uploads(
            tmp_path, FIELDS, {"raw_file": [_upload("a.csv"), _upload("b.csv")]}
        )


def test_unknown_options_and_empty_lists_are_ignored(tmp_path: Path) -> None:
    """Only options the form offers can receive files."""
    saved = save_uploads(
        tmp_path, FIELDS, {"nonsense": [_upload("a.csv")], "raw_file": []}
    )
    assert saved == {}
    assert not (tmp_path / "a.csv").exists()


def test_reused_inputs_keep_their_paths_and_survive_the_source(tmp_path: Path) -> None:
    """Inputs of an earlier run are shared by link, under the same names."""
    source = tmp_path / "old"
    target = tmp_path / "new"
    source.mkdir()
    target.mkdir()
    saved = save_uploads(
        source,
        FIELDS,
        {
            "raw_file": [_upload("raw.csv", b"r")],
            "trading212_dir": [_upload("a.csv", b"1"), _upload("b.csv", b"2")],
        },
    )
    reused = reuse_inputs(source, saved, ["raw_file", "trading212_dir"], target)
    assert reused == saved
    assert (target / "trading212" / "b.csv").read_bytes() == b"2"

    # Deleting the earlier run must not touch the new one.
    shutil.rmtree(source)
    assert (target / "raw.csv").read_bytes() == b"r"
    assert (target / "trading212" / "a.csv").read_bytes() == b"1"


def test_only_wanted_inputs_are_reused(tmp_path: Path) -> None:
    """Options left out of ``keep`` bring nothing along."""
    source = tmp_path / "old"
    target = tmp_path / "new"
    source.mkdir()
    target.mkdir()
    saved = save_uploads(source, FIELDS, {"raw_file": [_upload("raw.csv")]})
    assert reuse_inputs(source, saved, [], target) == {}
    assert not (target / "raw.csv").exists()
