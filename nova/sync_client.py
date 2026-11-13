"""Instance sync — local-side HTTP client (step 6).

Everything the local side says to a hub over the wire: the handshake and clock
measurement, paged snapshot assembly, chunked upload with resume, file download
with Range resume, and the push/repair endpoints. No Flask, no database: this
module turns a base URL + bearer token into parsed results, and turns a refusal
response into the engine's own ``SyncRefused`` so the job runner and the UI
translate codes rather than inspect HTTP.

The token never appears in a URL, a log line, or an exception. TLS verification
is on (requests' default), and every call carries the protocol header the hub
checks before it reads anything else.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from nova.sync_engine import PAYLOAD_FORMAT, PLAN_ORDER, Refusal, SyncRefused, parse_datetime
from nova.sync_transport import (AUTH_HEADER, BEARER_PREFIX, CODE_PROTOCOL_MISMATCH,
                                 PROTOCOL_HEADER, PROTOCOL_VERSION)

logger = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "https://app.nova-tracker.com"

# One upload chunk. Matches the hub's CHUNK_BYTES: the chunk size is what bounds
# a single request's duration on a slow uplink.
CHUNK_BYTES = 1024 * 1024

# Transient conditions the caller may retry. Deterministic refusals (bad token,
# needs_download, clock_skew, mass_delete, server_untouched, local_replaced,
# remote_replaced) are raised immediately and never retried here.
_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})

# The address answered, but not as a sync server: a Nova from before instance
# sync has no /api/sync/ routes (an HTML 404/405 in single-user mode) or stops
# the request at its login gate (a 401 that carries no protocol field). A
# client-side code — no hub ever sends it.
CODE_SERVER_NO_SYNC = "server_no_sync"
_NO_SYNC_HTTP = frozenset({401, 403, 404, 405})


class SyncTransportError(Exception):
    """A network/timeout/protocol failure with no engine refusal behind it."""

    def __init__(self, message: str, *, cause: Exception = None):
        super().__init__(message)
        self.cause = cause


# --- URL rules ----------------------------------------------------------------

def normalize_server_url(raw: str) -> str:
    """A hub origin as ``https://host[:port]``, or raise ValueError.

    Rejects a path/query/fragment/credentials and any scheme other than
    http/https; trailing slashes are dropped. The scheme policy (and its
    warning) lives in ``validate_server_url``.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("scheme")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("credentials_or_query")
    if parsed.path and parsed.path.rstrip("/") != "":
        raise ValueError("path")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("host")
    port = parsed.port
    return f"{parsed.scheme}://{host}" + (f":{port}" if port else "")


def _is_local_or_private(host: str) -> bool:
    """Whether an http origin may be used at all (with a warning)."""
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified


def validate_server_url(raw: str) -> tuple[str, bool]:
    """(normalized origin, insecure_warning).

    https is always allowed with no warning. http is allowed only for
    localhost/127.0.0.1/::1, private-LAN ranges and ``*.local``, and only with a
    visible warning (the token travels in the clear on that network). http to a
    public host is refused.
    """
    normalized = normalize_server_url(raw)
    if normalized.startswith("https://"):
        return normalized, False
    host = normalized.split("://", 1)[1].split(":", 1)[0]
    if _is_local_or_private(host):
        return normalized, True
    raise ValueError("insecure")


# --- Clock offset -------------------------------------------------------------

def measure_clock_offset(server_time: datetime, t0: float, t3: float) -> float:
    """Local clock minus hub clock, in seconds.

    ``server_time`` is the hub's answer, ``t0`` the instant before the request
    was sent and ``t3`` the instant the response arrived. ``(t3 - t0) / 2`` is
    the best estimate of one-way latency, so the offset is
    ``t3 - server - (t3 - t0) / 2``.
    """
    t2 = server_time.replace(tzinfo=timezone.utc).timestamp()
    return t3 - t2 - (t3 - t0) / 2.0


# --- Client -------------------------------------------------------------------

