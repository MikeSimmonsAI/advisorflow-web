"""Credential audit for TRACKED files. Reports shape and location, never value.

WHY THIS PRINTS NOTHING SENSITIVE
---------------------------------
An audit that echoes the secret it found puts that secret into a terminal
buffer, a scrollback, a log and a transcript - which is four more places it
now lives. So every hit is reported as label + verdict + length + file:line,
and the body of the token is never rendered. Not truncated. Not masked. Never
read out at all.

VERDICTS
    LIVE-SHAPED   long enough and random enough to be a working credential
    PLACEHOLDER   obvious filler, or too little entropy to be vendor-issued
    FIXTURE       marked on its own line as a deliberate test fixture
    SHORT         too short for the vendor's real token length

Exit status is 0 unless at least one LIVE-SHAPED hit is in a scanned file.

Usage:  python scripts/_secret_audit.py [path ...]
"""
import re
import subprocess
import sys

# (label, pattern, the real minimum body length that vendor issues)
#
# EVERY PATTERN IS ANCHORED WITH A LEFT BOUNDARY, and the bodies allow only the
# characters the vendor actually issues. The first version of this file did
# neither, and `re_[A-Za-z0-9_-]{16,}` duly "found" 132 live-shaped Resend keys
# - every one of them the tail of an ordinary identifier like
# `pre_need_lock_price`. An audit that cries wolf 132 times is an audit nobody
# reads the 133rd time, which is worse than no audit at all.
PATTERNS = [
    ("render",   re.compile(r"(?<![A-Za-z0-9_])rnd_[A-Za-z0-9]{20,}"),   24),
    ("openai",   re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9]{32,}"),    32),
    ("sendgrid", re.compile(r"(?<![A-Za-z0-9_])SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"), 40),
    ("resend",   re.compile(r"(?<![A-Za-z0-9_])re_[A-Za-z0-9]{24,}(?![A-Za-z0-9_])"), 24),
    ("github",   re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,}"), 36),
    ("twilio-sid", re.compile(r"(?<![A-Za-z0-9_])AC[0-9a-f]{32}(?![0-9a-f])"), 34),
    ("twilio-key", re.compile(r"(?<![A-Za-z0-9_])SK[0-9a-f]{32}(?![0-9a-f])"), 34),
    ("stripe-live", re.compile(r"(?<![A-Za-z0-9_])(?:sk|rk)_live_[A-Za-z0-9]{20,}"), 28),
    ("stripe-test", re.compile(r"(?<![A-Za-z0-9_])(?:sk|rk)_test_[A-Za-z0-9]{20,}"), 28),
    ("slack",    re.compile(r"(?<![A-Za-z0-9_])xox[abposr]-[A-Za-z0-9-]{20,}"), 24),
    ("aws",      re.compile(r"(?<![A-Za-z0-9_])AKIA[0-9A-Z]{16}(?![0-9A-Z])"), 20),
    ("google",   re.compile(r"(?<![A-Za-z0-9_])AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), 39),
]

# `stripe-test` is deliberately listed even though every match will carry the
# word "test" and therefore land in FILLER below. It REPORTS a committed Stripe
# test key without BLOCKING the deploy over one, which is the right weight: a
# test key in the repo is worth seeing and is not a production credential.
FILLER = ("xxx", "test", "your", "placeholder", "example", "abc123",
          "0000000000", "redacted", "changeme", "<", "dummy", "sample",
          "deadbeef", "cafebabe", "notarealkey", "fake")

# THE ONLY EXPLICIT EXCLUSION, AND IT IS DELIBERATELY NARROW.
#
# A test needs a value the scanner DOES classify as live - otherwise the test
# proves nothing. Writing that value into a tracked test file would then make
# the audit fail forever against its own fixture, and an audit that is
# permanently red is an audit somebody switches off. That is the failure mode
# this marker exists to prevent, and the only one.
#
# So the exclusion is per-LINE and has to be typed on purpose: the marker must
# appear on the same physical line as the token. It cannot be set per file, per
# directory, by glob or by environment variable; it shows up in every diff that
# adds one; and it suppresses exactly the line it is written on. Broadening it
# to a file or a path would turn the one narrow escape hatch into the way a
# real key gets past the gate.
FIXTURE_MARKER = re.compile(r"secret-audit:\s*fixture", re.IGNORECASE)


def _is_repeated_unit(body):
    """Is this the same short run over and over?

    `ACdeadbeefdeadbeefdeadbeefdeadbeef` in the Twilio webhook probe is a
    fixture with five distinct characters and the right length, so a
    distinct-character count alone calls it live. It is `deadbeef` four times,
    and nothing a vendor issues looks like that.
    """
    for size in range(1, 13):
        if len(body) >= size * 2 and body == (body[:size] * (len(body) // size
                                                             + 1))[:len(body)]:
            return True
    return False


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    return [p for p in out.stdout.splitlines() if p.strip()]


def verdict(token, minimum, marked_fixture=False):
    if marked_fixture:
        return "FIXTURE"
    low = token.lower()
    if any(f in low for f in FILLER):
        return "PLACEHOLDER"
    # LOW ENTROPY IS A PLACEHOLDER, whatever its length.
    #
    # `ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` in the Twilio webhook probe is the
    # right length and the right prefix and is obviously not a credential. A
    # gate that blocks a deploy over it would be switched off within a week,
    # so the audit has to be able to tell a fixture from a key.
    body = token.split("_", 1)[-1].split("-", 1)[-1].split(".", 1)[-1]
    if len(set(body.lower())) <= 4 or _is_repeated_unit(body.lower()):
        return "PLACEHOLDER"
    if len(token) < minimum:
        return "SHORT"
    return "LIVE-SHAPED"


def scan(paths):
    """Return [(path, lineno, label, length, verdict)] - never the token.

    The token exists only inside this loop. It is never placed in the returned
    structure, so nothing downstream - print, log, test assertion - is able to
    render it even by accident.
    """
    findings = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    marked = bool(FIXTURE_MARKER.search(line))
                    for label, pattern, minimum in PATTERNS:
                        for match in pattern.finditer(line):
                            token = match.group(0)
                            findings.append((path, lineno, label, len(token),
                                             verdict(token, minimum, marked)))
        except (OSError, IsADirectoryError, UnicodeError):
            continue
    return findings


def main(argv):
    paths = argv[1:] or tracked_files()
    findings = scan(paths)

    if not findings:
        print("No credential-shaped strings in tracked files.")
        return 0

    live = [f for f in findings if f[4] == "LIVE-SHAPED"]
    for path, lineno, label, length, v in sorted(findings):
        print("%-12s %-11s len=%-3d %s:%d" % (label, v, length, path, lineno))
    print()
    print("TOTAL %d hit(s); %d LIVE-SHAPED." % (len(findings), len(live)))
    # LIVE-SHAPED in a tracked file is a finding, not a warning.
    return 1 if live else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
