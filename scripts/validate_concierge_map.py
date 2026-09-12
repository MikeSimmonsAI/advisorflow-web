#!/usr/bin/env python3
"""Validate the AdvisorFlow concierge map.

Checks structure (every line is independently valid JSON, ids unique, required
fields present, referenced ids resolve) and coverage (the map accounts for
every declared frontend route, every navigation entry and every registered API
endpoint). Exits non-zero on any failure.

Standard library only. Imports nothing from app/ or frontend/.

USAGE
    python scripts/validate_concierge_map.py
    python scripts/validate_concierge_map.py --dir docs/concierge --strict
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

RECORD_TYPES = {"page", "navigation", "action", "api_endpoint",
                "workflow_edge", "permission", "integration", "feature_gate"}
STATUSES = {"active", "hidden", "legacy", "incomplete", "unresolved"}
SURFACES = {"public_web", "authentication", "god_mode", "executive",
            "brand_sales", "sales_workspace", "customer_workspace",
            "finance_compensation", "billing", "support", "ai_workforce",
            "customer_launch", "shared_platform", "unknown"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

REQUIRED_COMMON = ("record_type", "id", "surface", "title", "description",
                   "aliases", "source_files", "status")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def ok(self):
        return not self.errors


def load_lines(path, rep):
    records = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                rep.error("line %d: blank line (JSONL must be one object per "
                          "line with no blanks)" % n)
                continue
            if line.lstrip().startswith(("#", "//")):
                rep.error("line %d: comment found; JSONL must contain only "
                          "JSON objects" % n)
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                rep.error("line %d: invalid JSON (%s)" % (n, exc))
                continue
            if not isinstance(obj, dict):
                rep.error("line %d: top-level value is %s, expected an object"
                          % (n, type(obj).__name__))
                continue
            obj["__line"] = n
            records.append(obj)
    return records


def structural(records, rep):
    seen = {}
    for r in records:
        n = r["__line"]
        for field in REQUIRED_COMMON:
            if field not in r:
                rep.error("line %d: missing required field '%s'" % (n, field))
        rt = r.get("record_type")
        if rt not in RECORD_TYPES:
            rep.error("line %d: unknown record_type %r" % (n, rt))
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            rep.error("line %d: id must be a non-empty string" % n)
        elif rid in seen:
            rep.error("duplicate id %r on lines %d and %d"
                      % (rid, seen[rid], n))
        else:
            seen[rid] = n
        if r.get("surface") not in SURFACES:
            rep.error("line %d: unknown surface %r" % (n, r.get("surface")))
        if r.get("status") not in STATUSES:
            rep.error("line %d: unknown status %r" % (n, r.get("status")))
        if not isinstance(r.get("aliases"), list):
            rep.error("line %d: aliases must be a list" % n)
        if not isinstance(r.get("source_files"), list):
            rep.error("line %d: source_files must be a list" % n)
        elif not r["source_files"] and r.get("status") == "active":
            rep.warn("%s: active record with no source_files" % rid)

        if rt == "page":
            route = r.get("route")
            if not isinstance(route, str) or not route:
                rep.error("%s: page route must be a non-empty string" % rid)
            elif not (route.startswith("/") or route == "*"):
                rep.error("%s: page route %r does not start with '/'"
                          % (rid, route))
            if not isinstance(r.get("route_params"), list):
                rep.error("%s: route_params must be a list" % rid)
            for p in r.get("route_params") or []:
                if (":" + p) not in (route or ""):
                    rep.error("%s: route_param %r does not appear in route %r"
                              % (rid, p, route))
        if rt == "api_endpoint":
            if r.get("method") not in METHODS:
                rep.error("%s: unknown HTTP method %r" % (rid, r.get("method")))
            path = r.get("path")
            if not isinstance(path, str) or not path.startswith("/"):
                rep.error("%s: api path %r must start with '/'" % (rid, path))
            if not isinstance(r.get("auth_required"), bool):
                rep.error("%s: auth_required must be a boolean" % rid)
            if not isinstance(r.get("scope_behavior"), dict):
                rep.error("%s: scope_behavior must be an object" % rid)
        if rt == "action":
            opens = r.get("opens")
            if not isinstance(opens, dict) or "type" not in opens:
                rep.error("%s: action.opens must be an object with a type"
                          % rid)
            elif opens["type"] not in ("route", "modal", "drawer", "menu",
                                       "external", "none"):
                rep.error("%s: unknown opens.type %r" % (rid, opens["type"]))
            if not isinstance(r.get("mutates_data"), bool):
                rep.error("%s: mutates_data must be a boolean" % rid)
            for m in r.get("http_methods") or []:
                if m not in METHODS:
                    rep.error("%s: unknown http method %r" % (rid, m))
        if rt == "workflow_edge":
            for key in ("from", "to", "trigger"):
                if not r.get(key):
                    rep.error("%s: workflow_edge missing %r" % (rid, key))
        if rt == "feature_gate":
            if r.get("gate_type") not in ("feature_flag", "entitlement",
                                          "package", "role", "scope",
                                          "environment", "activation"):
                rep.error("%s: unknown gate_type %r" % (rid, r.get("gate_type")))
    return seen


def referential(records, ids, rep):
    pages = {r["id"]: r for r in records if r["record_type"] == "page"}
    apis = {r["id"]: r for r in records if r["record_type"] == "api_endpoint"}
    routes = {r["route"] for r in pages.values() if r.get("route")}

    def check_ids(rid, field, values, allowed, label):
        for v in values or []:
            if v not in allowed:
                rep.error("%s: %s references unknown %s %r"
                          % (rid, field, label, v))

    for r in records:
        rid, rt = r["id"], r["record_type"]
        if rt == "page":
            if r.get("parent_page") and r["parent_page"] not in pages:
                rep.error("%s: parent_page %r is not a page record"
                          % (rid, r["parent_page"]))
            check_ids(rid, "child_pages", r.get("child_pages"), pages, "page")
            check_ids(rid, "important_actions", r.get("important_actions"),
                      ids, "action")
            check_ids(rid, "api_endpoint_ids", r.get("api_endpoint_ids"),
                      apis, "api_endpoint")
            if r.get("redirects_to") and r["redirects_to"].startswith("/"):
                if r["redirects_to"] not in routes:
                    rep.warn("%s: redirects to %r, which is not a registered "
                             "route" % (rid, r["redirects_to"]))
        if rt == "navigation":
            if r.get("destination_page_id") \
                    and r["destination_page_id"] not in pages:
                rep.error("%s: destination_page_id %r is not a page record"
                          % (rid, r["destination_page_id"]))
            dest = r.get("destination_route")
            if dest and not r.get("destination_page_id") \
                    and not r.get("action") and r["status"] == "active":
                rep.warn("%s: active nav entry points at %r with no resolved "
                         "page" % (rid, dest))
        if rt == "action":
            if r.get("page_id") not in pages:
                rep.error("%s: page_id %r is not a page record"
                          % (rid, r.get("page_id")))
            check_ids(rid, "page_ids", r.get("page_ids"), pages, "page")
            check_ids(rid, "api_endpoint_ids", r.get("api_endpoint_ids"),
                      apis, "api_endpoint")
            if r.get("mutates_data") and not r.get("api_calls"):
                rep.error("%s: marked as mutating with no api_calls" % rid)
            if r.get("api_calls") and not r.get("http_methods"):
                rep.error("%s: has api_calls but no http_methods" % rid)
        if rt == "api_endpoint":
            check_ids(rid, "called_by_pages", r.get("called_by_pages"),
                      pages, "page")
            check_ids(rid, "called_by_actions", r.get("called_by_actions"),
                      ids, "action")
            if r.get("shadowed_by") and r["shadowed_by"] not in apis:
                rep.error("%s: shadowed_by %r is not a registered endpoint"
                          % (rid, r["shadowed_by"]))
        if rt == "workflow_edge":
            for key in ("from_page_id", "to_page_id"):
                if r.get(key) and r[key] not in pages:
                    rep.error("%s: %s %r is not a page record"
                              % (rid, key, r[key]))
            check_ids(rid, "api_endpoint_ids", r.get("api_endpoint_ids"),
                      apis, "api_endpoint")
        if rt == "integration":
            check_ids(rid, "related_pages", r.get("related_pages"),
                      pages, "page")
            check_ids(rid, "related_actions", r.get("related_actions"),
                      ids, "action")
            check_ids(rid, "related_api_endpoints",
                      r.get("related_api_endpoints"), apis, "api_endpoint")
        if rt in ("permission", "feature_gate"):
            key = "applies_to" if rt == "permission" else "controls"
            for v in r.get(key) or []:
                if v not in ids:
                    rep.error("%s: %s references unknown id %r"
                              % (rid, key, v))


# ---------------------------------------------------------------------------
# coverage: the map against the source tree
# ---------------------------------------------------------------------------

def declared_routes():
    p = os.path.join(ROOT, "frontend", "src", "App.jsx")
    if not os.path.isfile(p):
        return None
    text = open(p, encoding="utf-8-sig", errors="replace").read()
    return {m.group(1) for m in
            re.finditer(r'<Route\s+[^>]*?path=\{?["\']([^"\']+)["\']', text)}


def declared_nav_labels():
    files = [
        os.path.join(ROOT, "frontend", "src", "components", "Layout.jsx"),
        os.path.join(ROOT, "frontend", "src", "pages", "GodShell.jsx"),
        os.path.join(ROOT, "frontend", "src", "pages", "sales", "SalesShell.jsx"),
        os.path.join(ROOT, "frontend", "src", "pages", "executive",
                     "ExecutiveSuite.jsx"),
    ]
    labels = set()
    for f in files:
        if not os.path.isfile(f):
            continue
        text = open(f, encoding="utf-8-sig", errors="replace").read()
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        for m in re.finditer(r"\blabel:\s*'([^']+)'", text):
            labels.add(m.group(1))
        for m in re.finditer(r'\blabel:\s*"([^"]+)"', text):
            labels.add(m.group(1))
    return labels


def coverage(records, manifest, rep):
    pages = [r for r in records if r["record_type"] == "page"]
    navs = [r for r in records if r["record_type"] == "navigation"]
    apis = [r for r in records if r["record_type"] == "api_endpoint"]

    routes_in_map = {p["route"] for p in pages}
    routes_in_src = declared_routes()
    if routes_in_src is None:
        rep.warn("frontend/src/App.jsx not found; route coverage not checked")
    else:
        missing = sorted(routes_in_src - routes_in_map)
        extra = sorted(routes_in_map - routes_in_src)
        for r in missing:
            rep.error("route %r is declared in App.jsx but has no page record"
                      % r)
        for r in extra:
            rep.error("page record for route %r which App.jsx does not declare"
                      % r)

    labels_in_map = {n["label"] for n in navs}
    labels_in_src = declared_nav_labels()
    missing_labels = sorted(labels_in_src - labels_in_map)
    if missing_labels:
        rep.warn("navigation labels present in the shells but not in the map "
                 "(may belong to a non-navigation object literal): %s"
                 % ", ".join(missing_labels[:20]))

    if manifest:
        cov = manifest.get("coverage") or {}
        if cov.get("page_records") != len(pages):
            rep.error("manifest coverage.page_records=%r but the map has %d"
                      % (cov.get("page_records"), len(pages)))
        if cov.get("navigation_records") != len(navs):
            rep.error("manifest coverage.navigation_records=%r but the map has "
                      "%d" % (cov.get("navigation_records"), len(navs)))
        if cov.get("endpoints_registered") != len(apis):
            rep.error("manifest coverage.endpoints_registered=%r but the map "
                      "has %d" % (cov.get("endpoints_registered"), len(apis)))
        if manifest.get("record_count") != len(records):
            rep.error("manifest record_count=%r but the map has %d records"
                      % (manifest.get("record_count"), len(records)))
        if cov.get("frontend_routes_declared") and routes_in_src is not None \
                and cov["frontend_routes_declared"] != len(routes_in_src):
            rep.warn("manifest says %d routes declared; this validator counted "
                     "%d in App.jsx" % (cov["frontend_routes_declared"],
                                        len(routes_in_src)))

    # A nav entry whose destination has no page record, or a page reached by
    # nothing at all, is the kind of hole the map exists to expose.
    nav_dests = {(n.get("destination_route") or "").split("#")[0]
                 for n in navs}
    orphan_pages = [p["id"] for p in pages
                    if p["route"] not in nav_dests
                    and not p.get("important_actions")
                    and not p.get("parent_page")
                    and p["route"] not in ("/", "*")]
    for p in orphan_pages:
        rep.warn("%s is in no navigation, has no actions and no parent page "
                 "- confirm it is reachable" % p)

    for a in (r for r in records if r["record_type"] == "action"):
        if a["status"] == "unresolved" and not a.get("delegates_to"):
            rep.warn("%s: control could not be bound to a call or a "
                     "destination" % a["id"])

    for r in apis:
        if r["status"] == "legacy" and not r.get("shadowed_by"):
            rep.error("%s: legacy endpoint without shadowed_by" % r["id"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate the concierge map.")
    ap.add_argument("--dir", default=os.path.join("docs", "concierge"))
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    args = ap.parse_args(argv)

    d = args.dir if os.path.isabs(args.dir) else os.path.join(ROOT, args.dir)
    map_path = os.path.join(d, "advisorflow_concierge_map.jsonl")
    man_path = os.path.join(d, "advisorflow_concierge_manifest.json")

    rep = Report()
    if not os.path.isfile(map_path):
        print("FAIL: %s does not exist" % map_path)
        return 2

    records = load_lines(map_path, rep)
    manifest = None
    if os.path.isfile(man_path):
        try:
            manifest = json.load(open(man_path, encoding="utf-8"))
        except json.JSONDecodeError as exc:
            rep.error("manifest is not valid JSON (%s)" % exc)
    else:
        rep.warn("manifest not found at %s" % man_path)

    ids = structural(records, rep)
    referential(records, ids, rep)
    coverage(records, manifest, rep)

    for r in records:
        r.pop("__line", None)

    print("records: %d" % len(records))
    counts = {}
    for r in records:
        counts[r["record_type"]] = counts.get(r["record_type"], 0) + 1
    for k in sorted(counts):
        print("  %-14s %d" % (k, counts[k]))
    print("errors: %d   warnings: %d" % (len(rep.errors), len(rep.warnings)))
    for e in rep.errors[:100]:
        print("  ERROR   %s" % e)
    if len(rep.errors) > 100:
        print("  ... %d more errors" % (len(rep.errors) - 100))
    for w in rep.warnings[:60]:
        print("  WARN    %s" % w)
    if len(rep.warnings) > 60:
        print("  ... %d more warnings" % (len(rep.warnings) - 60))

    if rep.errors:
        print("VALIDATION FAILED")
        return 1
    if args.strict and rep.warnings:
        print("VALIDATION FAILED (strict: warnings present)")
        return 1
    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
