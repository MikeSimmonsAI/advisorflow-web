"""Shared plumbing for public-record adapters: errors, polite HTTP, remote zips.

POLITENESS. One identifying User-Agent, a timeout on every call, HTTP 429 /
503 surfaced as RATE_LIMITED (never retried in a loop), and bulk files read
with byte-range requests so a pilot touches megabytes, not the whole roll.

DISK. A file that cannot be range-read is downloaded to EVOSENSE_SOURCE_CACHE
(default: the system temp dir) only when the free space is at least twice the
file, and never beyond EVOSENSE_SOURCE_MAX_BYTES (default 600 MB). Otherwise
the adapter fails with INSUFFICIENT_DISK — a first-class failure, not a crash.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from typing import Any, Dict, Optional

USER_AGENT = ("EvoSense/1.0 public-records reader (EvoSys; low volume; "
              "contact support@evosyspro.live)")
TIMEOUT = 45

# ── first-class failure codes ──────────────────────────────────────────────
RATE_LIMITED = "RATE_LIMITED"
SOURCE_FORMAT_CHANGED = "SOURCE_FORMAT_CHANGED"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
AUTH_FAILED = "AUTH_FAILED"
TIMEOUT_ERR = "TIMEOUT"
PARSING_FAILED = "PARSING_FAILED"
IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
INSUFFICIENT_DISK = "INSUFFICIENT_DISK"
NO_MATCH = "NO_MATCH"
ERROR_CODES = (RATE_LIMITED, SOURCE_FORMAT_CHANGED, DOWNLOAD_FAILED, AUTH_FAILED, TIMEOUT_ERR,
               PARSING_FAILED, IDENTITY_AMBIGUOUS, PROVIDER_NOT_CONFIGURED, INSUFFICIENT_DISK,
               NO_MATCH)


class SourceError(Exception):
    def __init__(self, code: str, message: str, retry_after: Optional[int] = None):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message
        self.retry_after = retry_after


def sha256(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest()


def local_override(key: str) -> Optional[str]:
    """EVOSENSE_SRC_<KEY>_PATH points an adapter at a local file (tests, or an
    operator who downloaded the file by hand)."""
    p = os.environ.get("EVOSENSE_SRC_%s_PATH" % key.upper())
    return p if p and os.path.exists(p) else None


def _request(url: str, headers: Optional[Dict[str, str]] = None, method: str = "GET"):
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, method=method)
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 503):
            ra = exc.headers.get("Retry-After") if exc.headers else None
            raise SourceError(RATE_LIMITED, "%s answered %s" % (_host(url), exc.code),
                              retry_after=int(ra) if ra and str(ra).isdigit() else 900)
        if exc.code in (401, 403):
            raise SourceError(AUTH_FAILED, "%s refused the request (%s)" % (_host(url), exc.code))
        raise SourceError(DOWNLOAD_FAILED, "%s answered %s" % (_host(url), exc.code))
    except (TimeoutError, OSError) as exc:  # URLError is an OSError
        if "timed out" in str(exc).lower():
            raise SourceError(TIMEOUT_ERR, "%s did not answer in %ss" % (_host(url), TIMEOUT))
        raise SourceError(DOWNLOAD_FAILED, "%s: %s" % (_host(url), str(exc)[:160]))


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc or url[:40]


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    with _request(full) as r:
        body = r.read(20 * 1024 * 1024)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise SourceError(SOURCE_FORMAT_CHANGED, "%s did not return JSON" % _host(url))


def mailing_line(*lines) -> Optional[str]:
    """Owner mailing blocks mix name continuations and care-of lines with the
    street ("ANAHI" / "3337 CORONET BLVD"; "C/O TRUSTEE" / "PO BOX 44").
    The street is the first line that starts with a number or is a PO box;
    otherwise None (UNKNOWN, not a guess)."""
    import re as _re
    clean = [(l or "").strip() for l in lines if l and l.strip()]
    for l in clean:
        if _re.match(r"^(\d|P\.?\s*O\.?\s*BOX|POST OFFICE BOX|BOX\s+\d|RR\s*\d|HC\s*\d)", l.upper()):
            return l
    return None


def name_continuation(*lines) -> Optional[str]:
    """Lines above the street that continue the owner's name (not C/O / ATTN)."""
    import re as _re
    out = []
    for l in lines:
        l = (l or "").strip()
        if not l:
            continue
        if _re.match(r"^(\d|P\.?\s*O\.?\s*BOX|POST OFFICE BOX)", l.upper()):
            break
        if _re.match(r"^(C/O|ATTN|%)", l.upper()):
            continue
        out.append(l)
    return " ".join(out) or None


def get_text(url: str, max_bytes: int = 5 * 1024 * 1024) -> str:
    with _request(url) as r:
        return r.read(max_bytes).decode("utf-8", "replace")


class HttpRangeFile(io.RawIOBase):
    """A read-only, seekable view of a remote file through HTTP Range requests.
    zipfile reads the central directory (tail) and then streams one member, so
    only the bytes actually consumed are transferred."""

    CHUNK = 1024 * 1024

    def __init__(self, url: str, size: int):
        super().__init__()
        self.url = url
        self.size = size
        self.pos = 0
        self.bytes_fetched = 0
        self._buf_start = -1
        self._buf = b""

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        else:
            self.pos = self.size + offset
        self.pos = max(0, min(self.pos, self.size))
        return self.pos

    def _fill(self, start: int, want: int):
        end = min(self.size - 1, start + max(want, self.CHUNK) - 1)
        for attempt in range(3):
            try:
                with _request(self.url, {"Range": "bytes=%d-%d" % (start, end)}) as r:
                    if r.status != 206:
                        raise SourceError(DOWNLOAD_FAILED, "%s ignored the byte range" % _host(self.url))
                    data = r.read()
                break
            except SourceError as exc:
                if exc.code in (TIMEOUT_ERR, DOWNLOAD_FAILED) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        self.bytes_fetched += len(data)
        self._buf_start, self._buf = start, data

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        n = len(b)
        if not (self._buf_start <= self.pos < self._buf_start + len(self._buf)):
            self._fill(self.pos, n)
        off = self.pos - self._buf_start
        chunk = self._buf[off:off + n]
        b[:len(chunk)] = chunk
        self.pos += len(chunk)
        return len(chunk)


