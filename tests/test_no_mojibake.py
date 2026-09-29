"""No double-encoded UTF-8 ("mojibake") anywhere in shipped source.

Spec section 8 (P0 - garbled / corrupted UI text): Demo Sites rendered
"built" + three junk chars + "publish" (an em dash read as cp1252)
instead of "built \u2014 publish". The cause was
never one string - whole files had been saved after being read as cp1252, so
every em dash, ellipsis, middot and box-drawing rule became 2-9 junk chars.

This gate scans every source file under frontend/src and app/ (the code that
reaches the browser or the API) and fails if any mojibake signature remains.
The signatures are written as escapes so this file never matches itself.

ALLOW-LIST: intentionally empty. Nothing in the product needs to display a
literal mojibake sequence. The one test that must spell them out
(tests/test_qa_visible_text_encoding.py) lives in tests/, which is not scanned.
If a genuine need appears, add "relative/path": "justification" to _ALLOW.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("frontend/src", "app", "frontend/public")
EXTS = (".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".css", ".html", ".json")
SKIP_DIRS = {"node_modules", "dist", "__pycache__", ".vite"}

_ALLOW = {}  # {"relative/path": "why this file must contain a signature"}


def _cp1252_table():
    """char -> byte for the cp1252 upper half (latin-1 for its 5 holes)."""
    table = {}
    for b in range(0x80, 0x100):
        try:
            ch = bytes([b]).decode("cp1252")
        except UnicodeDecodeError:
            ch = chr(b)
        table[ch] = b
        table.setdefault(chr(b), b)
    return table


_C2B = _cp1252_table()
_LEAD = "".join(re.escape(c) for c, b in _C2B.items() if 0xC2 <= b <= 0xF4)
_CONT = "".join(re.escape(c) for c, b in _C2B.items() if 0x80 <= b <= 0xBF)
# A UTF-8 lead byte followed by continuation bytes, all shown as cp1252 chars.
_CANDIDATE = re.compile("[" + _LEAD + "][" + _CONT + "]{1,3}")
_REPLACEMENT = "\ufffd"  # the replacement diamond: bytes already lost


def _mojibake_hits(text):
    """Real mojibake only: a candidate run that decodes as valid UTF-8."""
    hits = []
    for m in _CANDIDATE.finditer(text):
        raw = bytes(_C2B[c] for c in m.group(0))
        for n in range(len(raw), 1, -1):
            try:
                raw[:n].decode("utf-8")
            except UnicodeDecodeError:
                continue
            hits.append(m.group(0))
            break
    if _REPLACEMENT in text:
        hits.append(_REPLACEMENT)
    return hits


def _source_files():
    for rel_dir in SCAN_DIRS:
        base = os.path.join(ROOT, rel_dir)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(EXTS):
                    path = os.path.join(dirpath, name)
                    yield os.path.relpath(path, ROOT).replace(os.sep, "/"), path


def _as_cp1252(text):
    """Simulate a UTF-8 file being read as cp1252 (latin-1 for its 5 holes)."""
    b2c = {b: c for c, b in _C2B.items() if len(c) == 1 and ord(c) >= 0x80}
    b2c.update({b: bytes([b]).decode("cp1252") for b in range(0x80, 0x100)
                if b not in (0x81, 0x8D, 0x8F, 0x90, 0x9D)})
    return "".join(b2c.get(b, chr(b)) for b in text.encode("utf-8"))


def test_detector_catches_known_signatures():
    # em dash, right quote, ellipsis, middot, box rule, and the triple-encoded dash
    samples = ["—", "’", "…", "·", "─", "é"]
    for s in samples:
        once = _as_cp1252(s)
        assert _mojibake_hits(once), repr(once)
    twice = _as_cp1252(_as_cp1252("—"))
    assert _mojibake_hits(twice)


def test_detector_ignores_clean_typography():
    clean = "Loading… — café · 5–480 → ── § 21 © “quoted”"
    assert _mojibake_hits(clean) == []


def test_no_mojibake_in_shipped_source():
    problems = []
    for rel, path in _source_files():
        if rel in _ALLOW:
            continue
        try:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            problems.append(f"{rel}: not valid UTF-8")
            continue
        hits = _mojibake_hits(text)
        if hits:
            sample = ", ".join(repr(h) for h in hits[:3])
            problems.append(f"{rel}: {len(hits)} hit(s), e.g. {sample}")
    assert not problems, "Mojibake found:\n" + "\n".join(problems)
