"""The signed contract as a PDF: the document, every signature, and a
certificate-of-completion page (who signed, when, from where, how they were
verified, and the fingerprint of the exact document they saw).

The document HTML comes from wholesale_contract_docs (tags we control: h1,
h2, p, b, span.blank, div.sig). It is converted here to ReportLab flowables;
the HTML signature lines are replaced by the real signatures.
"""
from __future__ import annotations

import html as _h
import io
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

try:
    from zoneinfo import ZoneInfo
    _CENTRAL = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - tzdata missing
    _CENTRAL = None

_SS = getSampleStyleSheet()
ST = {
    "h1": ParagraphStyle("h1", parent=_SS["Title"], fontName="Times-Bold", fontSize=16, leading=20, spaceAfter=2),
    "sub": ParagraphStyle("sub", parent=_SS["Normal"], fontName="Times-Roman", fontSize=10, alignment=TA_CENTER,
                          textColor=colors.HexColor("#444444"), spaceAfter=12),
    "h2": ParagraphStyle("h2", parent=_SS["Normal"], fontName="Times-Bold", fontSize=11.5, leading=14,
                         spaceBefore=8, spaceAfter=3),
    "p": ParagraphStyle("p", parent=_SS["Normal"], fontName="Times-Roman", fontSize=11, leading=14.5, spaceAfter=5),
    "small": ParagraphStyle("small", parent=_SS["Normal"], fontName="Helvetica", fontSize=8.5, leading=11),
    "cert_h": ParagraphStyle("cert_h", parent=_SS["Title"], fontName="Helvetica-Bold", fontSize=15, leading=19),
    "cert": ParagraphStyle("cert", parent=_SS["Normal"], fontName="Helvetica", fontSize=9, leading=12),
    "typed": ParagraphStyle("typed", parent=_SS["Normal"], fontName="Times-BoldItalic", fontSize=22, leading=26,
                            textColor=colors.HexColor("#1e3a8a")),
}


