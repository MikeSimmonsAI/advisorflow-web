"""/sell keeps its parent platform visible at every width: the desktop nav has
"EvoSysPro Home", and on tablet/phone (where that nav collapses into the menu)
a slim strip above the header links back to the EvoSysPro home page."""
import re
from pathlib import Path

SELL = Path(__file__).resolve().parents[1] / "public-site" / "sell" / "index.php"


def _page():
    return SELL.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_parent_strip_links_home_and_names_evosyspro():
    s = _page()
    m = re.search(r'<div class="eco-bar"><a href="([^"]+)">(.*?)</a></div>', s, re.S)
    assert m, "the parent-platform strip is missing"
    assert m.group(1) == "/"
    assert "EvoSysPro" in m.group(2) and "EvoSysPro Home" in m.group(2)
    assert s.index('class="eco-bar"') < s.index('<header class="top dark"')


def test_the_strip_shows_only_where_the_desktop_nav_collapses():
    s = _page()
    assert ".eco-bar{display:none;" in s
    block = s[s.index("@media (max-width:1024px){"):]
    block = block[:block.index("}\n@media")]
    assert ".top .dnav{display:none}" in block and ".eco-bar{display:block}" in block
    # the desktop nav still carries the same destination
    assert re.search(r'<nav class="dnav".*?<a class="home" href="/">', s, re.S)
