"""Atlas transport — the ONLY object permitted to open a socket to Atlas.

Nothing else in the system imports urllib. Enforced by test.

Concerns:
  Auth     — Header AK/SK from .env, never a call site.
  Live gate — No live call unless LIVE=1; otherwise LiveCallBlocked.
  Encoding — gzip; urllib does not auto-decompress, so _decode() does.
  Timeout  — 12s. A sweep is a dozen sequential calls.
  Errors   — AtlasHTTPError carries status + body. An error body is never
             cached and never returned as data.
  Cache    — Every successful response lands in RESPONSE_CACHE.
  Retry    — 429/5xx retried up to MAX_RETRIES with exponential backoff.
  Rate     — MIN_INTERVAL between calls prevents QPS exhaustion.
  Request ID — Every call generates a request_id for tracing.
"""

import gzip
import json
import os
import pathlib
import threading
import time
import urllib.request
import urllib.error
import uuid

from . import cache as response_cache


class LiveCallBlocked(Exception):
    """Raised when a live call fires without LIVE=1."""


class AtlasHTTPError(Exception):
    """Carries status and body from a failed Atlas call.

    An error body is NEVER cached and NEVER returned as data — otherwise a
    401 becomes 'Bali has no flights'.
    """
    def __init__(self, status, body, url="", request_id=""):
        self.status = status
        self.body = body
        self.url = url
        self.request_id = request_id
        super().__init__("Atlas HTTP %d: %s" % (status, body[:200]))


# ---------------------------------------------------------------------------
# Config loader — reads .env without any dependency
# ---------------------------------------------------------------------------

def _load_env(path=None):
    """Parse a .env file into a dict. Simple KEY=VALUE, no quoting support."""
    if path is None:
        # Walk upward from this file's package root to find .env
        here = pathlib.Path(__file__).resolve()
        for parent in [here.parent] + list(here.parents):
            candidate = parent / ".env"
            if candidate.is_file():
                path = candidate
                break
            # Also check the project root (three levels up: src/atlas/client.py)
            if parent.name == "src":
                candidate = parent.parent / ".env"
                if candidate.is_file():
                    path = candidate
                    break
    if path is None or not pathlib.Path(path).is_file():
        return {}
    result = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "https://sandbox.atriptech.com"
_TIMEOUT = 12
MAX_RETRIES = 3              # Max retry attempts for 429/5xx
MIN_INTERVAL = 0.3           # Seconds between calls (rate floor)
_RETRY_BACKOFF_BASE = 1.0    # Initial backoff seconds

# Process-wide rate limiter — prevents burst across threads
_rate_lock = threading.Lock()
_last_call_time = [0.0]


