#!/usr/bin/env python3
"""
clean_wupa_spam.py
Removes WUPA spam messages from the Pixel 9's SMS inbox via ADB content
provider queries, per Mike's Phase 1 priority list item #5.

WUPA = "Wireless Universal Provisioning Alert" type carrier/spam messages
that clutter the inbox and the content://sms provider used for reply
capture. This script identifies and deletes them so they don't interfere
with the dedup/reply-matching logic that reads from the same provider.

USAGE:
    python clean_wupa_spam.py --dry-run     # preview what would be deleted
    python clean_wupa_spam.py --execute     # actually delete

Matches Mike's existing pattern of pairing .py scripts with .bat launchers
instead of inline multi-line python -c commands in CMD (per his stated
preference - CMD breaks multi-line Python syntax).

Requires: adb in PATH, phone connected (USB or WiFi per the existing
AdvisorFlow ADB setup), USB debugging authorized.
"""

import subprocess
import argparse
import sys

# Common WUPA / carrier-spam sender patterns and body keywords. Extend this
# list as new spam patterns are identified - these are deliberately
# conservative (specific known patterns) rather than aggressive keyword
# matching, to avoid accidentally deleting real lead replies.
WUPA_SENDER_PATTERNS = ["WUPA", "VZWPIX", "311660", "26000", "Carrier Services"]
WUPA_BODY_KEYWORDS = [
    "wireless universal provisioning",
    "your wireless account",
    "carrier settings update",
]


def run_adb_command(args: list[str]) -> str:
    result = subprocess.run(["adb"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ADB command failed: {' '.join(args)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout


def query_sms_inbox() -> list[dict]:
    """
    Queries content://sms/inbox for all messages, returns list of dicts
    with _id, address (sender), body, date.
    """
    output = run_adb_command([
        "shell", "content", "query",
        "--uri", "content://sms/inbox",
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


def is_wupa_spam(row: dict) -> bool:
    address = (row.get("address") or "").upper()
    body = (row.get("body") or "").lower()

    if any(pattern.upper() in address for pattern in WUPA_SENDER_PATTERNS):
        return True
    if any(keyword in body for keyword in WUPA_BODY_KEYWORDS):
        return True
    return False


def delete_message(message_id: str) -> bool:
    output = run_adb_command([
        "shell", "content", "delete",
        "--uri", "content://sms",
        "--where", f"_id={message_id}",
    ])
    return True


def main():
    parser = argparse.ArgumentParser(description="Clean WUPA spam from Pixel 9 SMS inbox via ADB")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    parser.add_argument("--execute", action="store_true", help="Actually delete matched spam")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Specify --dry-run to preview or --execute to actually delete.")
        sys.exit(1)

    print("Querying SMS inbox via ADB...")
    messages = query_sms_inbox()
    print(f"Found {len(messages)} total messages in inbox.")

    spam_matches = [m for m in messages if is_wupa_spam(m)]
    print(f"Identified {len(spam_matches)} WUPA/spam matches.")

    for m in spam_matches:
        preview = (m.get("body") or "")[:60]
        print(f"  [{m.get('_id')}] from {m.get('address')}: {preview}...")

    if args.dry_run:
        print("\nDry run only - nothing deleted. Re-run with --execute to delete these.")
        return

    print(f"\nDeleting {len(spam_matches)} messages...")
    deleted = 0
    for m in spam_matches:
        if delete_message(m["_id"]):
            deleted += 1
    print(f"Deleted {deleted} of {len(spam_matches)} spam messages.")


if __name__ == "__main__":
    main()
