<?php
declare(strict_types=1);
/*
 * EvoSys Pro Universal SMS Consent Center - https://evosyspro.live/sms-optin/
 *
 * One page, one program per visit (?program=wholesale | ?program=sci; none =
 * the general EvoSys Pro program, whose page is unchanged). Every program shows
 * its own sender and its own wording; the checkbox is never pre-checked.
 *
 * Evidence is kept twice: a CSV row on this host (per program), and the
 * platform's consent ledger (sms_consent_records) via SMS_OPTIN_WEBHOOK_URL,
 * which stamps its OWN server time and the program, wording and version.
 * Nothing here sends a text.
 */
require __DIR__.'/../private/form-utils.php';
require __DIR__.'/../private/site.php';
require __DIR__.'/../private/sms-programs.php';

$key = sms_program_key();
$programs = sms_programs();
$p = $key !== null ? $programs[$key] : null;
$open = $p !== null && sms_program_open($p);
$errors = [];
$success = false;
$sourceUrl = 'https://evosyspro.live/sms-optin/'.($key !== null && $key !== 'general' ? '?program='.$key : '');

if ($p !== null && $_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!$open) {
        $errors[] = 'This opt-in is not open yet.';
    } else {
        if (evosys_honeypot()) { header('Location: ./?'.($key !== 'general' ? 'program='.$key.'&' : '').'success=1'); exit; }
        if (evosys_too_fast()) $errors[] = 'Please wait a moment and submit again.';
        $name = evosys_clean((string)($_POST['name'] ?? ''), 120);
        $phone = evosys_clean((string)($_POST['phone'] ?? ''), 40);
        $email = evosys_clean((string)($_POST['email'] ?? ''), 180);
        $consent = isset($_POST['consent']);
        if (strlen(preg_replace('/\D/', '', $phone)) < 10) $errors[] = 'A valid mobile phone number is required.';
        if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) $errors[] = 'Please enter a valid email or leave it blank.';
        if (!$consent) $errors[] = 'You must check the consent box to opt in to SMS.';
        if (!$errors) {
            // The general program keeps its original CSV columns exactly.
            $payload = ['submitted_at' => gmdate('c'), 'name' => $name, 'phone' => $phone, 'email' => $email,
                        'consent' => 'YES', 'consent_text' => $p['disclosure'], 'consent_version' => $p['version'],
                        'source_url' => $sourceUrl, 'ip' => evosys_ip(), 'user_agent' => $_SERVER['HTTP_USER_AGENT'] ?? ''];
            $program = ['program' => $key, 'sender' => $p['sender'], 'form_id' => $p['form_id'],
                        'form_version' => $p['form_version']];
            $row = $key === 'general' ? $payload : array_merge($program, $payload);
            $ok = evosys_store(__DIR__.'/../storage/'.$p['csv'], array_keys($row), array_values($row));
            $body = "New EvoSys Pro SMS opt-in\n\n";
            foreach (array_merge($program, $payload) as $k => $v) $body .= ucwords(str_replace('_', ' ', $k)).': '.$v."\n";
            evosys_notify($key === 'general' ? 'New EvoSys Pro SMS Opt-In' : 'New SMS Opt-In ('.$key.')', $body, $email);
            evosys_webhook('SMS_OPTIN_WEBHOOK_URL', array_merge($payload, $program));
            if ($ok) $success = true;
            else $errors[] = 'We could not record your opt-in. Please contact support@evosyspro.live.';
        }
    }
}

if ($p === null) {
    http_response_code(404);
    site_head('SMS Opt-In | EvoSys Pro', 'EvoSys Pro SMS opt-in.');
    site_nav(); ?>
<main><section class="page-hero"><div class="wrap"><div class="eyebrow">SMS Opt-In</div><h1>Program not found.</h1><p>That SMS program does not exist. Nothing was recorded. For EvoSys Pro service messages, use the <a href="./" style="color:#70e5de">EvoSys Pro SMS opt-in</a>.</p></div></section></main>
<?php site_footer(); exit; }

