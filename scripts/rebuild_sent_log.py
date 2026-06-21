#!/usr/bin/env python3
"""
rebuild_sent_log.py
Rebuilds FINAL_sent_log from the phone's SMS outbox via ADB content
provider, per Mike's Phase 1 priority list item #6.

This reconstructs a ground-truth record of every SMS actually sent from
the Pixel 9 (content://sms/sent) so it can be cross-referenced against
the web app's Message table and the ContactRegistry dedup ledger -
useful for reconciling what the OLD desktop/ADB pipeline already sent
before the web migration, so the web app's dedup registry can be
seeded with that history and avoid re-contacting anyone.

USAGE:
    python rebuild_sent_log.py --output FINAL_sent_log.csv

Then import that CSV into the ContactRegistry via the
seed_registry_from_sent_log.py companion script (see below) before
advisors start sending from the web app, so historical sends are
respected by the new dedup engine.
"""

import subprocess
import argparse
import csv
import sys
import re


def run_adb_command(args: list[str]) -> str:
    result = subprocess.run(["adb"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ADB command failed: {' '.join(args)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout


def query_sms_sent() -> list[dict]:
    """Queries content://sms/sent for all outbound messages."""
    output = run_adb_command([
        "shell", "content", "query",
        "--uri", "content://sms/sent",
        "--projection", "_id:address:body:date",
    ])
    rows = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        row = {}
        for field in line.split(", "):
            if "=" in field:
                key, _, val = field.partition("=")
                row[key.strip()] = val.strip()
        if row:
            rows.append(row)
    return rows


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    return digits


def main():
    parser = argparse.ArgumentParser(description="Rebuild FINAL_sent_log from phone outbox via ADB")
    parser.add_argument("--output", default="FINAL_sent_log.csv", help="Output CSV path")
    args = parser.parse_args()

    print("Querying SMS outbox via ADB...")
    messages = query_sms_sent()
    print(f"Found {len(messages)} sent messages.")

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["phone_normalized", "phone_raw", "body", "date_sent_epoch_ms"])
        for m in messages:
            phone_raw = m.get("address", "")
            writer.writerow([
                normalize_phone(phone_raw),
                phone_raw,
                (m.get("body") or "").replace("\n", " "),
                m.get("date", ""),
            ])

    print(f"Wrote {len(messages)} rows to {args.output}")
    print()
    print("Next step: import this CSV into the web app's ContactRegistry so")
    print("historical sends from the old ADB pipeline are respected by the")
    print("new dedup engine. Use seed_registry_from_sent_log.py for that.")


if __name__ == "__main__":
    main()