def fmt_time(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    utc = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    if _CENTRAL is not None:
        loc = utc.astimezone(_CENTRAL)
        return "%s (%s UTC)" % (loc.strftime("%b %d, %Y %I:%M:%S %p %Z"), utc.strftime("%H:%M:%S"))
    return utc.strftime("%b %d, %Y %H:%M:%S UTC")


class _Blocks(HTMLParser):
    """Our contract HTML -> [(style, markup)], skipping the signature block."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: List[tuple] = []
        self.cur: Optional[List[str]] = None
        self.style = "p"
        self.skip = 0          # inside <div class="sig">, <style>, <title>
        self.spans: List[bool] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip:
            if tag == "div":
                self.skip += 1
            return
        if tag in ("style", "title") or (tag == "div" and "sig" in (a.get("class") or "").split()):
            self.skip = 1
            return
        if tag in ("h1", "h2", "p"):
            self.cur, self.style = [], ("sub" if "sub" in (a.get("class") or "") else tag)
        elif tag == "b" and self.cur is not None:
            self.cur.append("<b>")
        elif tag == "span":
            blank = self.cur is not None and "blank" in (a.get("class") or "")
            self.spans.append(blank)
            if blank:
                self.cur.append("<font color='#92400e'>[")

    def handle_endtag(self, tag):
        if self.skip:
            if tag in ("div", "style", "title"):
                self.skip -= 1
            return
        if tag in ("h1", "h2", "p") and self.cur is not None:
            text = re.sub(r"\s+", " ", "".join(self.cur)).strip()
            if text:
                self.blocks.append((self.style, text))
            self.cur = None
        elif tag == "b" and self.cur is not None:
            self.cur.append("</b>")
        elif tag == "span":
            if self.spans and self.spans.pop() and self.cur is not None:
                self.cur.append("]</font>")

    def handle_data(self, data):
        if self.cur is not None and not self.skip:
            self.cur.append(_h.escape(data, quote=False))


def html_blocks(document_html: str) -> List[tuple]:
    p = _Blocks()
    p.feed(document_html)
    p.close()
    return p.blocks


def _mask(email: str) -> str:
    name, _, dom = (email or "").partition("@")
    return (name[:2] + "***@" + dom) if dom else email


def _signature_cell(s: Dict[str, Any]):
    if s.get("signature_kind") == "drawn" and s.get("signature_image"):
        try:
            img = Image(io.BytesIO(s["signature_image"]))
            ratio = img.imageHeight / float(img.imageWidth or 1)
            w = 2.4 * inch
            img.drawWidth, img.drawHeight = w, min(w * ratio, 0.9 * inch)
            return img
        except Exception:
            pass
    return Paragraph(_h.escape(s.get("signature_text") or s.get("name") or ""), ST["typed"])


def _signature_block(s: Dict[str, Any]):
    lines = [
        "<b>%s</b>: %s" % (_h.escape(s["role"]), _h.escape(s.get("name") or s.get("signature_text") or "")),
        "Signed electronically %s" % _h.escape(fmt_time(s.get("signed_at"))),
        "Email verified (%s) &middot; IP %s" % (_h.escape(_mask(s.get("email") or "")), _h.escape(s.get("sign_ip") or "")),
    ]
    t = Table([[_signature_cell(s)], [Paragraph("<br/>".join(lines), ST["small"])]], colWidths=[3.3 * inch])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.8, colors.HexColor("#555555")),
                           ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (0, 0), 2)]))
    return t


ACTION_LABEL = {
    "sent": "Envelope created and sent", "link_sent": "Signing link emailed", "viewed": "Opened the document",
    "code_sent": "Verification code emailed", "verified": "Email verified with one-time code",
    "consented": "Agreed to sign electronically", "signed": "Signed", "declined": "Declined to sign",
    "voided": "Voided by sender", "completed": "All parties signed; document sealed",
    "reminder": "Reminder sent", "code_failed": "Wrong verification code entered",
}


def render_signed_pdf(envelope: Dict[str, Any], signers: List[Dict[str, Any]],
                      events: List[Dict[str, Any]]) -> bytes:
    """The final PDF. `envelope`, `signers`, `events` are plain dicts (see
    evosys_esign.snapshot) so this can be rendered and tested without a DB."""
    buf = io.BytesIO()
    title = envelope.get("title") or "Document"
    env_id = envelope.get("id") or ""

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(0.75 * inch, 0.5 * inch,
                          "EvoSys e-signature  |  Envelope %s  |  Document fingerprint %s..." % (
                              env_id, (envelope.get("document_sha256") or "")[:16]))
        canvas.drawRightString(7.75 * inch, 0.5 * inch, "Page %d" % doc.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.85 * inch, rightMargin=0.85 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.8 * inch, title=title,
                            author="EvoSys e-signature", subject="Envelope %s" % env_id)
    story: List[Any] = []
    for style, markup in html_blocks(envelope.get("document_html") or ""):
        story.append(Paragraph(markup, ST.get(style, ST["p"])))
    story.append(Spacer(1, 14))
    blocks = [_signature_block(s) for s in signers]
    rows = [blocks[i:i + 2] + ([""] if len(blocks[i:i + 2]) == 1 else []) for i in range(0, len(blocks), 2)]
    if rows:
        sig = Table(rows, colWidths=[3.45 * inch, 3.45 * inch], hAlign="LEFT")
        sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("TOPPADDING", (0, 0), (-1, -1), 10)]))
        story.append(KeepTogether([sig]))

    # ── certificate of completion ──
    story.append(PageBreak())
    story.append(Paragraph("Certificate of Completion", ST["cert_h"]))
    story.append(Paragraph("EvoSys electronic signature record", ST["sub"]))
    info = [
        ["Document", title],
        ["Envelope ID", env_id],
        ["Status", "Completed - every party signed" if envelope.get("status") == "completed"
         else (envelope.get("status") or "").title()],
        ["Sent by", "%s (%s)" % (envelope.get("sender_name") or "", envelope.get("sender_email") or "")],
        ["Sent", fmt_time(envelope.get("created_at"))],
        ["Completed", fmt_time(envelope.get("completed_at"))],
        ["Document fingerprint (SHA-256 of the exact document each signer reviewed)",
         envelope.get("document_sha256") or ""],
    ]
    t = Table([[Paragraph("<b>%s</b>" % _h.escape(k), ST["cert"]), Paragraph(_h.escape(str(v)), ST["cert"])]
               for k, v in info], colWidths=[2.3 * inch, 4.6 * inch])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 12), Paragraph("<b>Signers</b>", ST["h2"])]
    srows = [[Paragraph("<b>%s</b>" % x, ST["cert"]) for x in ("Signer", "Verification", "Signed", "From")]]
    for s in signers:
        srows.append([
            Paragraph("%s<br/>%s<br/>%s" % (_h.escape(s.get("name") or ""), _h.escape(s.get("email") or ""),
                                            _h.escape(s["role"])), ST["cert"]),
            Paragraph("Email one-time code, %s<br/>Consent to e-sign: %s<br/>Signature: %s" % (
                _h.escape(fmt_time(s.get("verified_at"))), _h.escape(fmt_time(s.get("consent_at"))),
                "drawn" if s.get("signature_kind") == "drawn" else "typed name"), ST["cert"]),
            Paragraph(_h.escape(fmt_time(s.get("signed_at"))), ST["cert"]),
            Paragraph("IP %s<br/>%s" % (_h.escape(s.get("sign_ip") or ""),
                                        _h.escape((s.get("sign_user_agent") or "")[:90])), ST["cert"]),
        ])
    st = Table(srows, colWidths=[1.9 * inch, 2.0 * inch, 1.4 * inch, 1.6 * inch], repeatRows=1)
    st.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [st, Spacer(1, 12), Paragraph("<b>Audit trail</b>", ST["h2"])]
    names = {s.get("id"): "%s (%s)" % (s.get("name") or s.get("email"), s["role"]) for s in signers}
    erows = [[Paragraph("<b>%s</b>" % x, ST["cert"]) for x in ("When", "What", "Who", "IP")]]
    for e in events:
        erows.append([Paragraph(_h.escape(fmt_time(e.get("at"))), ST["cert"]),
                      Paragraph(_h.escape(ACTION_LABEL.get(e.get("action"), e.get("action") or "")), ST["cert"]),
                      Paragraph(_h.escape(names.get(e.get("signer_id")) or (
                          "Sender" if e.get("action") in ("sent", "voided") else "EvoSys")), ST["cert"]),
                      Paragraph(_h.escape(e.get("ip") or ""), ST["cert"])])
    et = Table(erows, colWidths=[2.0 * inch, 2.2 * inch, 1.7 * inch, 1.0 * inch], repeatRows=1)
    et.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [et, Spacer(1, 12), Paragraph(
        "Each signer opened a private link sent to their email address, confirmed a one-time code sent to "
        "that address, agreed to use an electronic signature and to receive this record electronically, "
        "and signed. Signatures are applied under the federal ESIGN Act (15 U.S.C. 7001) and the Texas "
        "Uniform Electronic Transactions Act (Bus. & Com. Code ch. 322). The SHA-256 fingerprint of this "
        "final PDF is kept by EvoSys; any change to the file changes that fingerprint.", ST["cert"])]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
