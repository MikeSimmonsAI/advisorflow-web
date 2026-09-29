"""WS7 - spec section 66/67: one polished light theme, no day/night toggle.

Static checks on the frontend source (no build needed).
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")


def _read(*rel):
    with open(os.path.join(FE, *rel), encoding="utf-8") as fh:
        return fh.read()


def _no_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_settings_does_not_import_the_toggle():
    src = _read("pages", "Settings.jsx")
    assert "AppearanceToggle" not in src
    assert "components/AppearanceToggle" not in src


def test_executive_suite_does_not_import_the_toggle():
    src = _read("pages", "executive", "ExecutiveSuite.jsx")
    assert "AppearanceToggle" not in src


def test_no_source_file_imports_the_toggle():
    hits = []
    for dirpath, _dirs, files in os.walk(FE):
        for name in files:
            if name.endswith((".js", ".jsx", ".ts", ".tsx")):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    if "AppearanceToggle" in _no_comments(fh.read()):
                        hits.append(os.path.relpath(path, FE))
    assert hits == [], hits


def test_appearance_js_does_not_default_to_dark():
    src = _no_comments(_read("appearance.js"))
    assert "return DARK" not in src
    assert "prefers-color-scheme" not in src
    for fn in ("getAppearancePreference", "effectiveAppearance", "systemAppearance"):
        body = src[src.index("export function %s" % fn):]
        body = body[:body.index("}")]
        assert "return LIGHT" in body, fn


def test_appearance_js_keeps_its_exports_for_old_importers():
    src = _read("appearance.js")
    for name in ("initAppearance", "applyAppearance", "setAppearancePreference",
                 "getAppearancePreference", "effectiveAppearance",
                 "systemAppearance", "APPEARANCES", "LIGHT", "DARK", "SYSTEM"):
        assert re.search(r"export (const|function) %s\b" % name, src), name


def test_appearance_js_clears_the_stale_localstorage_key():
    src = _read("appearance.js")
    assert "localStorage.removeItem(KEY)" in src
    assert "localStorage.getItem" not in src
    assert "localStorage.setItem" not in src


def test_no_stylesheet_keys_rules_on_the_dark_appearance():
    """Mixed theme CSS: no rule anywhere may target the dark attribute."""
    hits = []
    for dirpath, _dirs, files in os.walk(FE):
        for name in files:
            if name.endswith((".css", ".jsx", ".js")):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    text = _no_comments(fh.read())
                if '[data-appearance="dark"]' in text or \
                        ':not([data-appearance="light"])' in text:
                    hits.append(os.path.relpath(path, FE))
    assert hits == [], hits


def test_token_layer_is_unconditional_and_documented():
    css = _read("styles", "appearance.css")
    assert "TOKEN REFERENCE" in css
    code = _no_comments(css)
    assert "data-appearance" not in code
    assert "html:root {" in code
    assert "--color-primary:       #1f5eff" in code
    for pill in ("success", "warning", "danger", "info", "neutral"):
        for part in ("bg", "fg", "bd"):
            assert "--pill-%s-%s" % (pill, part) in code


def test_global_select_is_not_forced_dark():
    idx = _no_comments(_read("index.css"))
    rule = idx[idx.index("select {"):]
    rule = rule[:rule.index("}")]
    assert "color-scheme: light" in rule
