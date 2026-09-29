"""Save uploaded files into a run's folder."""

from __future__ import annotations

import os
import re
import shutil
from typing import TYPE_CHECKING

from .forms import Field, FieldKind

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path
    from typing import BinaryIO

MAX_NAME_LENGTH = 100

# The tool writes its results and caches to this folder next to the inputs.
OUTPUT_FOLDER = "out"


class UploadError(Exception):
    """Raised when the uploaded files cannot be used."""


class Upload:
    """A file received from the browser: its claimed name and its content."""

    def __init__(self, filename: str, content: BinaryIO) -> None:
        """Wrap an uploaded file."""
        self.filename = filename
        self.content = content


def safe_filename(name: str) -> str:
    """Reduce a name sent by the browser to a harmless file name.

    Only the last path component is kept, so a name such as ``../../x`` or a
    full Windows path cannot leave the folder it is saved in.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^\w. -]", "_", base).strip(" .")
    return cleaned[:MAX_NAME_LENGTH] or "upload"


def _free_path(directory: Path, name: str) -> Path:
    """Return a path in ``directory`` that no file or folder uses yet."""
    candidate = directory / name
    stem, suffix = candidate.stem, candidate.suffix
    number = 1
    while candidate.exists():
        number += 1
        name = f"{stem}-{number}{suffix}"
        candidate = directory / name
    return candidate


def _save(upload: Upload, directory: Path) -> Path:
    """Copy one upload into ``directory`` under a safe, unused name."""
    target = _free_path(directory, safe_filename(upload.filename))
    with target.open("wb") as handle:
        shutil.copyfileobj(upload.content, handle)
    return target


def _link_or_copy(source: Path, target: Path) -> None:
    """Give ``target`` the content of ``source`` without duplicating the data.

    A hard link shares the bytes, which matters when runs live in memory. It
    is safe because inputs are only ever read, and deleting either run leaves
    the other intact. Where links are not possible the file is copied.
    """
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def reuse_inputs(
    source_workspace: Path,
    uploaded: Mapping[str, str],
    keep: Collection[str],
    workspace: Path,
) -> dict[str, str]:
    """Bring the inputs of an earlier run into a new run's folder.

    ``keep`` names the options whose inputs are wanted. The result maps each of
    them to the same relative path it had before, so the command reads the same.
    """
    reused: dict[str, str] = {}
    for dest in keep:
        relative = uploaded.get(dest)
        if relative is None:
            continue
        source = source_workspace / relative
        target = workspace / relative
        if source.is_dir():
            target.mkdir(exist_ok=True)
            for path in source.iterdir():
                if path.is_file():
                    _link_or_copy(path, target / path.name)
        elif source.is_file():
            _link_or_copy(source, target)
        else:
            continue
        reused[dest] = relative
    return reused


def save_uploads(
    workspace: Path,
    fields: Mapping[str, Field],
    uploads: Mapping[str, Sequence[Upload]],
) -> dict[str, str]:
    """Save the uploads and return where each option's input now lives.

    The result maps an option name to a path relative to ``workspace``: the
    file for a single-file option, the folder for a directory option.
    """
    # Created first so that no upload can take the name of the output folder
    # or of a directory option's folder.
    (workspace / OUTPUT_FOLDER).mkdir(exist_ok=True)
    chosen = {
        dest: files for dest, files in uploads.items() if dest in fields and files
    }
    for dest in chosen:
        if fields[dest].kind is FieldKind.FILES:
            (workspace / fields[dest].directory_name).mkdir(exist_ok=True)

    saved: dict[str, str] = {}
    for dest, files in chosen.items():
        field = fields[dest]
        if field.kind is FieldKind.FILE:
            if len(files) > 1:
                raise UploadError(f"{field.flag} takes a single file")
            saved[dest] = _save(files[0], workspace).name
        elif field.kind is FieldKind.FILES:
            folder = workspace / field.directory_name
            for upload in files:
                _save(upload, folder)
            saved[dest] = folder.name
    return saved
