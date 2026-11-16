"""Estimates, invoices and payment status for a service company's customers.

No payment processor here and none is added: money moves outside EvoSys and
is RECORDED. "Payment reported" (someone says it was paid) and "payment
confirmed" (the company confirmed the money arrived) are separate; only
confirmed payments make an invoice Partially Paid or Paid. Nothing is ever
sent to a customer automatically.
"""
from __future__ import annotations

import io
from datetime import date as date_cls, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.service_ops_models import (ServiceCustomer, ServiceEstimate, ServiceInvoice, ServiceJob,
                                           ServiceLocation, ServiceOpsSettings, ServicePayment)
from app.services.service_ops import core as K

INVOICE_STATUS_LABEL = {"draft": "Draft", "issued": "Issued", "partially_paid": "Partially Paid", "paid": "Paid",
                        "overdue": "Overdue", "void": "Void"}
ESTIMATE_STATUSES = ("draft", "sent", "accepted", "declined", "expired")
BILLABLE_JOB = ("completed", "customer_review", "approved")


def r2(v: float) -> float:
    """Money rounds half-up to the cent, as an invoice reader expects
    (binary floats would turn 20.625 into 20.62)."""
    from decimal import ROUND_HALF_UP, Decimal
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def clean_lines(lines) -> List[Dict[str, Any]]:
    out = []
    for ln in (lines or [])[:60]:
        if not isinstance(ln, dict):
            raise HTTPException(422, "Each line needs a description, quantity and rate")
        desc = K.text(ln.get("description"), 300)
        if not desc:
            raise HTTPException(422, "Each line needs a description")
        q = K.money(ln.get("quantity", 1), "Quantity")
        r = K.money(ln.get("rate"), "Rate")
        if q is None or r is None:
            raise HTTPException(422, "Each line needs a quantity and a rate")
        out.append({"description": desc, "quantity": q, "rate": r, "amount": r2(q * r)})
    if not out:
        raise HTTPException(422, "Add at least one line")
    return out


def totals(lines: List[Dict[str, Any]], discount: float, tax_rate: float) -> Tuple[float, float, float]:
    sub = r2(sum(l["amount"] for l in lines))
    disc = min(r2(discount or 0.0), sub)
    tax = r2((sub - disc) * (tax_rate or 0.0) / 100.0)
    return sub, tax, r2(sub - disc + tax)


def local_today(db: Session, org_id: str, now: Optional[datetime] = None) -> date_cls:
    """The company's calendar date (its own time zone), not the server's UTC date:
    an invoice issued at 7 PM Central on Nov 15 is dated Nov 15."""
    from zoneinfo import ZoneInfo
    tz = K.company_tz(db, org_id)
    return (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)).date()


def _settings(db: Session, org_id: str) -> ServiceOpsSettings:
    s = K.settings(db, org_id)
    if s is None:
        raise HTTPException(402, "Service Operations is not switched on for this company")
    return s


def _next_number(db: Session, org_id: str, kind: str) -> str:
    """Per-company sequence, locked row-wise; the unique key backs it up."""
    s = (db.query(ServiceOpsSettings).filter(ServiceOpsSettings.organization_id == org_id)
         .with_for_update().first())
    if kind == "invoice":
        n = s.next_invoice_seq
        s.next_invoice_seq = n + 1
        return "%s-%d" % (s.invoice_prefix or "INV", n)
    n = s.next_estimate_seq
    s.next_estimate_seq = n + 1
    return "%s-%d" % (s.estimate_prefix or "EST", n)


# ── estimates ─────────────────────────────────────────────────────────────
def estimate_json(e: ServiceEstimate, customer: Optional[ServiceCustomer] = None,
                  today: Optional[date_cls] = None) -> Dict[str, Any]:
    today = today or date_cls.today()
    status = "expired" if e.status == "sent" and e.expires_on and e.expires_on < today else e.status
    return {"id": e.id, "number": e.number, "customer_id": e.customer_id,
            "customer_name": customer.name if customer else None, "location_id": e.location_id,
            "opportunity_id": e.opportunity_id, "lines": K.jload(e.lines, []), "discount": e.discount,
            "tax_rate": e.tax_rate, "subtotal": e.subtotal, "tax": e.tax, "total": e.total, "status": status,
            "expires_on": e.expires_on.isoformat() if e.expires_on else None,
            "accepted_at": K.iso(e.accepted_at), "notes": e.notes, "created_at": K.iso(e.created_at)}


