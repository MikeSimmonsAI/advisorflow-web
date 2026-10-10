"""Machine feed for public-record files a publisher refuses to serve to our
servers but serves to the operator's own computer (TAD).

    POST /source-feeds/{source_key}
      X-Source-Token: <EVOSENSE_SOURCE_UPLOAD_TOKEN>
      X-Source-Date:  <the publisher's Last-Modified>   (optional)
      body:           the publisher's zip, unchanged (application/zip)

No login: the shared token in the backend's environment is the authorization,
compared in constant time. With the setting absent the route answers 503 and
accepts nothing. It can only replace the copy of a source listed in
providers.UPLOADABLE, and the file must pass that source's validation.
"""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services.evosense import providers as PV

router = APIRouter(prefix="/source-feeds", tags=["evosense-source-feed"])


def _token_ok(given: str) -> bool:
    want = (os.environ.get("EVOSENSE_SOURCE_UPLOAD_TOKEN") or "").strip()
    if len(want) < 24:
        raise HTTPException(status_code=503, detail="Source feed is not configured on this server.")
    return hmac.compare_digest((given or "").strip().encode(), want.encode())


@router.post("/{source_key}")
async def feed(source_key: str, request: Request, db: Session = Depends(get_db)):
    if not _token_ok(request.headers.get("x-source-token", "")):
        raise HTTPException(status_code=401, detail="Bad source token.")
    if source_key not in PV.UPLOADABLE:
        raise HTTPException(status_code=404, detail="This source does not take uploads.")
    from app.routers.evosense_router import _save_source_upload
    stream = request.stream()

    async def read_chunk(_n):
        try:
            return await stream.__anext__()
        except StopAsyncIteration:
            return b""
    out = await _save_source_upload(db, source_key, read_chunk, via="feed", by="feed job",
                                    source_date=(request.headers.get("x-source-date") or "")[:64] or None)
    return {k: out.get(k) for k in ("key", "ok", "size", "uploaded_at", "source_date", "columns")}
