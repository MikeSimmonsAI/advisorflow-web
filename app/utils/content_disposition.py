"""Content-Disposition for a stored file's ORIGINAL name - safely.

Response headers are Latin-1. A customer's upload named "Résumé – final.pdf"
or "合同.pdf" or with an emoji put straight into `filename="..."` raised
UnicodeEncodeError while building the response, so the file could be uploaded
but never downloaded (a 500 on every attempt). A name containing a quote or a
newline could also break the header.

This emits the RFC 6266 / RFC 5987 pair every current browser understands:

    attachment; filename="Resume - final.pdf"; filename*=UTF-8''R%C3%A9sum%C3%A9%20%E2%80%93%20final.pdf

`filename` is a plain-ASCII fallback (quotes, backslashes and control
characters removed); `filename*` carries the real name, percent-encoded.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

_CTRL = re.compile(r'[\x00-\x1f\x7f"\\]')


def _ascii_fallback(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    folded = _CTRL.sub("", folded).strip()
    return folded or "download"


def content_disposition(filename: str | None, disposition: str = "attachment") -> str:
    disposition = "inline" if disposition == "inline" else "attachment"
    name = (filename or "download").replace("\r", " ").replace("\n", " ").strip() or "download"
    name = name[:200]
    return '%s; filename="%s"; filename*=UTF-8\'\'%s' % (
        disposition, _ascii_fallback(name), quote(name, safe=""))
