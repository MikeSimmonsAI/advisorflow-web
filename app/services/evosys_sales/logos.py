"""The prospect's own logo, for personal outreach (owner request 2026-11-17).

Mike collects cleaning businesses from their Facebook / Instagram ads. The
logo is cut out of the ad they ran, saved on the lead, and shown at the top of
the emails we send them, so the business sees at once the message is about
them and not a mass blast.

Rules this module keeps:
  * The picture is stored on the lead itself (a small PNG), not on a third-party
    host, and is served from our own API.
  * The public link names one lead through a signed token. Lead ids cannot be
    guessed from it and a made-up token is refused.
  * Only a logo marked usable ("clean" or "name_only") goes into an email.
    A "rough" one is kept for reference but never sent - a blurry logo in a
    personal email does more harm than none.
  * Every picture is re-drawn as a PNG no bigger than MAX_W x MAX_H, so what we
    serve is always a small, plain image whatever was uploaded.
"""
from __future__ import annotations

import base64
import io
import os
from typing import Optional

QUALITIES = ("clean", "name_only", "rough")
USABLE = ("clean", "name_only")
MAX_W, MAX_H = 600, 300
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_STORED_BYTES = 400 * 1024


class LogoError(ValueError):
    pass


def normalize(raw: bytes) -> bytes:
    """Any common picture in, a small PNG out. Raises LogoError when the bytes
    are not a picture or come out too large."""
    if not raw:
        raise LogoError("No picture was sent.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise LogoError("That picture is too large (over 5 MB).")
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:  # noqa: BLE001 - anything PIL cannot read is not a picture
        raise LogoError("That file is not a picture we can read.")
    im = im.convert("RGBA")
    im.thumbnail((MAX_W, MAX_H))
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    data = out.getvalue()
    if len(data) > MAX_STORED_BYTES:
        raise LogoError("That picture is still too large after resizing.")
    return data


def decode_upload(image_base64: str) -> bytes:
    s = (image_base64 or "").strip()
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s, validate=False)
    except Exception:  # noqa: BLE001
        raise LogoError("The picture could not be read.")


def make_token(opp_id: str) -> str:
    from app.services.programs import unsubscribe as _u
    return _u.make_token("evl:" + opp_id)


def read_token(token: str) -> Optional[str]:
    from app.services.programs import unsubscribe as _u
    v = _u.read_token(token or "")
    return v[4:] if v and v.startswith("evl:") else None


def _base() -> str:
    base = (os.environ.get("PROGRAM_ASSET_BASE_URL") or "").strip().rstrip("/")
    if not base:
        from app.services.twilio_callbacks import public_api_base
        base = public_api_base()
    return base


def has_logo(opp) -> bool:
    # logo_updated_at is set whenever a logo is stored and cleared when it is
    # removed, so this never has to load the picture bytes (they are deferred).
    return bool(getattr(opp, "logo_updated_at", None))


def usable(opp) -> bool:
    return has_logo(opp) and (getattr(opp, "logo_quality", None) or "") in USABLE


def public_url(opp) -> Optional[str]:
    """The link an email may use, or None when this lead has no usable logo."""
    if not usable(opp):
        return None
    stamp = getattr(opp, "logo_updated_at", None)
    v = "?v=%d" % int(stamp.timestamp()) if stamp else ""
    return "%s/evosys-sales-public/logo/%s.png%s" % (_base(), make_token(opp.id), v)


def view_url(opp) -> Optional[str]:
    """For the app: shows any stored logo, rough ones included."""
    if not has_logo(opp):
        return None
    stamp = getattr(opp, "logo_updated_at", None)
    v = "?v=%d" % int(stamp.timestamp()) if stamp else ""
    return "%s/evosys-sales-public/logo/%s.png%s" % (_base(), make_token(opp.id), v)


def email_header_html(opp) -> str:
    """The block that sits above the first paragraph, or "" with no logo."""
    import html as _html
    url = public_url(opp)
    if not url:
        return ""
    alt = _html.escape((getattr(opp, "company_name", None) or "").strip())
    return ('<p style="margin:0 0 16px 0"><img src="%s" alt="%s" '
            'style="display:block;max-width:220px;max-height:90px;height:auto;border:0"></p>'
            % (_html.escape(url, quote=True), alt))


def store(opp, png: bytes, quality: str, now) -> None:
    if quality not in QUALITIES:
        raise LogoError("Quality must be one of: clean, name_only, rough.")
    opp.logo_png, opp.logo_quality, opp.logo_updated_at = png, quality, now


def clear(opp) -> None:
    opp.logo_png, opp.logo_quality, opp.logo_updated_at = None, None, None
