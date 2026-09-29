"""Settings for the web interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_UPLOAD_MB = 50
DEFAULT_RUN_TTL_MINUTES = 60
DEFAULT_RUN_TIMEOUT_MINUTES = 30

# Host names the server answers to. A page on another site can point its own
# name at 127.0.0.1 (DNS rebinding) and send requests to this server from the
# person's browser, so any other Host header is refused.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})

MAX_UPLOAD_FILES = 200
MAX_FORM_FIELDS = 100

# The cookie carrying the per-launch token, see security.py.
AUTH_COOKIE = "cgt_calc_web"


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the application needs to know about how it was started."""

    token: str
    work_root: Path
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
    run_ttl_seconds: float = DEFAULT_RUN_TTL_MINUTES * 60
    run_timeout_seconds: float = DEFAULT_RUN_TIMEOUT_MINUTES * 60
    allowed_hosts: frozenset[str] = LOOPBACK_HOSTS
    # Set when the page is reached over HTTPS, e.g. behind a reverse proxy.
    secure_cookie: bool = False
