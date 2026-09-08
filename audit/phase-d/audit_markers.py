# -*- coding: utf-8 -*-
"""Tighter gap scan. The first pass matched prose ("upcoming", "for now" inside
message templates) and was useless. This one looks only for markers a developer
writes deliberately."""
import io
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))


def read(p):
    with io.open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def walk(base, exts):
    out = []
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in ("node_modules", "dist", "__pycache__", ".git")]
        for f in fn:
            if f.endswith(exts):
                out.append(os.path.join(dp, f))
    return out


FILES = ([(p, read(p)) for p in walk(os.path.join(ROOT, "app"), (".py",))]
         + [(p, read(p)) for p in walk(os.path.join(ROOT, "frontend", "src"),
                                       (".js", ".jsx"))])

MARK = re.compile(
    r"\b(TODO|FIXME|XXX:|HACK)\b"
    r"|NotImplementedError"
    r"|\bCOMING SOON\b"
    r"|not implemented"
    r"|NOT IMPLEMENTED"
    r"|POLICY REQUIRED"
    r"|not yet (built|wired|implemented|supported|available)"
    r"|deliberately (empty|inert|does nothing)"
    r"|\bstubbed\b"
)

out = ["== DELIBERATE INCOMPLETENESS MARKERS =="]
for p, t in sorted(FILES):
    rel = os.path.relpath(p, ROOT)
    for i, line in enumerate(t.split("\n"), 1):
        if MARK.search(line):
            s = line.strip()
            out.append("  %s:%d  %s" % (rel, i, s[:160]))

out.append("")
out.append("== WHO REFERENCES THE 5 ORPHANED SERVICES (whole repo, incl. tests/main) ==")
for name in ("billing_webhook", "auto_send_candidate_service", "feature_flags_service",
             "source_ingest", "tenancy"):
    out.append("-- %s" % name)
    pat = re.compile(r"\b%s\b" % name)
    everywhere = FILES + [(p, read(p)) for p in walk(os.path.join(ROOT, "tests"), (".py",))]
    for p in (os.path.join(ROOT, "main.py"), os.path.join(ROOT, "app", "main.py")):
        if os.path.exists(p):
            everywhere.append((p, read(p)))
    hits = 0
    for p, t in everywhere:
        rel = os.path.relpath(p, ROOT)
        if rel.endswith("services\\%s.py" % name) or rel.endswith("services/%s.py" % name):
            continue
        for i, line in enumerate(t.split("\n"), 1):
            if pat.search(line):
                out.append("    %s:%d  %s" % (rel, i, line.strip()[:130]))
                hits += 1
                if hits > 12:
                    break
        if hits > 12:
            out.append("    ... (truncated)")
            break
    if hits == 0:
        out.append("    NO REFERENCE ANYWHERE")

with io.open(os.path.join(ROOT, "_audit_gaps2.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("wrote _audit_gaps2.txt lines=%d" % len(out))