class SyncClient:
    """One hub, one token. Stateless across calls; a Session carries cookies only."""

    def __init__(self, server_url: str, token: str, *, session: requests.Session = None,
                 protocol: int = PROTOCOL_VERSION,
                 connect_timeout: float = 10.0, read_timeout: float = 60.0):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.protocol = protocol
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._session = session or requests.Session()

    # -- plumbing --------------------------------------------------------------

    def _headers(self, extra: dict = None) -> dict:
        headers = {
            PROTOCOL_HEADER: str(self.protocol),
            AUTH_HEADER: BEARER_PREFIX + self.token,
        }
        if extra:
            headers.update(extra)
        return headers

    def _post(self, path: str, body: dict = None, *, extra_headers: dict = None,
              data: bytes = None, stream: bool = False, timeout=None):
        url = self.server_url + path
        kwargs = dict(headers=self._headers(extra_headers), stream=stream,
                      timeout=timeout or (self.connect_timeout, self.read_timeout))
        if data is not None:
            kwargs["data"] = data
        else:
            kwargs["json"] = body
        try:
            return self._session.post(url, **kwargs)
        except requests.RequestException as exc:
            # The request holds the token; never format it into the message.
            raise SyncTransportError(f"sync request failed for {path}: {type(exc).__name__}",
                                     cause=exc) from exc

    def _json(self, path: str, body: dict = None, *, extra_headers: dict = None,
              data: bytes = None, timeout=None) -> dict:
        return self._parse_json(self._post(path, body, extra_headers=extra_headers,
                                           data=data, timeout=timeout), path)

    def _parse_json(self, resp: requests.Response, path: str) -> dict:
        """A response body as a dict, or the refusal it carries.

        Refusals are read BEFORE the protocol field. A hub on another protocol
        version says so with a protocol_mismatch refusal, and that refusal's
        own `protocol` is the hub's version: checking the field first would
        turn the one answer written for this case into a generic network
        error. A server that is no sync server at all gets its own code.
        """
        if resp.status_code in _RETRYABLE_HTTP:
            raise SyncTransportError(f"hub answered {resp.status_code} for {path}")
        try:
            payload = resp.json()
        except ValueError as exc:
            if resp.status_code in _NO_SYNC_HTTP:
                raise SyncRefused(Refusal(CODE_SERVER_NO_SYNC,
                                          {"status": resp.status_code})) from exc
            raise SyncTransportError(f"hub answered non-JSON ({resp.status_code}) for {path}",
                                     cause=exc) from exc
        if not isinstance(payload, dict):
            raise SyncTransportError(f"hub answered a non-object for {path}")
        refusals = payload.get("refusals")
        if refusals:
            first = refusals[0] if isinstance(refusals, list) and refusals else None
            if isinstance(first, dict):
                raise SyncRefused(Refusal(str(first.get("code") or "bad_format"),
                                          dict(first.get("detail") or {})))
            raise SyncRefused(Refusal("bad_format", {}))
        self._check_protocol(payload, path, resp.status_code)
        return payload

    def _check_protocol(self, payload: dict, path: str, status: int = 200) -> None:
        sent = payload.get("protocol")
        if sent is None:
            if status in _NO_SYNC_HTTP:
                # JSON, but not the sync API's: an older multi-user Nova's
                # login gate answers /api/ paths with {"error": ...} and a 401.
                raise SyncRefused(Refusal(CODE_SERVER_NO_SYNC, {"status": status}))
            raise SyncTransportError(f"hub answer for {path} carried no protocol")
        try:
            version = int(sent)
        except (TypeError, ValueError):
            version = None
        if version != self.protocol:
            raise SyncRefused(Refusal(CODE_PROTOCOL_MISMATCH,
                                      {"supported": sent, "found": self.protocol}))

    # -- handshake -------------------------------------------------------------

    def hello(self) -> dict:
        """POST /hello. Returns the parsed body with ``_server_time`` (datetime)
        and ``_clock_offset_seconds`` added."""
        t0 = time.time()
        payload = self._json("/api/sync/v1/hello", {}, timeout=(self.connect_timeout, 30.0))
        t3 = time.time()
        server_time = parse_datetime(payload.get("server_time"))
        if server_time is None:
            raise SyncTransportError("hello carried no usable server_time")
        payload["_server_time"] = server_time
        payload["_clock_offset_seconds"] = measure_clock_offset(server_time, t0, t3)
        return payload

    # -- pull ------------------------------------------------------------------

    def changes(self, since_rev: int, exclude_revs=None, with_files: bool = True) -> dict:
        """POST /changes. Returns the engine payload (records/files/tombstones)."""
        body = {"since_rev": int(since_rev),
                "exclude_revs": [int(r) for r in (exclude_revs or [])],
                "with_files": bool(with_files)}
        out = self._json("/api/sync/v1/changes", body)
        payload = out.get("payload")
        if not isinstance(payload, dict):
            raise SyncTransportError("changes carried no payload")
        return payload

    def snapshot_page(self, table: str, after_id: int = 0,
                      limit: int = 500) -> dict:
        return self._json("/api/sync/v1/snapshot",
                          {"table": table, "after_id": int(after_id), "limit": int(limit)})

    def snapshot(self, page_limit: int = 500, max_restarts: int = 3) -> dict:
        """The whole account, assembled from keyset pages, torn-snapshot safe.

        Every page carries ``server_rev``; if it changes mid-walk the account
        moved and the assembly restarts (bounded). Tombstones are deliberately
        absent: a download is a full replacement and writes every record
        verbatim, so a snapshot needs live records + files only.
        """
        for _attempt in range(max_restarts + 1):
            try:
                return self._assemble_snapshot(page_limit=page_limit)
            except _TornSnapshot:
                continue
        raise SyncTransportError("snapshot kept changing; server is busy")

    def _assemble_snapshot(self, page_limit) -> dict:
        server_rev = None
        records: dict = {}
        files: dict = {}
        skipped: list = []
        # The hub repeats `tables` on every page; bootstrap from the local table
        # set (same app) and prefer the hub's list once seen (version skew).
        tables = [m.__tablename__ for m in PLAN_ORDER]
        for table in tables:
            after_id = 0
            while True:
                page = self.snapshot_page(table, after_id, page_limit)
                hub_tables = [t for t in (page.get("tables") or []) if t]
                if hub_tables:
                    tables = hub_tables
                rev = page.get("server_rev")
                if server_rev is None:
                    server_rev = rev
                elif rev != server_rev:
                    raise _TornSnapshot()
                for record in page.get("records") or []:
                    records.setdefault(table, []).append(record)
                for digest, descriptor in (page.get("files") or {}).items():
                    files.setdefault(digest, descriptor)
                skipped.extend(page.get("skipped_files") or [])
                if page.get("done"):
                    break
                after_id = page.get("next_after_id", after_id)
        return {"format": PAYLOAD_FORMAT, "account": "", "instance": "",
                "side": "server", "rev": server_rev, "snapshot": True,
                "server_rev": server_rev, "records": records,
                "files": files, "skipped_files": skipped, "tombstones": []}

    # -- pull: one file, with Range resume --------------------------------------

    def fetch_file_iter(self, ref, resume_from: int = 0, chunk: int = CHUNK_BYTES):
        """Yield the bytes of one file, resuming at ``resume_from`` via Range."""
        body = {"table": ref.table, "key": ref.key, "slot": ref.slot,
                "sha256": ref.sha256}
        headers = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
        resp = self._post("/api/sync/v1/file", body, extra_headers=headers,
                          stream=True, timeout=(self.connect_timeout, 120.0))
        if resp.status_code in (200, 206):
            for piece in resp.iter_content(chunk):
                if piece:
                    yield piece
            return
        if resume_from > 0 and resp.status_code == 416:
            # Already complete at this offset: nothing more to read.
            return
        self._parse_json(resp, "/api/sync/v1/file")  # raises on a refusal
        raise SyncTransportError(f"file fetch answered {resp.status_code}")

    def fetch_slot(self, table: str, key: str, slot: str, sha256: str = "",
                   chunk: int = CHUNK_BYTES):
        """One file by the record and slot that names it: (info, byte iterator).

        For a file this side cannot hash because it is not here. `info` is what
        the hub says it is sending — {"sha256", "ext", "size"} from the response
        headers — and the caller verifies the stream against that digest.
        """
        body = {"table": table, "key": key, "slot": slot, "sha256": sha256 or ""}
        resp = self._post("/api/sync/v1/file", body, stream=True,
                          timeout=(self.connect_timeout, 120.0))
        if resp.status_code == 200:
            headers = resp.headers or {}
            info = {"sha256": headers.get("X-Nova-Sha256") or "",
                    "ext": headers.get("X-Nova-Ext") or "",
                    "size": headers.get("X-Nova-Size") or 0}
            return info, (piece for piece in resp.iter_content(chunk) if piece)
        self._parse_json(resp, "/api/sync/v1/file")  # raises on a refusal
        raise SyncTransportError(f"file fetch answered {resp.status_code}")

    # -- push: plan / upload / apply -------------------------------------------

    def push_plan(self, payload: dict, clock_offset_seconds: float,
                  allow_mass_delete: bool = False) -> dict:
        return self._json("/api/sync/v1/push/plan", {
            "payload": payload,
            "clock_offset_seconds": float(clock_offset_seconds),
            "allow_mass_delete": bool(allow_mass_delete),
        }, timeout=(self.connect_timeout, 60.0))

    def push_file_resume(self, session_id: str, sha256: str) -> dict:
        """offset -1: how many bytes the hub already holds for this digest."""
        return self._json("/api/sync/v1/push/file", None,
                          extra_headers={"X-Nova-Session": session_id,
                                         "X-Nova-Sha256": sha256,
                                         "X-Nova-Offset": "-1"})

    def push_file_chunk(self, session_id: str, sha256: str, offset: int,
                        data: bytes) -> dict:
        return self._json("/api/sync/v1/push/file", None,
                          extra_headers={"X-Nova-Session": session_id,
                                         "X-Nova-Sha256": sha256,
                                         "X-Nova-Offset": str(int(offset)),
                                         "X-Nova-Chunk-Sha256": hashlib.sha256(data).hexdigest()},
                          data=data, timeout=(self.connect_timeout, 120.0))

    def push_apply(self, session_id: str, fingerprint: str) -> dict:
        return self._json("/api/sync/v1/push/apply",
                          {"session_id": session_id, "fingerprint": fingerprint},
                          timeout=(self.connect_timeout, 60.0))

    def push_cancel(self) -> dict:
        return self._json("/api/sync/v1/push/cancel", {})

    # -- repair -----------------------------------------------------------------

    def _repair_headers(self, table, key, slot, sha256, ext, total, offset, stamp=""):
        # X-Nova-Stamp: the sender's row stamp in the hub's clock. The hub
        # repairs only a row that is that same write.
        return {"X-Nova-Table": table, "X-Nova-Key": key, "X-Nova-Slot": slot,
                "X-Nova-Sha256": sha256, "X-Nova-Ext": ext,
                "X-Nova-Total": str(int(total)), "X-Nova-Offset": str(int(offset)),
                "X-Nova-Stamp": stamp or ""}

    def repair_resume(self, table: str, key: str, slot: str, sha256: str,
                      ext: str, total: int, stamp: str = "") -> dict:
        """offset -1: how many bytes of this repair the hub already holds."""
        return self._json("/api/sync/v1/repair", None,
                          extra_headers=self._repair_headers(
                              table, key, slot, sha256, ext, total, -1, stamp))

    def repair_chunk(self, table: str, key: str, slot: str, sha256: str,
                     ext: str, total: int, offset: int, data: bytes,
                     stamp: str = "") -> dict:
        return self._json("/api/sync/v1/repair", None,
                          extra_headers=self._repair_headers(
                              table, key, slot, sha256, ext, total, offset, stamp),
                          data=data, timeout=(self.connect_timeout, 120.0))


class _TornSnapshot(Exception):
    """The account changed between snapshot pages; discard and restart."""
