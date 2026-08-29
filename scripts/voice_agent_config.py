"""Inspect and pin voice agent configuration.

    python scripts/voice_agent_config.py list
    python scripts/voice_agent_config.py pin-version --id <config_id> --version 3
    python scripts/voice_agent_config.py pin-version --id <config_id> --version 3 --apply

WHY THIS EXISTS. Which agent VERSION an organization runs is configuration, and
the supported way to change it is `PATCH /god/voice/agents/{id}/version`. This is
the same operation for the case where that console is unreachable — the identical
column, the same refusals — so nobody is ever tempted to open a psql prompt and
run an UPDATE by hand against a customer's row.

IT CHANGES EXACTLY ONE COLUMN. `agent_version` and nothing else. Every other
field is snapshotted before the write and re-compared after it; if anything else
moved, that is reported as a failure rather than discovered later on a call to a
real family.

DRY RUN BY DEFAULT, matching `integration_key.py`. Read the plan, then run it
again with --apply.

NO SECRET IS PRINTED. The per-org provider key is reported as configured or not,
never by value, and DATABASE_URL is never echoed.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DATABASE_URL") or "").strip():
    print("DATABASE_URL is not set. Refusing to guess which database to touch.")
    sys.exit(2)

from app.deps import SessionLocal                                  # noqa: E402
from app.models.models import Organization, VoiceAgentConfig       # noqa: E402

# Every column except the one this tool is allowed to move. Named explicitly
# rather than derived, so adding a column to the model makes this list stale
# in an obvious way instead of silently widening what may change.
IMMUTABLE = ("id", "organization_id", "provider", "agent_id", "from_number",
             "use_case", "label", "api_key_encrypted", "is_active",
             "created_by", "created_at")


def snapshot(cfg):
    return {name: getattr(cfg, name, None) for name in IMMUTABLE}


def describe(db, cfg, org_names=None):
    org = (org_names or {}).get(cfg.organization_id)
    if org is None:
        row = (db.query(Organization)
               .filter(Organization.id == cfg.organization_id).first())
        org = (row.brand_name or row.name) if row else "MISSING"
    ready, why = None, None
    try:
        from app.services.comms import get_voice_provider
        ready, why = get_voice_provider(db, cfg).is_ready()
    except Exception as exc:                                       # noqa: BLE001
        ready, why = False, str(exc)[:160]

    version = getattr(cfg, "agent_version", None)
    print("  id            : %s" % cfg.id)
    print("  tenant        : %s (%s)" % (org, cfg.organization_id))
    print("  provider      : %s" % cfg.provider)
    print("  agent_id      : %s" % cfg.agent_id)
    print("  agent_version : %s" % (version if version is not None
                                    else "- NOT PINNED - calls are refused -"))
    print("  from_number   : %s" % cfg.from_number)
    print("  use_case      : %s" % cfg.use_case)
    print("  label         : %s" % (cfg.label or "-"))
    print("  is_active     : %s" % bool(cfg.is_active))
    # Whether one exists. Never the value.
    print("  org api key   : %s" % ("configured (value not shown)"
                                    if cfg.api_key_encrypted else "not set"))
    print("  provider_ready: %s%s" % (ready, "" if ready else "  (%s)" % why))
    return ready


def cmd_list(db, args):
    rows = (db.query(VoiceAgentConfig)
            .order_by(VoiceAgentConfig.created_at.desc()).all())
    if not rows:
        print("No voice agent configurations exist.")
        return
    for cfg in rows:
        print()
        describe(db, cfg)
    print()


def cmd_pin_version(db, args):
    cfg = (db.query(VoiceAgentConfig)
           .filter(VoiceAgentConfig.id == args.id).first())
    if cfg is None:
        print("No voice agent configuration with id %r. Run `list` first."
              % args.id)
        sys.exit(1)
    if args.version < 0:
        print("agent_version must be zero or greater. Refusing.")
        sys.exit(1)

    before = snapshot(cfg)
    previous = getattr(cfg, "agent_version", None)

    print()
    print("  BEFORE")
    describe(db, cfg)
    print()
    print("  CHANGE")
    print("    agent_version : %s -> %s" % (
        previous if previous is not None else "- not pinned -", args.version))
    print("    everything else: UNCHANGED (%d fields held)" % len(IMMUTABLE))
    print()

    if previous == args.version:
        print("  Already pinned to %s. Nothing to do." % args.version)
        print()
        return

    if not args.apply:
        print("  DRY RUN. Nothing written. Re-run with --apply.")
        print()
        return

    cfg.agent_version = args.version
    db.commit()

    # Read it back on a CLEAN session, so this is the database's answer and not
    # the identity map's memory of what we just set.
    db.close()
    fresh = SessionLocal()
    try:
        again = (fresh.query(VoiceAgentConfig)
                 .filter(VoiceAgentConfig.id == args.id).first())
        after = snapshot(again)
        drifted = [k for k in IMMUTABLE if before[k] != after[k]]

        print("  AFTER (re-read from the database)")
        ready = describe(fresh, again)
        print()
        if drifted:
            print("  FAIL: fields other than agent_version changed: %s"
                  % ", ".join(drifted))
            sys.exit(1)
        if getattr(again, "agent_version", None) != args.version:
            print("  FAIL: agent_version did not persist.")
            sys.exit(1)
        print("  PASS: agent_version = %s; %d other fields byte-identical."
              % (args.version, len(IMMUTABLE)))

        # What the provider would actually put on the wire, built by the real
        # code path rather than asserted from the column.
        from app.services.comms.voice.retell import _coerce_version
        print("  Outbound request would send override_agent_version = %r"
              % _coerce_version(getattr(again, "agent_version", None)))
        print("  Provider ready: %s" % ready)
        print()
    finally:
        fresh.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    v = sub.add_parser("pin-version")
    v.add_argument("--id", required=True, help="voice_agent_configs.id")
    v.add_argument("--version", type=int, required=True,
                   help="Published provider agent version to pin")
    v.add_argument("--apply", action="store_true")

    args = p.parse_args()
    db = SessionLocal()
    try:
        {"list": cmd_list, "pin-version": cmd_pin_version}[args.cmd](db, args)
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
