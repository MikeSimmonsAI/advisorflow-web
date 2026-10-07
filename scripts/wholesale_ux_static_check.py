"""Static Wholesale UX/route contract (STATIC SOURCE; no browser, no node needed).

  1. Every internal `/wholesale...` destination written in Wholesale screens
     resolves to a <Route path=...> in App.jsx (params and query strings handled).
  2. Every lazy page import in App.jsx for Wholesale points at an existing file.
  3. Screens do not render raw internal enum keys (snake_case) via obvious
     patterns, and do not contain garbled mojibake text.
  4. Send failures are surfaced to the person (the disposition and seller-send
     screens read the server's reason).

Run: python3 scripts/wholesale_ux_static_check.py [-v]
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "frontend/src")
verbose = "-v" in sys.argv
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        if verbose:
            print("ok:", name)
    else:
        failed += 1
        print("FAIL:", name)


def read(p):
    with open(p, encoding="utf-8-sig") as fh:
        return fh.read()


app = read(os.path.join(SRC, "App.jsx"))
routes = re.findall(r'<Route\s+path="([^"]+)"', app)
route_res = []
for r in routes:
    pat = re.escape(r).replace(r"\*", ".*")
    pat = re.sub(r":[A-Za-z_]+", "[^/]+", pat.replace(r"\:", ":"))
    route_res.append(re.compile("^" + pat + "$"))


def resolves(path):
    return any(rx.match(path) for rx in route_res)


files = []
for base in ("pages/wholesale", "components"):
    for dp, _, fs in os.walk(os.path.join(SRC, base)):
        for f in fs:
            if f.endswith(".jsx") and (base != "components" or "holesale" in f):
                files.append(os.path.join(dp, f))
check("found wholesale screens", len(files) >= 10)

# 1. internal destinations --------------------------------------------------
dest_re = re.compile(r"""(?:to=|href=|navigate\(|path:\s*)\s*\{?\s*[`'"](/wholesale[^`'"?#]*)""")
checked = 0
for p in sorted(files):
    text = read(p)
    for m in dest_re.finditer(text):
        raw = m.group(1)
        norm = re.sub(r"\$\{[^}]*\}", "x", raw)
        norm = norm.rstrip("/") or "/wholesale"
        # `'/wholesale/deals/' + id` style leaves a trailing slash we already stripped
        if raw.endswith("/") and not resolves(norm):
            norm = norm + "/x"
        checked += 1
        check("%s -> %s resolves to a Route" % (os.path.relpath(p, SRC), raw), resolves(norm))
check("checked at least 15 internal destinations (got %d)" % checked, checked >= 15)

# 2. lazy imports exist ------------------------------------------------------
imps = re.findall(r"import\('\./pages/wholesale/([^']+)'\)", app)
for i in sorted(set(imps)):
    ok = any(os.path.exists(os.path.join(SRC, "pages/wholesale", i + e)) for e in (".jsx", ".js", "/index.jsx"))
    check("lazy import ./pages/wholesale/%s exists" % i, ok)
check("wholesale lazy imports found", len(imps) >= 10)

# 3. enum / mojibake leakage -------------------------------------------------
mojibake = re.compile("Ã[\u0080-¿]|â\u0080[\u0090-¿]|�")
raw_enum = re.compile(r"(?<!=)\{\s*(?:deal|d|row|m|b|buyer|item|r|prop)\.(?:stage|status|channel|audience|deal_result|funding_status|payment_state)\s*\}")
for p in sorted(files):
    text = read(p)
    rel = os.path.relpath(p, SRC)
    check("no mojibake in " + rel, not mojibake.search(text))
    hits = raw_enum.findall(text)
    check("no raw enum rendered bare in %s %s" % (rel, hits[:3]), not hits)

# 4. visible failure reasons --------------------------------------------------
deal_jsx = read(os.path.join(SRC, "pages/wholesale/WholesaleDeal.jsx"))
check("disposition result shows the server's per-buyer reason",
      re.search(r"\.reason", deal_jsx) is not None)
check("disposition surfaces blocked/failed outcomes",
      re.search(r"blocked|Not sent|failed", deal_jsx) is not None)

# 4b. send refusal codes are worded, not shown raw -------------------------
shared = read(os.path.join(SRC, "pages/wholesale/wsShared.jsx"))
disp_py = read(os.path.join(ROOT, "app/services/wholesale_disposition.py"))
codes = set(re.findall(r'SendRefused\([^()]*?,\s*"([a-z_]+)"\s*\)', disp_py, re.S))
codes |= set(re.findall(r'"code":\s*"([a-z_]+)"', disp_py))
codes.discard("sent")  # success, not a refusal
check("found send refusal codes (got %s)" % sorted(codes), len(codes) >= 6)
for code in sorted(codes):
    check("refusal code %r has a plain-language label" % code,
          re.search(r"\b%s:\s*'" % code, shared) is not None)
check("disposition result renders refusalLabel(), not the bare code",
      "refusalLabel(r.code)" in deal_jsx and ": r.code}" not in deal_jsx)

# 4c. buyer-match factors are readable without colour or glyph --------------
check("match factor marks carry an accessible name",
      re.search(r"aria-label=\{f\.matched", shared) is not None)

# 4d. closing screen mirrors the server rules --------------------------------
closing = read(os.path.join(SRC, "pages/wholesale/wsClosing.jsx"))
check("closing does not offer Close on a deal in Dead (server answers 409)",
      "deal.stage === 'dead'" in closing)
check("close fee input blocks and explains a negative value",
      "feeInvalid" in closing and "ws-close-fee-err" in closing)
check("collected-amount input blocks and explains a negative value",
      "amountInvalid" in closing and "pay-amt-err" in closing)

# 4e. every labelled form control in closing has a matching input id ---------
for label_for in sorted(set(re.findall(r'htmlFor="([^"]+)"', closing))):
    check("closing label for=%s has an input" % label_for,
          re.search(r'id="%s"' % re.escape(label_for), closing) is not None)

# 4f. every Wholesale form label is programmatically tied to its control -----
# A bare <label>text</label> next to an input gives the control no accessible
# name. Allowed: htmlFor=, a label that wraps its control, or an sr-only span.
for sub in ("WholesaleBuyers.jsx", "WholesaleSettings.jsx", "wsBuyerBoard.jsx"):
    txt = read(os.path.join(SRC, "pages/wholesale", sub))
    bare = re.findall(r"<label>[^<]*(?:\{[^}]*\})?[^<]*</label>", txt)
    check("%s has no bare unassociated <label> (%s)" % (sub, bare[:2]), not bare)
    for f in sorted(set(re.findall(r'htmlFor="([^"]+)"', txt))):
        check("%s label for=%s has an input id" % (sub, f),
              re.search(r'id="%s"' % re.escape(f), txt) is not None)
buyers = read(os.path.join(SRC, "pages/wholesale/WholesaleBuyers.jsx"))
check("buyer channel select shows worded options, not sms/phone keys",
      "Text message" in buyers and ">{c}</option>" not in buyers)
check("buyer import file input has an id tied to its label",
      'htmlFor="bi-file"' in buyers and 'id="bi-file"' in buyers)

# 6. buyer board / buyers list truthful states and accessible controls -------
board = read(os.path.join(SRC, "pages/wholesale/wsBuyerBoard.jsx"))
check("buyer board load failure offers Try again", "Try again" in board)
check("buyer board keeps rows and warns when only a refresh fails",
      "boardView(state)" in board and "view === 'error'" in board
      and "may be out of date" in board and "loadFailed(s, gen" in board)
check("buyer board error/loading are announced (role alert/status)",
      'role="alert"' in board and 'role="status"' in board)
check("buyer board offer amount is validated, not coerced to NaN",
      "offerInvalid" in board and "Number(draft.offer_amount)" not in board)
check("buyer board resend is blocked for do-not-contact buyers",
      "disabled={busy || row.do_not_contact}" in board)
check("buyer board row buttons name the buyer",
      "aria-label={`Select ${" in board and "aria-label={`Resend deal sheet to" in board
      and "aria-label={`Record response from" in board)
check("buyers list does not claim 'No cash buyers yet' after a failed load",
      "view === 'empty'" in buyers and "view === 'error'" in buyers and "listView(list)" in buyers)

check("board disables Select for a buyer who passed",
      "selectDisabledReason(row)" in board
      and "disabled={busy || Boolean(selectBlocked)}" in board)
check("board proof-of-funds toggle exposes aria-expanded and no bare 'Close'",
      "aria-expanded={pofOpen}" in board and "'Close'" not in board)
check("board selection confirm is an alertdialog",
      'role="alertdialog"' in board)
check("import result separates rejected (alert) from skipped (status)",
      "result.rejected" in buyers and 'role="alert"' in buyers and 'role="status"' in buyers)

# 4b. board runtime state / responsive / keyboard (static source) -----------
state_src = read(os.path.join(SRC, "pages/wholesale/wsBoardState.js"))
wcss = read(os.path.join(SRC, "pages/wholesale/wholesale.css"))
check("board state module is pure (no imports)", not re.search(r"^import ", state_src, re.M))
check("board refresh is generation-guarded", "if (gen !== state.latest) return state" in state_src)
check("board mutations use a synchronous in-flight latch", "inFlight.current.run" in board)
check("board shows a refreshing status while keeping rows",
      "Refreshing" in board and 'role="status"' in board)
check("board response form offers only server-accepted statuses",
      "allowedResponseStatuses(row)" in board)
check("board confirm closes on Escape and returns focus",
      "Escape" in board and "selectTrigger.current" in board)
check("board refusals keep stable code beside words",
      board.count("describeRefusal(") >= 2 and "resendOutcome(r)" in board)
check("board cells carry data-labels for stacked mobile layout",
      board.count("data-label=") >= 7)
check("css stacks the board at phone width without hiding Actions",
      "@media (max-width: 720px)" in wcss and "attr(data-label)" in wcss
      and ".ws-dispo thead" in wcss)
check("css gives board controls a visible focus ring from tokens",
      ".ws-dispo button:focus-visible" in wcss
      and "outline: 2px solid var(--signal-blue)" in wcss)
check("board never submits status 'selected' from the response form",
      "status: 'selected'" not in board)

# 5. light-theme / mobile hooks ---------------------------------------------
css = read(os.path.join(SRC, "components/wholesale-shell.css"))
check("shell css has a mobile breakpoint", "@media" in css and "max-width" in css)

# 6. deal-room action scoping ------------------------------------------------
deal = read(os.path.join(SRC, "pages/wholesale/WholesaleDeal.jsx"))
astate = read(os.path.join(SRC, "pages/wholesale/wsActionState.js"))
check("deal room has no page-wide busy state", "setBusy" not in deal)
check("deal room scopes pending per panel", "panelBusy(tab)" in deal and "createActionTracker" in deal)
check("deal room blocks duplicate mutation synchronously", "tracker.current.begin(k)" in deal)
check("deal room drops stale loads", "isCurrentLoad(gen)" in deal)
check("deal room announces outcomes", 'role="status"' in deal and 'role="alert"' in deal)
check("deal room errors keep reference code", "describeError(errText(e))" in deal)
check("action state module is pure (no imports)", not re.search(r"^import ", astate, re.M))

# 7. offer / document / contract truth ---------------------------------------
W = "pages/wholesale/"
offers_src = read(os.path.join(SRC, W + "wsOffers.jsx"))
docs_src = read(os.path.join(SRC, W + "wsDocuments.jsx"))
contracts_src = read(os.path.join(SRC, W + "wsContracts.jsx"))
ostate = read(os.path.join(SRC, W + "wsOfferState.js"))
dstate = read(os.path.join(SRC, W + "wsDocState.js"))
check("offer state module is pure (no imports)", not re.search(r"^import ", ostate, re.M))
check("doc state module is pure (no imports)", not re.search(r"^import ", dstate, re.M))
check("ledger formats money through the pure helper, not fmtMoney",
      "amountText(" in offers_src and "fmtMoney" not in offers_src)
check("ledger orders offers deterministically", "sortOffers(" in offers_src)
check("ledger record button has its own action key and blocks bad amounts",
      "ACT_RECORD" in offers_src and "amountBad" in offers_src)
check("ledger status select is keyed per offer", "actStatus(o.id)" in offers_src)
check("ledger explains amount problems with a status line",
      'id="ws-offer-amount-hint"' in offers_src and 'role="status"' in offers_src)
check("ledger shows unknown statuses with a reference code",
      "offerStatusText(" in offers_src and "(reference: " in ostate)
check("buyer match shows funds as three states and insufficient evidence",
      "fundsTruth(m)" in deal and "Insufficient evidence" in deal and "matchEvidence(m)" in deal)
check("buyer tab names the selected buyer and sorts without recommending",
      "currentBuyer(deal, matches)" in deal and "sortMatches(" in deal
      and "recommended:" not in ostate and "best:" not in ostate)
check("deal room exposes per-action busy to child panels",
      "isBusy={isBusy}" in deal and "const isBusy = (key)" in deal)
check("offer approvals, match, preview and send have independent keys",
      all(k in deal for k in ("offer:approval:${kind}", "'buyers:match'", "'buyers:preview'", "'buyers:send'", "'documents:contract'")))
check("document rows key every action per document",
      all(k in docs_src for k in ("documents:edit:${doc.id}", "documents:move:${doc.id}",
                                  "documents:sig:${doc.id}", "documents:buyer:${doc.id}",
                                  "documents:owner:${doc.id}")))
check("documents drawer has no whole-drawer busy lock on rows", "busy={busy}" not in docs_src.split("function DocumentDrawer")[1].split("return (")[1])
check("signed is offered only with an executed copy", "signatureOptions(doc)" in docs_src
      and "key === 'signed' && !held" in dstate)
check("document status comes from docTruth, not raw status", "docTruth(doc)" in docs_src)
check("contract form shows truth, warning and missing prerequisites",
      "contractTruth(deal, documents)" in deal and "missingPrerequisites(deal, documents)" in deal)
check("contract status select uses words, not raw keys",
      "CONTRACT_STATUS_LABEL" in deal and "(s) => <option key={s} value={s}>{s}</option>" not in deal)
check("papering sheet has loading, alert, retry and stale-load guard",
      "Loading the papering sheet" in contracts_src and 'role="alert"' in contracts_src
      and "Try again" in contracts_src and "gen.current" in contracts_src)
check("signature capability failure is announced, not swallowed",
      "Signature capability could not be checked" in docs_src and ".catch(() => {})" not in docs_src)

# 8. dialog / settings accessibility ----------------------------------------
files_src = read(os.path.join(SRC, W + "wsFiles.jsx"))
settings_src = read(os.path.join(SRC, W + "WholesaleSettings.jsx"))
confirm = files_src.split("export function ConfirmDelete")[1].split("\n}\n")[0]
check("delete confirm is an alertdialog with a labelled question",
      'role="alertdialog"' in confirm and "aria-labelledby" in confirm)
check("delete confirm closes on Escape (not while busy)", "'Escape'" in confirm and "!busy" in confirm)
check("delete confirm focuses Cancel and returns focus to the opener",
      "cancelRef.current.focus()" in confirm and "el.focus()" in confirm)
check("delete confirm announces pending", 'role="status"' in confirm)
check("settings save is latched synchronously", "saving.current" in settings_src)
check("settings save failure keeps the draft and says so",
      "your changes are still here" in settings_src)
check("settings load failure offers retry", "Try again" in settings_src)
check("settings drops stale loads", "gen !== loadGen.current" in settings_src)
check("settings save button states why it is disabled", "ws-save-reason" in settings_src)
check("css repeats the sr-only class unscoped for Settings",
      re.search(r"^\.ws-vis-hidden \{", wcss, re.M) is not None)
check("css gives offer/document controls a token focus ring",
      ".ws-page .ws-offer-entry button:focus-visible" in wcss
      and ".ws-page .ws-docs select:focus-visible" in wcss)
check("css stacks the negotiation position at phone width",
      ".ws-page .ws-position { display: grid" in wcss)

# 9. per-action keys for Closing / Sharing / Seller / Analysis ---------------
deal_src = read(os.path.join(SRC, W + "WholesaleDeal.jsx"))
closing_src = read(os.path.join(SRC, W + "wsClosing.jsx"))
sharing_src = read(os.path.join(SRC, W + "wsSharing.jsx"))
for key in ("closing:correction", "closing:close", "closing:lost", "closing:fee",
            "closing:assign", "closing:contract", "closing:title"):
    check("closing action keyed: " + key, key in closing_src)
for key in ("sharing:save", "sharing:publish-", "sharing:create-link", "sharing:revoke-"):
    check("sharing action keyed: " + key, key in sharing_src)
for key in ("seller:attach", "seller:reply", "seller:edit", "seller:cadence", "seller:sms",
            "analysis:save", "analysis:recalc"):
    check("deal-room action keyed: " + key, key in deal_src)
check("sharing buttons disable on their own key, not the whole panel",
      "isBusy('sharing:save')" in sharing_src and "isBusy(`sharing:revoke-${link.id}`)" in sharing_src)
check("closing save/assign/fee disable on their own key",
      "isBusy(actionKey)" in closing_src and "isBusy('closing:assign')" in closing_src
      and "isBusy('closing:fee')" in closing_src)
check("sharing drops stale loads", "gen !== loadGen.current" in sharing_src)
check("deal room passes isBusy to Closing and Sharing",
      "<ClosingTab room={room} act={act} busy={panelBusy(tab)} isBusy={isBusy}" in deal_src
      and "busy={panelBusy(tab)} isBusy={isBusy} /> : null}\n      {tab === 'audit'" in deal_src)

# 10. callback queue / follow-up truth ---------------------------------------
cc_src = read(os.path.join(SRC, W + "ops/CallbackCenter.jsx"))
qs_src = read(os.path.join(SRC, W + "wsQueueState.js"))
ops_css = read(os.path.join(SRC, W + "ops/ops.css"))
check("callback center uses pure queue state", "from '../wsQueueState'" in cc_src)
check("callback center sorts deterministically client-side", "sortQueue(" in cc_src)
check("callback center keeps a synchronous in-flight latch", "inFlight.current" in cc_src)
check("callback center drops stale loads", "gen === latest.current" in cc_src)
check("callback center failures use role=alert with retry", 'role="alert"' in cc_src and "Try again" in cc_src)
check("callback center keeps rows on failed refresh (stale state)", "'stale'" in qs_src and "failedRefresh" in qs_src)
check("callback center never shows 0 for unknown count", "bucketCount(q, k)" in cc_src and "data.counts?.[k] ?? 0" not in cc_src)
check("callback center no raw enum status shown", "{it.status}" not in cc_src)
check("callback center exception rows cannot be completed as callbacks", "exceptionOnly" in cc_src)
check("callback center can adopt a seller request", "from-exception/" in cc_src and "Start a callback" in cc_src)
check("callback center keeps outcome note on failure", "Your note is kept" in cc_src)
check("callback center focus ring and narrow layout", ".wso-bucket:focus-visible" in ops_css and "max-width: 640px" in ops_css)
check("backend queue sort has id tiebreak", 'str(i["id"])' in read(os.path.join(ROOT, "app/services/wholesale_ops.py")))

# 11. disposition handoff / appointment truth --------------------------------
ds_src = read(os.path.join(SRC, W + "wsDispositionState.js"))
dtest = read(os.path.join(ROOT, "tests/frontend/wsDispositionState.test.mjs"))
check("deal room uses pure disposition state", "from './wsDispositionState'" in deal_src)
check("send validates before posting and focuses first invalid field", "validateSend(" in deal_src and "?.focus()" in deal_src)
check("asking price no longer coerced with bare Number()", "Number(askingPrice)" not in deal_src)
check("send and preview use their own keys", "'buyers:send'" in deal_src and "'buyers:preview'" in deal_src)
check("send result counted from per-buyer rows", "summarizeOutcome(outcome.data)" in deal_src and "rows.length - sent" in ds_src)
check("send result marked as earlier when inputs change", "outcomeCurrent" in deal_src and "previewCurrent" in deal_src)
check("send result does not claim buyer response", "not a buyer response" in deal_src)
check("send errors are role=alert", "role=\"alert\">{sendErrs" in deal_src)
check("appointment validated before seller save", "validateAppointment(" in deal_src and "appt.firstInvalid" in deal_src)
check("appointment display ties time to status", "appointmentSummary(" in deal_src)
check("appointment unknown status shown as unknown", "'Unknown status'" in ds_src)
check("seller editor errors announced and linked", "aria-describedby={errs[key]" in deal_src)
check("disposition node test covers each helper", all(x in dtest for x in ("parseMoney", "validateSend", "summarizeOutcome", "validateAppointment", "isCurrentResult")))

# 12. buyers / properties recovery states ------------------------------------
ls_src = read(os.path.join(SRC, W + "wsListState.js"))
props_src = read(os.path.join(SRC, W + "WholesaleProperties.jsx"))
ltest = read(os.path.join(ROOT, "tests/frontend/wsListState.test.mjs"))
for name, src in (("buyers", buyers), ("properties", props_src)):
    check(name + " uses pure list state", "from './wsListState'" in src)
    check(name + " drops stale loads", "gen === latest.current" in src or "gen !== latest.current" in src)
    check(name + " failed refresh keeps rows and says they may be out of date", "may be out of date" in src and "view === 'stale'" in src)
    check(name + " load failure is role=alert with retry and support code", "view === 'error'" in src and "support code" in src and "Try again" in src)
    check(name + " retry takes focus and has a disabled reason", "autoFocus" in src and "retryDisabledReason(list)" in src)
    check(name + " refresh announced politely", "role=\"status\"" in src and "Refreshing" in src)
    check(name + " search is trimmed before sending", "cleanQuery(q)" in src)
    check(name + " unknown total is not shown as 0", "knownCount(total)" in src)
    check(name + " truncated page says so", "truncationNote(list)" in src)
check("buyers no failed-load error clobbers rows", "setBuyers(" not in buyers and "setLoadFailed" not in buyers)
check("buyers drawers cannot dereference a vanished row", "findById(buyers, editing)" in buyers and "findById(buyers, expanded)" in buyers)
check("buyers same-action duplicates blocked synchronously", buyers.count("saving.current") >= 12 and "busyLatch.current" in buyers)
check("buyers opted-out status never reads as active", "buyerStatusLabel(" in buyers and "'Opted out'" in ls_src)
check("buyers verified-only filter has its own empty state", "No verified buyers in this list" in buyers)
check("properties enrich only visible selected rows", "rows.filter((p) => selected[p.id])" in props_src and "hiddenChosen" in props_src)
check("properties enrich latch is synchronous", "busyLatch.current" in props_src)
check("properties summary failure keeps last figures and says so", "boardFailed" in props_src and ".catch(() => undefined)" in props_src)
check("backend buyers/properties lists tiebreak by id",
      "WholesaleBuyer.id.asc()" in read(os.path.join(ROOT, "app/routers/wholesale_buyers_router.py"))
      and "WholesaleProperty.id.asc()" in read(os.path.join(ROOT, "app/routers/wholesale_router.py")))
check("list state node test covers each helper", all(x in ltest for x in ("loadSucceeded", "loadFailed", "listView", "supportCode", "knownCount", "truncationNote", "buyerStatusLabel", "isVerifiedBuyer", "findById", "cleanQuery")))

# 13. exceptions / funding partners / command recovery -----------------------
exc = read(os.path.join(SRC, W + "WholesaleExceptions.jsx"))
fp = read(os.path.join(SRC, W + "WholesaleFundingPartners.jsx"))
cmd = read(os.path.join(SRC, W + "WholesaleCommand.jsx"))
wcss = read(os.path.join(SRC, W + "wholesale.css"))
rtest = read(os.path.join(ROOT, "tests/frontend/wsRecoveryState.test.mjs"))
for name, src in (("exceptions", exc), ("funding partners", fp)):
    check(name + " uses pure list state", "from './wsListState'" in src)
    check(name + " drops stale loads", "gen === latest.current" in src)
    check(name + " failed refresh keeps rows and says they may be out of date", "may be out of date" in src and "view === 'stale'" in src)
    check(name + " first-load failure is role=alert with focused retry and support code", "view === 'error'" in src and "autoFocus" in src and "support code" in src)
    check(name + " refresh announced politely", 'role="status"' in src and "Refreshing" in src)
    check(name + " unknown count is not shown as 0", "count={known ? " in src)
    check(name + " buttons are type=button", "<button className" not in src)
check("exceptions scope change resets rows", "changeScope(" in exc and "initialList()" in exc)
check("exceptions no raw status enum", "replace(/_/g, ' ')" not in exc and "exceptionStatusLabel(" in exc)
check("exceptions outcome buttons give a disabled reason and keep the note", "resolveBlockedReason(" in exc and "Your note is kept" in exc)
check("exceptions resolve/assign have a synchronous latch", "latch.current" in exc and "sweepLatch.current" in exc)
check("exceptions assignee list failure is visible with retry", "peopleErr" in exc and ".catch(() => {})" not in exc)
check("exceptions sweep result never claims nothing new on unreadable reply", "sweepSummary(" in exc)
check("exceptions resolved item cannot be resubmitted before refresh lands", "submitted" in exc and "Already submitted" in exc)
check("funding partners no longer refetches via meta dependency", "[meta, showInactive]" not in fp and "metaRef" in fp)
check("funding partners meta failure does not blank the list", "metaFailed" in fp)
check("funding partners form validates before saving and focuses first invalid", "validatePartner(" in fp and ".focus()" in fp and "aria-invalid" in fp)
check("funding partners failed save keeps the form and says so", "Nothing you typed was lost" in fp)
check("funding partners per-action latch, unrelated rows stay usable", "latch.current.has(key)" in fp and "pending['v' + p.id]" in fp and "pending['a' + p.id]" in fp)
check("funding partners vanished edit row is guarded", "findById(partners, editing)" in fp and "no longer in this list" in fp)
check("funding partners stated zero is not hidden and unstated is not zero", "stated(p.min_loan)" in fp and "trackRecordText(" in fp)
check("funding partners filter change resets rows", "changeInactive(" in fp)
check("funding partners table has caption, column scopes and scroll wrapper", "<caption" in fp and 'scope="col"' in fp and "ws-fp__wrap" in fp)
check("funding backend partner list tiebreaks by id", "WholesaleFundingPartner.name, WholesaleFundingPartner.id" in read(os.path.join(ROOT, "app/routers/wholesale_funding_router.py")))
check("command uses pure record state and drops stale loads", "recordStarted(" in cmd and "gen === latest.current" in cmd)
check("command no swallowed deal-room error", ".catch(() => [d.deal_id, null])" not in cmd and "roomFailed" in cmd)
check("command failed refresh keeps data and says it may be out of date", "may be out of date" in cmd and "view === 'stale'" in cmd)
check("command first-load failure is role=alert with focused retry and support code", "autoFocus" in cmd and "support code" in cmd and "Try again" in cmd)
check("command room totals are unknown rather than partial or zero", "roomTotal(" in cmd and "Object.values(rooms).reduce" not in cmd)
check("command room failure has working retry", "onRetry" in cmd and "setRoomsTick" in cmd)
check("command says when figures cover only some deals", "Buyer figures cover the first" in cmd)
check("command sandbox toggle resets to avoid mislabelled figures", "changeSandbox(" in cmd)
check("command closing order has id tiebreak", "sortByKeyThenId(" in cmd and "d.deal_id" in cmd)
check("command unknown fee total is not $0", "fees === null" in cmd and "money(fees)" in cmd)
check("command stage counts unknown is a dash", "knownCount(st.count)" in cmd)
check("recovery css has focus rings, narrow layout and error colour", ".ws-exc__item button:focus-visible" in wcss and ".ws-fp__err" in wcss and ".ws-fp__form { grid-template-columns: 1fr; }" in wcss)
check("recovery node test covers each new helper", all(x in rtest for x in (
    "recordFailed", "recordView", "sortByKeyThenId", "exceptionStatusLabel", "resolveBlockedReason", "sweepSummary",
    "validatePartner", "partnerSaveBlockedReason", "trackRecordText", "roomsLoadState", "roomTotal", "roomIdsToLoad")))

print("wholesale ux static: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
