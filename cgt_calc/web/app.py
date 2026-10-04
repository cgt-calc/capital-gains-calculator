"""The web application: routes, templates and wiring."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING
import zipfile

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from cgt_calc.version import get_version

from .command import Submission, build_command
from .config import MAX_FORM_FIELDS, MAX_UPLOAD_FILES, Settings
from .forms import FieldKind, build_sections, flatten
from .runs import DEFAULT_PROGRAM, Run, RunManager, RunStatus
from .security import (
    HostCheckMiddleware,
    MaxBodySizeMiddleware,
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
    TokenAuthMiddleware,
)
from .uploads import Upload, UploadError, reuse_inputs, save_uploads

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Mapping, Sequence

    from starlette.datastructures import FormData
    from starlette.requests import Request

    from .forms import Field, Section

PACKAGE_DIR = Path(__file__).parent
# Form fields that are not command line options.
FROM_RUN_FIELD = "from_run"
# A checkbox named drop.<option> stops an earlier run's input being reused.
DROP_PREFIX = "drop."
# Seconds between comments that stop proxies closing an idle event stream.
KEEP_ALIVE_SECONDS = 15.0


def log_level(line: str) -> str:
    """Classify a log line by the prefix the tool gives warnings and errors."""
    if line.startswith(("ERROR", "CRITICAL")):
        return "error"
    if line.startswith("WARNING"):
        return "warning"
    return "info"


def _sse(event: str, data: object) -> str:
    """Format one server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _start_index(request: Request) -> int:
    """Return the log line to resume from, as given in the query string."""
    start = request.query_params.get("start", "")
    return int(start) if start.isdigit() else 0


@dataclass(slots=True)
class FormInput:
    """The parts of a submitted form, sorted by kind."""

    text: dict[str, str] = field(default_factory=dict)
    checked: set[str] = field(default_factory=set)
    uploads: dict[str, list[Upload]] = field(default_factory=dict)
    # Options whose inputs from an earlier run were switched off.
    dropped: set[str] = field(default_factory=set)
    # The earlier run whose inputs are offered again, if any.
    from_run: str = ""


def _split_form(form: FormData, fields: Mapping[str, Field]) -> FormInput:
    """Sort submitted values into typed text, switched-on options and files."""
    result = FormInput()
    for name, value in form.multi_items():
        if name == FROM_RUN_FIELD and isinstance(value, str):
            result.from_run = value
        elif name.startswith(DROP_PREFIX) and name[len(DROP_PREFIX) :] in fields:
            result.dropped.add(name[len(DROP_PREFIX) :])
        elif name in fields:
            field_ = fields[name]
            if isinstance(value, UploadFile):
                # A browser sends an empty file part when nothing was chosen.
                if value.filename:
                    result.uploads.setdefault(name, []).append(
                        Upload(value.filename, value.file)
                    )
            elif field_.kind in {FieldKind.FLAG, FieldKind.OUTPUT_FILE}:
                result.checked.add(name)
            else:
                result.text[name] = value
    return result


