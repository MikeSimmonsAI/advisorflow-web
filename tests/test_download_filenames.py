"""A FILE THAT CAN BE UPLOADED CAN BE DOWNLOADED - whatever its name.

Response headers are Latin-1. Before app/utils/content_disposition.py the
stored name went straight into `filename="..."`, so an upload called
"合同 – résumé 🙂.pdf" was accepted and then 500'd on every download, and a
name with a quote or newline could break the header. Every download route now
uses the one helper; this file pins the helper and one real route.
"""
from urllib.parse import unquote

from app.services import support_tickets
from app.utils.content_disposition import content_disposition
from tests.test_support_security import (  # noqa: F401  (fixtures)
    brand_a, headers_a, org_a, user_a, _raise,
)

NAME = "合同 – résumé 🙂.pdf"


def _star(header):
    return unquote(header.split("filename*=UTF-8''", 1)[1])


def test_helper_keeps_the_real_name_and_an_ascii_fallback():
    h = content_disposition(NAME)
    h.encode("latin-1")                                   # would raise before the fix
    assert h.startswith("attachment; filename=\"")
    assert _star(h) == NAME


def test_helper_cannot_be_broken_out_of():
    h = content_disposition('a"b\\c\r\nSet-Cookie: x=1.txt', "inline")
    assert h.startswith("inline; ")
    assert "\r" not in h and "\n" not in h
    fallback = h.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in fallback and "\\" not in fallback
    assert content_disposition(None) .startswith('attachment; filename="download"')
    assert content_disposition("x", "evil") .startswith("attachment;")


def test_a_support_attachment_with_a_unicode_name_downloads(client, db_session,
                                                           org_a, user_a, headers_a):
    ticket = _raise(db_session, org_a, user_a)
    att = support_tickets.add_attachment(db_session, ticket, filename=NAME,
                                         content_type="application/pdf",
                                         data=b"%PDF-1.4 test", user=user_a)
    db_session.commit()
    r = client.get("/support/tickets/%s/attachments/%s" % (ticket.id, att.id), headers=headers_a)
    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.4 test"
    assert _star(r.headers["content-disposition"]) == NAME
    assert r.headers["x-content-type-options"] == "nosniff"


def test_no_download_route_formats_a_raw_filename_header():
    import pathlib
    import re
    bad = []
    for p in pathlib.Path("app/routers").rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"filename=\\?\"%s|filename=\"\{", src):
            bad.append("%s:%d" % (p, src[:m.start()].count("\n") + 1))
    assert not bad, bad