def create_estimate(ctx: K.OpsCtx, data: Dict[str, Any]) -> ServiceEstimate:
    ctx.need("billing", "write estimates")
    db = ctx.db
    s = _settings(db, ctx.org_id)
    cust = K.get_customer(db, ctx.org_id, data.get("customer_id") or "-")
    loc_id = data.get("location_id") or None
    if loc_id and K.get_location(db, ctx.org_id, loc_id).customer_id != cust.id:
        raise HTTPException(422, "That location belongs to a different customer")
    opp_id = data.get("opportunity_id") or None
    if opp_id:
        from app.services.acquisition import engine as AE
        AE.load_opp(db, ctx.org_id, opp_id, client=True)
    lines = clean_lines(data.get("lines"))
    disc = K.money(data.get("discount"), "Discount") or 0.0
    rate = K.money(data.get("tax_rate"), "Tax rate")
    rate = s.default_tax_rate if rate is None else rate
    sub, tax, total = totals(lines, disc, rate)
    exp = data.get("expires_on")
    e = ServiceEstimate(organization_id=ctx.org_id, number=_next_number(db, ctx.org_id, "estimate"),
                        customer_id=cust.id, location_id=loc_id, opportunity_id=opp_id, lines=K.jdump(lines),
                        discount=disc, tax_rate=rate, subtotal=sub, tax=tax, total=total, status="draft",
                        expires_on=date_cls.fromisoformat(exp[:10]) if exp else local_today(db, ctx.org_id) + timedelta(days=30),
                        notes=K.text(data.get("notes"), 2000), created_by_id=ctx.user.id)
    db.add(e)
    db.commit()
    K.audit(db, ctx.org_id, ctx.user, "service_estimate_create", "service_estimate", e.id, {"total": total})
    return e


def set_estimate_status(ctx: K.OpsCtx, eid: str, status: str, *, by_buyer: bool = False) -> ServiceEstimate:
    if not by_buyer:
        ctx.need("billing", "change estimates")
    if status not in ("sent", "accepted", "declined"):
        raise HTTPException(422, "Unknown status")
    e = (ctx.db.query(ServiceEstimate).filter(ServiceEstimate.organization_id == ctx.org_id,
                                              ServiceEstimate.id == eid).first())
    if e is None:
        raise HTTPException(404, "Estimate not found")
    allowed = {"draft": {"sent", "accepted", "declined"}, "sent": {"accepted", "declined"}}
    if status == e.status:
        return e
    if status not in allowed.get(e.status, set()):
        raise HTTPException(409, "A %s estimate can't be marked %s" % (e.status, status))
    if status == "accepted" and e.expires_on and e.expires_on < local_today(ctx.db, ctx.org_id):
        raise HTTPException(409, "This estimate has expired")
    e.status = status
    if status == "accepted":
        e.accepted_at = datetime.utcnow()
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_estimate_status", "service_estimate", e.id,
            {"status": status, "by_buyer": by_buyer})
    return e


def list_estimates(ctx: K.OpsCtx) -> List[Dict[str, Any]]:
    ctx.need("billing", "see estimates")
    rows = (ctx.db.query(ServiceEstimate).filter(ServiceEstimate.organization_id == ctx.org_id)
            .order_by(ServiceEstimate.created_at.desc()).limit(500).all())
    custs = {c.id: c for c in ctx.db.query(ServiceCustomer)
             .filter(ServiceCustomer.id.in_([e.customer_id for e in rows] or ["-"])).all()}
    today = local_today(ctx.db, ctx.org_id)
    return [estimate_json(e, custs.get(e.customer_id), today) for e in rows]


# ── invoices ──────────────────────────────────────────────────────────────
def display_status(i: ServiceInvoice, today: Optional[date_cls] = None) -> str:
    today = today or date_cls.today()
    if i.status in ("issued", "partially_paid") and i.due_date and i.due_date < today:
        return "overdue"
    return i.status


