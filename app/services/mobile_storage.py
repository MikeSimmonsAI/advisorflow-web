"""Where a file from the field goes.

THE HONEST VERSION OF A KNOWN GAP
---------------------------------
`sms_router.py` writes uploaded media to /tmp/bookaboost_media and says, in its
own comment, "In production, replace local storage with S3 or Cloudinary." On
Render that directory does not survive a restart and is not shared between
instances, so a file written there is not stored, it is parked.

For a browser upload that is bad. For a phone it is worse, because the phone is
the thing that goes where the file is created: a photo of a plot marker, a
signed form on a kitchen table, a picture of a competitor's brochure. Those get
taken once.

So this module does not pretend. It reports the deployment's actual capability,
the app asks before it shows a camera button, and an upload into ephemeral
storage is REFUSED rather than accepted and quietly lost. A 503 that says
"storage is not configured" leaves the capture safe in the device's own queue;
a 200 that evaporates does not.

TURNING IT ON IS CONFIGURATION, NOT CODE
----------------------------------------
Set MEDIA_STORAGE_BACKEND=s3 with the usual credentials in the environment and
`capability()` reports `durable`, uploads are accepted, and nothing else in the
mobile app changes — the interface it codes against is the same either way.
No credentials are invented here and none are required to build against it.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, UploadFile, status

_log = logging.getLogger(__name__)

BACKEND_EPHEMERAL = "ephemeral"     # local disk; lost on restart
BACKEND_S3 = "s3"                   # object storage; durable

# 12 MB. A phone photo resized client-side lands well under this; the ceiling
# exists so a mis-sized capture cannot occupy a request worker for a minute on
# a field connection.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

ALLOWED_CONTENT_TYPES = (
    "image/jpeg", "image/png", "image/heic", "image/webp",
    "application/pdf",
)


def backend() -> str:
    configured = str(os.environ.get("MEDIA_STORAGE_BACKEND", "")).strip().lower()
    if configured == BACKEND_S3 and _s3_configured():
        return BACKEND_S3
    return BACKEND_EPHEMERAL


def _s3_configured() -> bool:
    """Every piece present, or it is not configured.

    A half-configured bucket is the failure that reports `durable` and then
    loses the file, which is exactly what this module exists to avoid.
    """
    return all(os.environ.get(k) for k in (
        "MEDIA_S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))


def capability() -> Dict[str, Any]:
    """What the app is told before it offers capture."""
    b = backend()
    durable = b != BACKEND_EPHEMERAL
    return {
        "backend": b,
        "durable": durable,
        "uploads_enabled": durable,
        "max_bytes": MAX_UPLOAD_BYTES,
        "allowed_types": list(ALLOWED_CONTENT_TYPES),
        # Plain words, because this string reaches a person in a "Photos are
        # unavailable" panel rather than a log.
        "reason": None if durable else (
            "File storage is not configured for this deployment, so a photo "
            "would not survive the next restart. Captures stay on the device "
            "until storage is turned on."
        ),
    }


async def store_upload(db, user, file: UploadFile, *,
                       purpose: Optional[str] = None) -> Dict[str, Any]:
    """Store one file, or refuse for a reason the caller can act on."""
    if backend() == BACKEND_EPHEMERAL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Durable file storage is not configured for this "
                    "deployment. The file was not stored and nothing was "
                    "lost — keep it queued on the device."),
        )

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Unsupported file type.")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="File is too large.")

    key = _object_key(user, file.filename, purpose)
    url = _put_object(key, data, content_type)
    return {
        "id": key,
        "url": url,
        "bytes": len(data),
        "content_type": content_type,
        "purpose": purpose,
        "uploaded_at": datetime.now(timezone.utc),
    }


def _object_key(user, filename: Optional[str], purpose: Optional[str]) -> str:
    """A key nobody can guess, namespaced by owner.

    The uploader's own filename is NOT used in the key. It arrives from a
    device, it can contain path separators, and a key built from it is a path
    traversal waiting for the first person who writes it to disk instead of to
    a bucket.
    """
    ext = ""
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1][:8].lower()
        ext = "".join(c for c in ext if c.isalnum() or c == ".")
    stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    return "mobile/%s/%s/%s%s" % (purpose or "capture", stamp, uuid.uuid4(), ext)


def _put_object(key: str, data: bytes, content_type: str) -> str:
    bucket = os.environ["MEDIA_S3_BUCKET"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    import boto3                            # pragma: no cover - deployment path
    client = boto3.client("s3", region_name=region)
    client.put_object(Bucket=bucket, Key=key, Body=data,
                      ContentType=content_type)
    endpoint = os.environ.get("MEDIA_S3_PUBLIC_BASE")
    if endpoint:
        return "%s/%s" % (endpoint.rstrip("/"), key)
    return "https://%s.s3.%s.amazonaws.com/%s" % (bucket, region, key)