$q = $key !== 'general' ? '?program='.$key : '';
site_head($p['meta_title'], $p['meta_desc']);
site_nav(); ?>
<main><section class="page-hero"><div class="wrap"><div class="eyebrow"><?=htmlspecialchars($p['eyebrow'])?></div><h1><?=htmlspecialchars($p['title'])?></h1><p><?=htmlspecialchars($p['intro'])?></p><?php if ($key !== 'general'): ?><p class="form-meta" data-sender>Sender: <strong><?=htmlspecialchars($p['sender'])?></strong></p><?php endif; ?></div></section><section><div class="wrap form-shell"><div class="form-card"><?php if (!$open): ?><div class="error" data-pending><strong>This opt-in is not open yet.</strong><br>The text-message program for this page is still being finalized. No opt-in can be submitted here and nothing is recorded. Please check back soon.</div><?php if ($p['disclosure'] !== ''): ?><div class="form-meta" data-pending-wording style="margin-top:14px">Proposed consent wording (pending final review, version <?=htmlspecialchars($p['version'])?>): &ldquo;<?=htmlspecialchars($p['disclosure'])?>&rdquo;</div><?php endif; ?><?php elseif ($success || isset($_GET['success'])): ?><div class="success"><strong>Your SMS opt-in has been recorded.</strong><br>Message frequency varies. Message &amp; data rates may apply. Reply STOP to unsubscribe or HELP for help.</div><?php else: ?><?php if ($errors): ?><div class="error"><strong>Please fix the following:</strong><ul><?php foreach ($errors as $e): ?><li><?=htmlspecialchars($e)?></li><?php endforeach; ?></ul></div><?php endif; ?><form method="post" action="./<?=$q?>" novalidate><input class="hp" name="website_url" tabindex="-1" autocomplete="off"><input type="hidden" name="form_started_at" data-started><input type="hidden" name="program" value="<?=htmlspecialchars($key)?>"><div class="form-grid"><div class="field"><label>Full name</label><input name="name" autocomplete="name" placeholder="Your name" value="<?=htmlspecialchars($_POST['name'] ?? '')?>"></div><div class="field"><label>Mobile phone number *</label><input name="phone" required autocomplete="tel" placeholder="(000) 000-0000" value="<?=htmlspecialchars($_POST['phone'] ?? '')?>"></div><div class="field full"><label>Email address</label><input type="email" name="email" autocomplete="email" placeholder="you@example.com" value="<?=htmlspecialchars($_POST['email'] ?? '')?>"></div><div class="field full"><label class="check"><input type="checkbox" name="consent" value="1" required><span><span data-disclosure><?=htmlspecialchars($p['disclosure'])?></span><?php if ($p['links_suffix']): ?> View our <a href="../sms-terms.html">SMS Terms</a> and <a href="../privacy.html">Privacy Policy</a>.<?php else: ?> (<a href="../privacy.html">Privacy Policy</a> · <a href="../sms-terms.html">SMS Terms</a>)<?php endif; ?></span></label></div><div class="field full"><button class="btn btn-gold" type="submit"><?=htmlspecialchars($p['button'])?></button><div class="form-meta">No purchase required. You can opt out at any time by replying STOP. No mobile information will be sold or shared with third parties for their own marketing purposes.</div></div></div></form><?php endif; ?></div><aside class="form-side"><div class="card"><div class="eyebrow">Program Disclosure</div><h3><?=htmlspecialchars($p['side_title'])?></h3><p><?=htmlspecialchars($p['side_text'])?></p></div><div class="card"><div class="eyebrow">Opt Out Anytime</div><p>Reply <strong>STOP</strong> to unsubscribe. Reply <strong>HELP</strong> for help. Consent is not a condition of purchase.</p></div><div class="card"><div class="eyebrow">Legal</div><p><a href="../sms-terms.html" style="color:#70e5de">SMS Terms</a> · <a href="../privacy.html" style="color:#70e5de">Privacy Policy</a> · <a href="../compliance.html" style="color:#70e5de">Trust &amp; Compliance</a></p></div><?php if ($key === 'general'): ?><div class="card"><div class="eyebrow">Selling a Property?</div><p>This page is for EvoSys Pro service messages. EvoSys Wholesale seller text messages are a separate program: sellers opt in with the optional box on the <a href="/sell" style="color:#70e5de">seller inquiry form</a> or on the <a href="./?program=wholesale" style="color:#70e5de">Wholesale SMS opt-in</a>.</p></div><?php elseif ($key === 'sci'): ?><div class="card"><div class="eyebrow">Planning Guide</div><p>Looking for the pre-planning guide? <a href="../planning-guide/" style="color:#70e5de">Read it here</a>.</p></div><?php endif; ?></aside></div></section></main><?php site_footer(); ?>
