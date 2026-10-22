"""Nightly practice run for the SCI appointment setter.

Once a day (from 6 AM Central) the setter answers every regression
conversation - the real situations it has got wrong before, Henry's "I can
come by tomorrow" included - with SYNTHETIC contacts that can never be
messaged (bookings are simulated against the live calendar). The team hears
about it only when something went wrong, before a real family sees it.
"""
import html as _html
import logging
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
CENTRAL = ZoneInfo("America/Chicago")
START_HOUR = 6


def _tag(day) -> str:
    return "n%s" % day.strftime("%Y%m%d")


def already_ran(db: Session, org_id: str, day) -> bool:
    from app.models.models import Lead
    return db.query(Lead.id).filter(Lead.organization_id == org_id,
                                    Lead.last_name.like("SIM-EVAL-%%-%s" % _tag(day))).first() is not None


def run_if_due(db: Session, now: Optional[datetime] = None) -> Optional[Dict]:
    from app.services import sms_programs
    from app.services.programs import identity
    now_c = (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(CENTRAL)
    if now_c.hour < START_HOUR:
        return None
    org_id = sms_programs.sci_org_id(db)
    prog = identity.program_for_org(db, org_id) if org_id else None
    if prog is None or already_ran(db, prog.organization_id, now_c.date()):
        return None
    return run(db, prog, now_c.date())


def run(db: Session, prog, day) -> Dict:
    from app.models.program_models import LocationProfile
    from app.services.programs import appointments as A
    from app.services.programs import setter_eval as E
    from app.services.programs.setter_eval_scenarios import REGRESSIONS
    user = A.program_advisor(db, prog)
    if user is None:
        return {"skipped": "no booking calendar user"}
    loc = (db.query(LocationProfile).filter(LocationProfile.organization_id == prog.organization_id,
                                            LocationProfile.is_review_bucket.isnot(True),
                                            LocationProfile.location_id.isnot(None))
           .order_by(LocationProfile.official_name).first())
    failed = []
    for sc in REGRESSIONS:
        try:
            res = E.run_scenario(db, prog, user, sc, run_tag=_tag(day),
                                 location_id=loc.location_id if loc else None)
        except Exception as exc:                         # noqa: BLE001
            db.rollback()
            log.exception("practice scenario %s failed to run", sc["id"])
            res = {"passed": False, "failures": [{"turn": 0, "problems": ["could not run: %s" % type(exc).__name__]}],
                   "turns": []}
        if not res.get("passed"):
            failed.append((sc, res))
    out = {"ran": len(REGRESSIONS), "failed": len(failed)}
    if failed:
        _alert(db, prog, day, out, failed)
    return out


def _alert(db: Session, prog, day, out: Dict, failed) -> None:
    from app.services.programs import alert_ledger as L
    from app.services.programs import responses as R
    rows = []
    for sc, res in failed:
        t = (res.get("turns") or [{}])[0]
        probs = "; ".join(p for f in res.get("failures") or [] for p in f.get("problems") or [])
        rows.append("<li><b>%s</b> - family: &ldquo;%s&rdquo;<br>AI: &ldquo;%s&rdquo;<br>Wrong: %s</li>" % (
            _html.escape(sc["id"]), _html.escape((sc["turns"][0]["text"] or "")[:200]),
            _html.escape((t.get("reply") or "(no reply)")[:300]), _html.escape(probs[:300])))
    sms = ("SCI Practice Check | %d of %d practice conversations went wrong overnight. Nothing was sent "
           "to any family. Details are in your email." % (out["failed"], out["ran"]))
    try:
        L.page(db, prog, lead_id=None, key="practice:%s" % day.isoformat(), kind="handoff", sms_text=sms,
               subject="%s - practice check: %d of %d went wrong" % (R.alert_title(prog), out["failed"], out["ran"]),
               html="<p>The appointment setter practised the conversations it has got wrong before. These "
                    "went wrong again (practice contacts only - no family was texted):</p><ul>%s</ul>" % "".join(rows),
               management=False, coalesce=False,
               audit_message="practice check: %d of %d failed" % (out["failed"], out["ran"]))
        db.commit()
    except Exception:                                    # noqa: BLE001
        db.rollback()
        log.exception("practice-run alert failed")