def _probe(url: str) -> Dict[str, Any]:
    with _request(url, {"Range": "bytes=0-0"}) as r:
        rng = r.headers.get("Content-Range") or ""
        size = int(rng.split("/")[-1]) if "/" in rng and rng.split("/")[-1].isdigit() else \
            int(r.headers.get("Content-Length") or 0)
        return {"ranged": r.status == 206, "size": size,
                "last_modified": r.headers.get("Last-Modified"), "etag": r.headers.get("ETag")}


def parse_http_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


class OpenedZip:
    def __init__(self, zf: zipfile.ZipFile, meta: Dict[str, Any], handle=None):
        self.zip = zf
        self.meta = meta
        self._handle = handle

    def bytes_fetched(self) -> int:
        return getattr(self._handle, "bytes_fetched", 0) if self._handle else 0

    def close(self):
        try:
            self.zip.close()
        finally:
            if self._handle is not None:
                try:
                    self._handle.close()
                except Exception:  # noqa: BLE001
                    pass


def open_zip(key: str, url: str, *, allow_download: bool = True) -> OpenedZip:
    """Open a published zip: a local override, else byte ranges, else a guarded
    download to the cache. meta = {url, ranged, size, last_modified, mode}."""
    local = local_override(key)
    if local:
        return OpenedZip(zipfile.ZipFile(local), {"url": url, "mode": "local_file", "path": local,
                                                 "size": os.path.getsize(local),
                                                 "last_modified": None})
    info = _probe(url)
    info["url"] = url
    if info["ranged"] and info["size"]:
        fh = HttpRangeFile(url, info["size"])
        buffered = io.BufferedReader(fh, buffer_size=HttpRangeFile.CHUNK)
        try:
            zf = zipfile.ZipFile(buffered)
        except zipfile.BadZipFile:
            raise SourceError(SOURCE_FORMAT_CHANGED, "%s is not a readable zip" % _host(url))
        info["mode"] = "range"
        return OpenedZip(zf, info, fh)
    if not allow_download:
        raise SourceError(DOWNLOAD_FAILED, "%s does not support byte ranges" % _host(url))
    path = download(key, url, info.get("size") or 0, etag=info.get("etag") or info.get("last_modified"))
    info["mode"] = "download"
    info["path"] = path
    try:
        return OpenedZip(zipfile.ZipFile(path), info)
    except zipfile.BadZipFile:
        raise SourceError(SOURCE_FORMAT_CHANGED, "%s is not a readable zip" % _host(url))


def cache_dir() -> str:
    d = os.environ.get("EVOSENSE_SOURCE_CACHE") or os.path.join(tempfile.gettempdir(), "evosense-sources")
    os.makedirs(d, exist_ok=True)
    return d


def download(key: str, url: str, expected_size: int, etag: Optional[str] = None) -> str:
    limit = int(os.environ.get("EVOSENSE_SOURCE_MAX_BYTES") or 600 * 1024 * 1024)
    if expected_size and expected_size > limit:
        raise SourceError(INSUFFICIENT_DISK, "file is %s MB; the configured cap is %s MB"
                          % (expected_size // 2 ** 20, limit // 2 ** 20))
    d = cache_dir()
    tag = sha256("%s|%s|%s" % (url, etag or "", expected_size))[:16]
    path = os.path.join(d, "%s-%s.zip" % (key, tag))
    if os.path.exists(path) and (not expected_size or os.path.getsize(path) == expected_size):
        return path
    free = shutil.disk_usage(d).free
    if expected_size and free < 2 * expected_size:
        raise SourceError(INSUFFICIENT_DISK, "needs %s MB free, has %s MB"
                          % (2 * expected_size // 2 ** 20, free // 2 ** 20))
    for old in os.listdir(d):                     # one cached copy per source
        if old.startswith(key + "-") and old.endswith(".zip"):
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
    tmp = path + ".part"
    got = 0
    with _request(url) as r, open(tmp, "wb") as out:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            got += len(chunk)
            if got > limit:
                out.close()
                os.remove(tmp)
                raise SourceError(INSUFFICIENT_DISK, "download exceeded the %s MB cap" % (limit // 2 ** 20))
            out.write(chunk)
    if expected_size and got != expected_size:
        os.remove(tmp)
        raise SourceError(DOWNLOAD_FAILED, "download truncated (%s of %s bytes)" % (got, expected_size))
    os.replace(tmp, path)
    return path


def text_lines(zf: zipfile.ZipFile, member: str, encoding: str = "latin-1"):
    """Stream one member line by line (bytes decoded, line endings stripped)."""
    names = {n.lower(): n for n in zf.namelist()}
    real = names.get(member.lower())
    if real is None:
        raise SourceError(SOURCE_FORMAT_CHANGED, "expected %s inside the file; found %s"
                          % (member, ", ".join(sorted(zf.namelist())[:8])))
    with zf.open(real) as f:
        for raw in f:
            yield raw.decode(encoding).rstrip("\r\n")
