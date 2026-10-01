"""Tests for running the tool and keeping its results."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from cgt_calc.web.command import Command
from cgt_calc.web.runs import RunManager, RunStatus

from .conftest import FAKE_CLI

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.web.runs import Run


def _manager(root: Path, *, timeout: float = 30) -> RunManager:
    return RunManager(root, ttl_seconds=60, timeout_seconds=timeout, program=FAKE_CLI)


async def _finish(manager: RunManager, argv: tuple[str, ...]) -> Run:
    run_id, directory = manager.allocate()
    run = manager.submit(run_id, directory, Command(argv))
    await run.wait_for_lines(0, 0)
    while not run.finished:
        await run.wait_for_lines(len(run.log), 5)
    return run


def test_successful_run(tmp_path: Path) -> None:
    """Output, log and report are collected and saved for download."""

    async def scenario() -> None:
        manager = _manager(tmp_path)
        run = await _finish(manager, ("--year", "2024"))
        assert run.status is RunStatus.SUCCEEDED
        assert run.returncode == 0
        assert run.log[0] == "Parsing --year 2024"
        assert "WARNING: a warning" in run.log
        assert run.report.startswith("Portfolio at the end")
        assert [a.name for a in run.artifacts()] == [
            "command.txt",
            "log.txt",
            "report.txt",
            "out/calculations.pdf",
        ]
        command = (run.directory / "results" / "command.txt").read_text("utf-8")
        assert command == "cgt-calc --year 2024\n"
        await manager.shutdown()

    asyncio.run(scenario())


def test_failed_run(tmp_path: Path) -> None:
    """A non-zero exit is a failed run whose log is still kept."""

    async def scenario() -> None:
        manager = _manager(tmp_path)
        run = await _finish(manager, ("--fail",))
        assert run.status is RunStatus.FAILED
        assert run.returncode == 1
        assert run.log[-1] == "ERROR: something went wrong"
        await manager.shutdown()

    asyncio.run(scenario())


def test_timeout_stops_the_process(tmp_path: Path) -> None:
    """A run that takes too long is killed and reported as failed."""

    async def scenario() -> None:
        manager = _manager(tmp_path, timeout=0.5)
        started = time.monotonic()
        run = await _finish(manager, ("--slow",))
        assert time.monotonic() - started < 10
        assert run.status is RunStatus.FAILED
        assert run.log[-1].startswith("ERROR: stopped after")
        await manager.shutdown()

    asyncio.run(scenario())


def test_missing_program_is_reported(tmp_path: Path) -> None:
    """If the tool cannot start, the person sees why instead of a hang."""

    async def scenario() -> None:
        manager = RunManager(
            tmp_path,
            ttl_seconds=60,
            timeout_seconds=5,
            program=(str(tmp_path / "does-not-exist"),),
        )
        run = await _finish(manager, ())
        assert run.status is RunStatus.FAILED
        assert "could not start" in run.log[-1]
        await manager.shutdown()

    asyncio.run(scenario())


def test_runs_are_queued_one_at_a_time(tmp_path: Path) -> None:
    """A second run waits for the first instead of running beside it."""

    async def scenario() -> None:
        manager = _manager(tmp_path)
        first_id, first_dir = manager.allocate()
        second_id, second_dir = manager.allocate()
        first = manager.submit(first_id, first_dir, Command(()))
        second = manager.submit(second_id, second_dir, Command(()))
        await asyncio.sleep(0)
        assert second.status is RunStatus.QUEUED
        while not second.finished:
            await second.wait_for_lines(len(second.log), 5)
        assert first.finished
        assert first.finished_at is not None
        assert second.finished_at is not None
        assert first.finished_at <= second.finished_at
        await manager.shutdown()

    asyncio.run(scenario())


def test_delete_removes_files(tmp_path: Path) -> None:
    """Deleting a finished run leaves nothing behind."""

    async def scenario() -> None:
        manager = _manager(tmp_path)
        run = await _finish(manager, ())
        await manager.delete(run.id)
        assert manager.get(run.id) is None
        assert not run.directory.exists()
        await manager.shutdown()

    asyncio.run(scenario())


def test_expired_runs_are_reaped(tmp_path: Path) -> None:
    """Finished runs older than the time to live are deleted."""

    async def scenario() -> None:
        manager = RunManager(
            tmp_path, ttl_seconds=0, timeout_seconds=30, program=FAKE_CLI
        )
        run = await _finish(manager, ())
        await asyncio.sleep(0.01)
        await manager.reap_expired()
        assert manager.get(run.id) is None
        assert not run.directory.exists()
        await manager.shutdown()

    asyncio.run(scenario())


def test_shutdown_stops_running_work_and_deletes_everything(tmp_path: Path) -> None:
    """Stopping the server cancels a run in progress and removes its inputs."""

    async def scenario() -> None:
        root = tmp_path / "runs"
        manager = _manager(root)
        run_id, directory = manager.allocate()
        (directory / "workspace" / "secret.csv").write_text("x", encoding="utf-8")
        run = manager.submit(run_id, directory, Command(("--slow",)))
        while run.status is not RunStatus.RUNNING:
            await run.wait_for_lines(len(run.log), 5)
        await manager.shutdown()
        assert not root.exists()

    asyncio.run(scenario())
