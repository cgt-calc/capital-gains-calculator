"""Run the command line tool and keep what it produced."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from enum import StrEnum
import logging
import os
import secrets
import shutil
import sys
import time
from typing import TYPE_CHECKING

from starlette.concurrency import run_in_threadpool

from .command import Submission

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .command import Command

LOGGER = logging.getLogger(__name__)

# Same entry point as the ``cgt-calc`` script, so a run behaves like a terminal.
DEFAULT_PROGRAM = (sys.executable, "-m", "cgt_calc.cli")

MAX_LOG_LINES = 20_000
# Longest single log line the reader accepts; tracebacks have long lines.
LOG_LINE_LIMIT = 1 << 20
REAPER_INTERVAL_SECONDS = 30.0

WORKSPACE = "workspace"
RESULTS = "results"


class RunStatus(StrEnum):
    """Where a run is in its life."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Artifact:
    """A file the person can download."""

    name: str
    path: Path
    size: int


class Run:
    """One execution of the tool and everything it wrote."""

    def __init__(
        self,
        run_id: str,
        directory: Path,
        command: Command,
        submission: Submission,
    ) -> None:
        """Create a queued run inside its directory."""
        self.id = run_id
        self.directory = directory
        self.command = command
        self.submission = submission
        self.status = RunStatus.QUEUED
        self.returncode: int | None = None
        self.log: list[str] = []
        self.report = ""
        self.finished_at: float | None = None
        self._changed = asyncio.Condition()

    @property
    def workspace(self) -> Path:
        """Folder the tool runs in; inputs go here and it writes ``out/``."""
        return self.directory / WORKSPACE

    @property
    def finished(self) -> bool:
        """Whether the run has ended, successfully or not."""
        return self.status in {RunStatus.SUCCEEDED, RunStatus.FAILED}

    async def notify(self) -> None:
        """Wake everything waiting for news."""
        async with self._changed:
            self._changed.notify_all()

    async def wait_for_lines(
        self, start: int, max_wait: float
    ) -> tuple[list[str], bool]:
        """Return log lines from ``start`` on, and whether the run has ended.

        Waits up to ``max_wait`` seconds when there is nothing new yet.
        """
        async with self._changed:
            if len(self.log) <= start and not self.finished:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), max_wait)
            return self.log[start:], self.finished

    def input_files(self) -> dict[str, list[str]]:
        """List the uploaded file names of each option that received files."""
        listed: dict[str, list[str]] = {}
        for dest, relative in self.submission.uploaded.items():
            path = self.workspace / relative
            if path.is_dir():
                listed[dest] = sorted(p.name for p in path.iterdir() if p.is_file())
            elif path.is_file():
                listed[dest] = [path.name]
        return listed

    def artifacts(self) -> list[Artifact]:
        """List downloadable files: written results and the tool's ``out/``."""
        found: list[Artifact] = []
        for folder, prefix in (
            (self.directory / RESULTS, ""),
            (self.workspace / "out", "out/"),
        ):
            if not folder.is_dir():
                continue
            found.extend(
                Artifact(f"{prefix}{path.name}", path, path.stat().st_size)
                for path in sorted(folder.iterdir())
                if path.is_file() and not path.is_symlink()
            )
        return found

    def write_results(self) -> None:
        """Save the command, log and text report next to the tool's output."""
        results = self.directory / RESULTS
        results.mkdir(exist_ok=True)
        (results / "command.txt").write_text(
            self.command.display + "\n", encoding="utf-8"
        )
        (results / "log.txt").write_text("\n".join(self.log) + "\n", encoding="utf-8")
        if self.report:
            (results / "report.txt").write_text(self.report, encoding="utf-8")