def invoice_json(db: Session, i: ServiceInvoice) -> Dict[str, Any]:
    cust = db.query(ServiceCustomer).filter(ServiceCustomer.id == i.customer_id).first()
    pays = db.query(ServicePayment).filter(ServicePayment.invoice_id == i.id).order_by(ServicePayment.reported_at).all()
    st = display_status(i, local_today(db, i.organization_id))
    return {"id": i.id, "number": i.number, "customer_id": i.customer_id, "customer_name": cust.name if cust else None,
            "location_id": i.location_id, "job_id": i.job_id, "schedule_id": i.schedule_id,
            "opportunity_id": i.opportunity_id, "billing_name": i.billing_name, "billing_address": i.billing_address,
            "lines": K.jload(i.lines, []), "discount": i.discount, "tax_rate": i.tax_rate, "subtotal": i.subtotal,
            "tax": i.tax, "total": i.total, "amount_confirmed": i.amount_confirmed,
            "amount_reported": i.amount_reported, "balance": round(i.total - i.amount_confirmed, 2),
            "due_date": i.due_date.isoformat() if i.due_date else None, "status": st,
            "status_label": INVOICE_STATUS_LABEL.get(st, st), "issued_at": K.iso(i.issued_at),
            "payment_instructions": i.payment_instructions, "void_reason": i.void_reason, "notes": i.notes,
            "payments": [{"id": p.id, "amount": p.amount, "method": p.method, "reference": p.reference,
                          "status": p.status, "reported_at": K.iso(p.reported_at),
                          "confirmed_at": K.iso(p.confirmed_at), "note": p.note} for p in pays],
            "created_at": K.iso(i.created_at)}


def get_invoice(db: Session, org_id: str, iid: str) -> ServiceInvoice:
    i = db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == org_id, ServiceInvoice.id == iid).first()
    if i is None:
        raise HTTPException(404, "Invoice not found")
    return i


def create_invoice(ctx: K.OpsCtx, data: Dict[str, Any]) -> Tuple[ServiceInvoice, bool]:
    """A DRAFT invoice. From a finished job (one invoice per job, ever -
    repeated completion events and retries return the same draft), or with
    lines for a customer. Returns (invoice, created)."""
    ctx.need("billing", "create invoices")
    db = ctx.db
    s = _settings(db, ctx.org_id)
    job = None
    key = None
    if data.get("job_id"):
        from app.services.service_ops import jobs as J
        job = J.get_job(db, ctx.org_id, data["job_id"])
        if job.status not in BILLABLE_JOB:
            raise HTTPException(409, "Only finished work is invoiced (this job is %s)"
                                % J.STATUS_LABEL.get(job.status, job.status))
        key = "job:%s" % job.id
        ex = (db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == ctx.org_id,
                                              ServiceInvoice.generation_key == key).first())
        if ex is not None:
            return ex, False
    cust = K.get_customer(db, ctx.org_id, data.get("customer_id") or (job.customer_id if job else "-"))
    if job and job.customer_id != cust.id:
        raise HTTPException(422, "That job belongs to a different customer")
    if data.get("lines"):
        lines = clean_lines(data.get("lines"))
    elif job is not None:
        if job.price is None:
            raise HTTPException(422, "This job has no price; add invoice lines")
        when = J.local(job.actual_end or job.scheduled_start, job.timezone)
        lines = clean_lines([{"description": "%s%s" % (job.service_type, (" - %s" % when.strftime("%b %d, %Y")) if when else ""),
                              "quantity": 1, "rate": job.price}])
    else:
        lines = clean_lines(None)
    disc = K.money(data.get("discount"), "Discount") or 0.0
    rate = K.money(data.get("tax_rate"), "Tax rate")
    rate = s.default_tax_rate if rate is None else rate
    sub, tax, total = totals(lines, disc, rate)
    inv = ServiceInvoice(organization_id=ctx.org_id, number=_next_number(db, ctx.org_id, "invoice"),
                         customer_id=cust.id, location_id=(job.location_id if job else data.get("location_id")) or None,
                         job_id=job.id if job else None, schedule_id=job.schedule_id if job else None,
                         opportunity_id=(job.opportunity_id if job else data.get("opportunity_id")) or None,
                         generation_key=key, billing_name=cust.billing_name or cust.name,
                         billing_address=cust.billing_address, lines=K.jdump(lines), discount=disc, tax_rate=rate,
                         subtotal=sub, tax=tax, total=total, status="draft",
                         payment_instructions=K.text(data.get("payment_instructions"), 2000) or s.payment_instructions,
                         notes=K.text(data.get("notes"), 2000), created_by_id=ctx.user.id)
    sp = db.begin_nested()
    try:
        db.add(inv)
        db.flush()
    except IntegrityError:
        sp.rollback()
        ex = (db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == ctx.org_id,
                                              ServiceInvoice.generation_key == key).first()) if key else None
        if ex is None:
            raise
        return ex, False
    db.commit()
    K.audit(db, ctx.org_id, ctx.user, "service_invoice_create", "service_invoice", inv.id,
            {"number": inv.number, "total": total, "job_id": inv.job_id})
    return inv, True


