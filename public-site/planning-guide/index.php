<?php
declare(strict_types=1);
/*
 * https://evosyspro.live/planning-guide - the pre-planning guide that text
 * messages link to ("Here's the planning guide you requested: ...").
 *
 * Public, no login, no form, no tracking, no redirect (/.htaccess serves
 * /planning-guide directly). General information only: no client is named, no
 * prices, no guarantees, no legal or financial advice.
 */
require __DIR__.'/../private/site.php';
site_head('Your Pre-Planning Guide | EvoSys Pro',
          'A plain-language guide to funeral and cemetery pre-planning: why people plan ahead, the main decisions, and a simple checklist.');
site_nav(); ?>
<style>
.pg{max-width:860px;margin:0 auto}
.pg h2{margin:34px 0 10px}
.pg ol,.pg ul{padding-left:1.25em;line-height:1.7}
.pg li{margin:6px 0}
.pg .checklist{list-style:none;padding-left:0}
.pg .checklist li{padding-left:30px;position:relative}
.pg .checklist li:before{content:"";position:absolute;left:0;top:.35em;width:16px;height:16px;border:2px solid #70e5de;border-radius:4px}
.pg .note{font-size:.92rem;opacity:.85;margin-top:28px}
.pg .printbtn{margin-top:18px}
@media print{nav,footer,.chat,.printbtn,.ask{display:none!important}body{background:#fff!important;color:#000!important}.pg *{color:#000!important}.pg .checklist li:before{border-color:#000}}
</style>
<main><section class="page-hero"><div class="wrap"><div class="eyebrow">Pre-Planning</div><h1>Your Pre-Planning Guide</h1><p>A short, plain-language guide to planning funeral and cemetery arrangements ahead of time. Presented by EvoSys Pro on behalf of participating funeral home and cemetery locations.</p></div></section>
<section><div class="wrap pg">

<h2>Why people plan ahead</h2>
<ul>
<li><strong>Your wishes are known.</strong> The people closest to you do not have to guess what you would have wanted.</li>
<li><strong>Fewer decisions at a hard time.</strong> Your family can focus on each other instead of a long list of choices made under pressure.</li>
<li><strong>Time to compare and ask questions.</strong> Planning now lets you take your time, talk it over, and decide what fits you.</li>
</ul>

<h2>The main decisions</h2>
<ol>
<li><strong>Burial or cremation.</strong> Think about which feels right for you and your family, and whether faith or tradition guides the choice.</li>
<li><strong>Cemetery property.</strong> If you choose burial or a place of memorial, consider the location, whether you want space near family, and the type of property (for example a ground space, a mausoleum crypt or a cremation niche).</li>
<li><strong>The service.</strong> A traditional service, a memorial gathering, a graveside service, or something simple and private. Note any music, readings, clergy or people you would like involved.</li>
<li><strong>Personal touches.</strong> Photos, favorite clothing, flowers, charities in lieu of flowers, or a theme that reflects your life.</li>
<li><strong>Who should be involved.</strong> Decide who you want to make arrangements on your behalf and let them know where your plans are kept.</li>
</ol>

<h2>Documents and information to gather</h2>
<ul>
<li>Full legal name, date and place of birth, and Social Security number (keep this somewhere safe; you do not need to share it by text)</li>
<li>Parents' names, including mother's maiden name</li>
<li>Military discharge papers (DD-214), if you served</li>
<li>Any existing cemetery deeds, prior arrangements or insurance policies</li>
<li>Contact details for the people who should be notified</li>
</ul>

<h2>A simple checklist</h2>
<ul class="checklist">
<li>Decide between burial and cremation</li>
<li>Decide where you would like to be laid to rest or memorialized</li>
<li>Write down the kind of service you would like</li>
<li>Gather the documents listed above in one place</li>
<li>Choose who will carry out your wishes and tell them where your plans are</li>
<li>Ask your local representative any questions you have</li>
</ul>

<h2>What happens next</h2>
<p>A representative from your local location can walk you through any of these steps and answer your questions. If you received this guide by text, just reply to that message. There is no obligation, and you can take as much time as you need.</p>
<p>Reply <strong>STOP</strong> to any text message to opt out, or <strong>HELP</strong> for help.</p>

<button class="btn btn-ghost-gold printbtn" type="button" onclick="window.print()">Print or save as PDF</button>

<p class="note">This guide is general information to help you think through your options. It is not legal, financial or tax advice, and it does not describe the prices, products or terms of any particular location. Your local location can explain the options available to you.</p>
<p class="note"><a href="../privacy.html" style="color:#70e5de">Privacy Policy</a> · <a href="../sms-terms.html" style="color:#70e5de">SMS Terms</a> · <a href="../terms.html" style="color:#70e5de">Terms</a></p>

</div></section></main>
<?php site_footer(); ?>
