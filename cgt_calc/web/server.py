"""Start the web interface from the command line."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
import webbrowser

from cgt_calc.logging import setup_logging

from .config import (
    DEFAULT_HOST,
    DEFAULT_MAX_UPLOAD_MB,
    DEFAULT_PORT,
    DEFAULT_RUN_TIMEOUT_MINUTES,
    DEFAULT_RUN_TTL_MINUTES,
    LOOPBACK_HOSTS,
    Settings,
)
from .security import new_token

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

LOGGER = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 16
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

MISSING_EXTRA_HINT = (
    "The web interface needs extra packages. Install them with:\n"
    '  pip install "cgt-calc[web]"        (or: uv sync --extra web)'
)


def _env(name: str, default: object) -> str:
    """Return an environment variable, or the default as text.

    Every option can be set from the environment, which is how a container
    is configured. A command line option wins over the variable.
    """
    return os.environ.get(name) or str(default)


def create_parser() -> argparse.ArgumentParser:
    """Create ArgumentParser for the server."""
    parser = argparse.ArgumentParser(
        description="Run a local web interface for cgt-calc. Files you upload "
        "are processed on this computer and deleted when the server stops.",
        allow_abbrev=False,
        epilog="""
Environment variables (a command line option wins):
  CGT_WEB_HOST                  --host
  CGT_WEB_PORT                  --port
  CGT_WEB_ALLOWED_HOSTS         comma-separated names for --allow-host
  CGT_WEB_PUBLIC_URL            --public-url
  CGT_WEB_NO_BROWSER            1 for --no-browser
  CGT_WEB_MAX_UPLOAD_MB         --max-upload-mb
  CGT_WEB_RUN_TTL_MINUTES       --run-ttl-minutes
  CGT_WEB_RUN_TIMEOUT_MINUTES   --run-timeout-minutes
  CGT_WEB_TOKEN                 fixed login secret (at least 16 characters)
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default=_env("CGT_WEB_HOST", DEFAULT_HOST),
        help="address to listen on; anything but a loopback address exposes "
        "your transactions to the network (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env("CGT_WEB_PORT", DEFAULT_PORT),
        help="port to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=None,
        metavar="NAME",
        help="extra host name the server answers to, e.g. when reached through "
        "a container port mapping under another name (repeatable)",
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("CGT_WEB_PUBLIC_URL"),
        metavar="URL",
        help="address you open in the browser, if it differs from the listening "
        "one, e.g. http://192.168.1.10:8765; its host name is allowed and "
        "used in the printed link",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=os.environ.get("CGT_WEB_NO_BROWSER", "").lower() in TRUE_VALUES,
        help="do not open the page in a browser",
    )
    parser.add_argument(
        "--max-upload-mb",
        type=int,
        default=_env("CGT_WEB_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB),
        metavar="MB",
        help="largest request accepted (default: %(default)s)",
    )
    parser.add_argument(
        "--run-ttl-minutes",
        type=int,
        default=_env("CGT_WEB_RUN_TTL_MINUTES", DEFAULT_RUN_TTL_MINUTES),
        metavar="MINUTES",
        help="how long a finished run and its files are kept (default: %(default)s)",
    )
    parser.add_argument(
        "--run-timeout-minutes",
        type=int,
        default=_env("CGT_WEB_RUN_TIMEOUT_MINUTES", DEFAULT_RUN_TIMEOUT_MINUTES),
        metavar="MINUTES",
        help="longest a single calculation may take (default: %(default)s)",
    )
    return parser


def build_settings(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    environ: Mapping[str, str],
) -> Settings:
    """Combine options and environment into the settings the app runs with."""
    token = environ.get("CGT_WEB_TOKEN") or new_token()
    if len(token) < MIN_TOKEN_LENGTH:
        parser.error(f"CGT_WEB_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")
    named = [
        name.strip()
        for name in environ.get("CGT_WEB_ALLOWED_HOSTS", "").split(",")
        if name.strip()
    ]
    named += args.allow_host or []
    if args.public_url:
        public_host = urlsplit(args.public_url).hostname
        if not public_host:
            parser.error(f"--public-url needs a scheme and host, got {args.public_url}")
        # urlsplit drops the brackets of an IPv6 address; Host headers keep them.
        named.append(f"[{public_host}]" if ":" in public_host else public_host)
    return Settings(
        token=token,
        work_root=Path(tempfile.mkdtemp(prefix="cgt-calc-web-")),
        max_upload_bytes=args.max_upload_mb * 1024 * 1024,
        run_ttl_seconds=args.run_ttl_minutes * 60,
        run_timeout_seconds=args.run_timeout_minutes * 60,
        allowed_hosts=LOOPBACK_HOSTS | {host.lower() for host in named},
        secure_cookie=(args.public_url or "").lower().startswith("https://"),
    )


def login_url(args: argparse.Namespace, settings: Settings) -> str:
    """Return the link that signs a browser in."""
    base = (args.public_url or f"http://127.0.0.1:{args.port}").rstrip("/")
    return f"{base}/auth?token={settings.token}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the server until interrupted."""
    parser = create_parser()
    args = parser.parse_args(argv)
    setup_logging()
    try:
        import uvicorn  # noqa: PLC0415

        from .app import create_app  # noqa: PLC0415
    except ImportError as err:
        LOGGER.error("%s\n(%s)", MISSING_EXTRA_HINT, err)
        return 1

    if args.host not in LOOPBACK_HOSTS:
        LOGGER.warning(
            "Listening on %s. Anyone who can reach this address and knows the "
            "link below can read what you upload. Publish the port to "
            "127.0.0.1 only unless you mean to share it.",
            args.host,
        )

    settings = build_settings(parser, args, os.environ)
    url = login_url(args, settings)
    LOGGER.info("Open this link in your browser:\n\n  %s\n", url)
    if not args.no_browser:
        webbrowser.open(url)
    # No access log: it would print the token from the link above.
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="warning",
    )
    return 0


def init() -> None:
    """Entry point."""
    sys.exit(main())


if __name__ == "__main__":
    init()