class RunManager:
    """Start runs one at a time and delete them after a while."""

    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: float,
        timeout_seconds: float,
        program: Sequence[str] = DEFAULT_PROGRAM,
    ) -> None:
        """Store runs below ``root``."""
        self.root = root
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._program = tuple(program)
        self._runs: dict[str, Run] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        # The tool fetches prices and exchange rates from rate-limited services,
        # so runs queue instead of racing each other.
        self._slot = asyncio.Semaphore(1)

    def allocate(self) -> tuple[str, Path]:
        """Create the folder for a new run, private to this user."""
        run_id = secrets.token_urlsafe(12)
        directory = self.root / run_id
        (directory / WORKSPACE).mkdir(parents=True, mode=0o700)
        return run_id, directory

    def discard(self, directory: Path) -> None:
        """Delete a folder that never became a run."""
        shutil.rmtree(directory, ignore_errors=True)

    def submit(
        self,
        run_id: str,
        directory: Path,
        command: Command,
        submission: Submission | None = None,
    ) -> Run:
        """Queue a run whose inputs are already saved."""
        run = Run(run_id, directory, command, submission or Submission())
        self._runs[run_id] = run
        task = asyncio.create_task(self._execute(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def get(self, run_id: str) -> Run | None:
        """Return a run by id."""
        return self._runs.get(run_id)

    def recent(self) -> list[Run]:
        """Return the runs still held, newest first."""
        return list(reversed(self._runs.values()))

    async def delete(self, run_id: str) -> None:
        """Forget a finished run and remove everything it stored."""
        run = self._runs.get(run_id)
        if run is None or not run.finished:
            return
        del self._runs[run_id]
        await run_in_threadpool(shutil.rmtree, run.directory, ignore_errors=True)

    async def reap_expired(self) -> None:
        """Delete finished runs older than the time to live."""
        now = time.monotonic()
        for run in list(self._runs.values()):
            if run.finished_at is not None and now - run.finished_at > self._ttl:
                await self.delete(run.id)

    async def reap_forever(self) -> None:
        """Keep deleting expired runs until cancelled."""
        while True:
            await asyncio.sleep(REAPER_INTERVAL_SECONDS)
            await self.reap_expired()

    async def shutdown(self) -> None:
        """Stop running work and delete every run."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await run_in_threadpool(shutil.rmtree, self.root, ignore_errors=True)
        self._runs.clear()

    async def _execute(self, run: Run) -> None:
        """Run the tool and record its output; never raises."""
        async with self._slot:
            run.status = RunStatus.RUNNING
            await run.notify()
            try:
                run.returncode = await asyncio.wait_for(self._spawn(run), self._timeout)
            except TimeoutError:
                run.log.append(f"ERROR: stopped after {self._timeout / 60:.0f} minutes")
                run.returncode = 1
            except OSError as err:
                run.log.append(f"ERROR: could not start cgt-calc: {err}")
                run.returncode = 1
            finally:
                run.status = (
                    RunStatus.SUCCEEDED if run.returncode == 0 else RunStatus.FAILED
                )
                await run_in_threadpool(run.write_results)
                run.finished_at = time.monotonic()
                await run.notify()

    async def _spawn(self, run: Run) -> int:
        """Start the process and collect its output."""
        environment = {
            **os.environ,
            # Plain text: colour codes would end up in the page and the log.
            "NO_COLOR": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        environment.pop("FORCE_COLOR", None)
        process = await asyncio.create_subprocess_exec(
            *self._program,
            *run.command.argv,
            cwd=run.workspace,
            env=environment,
            # A question on stdin would wait forever; the tool then reports
            # that it needs an answer and stops.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LOG_LINE_LIMIT,
        )
        try:
            _, report, _ = await asyncio.gather(
                self._read_log(run, process),
                self._read_report(process),
                process.wait(),
            )
            run.report = report
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode is not None
        return process.returncode

    async def _read_log(self, run: Run, process: asyncio.subprocess.Process) -> None:
        """Append each line the tool logs, waking listeners as they arrive."""
        assert process.stderr is not None
        async for raw in process.stderr:
            if len(run.log) < MAX_LOG_LINES:
                run.log.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                await run.notify()

    async def _read_report(self, process: asyncio.subprocess.Process) -> str:
        """Return the text report the tool prints."""
        assert process.stdout is not None
        return (await process.stdout.read()).decode("utf-8", errors="replace")
