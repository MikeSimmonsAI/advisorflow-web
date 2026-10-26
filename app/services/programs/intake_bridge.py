"""The Intelligent Lead Importer -> SCI bridge.

The universal importer (app/services/intake) reads, maps, analyzes and lets a
person review a file. For an SCI batch (engine.is_sci_batch) it never writes
CRM contacts or leads: this module turns the batch's REVIEWED staged rows back
into the CSV that SCI's own loader (programs/importer.py) has always read, and
program_router hands that CSV to `_load_source`.

ORIGINAL VALUES. Every cell comes from the row's raw_data - exactly what the
customer's file said - through the batch's column mapping. Nothing normalized
by the importer is written into SCI's provenance (ProgramSourceRecord.raw_json).
Columns the mapping does not route to an SCI column are carried along under
their own header, so the stored row keeps everything that was supplied.

WHAT A REVIEWER LEFT OUT STAYS OUT:
    skipped    a person pressed Skip (row or bulk)
    skip       a duplicate / existing match a person chose to skip
    invalid    nothing usable on the row
    blocked    matches a do-not-contact family; left untouched (a person who
               approves it on Review sends it through)
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.import_models import ImportBatch, ImportStagedRow
from app.models.intake_models import DuplicateResolution, IntakeStatus
from app.services.intake import fields as F
from app.services.programs.importer import COL, COLUMN_ALIASES, IMPORT_FLAG_COL

# SCI column -> the mapping targets that feed it, in preference order.
# ("*", key) matches a custom / vertical / source column with that key.
SCI_COLUMNS: List[Tuple[str, List[Tuple[str, str]]]] = [
    (COL["lead_id"], [(F.KIND_STANDARD, "source_record_id")]),
    (COL["first"], [(F.KIND_STANDARD, "first_name")]),
    (COL["last"], [(F.KIND_STANDARD, "last_name")]),
    (COL["phone"], [(F.KIND_STANDARD, "phone"), (F.KIND_STANDARD, "mobile_phone")]),
    (COL["email"], [(F.KIND_STANDARD, "email")]),
    (COL["location"], [("*", "location_friendly_name")]),
    (COL["campaign"], [("*", "primary_campaign")]),
    (COL["status"], [("*", "lead_status"), (F.KIND_STANDARD, "classification")]),
    (COL["last_activity_date"], [(F.KIND_STANDARD, "last_activity_date")]),
    (COL["last_activity"], [("*", "last_activity")]),
    (COL["channel"], [("*", "campaign_channel")]),
    (COL["created"], [(F.KIND_STANDARD, "source_created_at")]),
    (COL["owner"], [(F.KIND_STANDARD, "owner_name")]),
    (COL["manager"], [("*", "lead_owner_manager")]),
]
# Always written (blank when the file had none); the rest only when mapped.
ALWAYS = (COL["lead_id"], COL["first"], COL["last"], COL["phone"], COL["email"],
          COL["location"], COL["campaign"], COL["status"], COL["last_activity_date"])
# Without this SCI cannot place a family: the screen says so before loading.
# (#385: a missing Lead ID no longer blocks - SCI generates one, marked as
# generated, so a normal export loads without anyone inventing IDs.)
REQUIRED = (COL["location"],)

EXCLUDED_LABELS = {
    "skipped": "Skipped by the reviewer",
    "skip_duplicate": "Duplicate the reviewer chose to skip",
    "invalid": "Nothing usable on the row",
    "failed": "Could not be read",
    "blocked": "Matches a do-not-contact family (left untouched)",
}


def _j(s, d):
    try:
        return json.loads(s) if s else d
    except Exception:  # noqa: BLE001
        return d


def resolve_columns(headers: List[str], mapping: Dict[str, dict]) -> Dict[str, str]:
    """SCI column -> the header in THIS file that feeds it."""
    out: Dict[str, str] = {}
    used = set()
    for sci_col, wants in SCI_COLUMNS:
        hit = None
        for kind, target in wants:
            for h in headers:
                if h in used:
                    continue
                m = mapping.get(h) or {}
                if m.get("target") != target:
                    continue
                if kind == "*" and m.get("kind") in (F.KIND_CUSTOM, F.KIND_VERTICAL, F.KIND_SOURCE):
                    hit = h
                elif m.get("kind") == kind:
                    hit = h
                if hit:
                    break
            if hit:
                break
        if hit is None:
            # The file already uses SCI's own header - or one of the names that
            # column goes by in a normal export ("Nearest/Preferred Location").
            key = next((k for k, v in COL.items() if v == sci_col), None)
            for name in (sci_col,) + tuple(COLUMN_ALIASES.get(key, ())):
                h = next((x for x in headers if x.strip().lower() == name.lower()), None)
                if h and h not in used and (mapping.get(h) or {}).get("kind") != F.KIND_IGNORE:
                    hit = h
                    break
        if hit is not None:
            out[sci_col] = hit
            used.add(hit)
    return out


def exclusion_reason(r: ImportStagedRow) -> Optional[str]:
    """Only what a PERSON chose to leave out, or a row that could not be read.
    #385: every other source record goes to SCI and is kept - a match to a
    do-not-contact family is held there (never contacted), a match to an
    existing family is linked to that family by SCI itself, and a row with
    nothing usable is held for review."""
    st = r.intake_status
    if st == IntakeStatus.SKIPPED and r.reviewed_at is not None:
        return "skipped"
    if r.duplicate_resolution == DuplicateResolution.SKIP_INCOMING:
        return "skip_duplicate"             # a person chose to skip this duplicate
    if st == IntakeStatus.FAILED:
        return "failed"
    return None


def import_flag(r: ImportStagedRow) -> str:
    """Why SCI must hold this row rather than work it ('' = nothing to hold)."""
    st = r.intake_status
    if st == IntakeStatus.BLOCKED:
        return "do_not_contact_match"
    if st == IntakeStatus.INVALID:
        return "invalid"
    return ""


def build(db: Session, batch: ImportBatch) -> dict:
    """The SCI CSV for this batch plus everything the SCI step shows about it."""
    headers = _j(batch.headers_json, [])
    mapping = _j(batch.mapping_json, {})
    cols = resolve_columns(headers, mapping)
    rows = (db.query(ImportStagedRow)
            .filter(ImportStagedRow.batch_id == batch.id,
                    ImportStagedRow.organization_id == batch.organization_id)
            .order_by(ImportStagedRow.row_number).all())

    sci_cols = [c for c, _ in SCI_COLUMNS if c in ALWAYS or c in cols] + [IMPORT_FLAG_COL]
    consumed = set(cols.values())
    extra = [h for h in headers if h not in consumed and h not in sci_cols
             and (mapping.get(h) or {}).get("kind") != F.KIND_IGNORE]
    out_headers = sci_cols + extra

    excluded = Counter()
    excluded_rows: List[dict] = []
    included: List[Tuple[int, "OrderedDict[str, str]"]] = []
    undecided: List[int] = []
    for r in rows:
        why = exclusion_reason(r)
        raw = _j(r.raw_data, {})
        if why:
            excluded[why] += 1
            if len(excluded_rows) < 200:
                excluded_rows.append({
                    "row": r.row_number, "reason": why, "label": EXCLUDED_LABELS[why],
                    "lead_id": (raw.get(cols.get(COL["lead_id"]) or "") or "").strip() or None,
                    "name": " ".join(x for x in (r.first_name, r.last_name) if x) or None})
            continue
        line: "OrderedDict[str, str]" = OrderedDict()
        for c in sci_cols:
            if c == IMPORT_FLAG_COL:
                line[c] = import_flag(r)
                continue
            h = cols.get(c)
            v = raw.get(h, "") if h else ""
            if not v and c == COL["first"] and not h:
                v = r.first_name or ""          # split from a full-name column
            if not v and c == COL["last"] and not h:
                v = r.last_name or ""
            line[c] = "" if v is None else str(v)
        for h in extra:
            v = raw.get(h, "")
            line[h] = "" if v is None else str(v)
        included.append((r.row_number, line))
        # #385: an undecided possible duplicate no longer blocks the file -
        # SCI links the same person itself and holds a shared phone/email
        # under a different name for review. Counted, not refused.
        if r.intake_status == IntakeStatus.NEEDS_REVIEW \
                and r.duplicate_resolution == DuplicateResolution.REVIEW:
            undecided.append(r.row_number)

    # SCI refuses a file with a blank or repeated Lead ID; say which rows.
    ids = [(n, (line.get(COL["lead_id"]) or "").strip()) for n, line in included]
    seen = Counter(i for _, i in ids if i)
    blank_ids = [n for n, i in ids if not i]
    repeated = {}
    for n, i in ids:
        if i and seen[i] > 1:
            repeated.setdefault(i, []).append(n)

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=out_headers, extrasaction="ignore")
    w.writeheader()
    for _, line in included:
        w.writerow(line)

    missing = [c for c in REQUIRED if c not in cols]
    return {
        "csv": buf.getvalue().encode("utf-8"),
        "row_numbers": [n for n, _ in included],
        "lead_ids": [i for _, i in ids if i],
        "columns": {c: cols.get(c) for c, _ in SCI_COLUMNS if c in cols},
        "missing_columns": missing,
        "carried_columns": extra,
        "included_rows": len(included),
        "excluded": {"total": sum(excluded.values()),
                     "by_reason": [{"reason": k, "label": EXCLUDED_LABELS[k], "rows": v}
                                   for k, v in excluded.most_common()],
                     "rows": excluded_rows},
        # Possible duplicates nobody decided yet: SCI must not load them.
        "undecided_duplicates": {"count": len(undecided), "rows": undecided[:500]},
        "lead_id_problems": {
            "blank_rows": blank_ids[:500], "blank": len(blank_ids),
            "repeated": [{"lead_id": k, "rows": v} for k, v in sorted(repeated.items())][:500],
        },
    }


def sci_filename(batch: ImportBatch) -> str:
    """The name SCI records for the run. The CSV built here is always CSV,
    so an Excel upload is named .csv (SCI would otherwise try to read it as
    Excel)."""
    name = batch.source_filename or "import.csv"
    if name.lower().endswith(".csv"):
        return name
    return (name.rsplit(".", 1)[0] if "." in name else name) + ".csv"


def translate_rows(summary: dict, row_numbers: List[int]) -> dict:
    """SCI numbers rows by their place in the CSV built here (line 2 = first
    data row). Point every row number it reports back at the customer's file."""
    def orig(n):
        try:
            return row_numbers[int(n) - 2]
        except (IndexError, TypeError, ValueError):
            return n
    for e in summary.get("errors") or []:
        if "row" in e:
            e["row"] = orig(e["row"])
    for b in summary.get("bad_phones") or []:
        if "row" in b:
            b["row"] = orig(b["row"])
    return summary