def issue_invoice(ctx: K.OpsCtx, iid: str) -> ServiceInvoice:
    """Marks it issued (with a due date). Does NOT email anyone."""
    ctx.need("billing", "issue invoices")
    i = get_invoice(ctx.db, ctx.org_id, iid)
    if i.status == "issued":
        return i
    if i.status != "draft":
        raise HTTPException(409, "Only a draft can be issued")
    s = _settings(ctx.db, ctx.org_id)
    i.status, i.issued_at = "issued", datetime.utcnow()
    i.due_date = local_today(ctx.db, ctx.org_id) + timedelta(days=s.payment_terms_days or 0)
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_invoice_issue", "service_invoice", i.id, {"number": i.number})
    return i


def void_invoice(ctx: K.OpsCtx, iid: str, reason: str) -> ServiceInvoice:
    ctx.need("billing", "void invoices")
    i = get_invoice(ctx.db, ctx.org_id, iid)
    reason = K.text(reason, 500)
    if not reason:
        raise HTTPException(422, "Say why the invoice is void")
    if i.status == "void":
        return i
    if i.amount_confirmed > 0:
        raise HTTPException(409, "This invoice has confirmed payments; it can't be voided")
    i.status, i.voided_at, i.void_reason = "void", datetime.utcnow(), reason
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_invoice_void", "service_invoice", i.id, {"reason": reason})
    return i


def _recompute(db: Session, i: ServiceInvoice) -> None:
    pays = db.query(ServicePayment).filter(ServicePayment.invoice_id == i.id).all()
    i.amount_confirmed = round(sum(p.amount for p in pays if p.status == "confirmed"), 2)
    i.amount_reported = round(sum(p.amount for p in pays if p.status == "reported"), 2)
    if i.status in ("issued", "partially_paid", "paid"):
        if i.amount_confirmed >= i.total - 0.005:
            i.status = "paid"
        elif i.amount_confirmed > 0:
            i.status = "partially_paid"
        else:
            i.status = "issued"


def report_payment(ctx: K.OpsCtx, iid: str, data: Dict[str, Any], *, by_buyer: bool = False) -> ServicePayment:
    """Someone SAYS it was paid. Recorded as reported - not paid."""
    if not by_buyer:
        ctx.need("billing", "record payments")
    i = get_invoice(ctx.db, ctx.org_id, iid)
    if i.status not in ("issued", "partially_paid"):
        raise HTTPException(409, "Payments are recorded against issued invoices")
    amt = K.money(data.get("amount"), "Amount")
    if not amt:
        raise HTTPException(422, "Enter the amount paid")
    method = data.get("method") if data.get("method") in ("check", "ach", "card", "cash", "other") else "other"
    p = ServicePayment(organization_id=ctx.org_id, invoice_id=i.id, amount=amt, method=method,
                       reference=K.text(data.get("reference"), 120), status="reported",
                       reported_by_id=ctx.user.id, note=K.text(data.get("note"), 500))
    ctx.db.add(p)
    ctx.db.flush()
    _recompute(ctx.db, i)
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_payment_reported", "service_invoice", i.id,
            {"amount": amt, "by_buyer": by_buyer})
    return p


def confirm_payment(ctx: K.OpsCtx, iid: str, pid: str, confirm: bool = True) -> ServiceInvoice:
    """The company confirms (or rejects) that reported money really arrived."""
    ctx.need("billing", "confirm payments")
    i = get_invoice(ctx.db, ctx.org_id, iid)
    p = ctx.db.query(ServicePayment).filter(ServicePayment.invoice_id == i.id, ServicePayment.id == pid).first()
    if p is None:
        raise HTTPException(404, "Payment not found")
    if p.status != "reported":
        raise HTTPException(409, "This payment is already %s" % p.status)
    if confirm and i.amount_confirmed + p.amount > i.total + 0.005:
        raise HTTPException(409, "That would confirm more than the invoice total")
    p.status = "confirmed" if confirm else "rejected"
    p.confirmed_by_id, p.confirmed_at = ctx.user.id, datetime.utcnow()
    _recompute(ctx.db, i)
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_payment_%s" % p.status, "service_invoice", i.id, {"amount": p.amount})
    return i


def list_invoices(ctx: K.OpsCtx, status: Optional[str] = None) -> List[Dict[str, Any]]:
    ctx.need("billing", "see invoices")
    rows = (ctx.db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == ctx.org_id)
            .order_by(ServiceInvoice.created_at.desc()).limit(500).all())
    out = [invoice_json(ctx.db, i) for i in rows]
    return [x for x in out if not status or x["status"] == status]


