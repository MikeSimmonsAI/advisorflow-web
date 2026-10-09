"""The public pre-planning guide, served by the platform at GET /planning-guide.

SCI text messages link a family to "the planning guide you requested". That
link must work on every deployment (staging today, production after its own
GO) without waiting on a separate website upload, so the platform serves the
same guide as https://evosyspro.live/planning-guide itself - beside the hosted
flyers it already serves at /program-assets/<token>.

General information only: no client is named, no prices, no guarantees, no
legal or financial advice, no form, no script, no tracking, no external asset.
"""

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your Pre-Planning Guide | EvoSys Pro</title>
<meta name="description" content="A plain-language guide to funeral and cemetery pre-planning.">
<style>
:root{--ink:#14202b;--muted:#4a5a69;--line:#d9e1e8;--accent:#0f6f6a;--bg:#f7f9fb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:28px 16px 56px}
header{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:8px}
.eyebrow{font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-size:2rem;line-height:1.2;margin:.3em 0}h2{font-size:1.25rem;margin:1.8em 0 .5em}
p,li{color:var(--ink)}.lead{color:var(--muted)}ul,ol{padding-left:1.25em}li{margin:.35em 0}
.check{list-style:none;padding-left:0}.check li{padding-left:1.9em;position:relative}
.check li:before{content:"";position:absolute;left:0;top:.3em;width:1.05em;height:1.05em;border:2px solid var(--accent);border-radius:4px}
.note{font-size:.9rem;color:var(--muted);border-top:1px solid var(--line);margin-top:2.2em;padding-top:1em}
@media print{body{background:#fff}.check li:before{border-color:#000}}
</style></head><body><main>
<header><div class="eyebrow">Pre-Planning</div><h1>Your Pre-Planning Guide</h1>
<p class="lead">A short, plain-language guide to planning funeral and cemetery arrangements ahead of time.
Presented by EvoSys Pro on behalf of participating funeral home and cemetery locations.</p></header>

<h2>Why people plan ahead</h2><ul>
<li><strong>Your wishes are known.</strong> The people closest to you do not have to guess what you would have wanted.</li>
<li><strong>Fewer decisions at a hard time.</strong> Your family can focus on each other instead of a long list of choices made under pressure.</li>
<li><strong>Time to compare and ask questions.</strong> Planning now lets you take your time, talk it over, and decide what fits you.</li></ul>

<h2>The main decisions</h2><ol>
<li><strong>Burial or cremation.</strong> Think about which feels right for you and your family, and whether faith or tradition guides the choice.</li>
<li><strong>Cemetery property.</strong> If you choose burial or a place of memorial, consider the location, whether you want space near family, and the type of property (for example a ground space, a mausoleum crypt or a cremation niche).</li>
<li><strong>The service.</strong> A traditional service, a memorial gathering, a graveside service, or something simple and private. Note any music, readings, clergy or people you would like involved.</li>
<li><strong>Personal touches.</strong> Photos, favorite clothing, flowers, charities in lieu of flowers, or a theme that reflects your life.</li>
<li><strong>Who should be involved.</strong> Decide who you want to make arrangements on your behalf and let them know where your plans are kept.</li></ol>

<h2>Documents and information to gather</h2><ul>
<li>Full legal name, date and place of birth, and Social Security number (keep this somewhere safe; you do not need to share it by text)</li>
<li>Parents' names, including mother's maiden name</li>
<li>Military discharge papers (DD-214), if you served</li>
<li>Any existing cemetery deeds, prior arrangements or insurance policies</li>
<li>Contact details for the people who should be notified</li></ul>

<h2>A simple checklist</h2><ul class="check">
<li>Decide between burial and cremation</li>
<li>Decide where you would like to be laid to rest or memorialized</li>
<li>Write down the kind of service you would like</li>
<li>Gather the documents listed above in one place</li>
<li>Choose who will carry out your wishes and tell them where your plans are</li>
<li>Ask your local representative any questions you have</li></ul>

<h2>What happens next</h2>
<p>A representative from your local location can walk you through any of these steps and answer your questions.
If you received this guide by text, just reply to that message. There is no obligation, and you can take as much time as you need.</p>
<p>Reply <strong>STOP</strong> to any text message to opt out, or <strong>HELP</strong> for help. To keep a copy, use your browser's Print option and choose "Save as PDF".</p>

<p class="note">This guide is general information to help you think through your options. It is not legal, financial or tax advice,
and it does not describe the prices, products or terms of any particular location. Your local location can explain the options available to you.
<br>EvoSys Pro is owned and operated by EVO Integrated Solutions LLC.
<a href="https://evosyspro.live/privacy.html">Privacy Policy</a> &middot; <a href="https://evosyspro.live/sms-terms.html">SMS Terms</a></p>
</main></body></html>
"""