class WebApp:
    """Route handlers with the state they share."""

    def __init__(
        self, settings: Settings, program: Sequence[str] = DEFAULT_PROGRAM
    ) -> None:
        """Prepare the form description, templates and run manager."""
        self.settings = settings
        self.sections: list[Section] = build_sections()
        self.fields = {field.dest: field for field in flatten(self.sections)}
        self.manager = RunManager(
            settings.work_root,
            ttl_seconds=settings.run_ttl_seconds,
            timeout_seconds=settings.run_timeout_seconds,
            program=program,
        )
        self.templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
        self.templates.env.filters["log_level"] = log_level
        self.templates.env.globals["version"] = get_version()

    def _run(self, request: Request) -> Run:
        """Return the run named in the URL, or answer 404."""
        run = self.manager.get(request.path_params["run_id"])
        if run is None:
            raise HTTPException(status_code=404, detail="Unknown or expired run")
        return run

    def _form_page(
        self,
        request: Request,
        *,
        error: str | None = None,
        text: Mapping[str, str] | None = None,
        checked: Collection[str] = (),
        source: Run | None = None,
        dropped: Collection[str] = (),
        status_code: int = 200,
    ) -> Response:
        """Render the options form, optionally offering an earlier run's inputs."""
        return self.templates.TemplateResponse(
            request,
            "index.html",
            {
                "sections": self.sections,
                "runs": self.manager.recent(),
                "error": error,
                "text": text or {},
                "checked": set(checked),
                "ttl_minutes": round(self.settings.run_ttl_seconds / 60),
                "kinds": FieldKind,
                "source": source,
                "reuse": source.input_files() if source else {},
                "dropped": set(dropped),
            },
            status_code=status_code,
        )

    async def index(self, request: Request) -> Response:
        """Show the form."""
        return self._form_page(request)

    async def rerun(self, request: Request) -> Response:
        """Show the form filled in from an earlier run, with its inputs offered."""
        run = self._run(request)
        return self._form_page(
            request,
            text=run.submission.text,
            checked=run.submission.checked,
            source=run,
        )

    async def create_run(self, request: Request) -> Response:
        """Save the uploads, start the tool and go to the run's page."""
        async with request.form(
            max_files=MAX_UPLOAD_FILES, max_fields=MAX_FORM_FIELDS
        ) as form:
            given = _split_form(form, self.fields)
            source = self.manager.get(given.from_run) if given.from_run else None
            if given.from_run and source is None:
                return self._form_page(
                    request,
                    error="The earlier run has expired. Upload your files again.",
                    text=given.text,
                    checked=given.checked,
                    status_code=422,
                )
            run_id, directory = self.manager.allocate()
            workspace = directory / "workspace"
            try:
                reused: dict[str, str] = {}
                if source is not None:
                    # A newly chosen file replaces what the earlier run used.
                    keep = [
                        dest
                        for dest in source.submission.uploaded
                        if dest not in given.uploads and dest not in given.dropped
                    ]
                    reused = await run_in_threadpool(
                        reuse_inputs,
                        source.workspace,
                        source.submission.uploaded,
                        keep,
                        workspace,
                    )
                saved = await run_in_threadpool(
                    save_uploads, workspace, self.fields, given.uploads
                )
            except UploadError as err:
                self.manager.discard(directory)
                raise HTTPException(status_code=400, detail=str(err)) from err
        uploaded = {**reused, **saved}
        if not uploaded:
            self.manager.discard(directory)
            return self._form_page(
                request,
                error="Choose at least one input file.",
                text=given.text,
                checked=given.checked,
                source=source,
                dropped=given.dropped,
                status_code=422,
            )
        command = build_command(
            self.sections,
            text=given.text,
            checked=given.checked,
            uploaded=uploaded,
        )
        submission = Submission(given.text, frozenset(given.checked), uploaded)
        run = self.manager.submit(run_id, directory, command, submission)
        return RedirectResponse(f"/runs/{run.id}", status_code=303)

    async def show_run(self, request: Request) -> Response:
        """Show a run's command, log, report and downloads."""
        run = self._run(request)
        return self.templates.TemplateResponse(
            request,
            "run.html",
            {
                "run": run,
                "statuses": RunStatus,
                "artifacts": run.artifacts() if run.finished else [],
            },
        )

    async def events(self, request: Request) -> Response:
        """Stream the log of a run as server-sent events."""
        run = self._run(request)

        async def stream() -> AsyncIterator[str]:
            # The page was rendered with the lines logged so far.
            sent = _start_index(request)
            while True:
                lines, finished = await run.wait_for_lines(sent, KEEP_ALIVE_SECONDS)
                for line in lines:
                    yield _sse("log", line)
                sent += len(lines)
                if finished and not lines:
                    yield _sse("done", run.status.value)
                    return
                if not lines:
                    yield ": keep-alive\n\n"
                if await request.is_disconnected():
                    return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    async def download(self, request: Request) -> Response:
        """Send one file a run produced."""
        run = self._run(request)
        name = request.path_params["name"]
        # Only names from the listing are served, so a crafted path finds nothing.
        for artifact in run.artifacts():
            if artifact.name == name:
                return FileResponse(artifact.path, filename=Path(name).name)
        raise HTTPException(status_code=404, detail="No such file")

    async def bundle(self, request: Request) -> Response:
        """Send the command, log, report and output of a run as one zip."""
        run = self._run(request)
        artifacts = run.artifacts()

        def build() -> bytes:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for artifact in artifacts:
                    archive.write(artifact.path, artifact.name)
            return buffer.getvalue()

        content = await run_in_threadpool(build)
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="cgt-calc-{run.id[:6]}.zip"'
            },
        )

    async def delete_run(self, request: Request) -> Response:
        """Delete a finished run and its files."""
        run = self._run(request)
        await self.manager.delete(run.id)
        return RedirectResponse("/", status_code=303)


def create_app(
    settings: Settings, program: Sequence[str] = DEFAULT_PROGRAM
) -> Starlette:
    """Build the application; ``program`` is the command that runs the tool."""
    web = WebApp(settings, program)
    settings.work_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        reaper = asyncio.create_task(web.manager.reap_forever())
        try:
            yield
        finally:
            reaper.cancel()
            await web.manager.shutdown()

    return Starlette(
        routes=[
            Route("/", web.index),
            Route("/runs", web.create_run, methods=["POST"]),
            Route("/runs/{run_id}", web.show_run),
            Route("/runs/{run_id}/rerun", web.rerun),
            Route("/runs/{run_id}/events", web.events),
            Route("/runs/{run_id}/files/{name:path}", web.download),
            Route("/runs/{run_id}/bundle.zip", web.bundle),
            Route("/runs/{run_id}/delete", web.delete_run, methods=["POST"]),
            Mount("/static", StaticFiles(directory=PACKAGE_DIR / "static")),
        ],
        # The first entry is the outermost: every response gets the security
        # headers, and a request meets the token check only after the others.
        middleware=[
            Middleware(SecurityHeadersMiddleware),
            Middleware(HostCheckMiddleware, allowed_hosts=settings.allowed_hosts),
            Middleware(MaxBodySizeMiddleware, max_bytes=settings.max_upload_bytes),
            Middleware(OriginCheckMiddleware),
            Middleware(
                TokenAuthMiddleware,
                token=settings.token,
                secure=settings.secure_cookie,
            ),
        ],
        lifespan=lifespan,
    )
