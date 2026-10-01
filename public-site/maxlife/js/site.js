/* Max Life public site. No framework, no build step. */
(function () {
  "use strict";
  var CFG = window.MAXLIFE_SITE || { apiBase: "", platformSlug: "maxlife", consentVersion: "" };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var yearEl = $("#year");
  if (yearEl) yearEl.textContent = String(new Date().getFullYear());

  /* ---------- responsive nav ---------- */
  var toggle = $(".nav__toggle"), menu = $("#nav-menu");
  function closeMenu() { if (!toggle) return; toggle.setAttribute("aria-expanded", "false"); menu.classList.remove("is-open"); }
  if (toggle && menu) {
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!open));
      menu.classList.toggle("is-open", !open);
      if (!open) { var first = $("a", menu); if (first) first.focus(); }
    });
    $$("a", menu).forEach(function (a) { a.addEventListener("click", closeMenu); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") { closeMenu(); toggle.focus(); }
    });
  }

  /* ---------- UTM capture (kept for the visit; storage may be unavailable) ---------- */
  var UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"];
  var utm = {};
  try { utm = JSON.parse(sessionStorage.getItem("maxlife_utm") || "{}") || {}; } catch (e) { utm = {}; }
  try {
    var qs = new URLSearchParams(window.location.search);
    UTM_KEYS.forEach(function (k) { var v = qs.get(k); if (v) utm[k] = v.slice(0, 200); });
    sessionStorage.setItem("maxlife_utm", JSON.stringify(utm));
  } catch (e) { /* private mode: keep in memory only */ }

  /* ---------- intake tabs ---------- */
  var tabs = $$('.intake__tabs [role="tab"]');
  function selectTab(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () { selectTab(tab, false); });
    tab.addEventListener("keydown", function (e) {
      var n = null;
      if (e.key === "ArrowRight") n = tabs[(i + 1) % tabs.length];
      if (e.key === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
      if (e.key === "Home") n = tabs[0];
      if (e.key === "End") n = tabs[tabs.length - 1];
      if (n) { e.preventDefault(); selectTab(n, true); }
    });
  });
  function openForm(journey, interest) {
    var tab = journey === "builder" ? $("#tab-builder") : $("#tab-family");
    if (tab) selectTab(tab, false);
    var form = journey === "builder" ? $("#builder-intake") : $("#family-intake");
    if (!form) return;
    if (interest) {
      $$('input[name="interests"]', form).forEach(function (cb) { if (cb.value === interest) cb.checked = true; });
    }
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    var first = $("input[name=first_name]", form);
    if (first) setTimeout(function () { first.focus({ preventScroll: true }); }, 450);
  }
  // In-page links to a form also switch the tab.
  $$('a[href="#family-intake"], a[href="#builder-intake"]').forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      openForm(a.getAttribute("href") === "#builder-intake" ? "builder" : "family");
    });
  });

  /* ---------- Find Your Path ---------- */
  var STEP2 = {
    family: { journey: "family", options: [["Family protection", "Making sure my family is protected"], ["Retirement planning", "Planning retirement income"], ["Living benefits", "Understanding living benefits"], ["Legacy planning", "Leaving a legacy"]] },
    business: { journey: "family", options: [["Business-owner planning", "Protecting the business and key people"], ["Legacy planning", "Succession and what I pass on"], ["Retirement planning", "My own retirement as an owner"]] },
    career: { journey: "builder", options: [["Licensing", "Getting licensed"], ["Training", "Training and mentorship"], ["Team building", "Building and leading a team"], ["Agency ownership", "Owning an agency"]] },
    learn: { journey: "family", options: [["Financial education", "How protection and money work"], ["Opportunity", "What working in this field is like"]] }
  };
  var RESULT = {
    family: { title: "Families & Individuals", text: "Your next step is a no-pressure conversation focused on education and your family's goals.", cta: "Start a family conversation" },
    builder: { title: "Agents & Builders", text: "Your next step is an honest conversation about the path: licensing, training, leadership and ownership.", cta: "Explore the opportunity" }
  };
  var finder = $("#finder");
  if (finder) {
    var s1 = $('[data-step="1"]', finder), s2 = $('[data-step="2"]', finder), s2box = $("#finder-step2");
    var result = $("#finder-result"), back = $("#finder-back"), reset = $("#finder-reset");
    var why = null;
    function show(step) {
      s1.hidden = step !== 1; s2.hidden = step !== 2; result.hidden = step !== 3;
      back.hidden = step === 1; reset.hidden = step !== 3;
    }
    $$('input[name="why"]', s1).forEach(function (r) {
      r.addEventListener("change", function () {
        why = r.value;
        s2box.innerHTML = "";
        STEP2[why].options.forEach(function (o, idx) {
          var lab = document.createElement("label"); lab.className = "choice";
          var inp = document.createElement("input"); inp.type = "radio"; inp.name = "focus"; inp.value = o[0]; inp.id = "focus-" + idx;
          var sp = document.createElement("span"); sp.textContent = o[1];
          lab.appendChild(inp); lab.appendChild(sp); s2box.appendChild(lab);
          inp.addEventListener("change", function () { finish(o[0]); });
        });
        show(2);
        var f = $("input", s2box); if (f) f.focus();
      });
    });
    function finish(interest) {
      var journey = (interest === "Opportunity") ? "builder" : STEP2[why].journey;
      var r = RESULT[journey];
      result.innerHTML = "";
      var p0 = document.createElement("p"); p0.className = "eyebrow"; p0.textContent = "Your path";
      var h = document.createElement("h3"); h.textContent = r.title;
      var p = document.createElement("p"); p.textContent = r.text;
      var b = document.createElement("button"); b.type = "button"; b.className = "btn btn--gold"; b.textContent = r.cta;
      b.addEventListener("click", function () { openForm(journey, interest); });
      [p0, h, p, b].forEach(function (n) { result.appendChild(n); });
      show(3);
      b.focus();
    }
    back.addEventListener("click", function () {
      if (!result.hidden) { show(2); var c = $("input:checked", s2box) || $("input", s2box); if (c) c.focus(); }
      else { show(1); var c1 = $("input:checked", s1) || $("input", s1); if (c1) c1.focus(); }
    });
    reset.addEventListener("click", function () {
      finder.reset(); why = null; show(1); var f = $("input", s1); if (f) f.focus();
    });
  }

  /* ---------- intake forms ---------- */
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  function digits(s) { return (s || "").replace(/\D/g, ""); }
  function setErr(input, msg) {
    var id = input.id + "-err", old = document.getElementById(id);
    if (old) old.remove();
    var described = (input.getAttribute("aria-describedby") || "").split(" ").filter(function (x) { return x && x !== id; });
    if (msg) {
      var p = document.createElement("p"); p.className = "err"; p.id = id; p.textContent = msg;
      input.parentNode.appendChild(p);
      input.setAttribute("aria-invalid", "true");
      described.push(id);
    } else {
      input.removeAttribute("aria-invalid");
    }
    if (described.length) input.setAttribute("aria-describedby", described.join(" ")); else input.removeAttribute("aria-describedby");
  }
  function validate(form) {
    var first = form.elements.first_name, email = form.elements.email, phone = form.elements.phone;
    var bad = [];
    setErr(first, ""); setErr(email, ""); setErr(phone, "");
    if (!first.value.trim()) { setErr(first, "Please enter your first name."); bad.push(first); }
    var e = email.value.trim(), d = digits(phone.value);
    if (e && !EMAIL_RE.test(e)) { setErr(email, "Please enter a valid email address."); bad.push(email); }
    if (phone.value.trim() && !(d.length === 10 || (d.length === 11 && d.charAt(0) === "1"))) { setErr(phone, "Please enter a 10-digit US phone number."); bad.push(phone); }
    if (!e && !d) { setErr(email, "Enter an email or a phone number so we can reply."); bad.push(email); }
    return bad;
  }
  function payloadFor(form) {
    var consentBox = form.elements.consent;
    var consentLabel = $("[data-consent-text]", form);
    var interests = $$('input[name="interests"]:checked', form).map(function (c) { return c.value; });
    var data = {
      journey: form.getAttribute("data-journey"),
      form_name: form.getAttribute("data-form-name"),
      source: "maxlife_public_website",
      first_name: form.elements.first_name.value.trim(),
      last_name: form.elements.last_name.value.trim(),
      email: form.elements.email.value.trim(),
      phone: form.elements.phone.value.trim(),
      message: form.elements.message.value.trim(),
      interests: interests.join(", "),
      page_url: window.location.href,
      page_path: window.location.pathname + window.location.hash,
      referrer: document.referrer || null,
      submitted_at: new Date().toISOString(),
      user_agent: navigator.userAgent,
      // Consent: exactly what the box says. Unchecked -> false. Never inferred.
      consent: !!(consentBox && consentBox.checked),
      consent_text: consentLabel ? consentLabel.textContent.replace(/\s+/g, " ").trim() : null,
      consent_version: CFG.consentVersion || null
    };
    ["state", "best_time", "license_status", "background"].forEach(function (k) {
      if (form.elements[k] && form.elements[k].value) data[k] = form.elements[k].value;
    });
    Object.keys(utm).forEach(function (k) { data[k] = utm[k]; });
    return data;
  }
  function endpoint(form) {
    var path = form.getAttribute("action") || "";
    if (CFG.platformSlug) path = path.replace(/\/site-intake\/[^/]+\//, "/site-intake/" + encodeURIComponent(CFG.platformSlug) + "/");
    return (CFG.apiBase || "").replace(/\/$/, "") + path;
  }
  $$(".intake__form").forEach(function (form) {
    var status = $(".form-status", form), btn = $('button[type="submit"]', form);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      status.className = "form-status"; status.textContent = "";
      var bad = validate(form);
      if (bad.length) { bad[0].focus(); status.className = "form-status is-err"; status.textContent = "Please fix the highlighted fields."; return; }
      btn.disabled = true; var label = btn.textContent; btn.textContent = "Sending…";
      fetch(endpoint(form), {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(payloadFor(form))
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) { return { ok: res.ok, body: body }; });
      }).then(function (r) {
        if (r.ok) {
          form.reset();
          status.className = "form-status is-ok";
          status.textContent = "Thank you. Your request was received and someone from our team will reach out.";
        } else {
          status.className = "form-status is-err";
          status.textContent = (r.body && typeof r.body.detail === "string") ? r.body.detail : "We couldn't send that just now. Please try again in a moment.";
        }
      }).catch(function () {
        status.className = "form-status is-err";
        status.textContent = "We couldn't reach our server. Please check your connection and try again.";
      }).then(function () { btn.disabled = false; btn.textContent = label; });
    });
  });
})();
