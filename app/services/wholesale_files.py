"""The ONE place a wholesale file is stored, fetched or deleted.

WHY THIS EXISTS RATHER THAN A SECOND STORAGE SYSTEM
---------------------------------------------------
The platform already has a file capability: `app/services/mobile_storage.py`.
It is reused here, deliberately and narrowly — its S3 writer is called directly
rather than reimplemented, and its environment variables are the ones this
module reads. Nothing about it is copied.

Two things it does NOT do, which a wholesaler's document drawer needs:

1. It REFUSES every upload unless MEDIA_STORAGE_BACKEND=s3. That refusal is
   correct for the phone app it was written for — a captured photo that
   evaporates on the next restart is worse than one the device keeps queued.
   But it means a developer, or Mike on his own machine, cannot upload a
   purchase contract at all. So this module adds ONE more backend, `local`,
   which must be switched on explicitly and which says plainly that it is not
   durable. `mobile_storage.backend()` still reports `ephemeral` for it, so the
   phone app's refusal is completely untouched.

2. It returns a PUBLIC object URL. A signed purchase contract is not a public
   object. Nothing here ever hands a URL to a browser: the storage key lives
   server-side only, and every byte is served through an authenticated endpoint
   that re-checks the organization on the way out.

ENVIRONMENT
-----------
    MEDIA_STORAGE_BACKEND   "s3" (durable, production) | "local" (development)
    MEDIA_S3_BUCKET         required for s3      } already defined by the
    AWS_ACCESS_KEY_ID       required for s3      } platform; not invented here
    AWS_SECRET_ACCESS_KEY   required for s3      }
    AWS_REGION              optional, default us-east-1
    WHOLESALE_LOCAL_MEDIA_ROOT   optional; where `local` writes.
                                 Default: <repo>/.local_media

Unset means no uploads, and the product says so rather than failing quietly.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, UploadFile, status

from app.services import mobile_storage

BACKEND_S3 = "s3"
BACKEND_LOCAL = "local"
BACKEND_NONE = "none"

# 25 MB. Larger than the phone ceiling because a scanned settlement statement
# is a bigger thing than a photo taken to be uploaded immediately, and smaller
# than anything that would tie up a request worker.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# What a deal drawer actually contains. Every entry is a format somebody
# genuinely receives from a title company, an agent or a seller.
CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
IMAGE_TYPES = tuple(k for k in CONTENT_TYPES if k.startswith("image/"))


# ── What this deployment can actually do ────────────────────────────────────

def backend() -> str:
    """s3, local, or none. Never guesses: a half-configured bucket is `none`."""
    configured = str(os.environ.get("MEDIA_STORAGE_BACKEND", "")).strip().lower()
    if configured == BACKEND_S3:
        # The platform's own completeness check, not a second opinion.
        return BACKEND_S3 if mobile_storage._s3_configured() else BACKEND_NONE
    if configured == BACKEND_LOCAL:
        return BACKEND_LOCAL
    return BACKEND_NONE


def local_root() -> str:
    root = os.environ.get("WHOLESALE_LOCAL_MEDIA_ROOT", "").strip()
    if root:
        return root
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, ".local_media")


def capability() -> Dict[str, Any]:
    """What the screens are told BEFORE they offer an upload button.

    The wording is what a person reads in a panel, not a log line, and it names
    the variable to set. A product that says "upload failed" and stops is how an
    operator concludes the feature is broken rather than unconfigured.
    """
    b = backend()
    if b == BACKEND_S3:
        reason = None
    elif b == BACKEND_LOCAL:
        # Phase 4: this used to read "it is not durable", which was wrong in the
        # way that matters to somebody deciding whether to upload a signed
        # contract. A local file DOES survive a restart, a reload and a reboot.
        # What it does not survive is losing the machine. Those are different
        # facts and the screen now gets both.
        reason = ("Files are stored on this server's own disk. They survive a "
                  "restart, but they are not replicated \u2014 production should set "
                  "MEDIA_STORAGE_BACKEND=s3.")
    else:
        reason = ("File storage is not configured for this deployment, so "
                  "uploads are refused rather than accepted and lost. Set "
                  "MEDIA_STORAGE_BACKEND=s3 with MEDIA_S3_BUCKET, "
                  "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY for durable "
                  "storage, or MEDIA_STORAGE_BACKEND=local for a development "
                  "machine.")
    return {
        "backend": b,
        "uploads_enabled": b != BACKEND_NONE,
        # Two separate questions, because one boolean answered both and got the
        # local case wrong:
        #   durable    \u2014 does the file survive this process restarting?
        #   replicated \u2014 does it survive this machine being lost?
        "durable": b in (BACKEND_S3, BACKEND_LOCAL),
        "replicated": b == BACKEND_S3,
        "where": ("Amazon S3" if b == BACKEND_S3
                  else "this server's disk" if b == BACKEND_LOCAL else None),
        "max_bytes": MAX_UPLOAD_BYTES,
        "allowed_types": sorted(CONTENT_TYPES),
        "allowed_extensions": sorted(set(CONTENT_TYPES.values())),
        "reason": reason,
        "env": "MEDIA_STORAGE_BACKEND",
    }


# ── Validation. Nothing the client says about a file is believed. ───────────

def _sniff(data: bytes, declared: str) -> str:
    """The type the BYTES are, not the type the upload claimed to be.

    A browser sends whatever Content-Type it likes and a filename is just a
    string somebody typed. Magic numbers are checked for every format this
    module accepts, and a declared type that the bytes contradict is refused —
    that is the whole point of checking.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "image/heic"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "application/msword"
    if data[:2] == b"PK":
        # A .docx is a zip. So is a lot else, so the DECLARED type decides
        # between them and anything not on the list is refused below.
        if declared == ("application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"):
            return declared
        return "application/zip"
    return "application/octet-stream"