class AtlasClient:
    """POST to Atlas and record the response.

    fixture_key names the cache slot, e.g. 'search.do:SIN-DPS@20260918'.
    That key is the left-hand side of every cost_ref in the system.

    Batch usage:
      - Calls are rate-limited by MIN_INTERVAL (seconds between calls)
      - 429/5xx are retried up to MAX_RETRIES with exponential backoff
      - Every call logs a request_id for tracing
    """

    def __init__(self, config=None):
        if config is None:
            config = _load_env()
        self._config = config
        self._base_url = config.get("ATLAS_BASE_URL", _DEFAULT_BASE_URL)
        self._client_id = config.get("ATLAS_CLIENT_ID", "")
        self._client_secret = config.get("ATLAS_CLIENT_SECRET", "")
        self.last_error = None
        self.request_log = []  # List of {request_id, url, status, elapsed_ms}

    # -- the only path to Atlas ---------------------------------------------

    def post(self, endpoint, payload, fixture_key=None, allow_error=False):
        """Call Atlas and record the response.

        On success the parsed JSON is stored in RESPONSE_CACHE under
        fixture_key, making it dereferenceable by every cost_ref in the
        system. On error, nothing is cached.
        """
        if os.environ.get("LIVE", "0") != "1":
            raise LiveCallBlocked(
                "Live calls require LIVE=1. Set the env var to call Atlas.")

        url = self._base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        request_id = str(uuid.uuid4())[:12]
        return self._call_live_with_retry(url, payload, fixture_key,
                                          allow_error, request_id)

    def _call_live_with_retry(self, url, payload, fixture_key, allow_error,
                              request_id):
        """Rate-limit, retry on 429/5xx, and log every call."""
        global _last_call_time

        # Enforce minimum interval between calls
        with _rate_lock:
            elapsed_since = time.monotonic() - _last_call_time[0]
            if elapsed_since < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - elapsed_since)
            _last_call_time[0] = time.monotonic()

        t0 = time.monotonic()
        last_exc = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                result = self._call_live(url, payload, fixture_key,
                                         allow_error, request_id)
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                self.request_log.append({
                    "request_id": request_id,
                    "url": url,
                    "status": "ok",
                    "elapsed_ms": elapsed_ms,
                })
                return result

            except AtlasHTTPError as exc:
                last_exc = exc
                # Retry only on 429 (rate limit) and 5xx (server errors)
                if exc.status in (429, 500, 502, 503, 504):
                    if attempt < MAX_RETRIES:
                        backoff = _RETRY_BACKOFF_BASE * (2 ** attempt)
                        time.sleep(backoff)
                        continue
                # Non-retryable or exhausted retries
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                self.request_log.append({
                    "request_id": request_id,
                    "url": url,
                    "status": "error",
                    "http_code": exc.status,
                    "elapsed_ms": elapsed_ms,
                })
                raise

            except urllib.error.URLError as exc:
                # Timeout or network error — retry
                last_exc = exc
                if attempt < MAX_RETRIES:
                    backoff = _RETRY_BACKOFF_BASE * (2 ** attempt)
                    time.sleep(backoff)
                    continue
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                self.request_log.append({
                    "request_id": request_id,
                    "url": url,
                    "status": "timeout",
                    "error": str(exc)[:120],
                    "elapsed_ms": elapsed_ms,
                })
                if not allow_error:
                    raise AtlasHTTPError(
                        0, "Timeout: %s" % str(exc)[:200], url, request_id
                    ) from exc
                return None

        # Should not reach here, but guard
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        self.request_log.append({
            "request_id": request_id,
            "url": url,
            "status": "error",
            "error": "exhausted retries",
            "elapsed_ms": elapsed_ms,
        })
        if last_exc:
            raise last_exc
        return None

    def _call_live(self, url, payload, fixture_key, allow_error, request_id):
        """Execute a single live HTTP POST to Atlas. No retry here."""
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept-Encoding", "gzip")
        if self._client_id:
            req.add_header("x-atlas-client-id", self._client_id)
        if self._client_secret:
            req.add_header("x-atlas-client-secret", self._client_secret)

        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
                body = self._decode(raw, resp.headers.get("Content-Encoding"))
                parsed = json.loads(body)
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_raw = exc.read()
                err_body = self._decode(
                    err_raw,
                    getattr(exc, "headers", {}).get("Content-Encoding"))
            except Exception:
                pass
            # NEVER cache an error body — otherwise a 401 becomes
            # 'Bali has no flights'.
            self.last_error = {
                "code": exc.code, "body": err_body[:500], "url": url,
                "request_id": request_id,
            }
            if not allow_error:
                raise AtlasHTTPError(
                    exc.code, err_body, url, request_id) from exc
            return None

        if fixture_key:
            response_cache.put(fixture_key, parsed)

        return parsed

    # -- encoding -----------------------------------------------------------

    @staticmethod
    def _decode(raw, encoding):
        """Decode a response body. Handles gzip explicitly — urllib does not
        auto-decompress, and a mislabelled body must be tolerated."""
        if encoding == "gzip" or (
            encoding is None and raw[:2] == b"\x1f\x8b"
        ):
            try:
                return gzip.decompress(raw).decode("utf-8")
            except Exception:
                pass
        # Fallback: try raw UTF-8
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            # Last resort — try gzip even if not labelled
            return gzip.decompress(raw).decode("utf-8")