# ── PDF ───────────────────────────────────────────────────────────────────
def invoice_pdf(db: Session, i: ServiceInvoice) -> bytes:
    """A real PDF, built with the platform's existing PDF library (reportlab)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    s = K.settings(db, i.organization_id)
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == i.organization_id).first()
    cust = db.query(ServiceCustomer).filter(ServiceCustomer.id == i.customer_id).first()
    loc = db.query(ServiceLocation).filter(ServiceLocation.id == i.location_id).first() if i.location_id else None
    st = getSampleStyleSheet()
    e = lambda v: escape(str(v or ""))                                # noqa: E731
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title="Invoice %s" % i.number, author=(s.business_name if s else None) or org.name)
    company = (s.business_name if s and s.business_name else org.name)
    parts = [Paragraph("<b>%s</b>" % e(company), st["Title"])]
    contact = " · ".join(x for x in ((s.billing_email if s else None), (s.billing_phone if s else None)) if x)
    if s and s.billing_address:
        parts.append(Paragraph(e(s.billing_address).replace("\n", "<br/>"), st["Normal"]))
    if contact:
        parts.append(Paragraph(e(contact), st["Normal"]))
    parts.append(Spacer(1, 12))
    from zoneinfo import ZoneInfo
    tz = K.company_tz(db, i.organization_id)
    status = INVOICE_STATUS_LABEL.get(display_status(i, local_today(db, i.organization_id)), i.status).upper()
    issued_local = (i.issued_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)) if i.issued_at else None)
    head = [["INVOICE", i.number], ["Status", status],
            ["Issued", issued_local.strftime("%b %d, %Y") if issued_local else "Not issued (draft)"],
            ["Due", i.due_date.strftime("%b %d, %Y") if i.due_date else "-"]]
    t = Table(head, colWidths=[1.3 * inch, 2.6 * inch])
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 10)]))
    parts += [t, Spacer(1, 10), Paragraph("<b>Bill to</b>", st["Normal"]),
              Paragraph(e(i.billing_name or (cust.name if cust else "")), st["Normal"])]
    if i.billing_address:
        parts.append(Paragraph(e(i.billing_address).replace("\n", "<br/>"), st["Normal"]))
    if loc is not None:
        parts.append(Paragraph("Service location: %s%s" % (e(loc.name), (", " + e(loc.address)) if loc.address else ""),
                               st["Normal"]))
    parts.append(Spacer(1, 12))
    rows = [["Description", "Qty", "Rate", "Amount"]]
    for ln in K.jload(i.lines, []):
        rows.append([Paragraph(e(ln["description"]), st["Normal"]), "%g" % ln["quantity"], "$%.2f" % ln["rate"],
                     "$%.2f" % ln["amount"]])
    rows.append(["", "", "Subtotal", "$%.2f" % i.subtotal])
    if i.discount:
        rows.append(["", "", "Discount", "-$%.2f" % i.discount])
    if i.tax:
        rows.append(["", "", "Tax (%g%%)" % i.tax_rate, "$%.2f" % i.tax])
    rows.append(["", "", "Total", "$%.2f" % i.total])
    if i.amount_confirmed:
        rows.append(["", "", "Paid (confirmed)", "-$%.2f" % i.amount_confirmed])
    rows.append(["", "", "Balance due", "$%.2f" % round(i.total - i.amount_confirmed, 2)])
    lt = Table(rows, colWidths=[3.9 * inch, 0.6 * inch, 1.1 * inch, 1.2 * inch])
    lt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a2550")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LINEBELOW", (0, 0), (-1, len(K.jload(i.lines, []))), 0.25, colors.HexColor("#cccccc")),
                            ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold")]))
    parts += [lt, Spacer(1, 14)]
    if i.payment_instructions:
        parts += [Paragraph("<b>How to pay</b>", st["Normal"]),
                  Paragraph(e(i.payment_instructions).replace("\n", "<br/>"), st["Normal"])]
    if i.notes:
        parts += [Spacer(1, 8), Paragraph(e(i.notes).replace("\n", "<br/>"), st["Normal"])]
    if i.status == "void":
        parts += [Spacer(1, 10), Paragraph("<b>VOID</b> - %s" % e(i.void_reason), st["Normal"])]
    doc.build(parts)
    return buf.getvalue()