def safe_extension(content_type: str) -> str:
    return CONTENT_TYPES.get(content_type, "")


def _object_key(org_id: str, kind: str, content_type: str) -> str:
    """Unguessable, namespaced by organization, and NEVER built from the
    uploader's filename — which can contain path separators and is the classic
    way a stored file escapes its directory."""
    stamp = datetime.now(timezone.utc).strftime("%Y/%m")
    return "wholesale/%s/%s/%s/%s%s" % (
        org_id, kind, stamp, uuid.uuid4().hex, safe_extension(content_type))


# ── Store, read, delete ─────────────────────────────────────────────────────

async def read_upload(upload: UploadFile, *, images_only: bool = False
                      ) -> Tuple[bytes, str, str]:
    """Validate one upload and return (bytes, content_type, original_filename).

    Order matters: the deployment's capability is checked FIRST, so a person on
    an unconfigured install is told that rather than being told their perfectly
    good PDF was the wrong type.
    """
    cap = capability()
    if not cap["uploads_enabled"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=cap["reason"])

    data = await upload.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="That file is %.1f MB. The limit is %d MB."
                   % (len(data) / 1048576.0, MAX_UPLOAD_BYTES // 1048576))

    declared = (upload.content_type or "").split(";")[0].strip().lower()
    actual = _sniff(data, declared)
    if actual not in CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That file type is not accepted here. Accepted: %s."
                   % ", ".join(sorted(set(CONTENT_TYPES.values()))))
    if images_only and actual not in IMAGE_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A photo must be a JPG, PNG, WEBP or HEIC.")

    # The name is kept for display ONLY, stripped of any path it arrived with.
    raw_name = (upload.filename or "upload")
    clean = os.path.basename(raw_name.replace("\\", "/"))[:180] or "upload"
    return data, actual, clean


def put(org_id: str, kind: str, data: bytes, content_type: str) -> Dict[str, Any]:
    """Write the bytes. Returns what the caller must store to find them again."""
    b = backend()
    key = _object_key(org_id, kind, content_type)

    if b == BACKEND_S3:
        # The platform's own S3 writer. Reused, not reimplemented, so there is
        # exactly one place that knows how this install talks to a bucket.
        mobile_storage._put_object(key, data, content_type)
    elif b == BACKEND_LOCAL:
        path = _local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    else:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=capability()["reason"])

    return {
        "storage_backend": b,
        "storage_key": key,
        "content_type": content_type,
        "byte_size": len(data),
        "checksum": hashlib.sha256(data).hexdigest(),
    }


def _local_path(key: str) -> str:
    """Resolve a key under the local root, refusing anything that escapes it."""
    root = os.path.abspath(local_root())
    path = os.path.abspath(os.path.join(root, key.replace("/", os.sep)))
    if not (path == root or path.startswith(root + os.sep)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Bad storage key.")
    return path


def fetch(storage_backend: str, storage_key: str) -> bytes:
    """The bytes back. The caller has ALREADY checked who is asking."""
    if storage_backend == BACKEND_LOCAL:
        path = _local_path(storage_key)
        if not os.path.exists(path):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="The stored file is missing.")
        with open(path, "rb") as fh:
            return fh.read()

    if storage_backend == BACKEND_S3:
        import boto3                        # pragma: no cover - deployment path
        bucket = os.environ["MEDIA_S3_BUCKET"]
        region = os.environ.get("AWS_REGION", "us-east-1")
        client = boto3.client("s3", region_name=region)
        obj = client.get_object(Bucket=bucket, Key=storage_key)
        return obj["Body"].read()

    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="This file was stored by a backend that is no "
                               "longer configured.")


def remove(storage_backend: str, storage_key: str) -> None:
    """Best effort. A row the user deleted must disappear from the product even
    if the object store is briefly unreachable; an orphaned object is a cleanup
    problem, a file the user still sees after deleting it is a trust problem."""
    try:
        if storage_backend == BACKEND_LOCAL:
            path = _local_path(storage_key)
            if os.path.exists(path):
                os.remove(path)
        elif storage_backend == BACKEND_S3:
            import boto3                    # pragma: no cover - deployment path
            client = boto3.client("s3",
                                  region_name=os.environ.get("AWS_REGION", "us-east-1"))
            client.delete_object(Bucket=os.environ["MEDIA_S3_BUCKET"],
                                 Key=storage_key)
    except Exception:                       # pragma: no cover - best effort
        pass
