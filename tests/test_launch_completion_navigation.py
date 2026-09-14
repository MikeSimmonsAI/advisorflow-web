import os
import re


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
LAYOUT = os.path.join(FE, "components", "Layout.jsx")
ORG_SETTINGS = os.path.join(FE, "pages", "OrgSettings.jsx")


def _read(path):
    return open(path, encoding="utf-8").read()


def test_workspace_launch_nav_uses_implementation_status_not_percentage():
    src = _read(LAYOUT)
    assert "api.get('/launch/me'" in src
    assert "d?.implementation?.status" in src
    assert "status === 'live'" in src
    assert "overall_pct" not in src


def test_completed_launch_is_not_prominent_in_normal_workspace_nav():
    src = _read(LAYOUT)
    row = re.search(r"\{[^{}]*to:\s*'/launch'[^{}]*launchOnly:\s*true[^{}]*\}", src)
    assert row, "Layout.jsx has no launchOnly /launch nav row."
    assert "launchNavState.completed" in src
    assert "hasLaunch !== true" not in src


def test_org_admins_can_review_completed_company_setup_from_org_settings():
    src = _read(ORG_SETTINGS)
    assert "api.get('/launch/me'" in src
    assert "Company Setup" in src
    assert "navigate('/launch')" in src


def test_company_setup_review_does_not_create_a_non_admin_nav_entry():
    src = _read(LAYOUT)
    nav = src[:src.index("export default")] if "export default" in src else src
    assert "Company Setup" not in nav
