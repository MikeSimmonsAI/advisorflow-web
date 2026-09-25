<?php
declare(strict_types=1);
/*
 * EvoSys Wholesale - seller inquiry page (https://evosyspro.live/sell).
 *
 * The browser posts HERE. This script validates, then posts server-to-server
 * to the platform intake URL in private/config.php (WHOLESALE_SELLER_INTAKE_URL).
 * The browser never sees that URL, the intake key, or any organization id.
 *
 * SMS consent is SEPARATE, OPTIONAL and UNCHECKED. The inquiry works without
 * it. The exact disclosure below is what the platform files as evidence, with
 * the platform's own timestamp - nothing here supplies a consent time.
 */
require __DIR__.'/../private/form-utils.php';

const SELL_FORM_VERSION = 'sell-v1';
const SELL_DISCLOSURE_VERSION = 'evo-wholesale-sell-2026-09-25';
const SELL_DISCLOSURE = 'By checking this box, I agree to receive SMS text messages from EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) regarding my property inquiry, including follow-up questions, appointment scheduling, and transaction updates. Message frequency varies. Message and data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of any service. View our Privacy Policy and Terms.';

$STATES = ['AL'=>'Alabama','AK'=>'Alaska','AZ'=>'Arizona','AR'=>'Arkansas','CA'=>'California','CO'=>'Colorado','CT'=>'Connecticut','DE'=>'Delaware','DC'=>'District of Columbia','FL'=>'Florida','GA'=>'Georgia','HI'=>'Hawaii','ID'=>'Idaho','IL'=>'Illinois','IN'=>'Indiana','IA'=>'Iowa','KS'=>'Kansas','KY'=>'Kentucky','LA'=>'Louisiana','ME'=>'Maine','MD'=>'Maryland','MA'=>'Massachusetts','MI'=>'Michigan','MN'=>'Minnesota','MS'=>'Mississippi','MO'=>'Missouri','MT'=>'Montana','NE'=>'Nebraska','NV'=>'Nevada','NH'=>'New Hampshire','NJ'=>'New Jersey','NM'=>'New Mexico','NY'=>'New York','NC'=>'North Carolina','ND'=>'North Dakota','OH'=>'Ohio','OK'=>'Oklahoma','OR'=>'Oregon','PA'=>'Pennsylvania','RI'=>'Rhode Island','SC'=>'South Carolina','SD'=>'South Dakota','TN'=>'Tennessee','TX'=>'Texas','UT'=>'Utah','VT'=>'Vermont','VA'=>'Virginia','WA'=>'Washington','WV'=>'West Virginia','WI'=>'Wisconsin','WY'=>'Wyoming'];
$CONDITIONS = ['excellent'=>'Excellent - move-in ready','good'=>'Good - minor updates','fair'=>'Fair - needs some work','poor'=>'Poor - needs major repairs','distressed'=>'Distressed - significant damage'];
$TIMELINES = ['asap'=>'As soon as possible','30_days'=>'Within 30 days','90_days'=>'Within 90 days','6_months'=>'Within 6 months','no_rush'=>'No rush - just exploring'];
$METHODS = ['phone'=>'Phone call','email'=>'Email','sms'=>'Text message (requires the SMS box below)'];

function sell_val(string $k): string { return htmlspecialchars((string)($_POST[$k] ?? ''), ENT_QUOTES); }
function sell_sel(string $k, string $v): string { return ((string)($_POST[$k] ?? '')) === $v ? ' selected' : ''; }
function sell_e164(string $raw): ?string {
    $d = preg_replace('/\D/', '', $raw) ?? '';
    if (strlen($d) === 11 && $d[0] === '1') $d = substr($d, 1);
    if (strlen($d) !== 10 || $d[0] === '0' || $d[0] === '1' || $d[3] === '0' || $d[3] === '1') return null;
    return '+1'.$d;
}
function sell_err(array $errors, string $k): string {
    return isset($errors[$k]) ? '<p class="s-err" id="err-'.$k.'">'.htmlspecialchars($errors[$k]).'</p>' : '';
}
function sell_aria(array $errors, string $k, string $hint = ''): string {
    $ids = trim(($hint ? $hint.' ' : '').(isset($errors[$k]) ? 'err-'.$k : ''));
    return (isset($errors[$k]) ? ' aria-invalid="true"' : '').($ids !== '' ? ' aria-describedby="'.$ids.'"' : '');
}

$errors = [];
$result = null;
$fatal = null;
$publicPhone = evosys_cfg('WHOLESALE_PUBLIC_PHONE', '');
$publicEmail = evosys_cfg('WHOLESALE_PUBLIC_EMAIL', '');

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (evosys_honeypot()) { $result = ['sms' => false, 'reference' => null]; }
    else {
        if (evosys_too_fast()) $fatal = 'That was quick - please wait a moment and submit again.';
        $in = [];
        foreach (['full_name'=>120,'phone'=>40,'email'=>180,'street_address'=>200,'city'=>100,'state'=>2,'zip_code'=>10,'property_condition'=>40,'timeline'=>40,'reason_for_selling'=>500,'preferred_contact_method'=>20,'notes'=>2000] as $k=>$n) {
            $in[$k] = evosys_clean((string)($_POST[$k] ?? ''), $n);
        }
        $sms = isset($_POST['sms_consent']) && $_POST['sms_consent'] === 'yes';
        if (mb_strlen($in['full_name']) < 2) $errors['full_name'] = 'Please enter your full name.';
        if (!sell_e164($in['phone'])) $errors['phone'] = 'Please enter a valid 10-digit US mobile number.';
        if ($in['email'] !== '' && !filter_var($in['email'], FILTER_VALIDATE_EMAIL)) $errors['email'] = 'Please enter a valid email address, or leave it blank.';
        if (mb_strlen($in['street_address']) < 3) $errors['street_address'] = "Please enter the property's street address.";
        if ($in['city'] === '') $errors['city'] = 'Please enter the city.';
        if (!isset($STATES[$in['state']])) $errors['state'] = 'Please choose the state.';
        if (!preg_match('/^\d{5}(-\d{4})?$/', $in['zip_code'])) $errors['zip_code'] = 'Please enter a 5-digit ZIP code.';
        if ($in['property_condition'] !== '' && !isset($CONDITIONS[$in['property_condition']])) $errors['property_condition'] = 'Please choose a condition.';
        if ($in['timeline'] !== '' && !isset($TIMELINES[$in['timeline']])) $errors['timeline'] = 'Please choose a timeline.';
        if (!isset($METHODS[$in['preferred_contact_method']])) $errors['preferred_contact_method'] = 'Please choose how we should contact you.';
        if ($in['preferred_contact_method'] === 'sms' && !$sms) $errors['preferred_contact_method'] = 'To be contacted by text, check the SMS consent box below - or choose phone or email.';
        if ($in['preferred_contact_method'] === 'email' && $in['email'] === '') $errors['email'] = 'Please enter your email address, or choose another contact method.';

        if (!$errors && !$fatal) {
            $url = evosys_cfg('WHOLESALE_SELLER_INTAKE_URL', '');
            $payload = $in + [
                'sms_consent' => $sms,
                'disclosure_text' => $sms ? SELL_DISCLOSURE : null,
                'disclosure_version' => SELL_DISCLOSURE_VERSION,
                'form_version' => SELL_FORM_VERSION,
                'source_url' => 'https://evosyspro.live/sell',
                'submission_id' => preg_replace('/[^a-f0-9]/', '', (string)($_POST['submission_id'] ?? '')) ?: bin2hex(random_bytes(16)),
                'ip' => evosys_ip(),
                'user_agent' => substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 400),
            ];
            $r = evosys_post_json_url($url, $payload, 20);
            if ($r['ok'] && is_array($r['data']) && !empty($r['data']['success'])) {
                $result = ['sms' => !empty($r['data']['sms_consent_recorded']), 'reference' => (string)($r['data']['reference'] ?? '')];
            } elseif ($r['status'] === 422 && isset($r['data']['detail']['errors']) && is_array($r['data']['detail']['errors'])) {
                foreach ($r['data']['detail']['errors'] as $k => $m) $errors[(string)$k] = (string)$m;
            } else {
                error_log('sell: intake failed status='.$r['status'].' err='.($r['error'] ?? ''));
                $fatal = "We couldn't send your inquiry just now. Please try again in a few minutes"
                    .($publicPhone !== '' ? ', or call us at '.$publicPhone : '').'.';
            }
        }
    }
}
$submissionId = bin2hex(random_bytes(16));
$labels = ['full_name'=>'Full name','phone'=>'Mobile phone','email'=>'Email','street_address'=>'Property address','city'=>'City','state'=>'State','zip_code'=>'ZIP','property_condition'=>'Property condition','timeline'=>'Selling timeline','preferred_contact_method'=>'Preferred contact method','sms_consent'=>'SMS consent'];
?>
<?php require __DIR__.'/../private/site.php'; ?><!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sell Your Property | EvoSys Wholesale</title>
<meta name="description" content="Thinking about selling a property? Tell EvoSys Wholesale about it. No obligation. EvoSys Wholesale is a product of EVO Integrated Solutions LLC.">
<link rel="canonical" href="https://evosyspro.live/sell">
<link rel="icon" type="image/png" href="<?=site_favicon_data()?>">
<meta name="theme-color" content="#0f2448">
<style>
:root{--bg:#f3f6fb;--surface:#fff;--inset:#f6f8fc;--line:#e5eaf2;--line2:#c9d3e2;--ink:#0f1e3a;--ink2:#44536b;--ink3:#5d6b82;--navy:#0f2448;--blue:#1f6fe5;--blue-soft:#e8f0fd;--green:#15803d;--green-soft:#e5f6ec;--gold:#b7791f;--gold-soft:#fdf3e1;--danger:#b3203a;--danger-soft:#fde8eb;--focus:#1f6fe5}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;overflow-x:hidden}
a{color:var(--blue)}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.s-skip{position:absolute;left:-9999px;top:0;background:#fff;padding:10px 14px;z-index:10}
.s-skip:focus{left:12px;top:12px}
.s-wrap{max-width:1120px;margin:0 auto;padding:0 20px}
.s-bar{background:var(--navy);color:#dbe6f7}
.s-bar .s-wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:64px;flex-wrap:wrap}
.s-brand{display:flex;align-items:center;gap:12px;text-decoration:none;color:#fff}
.s-brand img{height:34px;width:auto;display:block}
.s-brand span{font-weight:700;letter-spacing:.2px;border-left:1px solid #3a5480;padding-left:12px;font-size:15px}
.s-bar small{font-size:13px;color:#b9c8e0}
.s-hero{background:linear-gradient(180deg,#fff 0%,var(--bg) 100%);border-bottom:1px solid var(--line)}
.s-hero .s-wrap{padding-top:44px;padding-bottom:36px}
.s-eyebrow{display:inline-block;font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#0f6b33;background:var(--green-soft);border-radius:999px;padding:4px 12px}
.s-hero h1{font-size:clamp(28px,4.2vw,44px);line-height:1.15;margin:14px 0 10px;color:var(--navy)}
.s-hero p{max-width:680px;color:var(--ink2);font-size:18px;margin:0}
.s-points{display:flex;flex-wrap:wrap;gap:10px 22px;margin:18px 0 0;padding:0;list-style:none;color:var(--ink2);font-size:15px}
.s-points li::before{content:"\2713";color:var(--green);font-weight:700;margin-right:8px}
.s-main{padding:32px 0 56px}
.s-grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:24px;align-items:start}
.s-card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:28px;box-shadow:0 1px 2px rgba(15,36,72,.04),0 8px 24px rgba(15,36,72,.06)}
.s-card h2{font-size:22px;margin:0 0 4px;color:var(--navy)}
.s-sub{color:var(--ink3);margin:0 0 20px;font-size:15px}
fieldset{border:0;margin:0 0 8px;padding:0;min-width:0}
legend{font-weight:700;color:var(--navy);font-size:15px;padding:0;margin:14px 0 10px}
.s-fields{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px 16px}
.s-f{grid-column:span 6;min-width:0}.s-f.h{grid-column:span 3}.s-f.t{grid-column:span 2}
label.s-l{display:block;font-weight:600;font-size:14px;margin:0 0 6px;color:var(--ink)}
.s-req{color:var(--danger);font-weight:700}
.s-opt{color:var(--ink3);font-weight:400}
input.s-i,select.s-i,textarea.s-i{width:100%;max-width:100%;font:inherit;color:var(--ink);background:#fff;border:1px solid var(--line2);border-radius:10px;padding:11px 12px;min-height:46px}
textarea.s-i{min-height:96px;resize:vertical}
.s-i[aria-invalid="true"]{border-color:var(--danger);background:var(--danger-soft)}
.s-hint{font-size:13px;color:var(--ink3);margin:6px 0 0}
.s-err{font-size:14px;color:var(--danger);margin:6px 0 0;font-weight:600}
.s-consent{margin-top:18px;background:var(--inset);border:1px solid var(--line);border-radius:12px;padding:16px}
.s-consent .s-optional{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink3);margin-bottom:8px}
.s-check{display:grid;grid-template-columns:24px minmax(0,1fr);gap:10px;align-items:start;font-size:14px;color:var(--ink2);line-height:1.5}
.s-check input{width:20px;height:20px;margin:2px 0 0;accent-color:var(--blue)}
.s-actions{margin-top:22px;display:flex;flex-wrap:wrap;gap:12px 18px;align-items:center}
.s-btn{appearance:none;border:0;border-radius:10px;background:var(--blue);color:#fff;font:inherit;font-weight:700;padding:14px 26px;cursor:pointer;min-height:48px}
.s-btn:hover{background:#1858bb}
.s-fine{font-size:13px;color:var(--ink3);margin:0}
.s-alert{border-radius:12px;padding:14px 16px;margin:0 0 18px;font-size:15px}
.s-alert.err{background:var(--danger-soft);border:1px solid #f3b6c1;color:#7d1428}
.s-alert.ok{background:var(--green-soft);border:1px solid #b6e3c6;color:#0f5a2c}
.s-alert ul{margin:8px 0 0;padding-left:20px}
.s-side{display:grid;gap:16px}
.s-side h3{font-size:16px;margin:0 0 10px;color:var(--navy)}
.s-steps{list-style:none;margin:0;padding:0;display:grid;gap:14px}
.s-steps li{display:grid;grid-template-columns:32px minmax(0,1fr);gap:12px;font-size:15px;color:var(--ink2)}
.s-steps b{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;background:var(--blue-soft);color:var(--blue);font-size:14px}
.s-steps strong{display:block;color:var(--ink)}
.s-who{font-size:14px;color:var(--ink2)}
.s-who p{margin:0 0 8px}
.s-gold{border-top:3px solid var(--gold)}
.s-foot{background:var(--navy);color:#b9c8e0;font-size:14px}
.s-foot .s-wrap{padding-top:28px;padding-bottom:28px;display:grid;gap:12px}
.s-foot nav{display:flex;flex-wrap:wrap;gap:8px 20px}
.s-foot a{color:#fff}
.hp{position:absolute!important;left:-10000px!important;width:1px;height:1px;overflow:hidden}
@media (max-width:900px){.s-grid{grid-template-columns:minmax(0,1fr)}}
@media (max-width:600px){.s-card{padding:20px 16px}.s-wrap{padding:0 16px}.s-f.h,.s-f.t{grid-column:span 6}.s-hero .s-wrap{padding-top:32px}.s-brand span{display:none}.s-btn{width:100%}}
</style>
</head>
<body>
<a class="s-skip" href="#seller-form">Skip to the seller form</a>
<header class="s-bar" role="banner">
  <div class="s-wrap">
    <a class="s-brand" href="/sell" aria-label="EvoSys Wholesale seller page">
      <img src="<?=site_logo_data()?>" alt="EvoSysPro" width="160" height="34"><span>EvoSys Wholesale</span>
    </a>
    <small>A product of EVO Integrated Solutions LLC</small>
  </div>
</header>

<section class="s-hero" aria-labelledby="s-title">
  <div class="s-wrap">
    <span class="s-eyebrow">For property owners</span>
    <h1 id="s-title">Thinking about selling a property?</h1>
    <p>Tell us about it. A member of our team reviews every inquiry and follows up with you about your property and your options.</p>
    <ul class="s-points">
      <li>No obligation to sell</li>
      <li>Any condition, including properties that need work</li>
      <li>You choose how we contact you</li>
    </ul>
  </div>
</section>

<main class="s-main" id="main">
  <div class="s-wrap s-grid">
    <div class="s-card" id="seller-form">
<?php if ($result !== null): ?>
      <div class="s-alert ok" role="status" aria-live="polite" tabindex="-1" id="s-done">
        <strong>Thank you - we received your property inquiry.</strong>
        <?php if ($result['reference']): ?><br>Your reference: <strong><?=htmlspecialchars($result['reference'])?></strong><?php endif; ?>
      </div>
      <h2>What happens next</h2>
      <p class="s-sub">A member of our team will review your property details and contact you using the method you chose.</p>
      <?php if ($result['sms']): ?>
      <p>You asked to receive text messages about your property inquiry. You may receive a text confirming your subscription. Message frequency varies. Message and data rates may apply. Reply <strong>STOP</strong> at any time to opt out, or <strong>HELP</strong> for help.</p>
      <?php else: ?>
      <p>You did not sign up for text messages, so we will not text you about this inquiry. We will follow up by phone or email.</p>
      <?php endif; ?>
      <p class="s-fine">See our <a href="/privacy.html#sms">Privacy Policy</a> and <a href="/terms.html#sms-wholesale">Terms</a>.</p>
      <script>document.getElementById('s-done').focus();</script>
<?php else: ?>
      <h2>Tell us about your property</h2>
      <p class="s-sub">Fields marked <span class="s-req" aria-hidden="true">*</span><span class="hp">with an asterisk</span> are required.</p>
      <?php if ($fatal): ?><div class="s-alert err" role="alert"><?=htmlspecialchars($fatal)?></div><?php endif; ?>
      <?php if ($errors): ?>
      <div class="s-alert err" role="alert" tabindex="-1" id="s-errs">
        <strong>Please correct the following:</strong>
        <ul><?php foreach ($errors as $k => $m): ?><li><a href="#f-<?=htmlspecialchars((string)$k)?>"><?=htmlspecialchars(($labels[$k] ?? ucfirst(str_replace('_',' ',(string)$k))).': '.$m)?></a></li><?php endforeach; ?></ul>
      </div>
      <script>document.getElementById('s-errs').focus();</script>
      <?php endif; ?>
      <form method="post" action="/sell/" novalidate>
        <input class="hp" name="website_url" tabindex="-1" autocomplete="off" aria-hidden="true">
        <input type="hidden" name="form_started_at" value="<?=time()?>">
        <input type="hidden" name="submission_id" value="<?=$submissionId?>">
        <fieldset>
          <legend>About you</legend>
          <div class="s-fields">
            <div class="s-f"><label class="s-l" for="f-full_name">Full name <span class="s-req" aria-hidden="true">*</span></label>
              <input class="s-i" id="f-full_name" name="full_name" autocomplete="name" required maxlength="120" value="<?=sell_val('full_name')?>"<?=sell_aria($errors,'full_name')?>><?=sell_err($errors,'full_name')?></div>
            <div class="s-f h"><label class="s-l" for="f-phone">Mobile phone <span class="s-req" aria-hidden="true">*</span></label>
              <input class="s-i" id="f-phone" name="phone" type="tel" inputmode="tel" autocomplete="tel" required maxlength="40" value="<?=sell_val('phone')?>"<?=sell_aria($errors,'phone')?>><?=sell_err($errors,'phone')?></div>
            <div class="s-f h"><label class="s-l" for="f-email">Email <span class="s-opt">(optional)</span></label>
              <input class="s-i" id="f-email" name="email" type="email" autocomplete="email" maxlength="180" value="<?=sell_val('email')?>"<?=sell_aria($errors,'email')?>><?=sell_err($errors,'email')?></div>
            <div class="s-f"><label class="s-l" for="f-preferred_contact_method">How should we contact you? <span class="s-req" aria-hidden="true">*</span></label>
              <select class="s-i" id="f-preferred_contact_method" name="preferred_contact_method" required<?=sell_aria($errors,'preferred_contact_method')?>>
                <?php foreach ($METHODS as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('preferred_contact_method',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'preferred_contact_method')?></div>
          </div>
        </fieldset>
        <fieldset>
          <legend>About the property</legend>
          <div class="s-fields">
            <div class="s-f"><label class="s-l" for="f-street_address">Property address <span class="s-req" aria-hidden="true">*</span></label>
              <input class="s-i" id="f-street_address" name="street_address" autocomplete="street-address" required maxlength="200" value="<?=sell_val('street_address')?>"<?=sell_aria($errors,'street_address')?>><?=sell_err($errors,'street_address')?></div>
            <div class="s-f t"><label class="s-l" for="f-city">City <span class="s-req" aria-hidden="true">*</span></label>
              <input class="s-i" id="f-city" name="city" autocomplete="address-level2" required maxlength="100" value="<?=sell_val('city')?>"<?=sell_aria($errors,'city')?>><?=sell_err($errors,'city')?></div>
            <div class="s-f t"><label class="s-l" for="f-state">State <span class="s-req" aria-hidden="true">*</span></label>
              <select class="s-i" id="f-state" name="state" autocomplete="address-level1" required<?=sell_aria($errors,'state')?>>
                <option value="">Choose…</option>
                <?php foreach ($STATES as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('state',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'state')?></div>
            <div class="s-f t"><label class="s-l" for="f-zip_code">ZIP <span class="s-req" aria-hidden="true">*</span></label>
              <input class="s-i" id="f-zip_code" name="zip_code" inputmode="numeric" autocomplete="postal-code" required maxlength="10" value="<?=sell_val('zip_code')?>"<?=sell_aria($errors,'zip_code')?>><?=sell_err($errors,'zip_code')?></div>
            <div class="s-f h"><label class="s-l" for="f-property_condition">Property condition <span class="s-opt">(optional)</span></label>
              <select class="s-i" id="f-property_condition" name="property_condition"<?=sell_aria($errors,'property_condition')?>>
                <option value="">Choose…</option>
                <?php foreach ($CONDITIONS as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('property_condition',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'property_condition')?></div>
            <div class="s-f h"><label class="s-l" for="f-timeline">When would you like to sell? <span class="s-opt">(optional)</span></label>
              <select class="s-i" id="f-timeline" name="timeline"<?=sell_aria($errors,'timeline')?>>
                <option value="">Choose…</option>
                <?php foreach ($TIMELINES as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('timeline',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'timeline')?></div>
            <div class="s-f"><label class="s-l" for="f-reason_for_selling">Why are you considering selling? <span class="s-opt">(optional)</span></label>
              <input class="s-i" id="f-reason_for_selling" name="reason_for_selling" maxlength="500" value="<?=sell_val('reason_for_selling')?>" aria-describedby="h-reason"><p class="s-hint" id="h-reason">For example: relocating, inherited property, downsizing, tired of being a landlord.</p></div>
            <div class="s-f"><label class="s-l" for="f-notes">Anything else we should know? <span class="s-opt">(optional)</span></label>
              <textarea class="s-i" id="f-notes" name="notes" maxlength="2000"><?=sell_val('notes')?></textarea></div>
          </div>
        </fieldset>

        <fieldset class="s-consent">
          <legend class="s-optional" style="margin:0 0 8px">Optional - text messages</legend>
          <div class="s-check">
            <input type="checkbox" id="f-sms_consent" name="sms_consent" value="yes"<?=(($_POST['sms_consent'] ?? '') === 'yes') ? ' checked' : ''?><?=sell_aria($errors,'sms_consent','sms-disclosure')?>>
            <label for="f-sms_consent" id="sms-disclosure">By checking this box, I agree to receive SMS text messages from EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) regarding my property inquiry, including follow-up questions, appointment scheduling, and transaction updates. Message frequency varies. Message and data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of any service. View our <a href="/privacy.html#sms">Privacy Policy</a> and <a href="/terms.html#sms-wholesale">Terms</a>.</label>
          </div>
          <?=sell_err($errors,'sms_consent')?>
        </fieldset>

        <div class="s-actions">
          <button class="s-btn" type="submit">Send my property inquiry</button>
          <p class="s-fine">Submitting this form does not sign you up for text messages. Only the optional box above does.</p>
        </div>
      </form>
<?php endif; ?>
    </div>

    <aside class="s-side" aria-label="About EvoSys Wholesale">
      <div class="s-card">
        <h3>How it works</h3>
        <ol class="s-steps">
          <li><b aria-hidden="true">1</b><span><strong>Tell us about the property</strong>The address, its condition and your timeline.</span></li>
          <li><b aria-hidden="true">2</b><span><strong>We review it</strong>A member of our team looks at the details and follows up the way you asked.</span></li>
          <li><b aria-hidden="true">3</b><span><strong>You decide</strong>Nothing is agreed until you choose to move forward, in writing.</span></li>
        </ol>
      </div>
      <div class="s-card s-gold s-who">
        <h3>Who you're contacting</h3>
        <p>EvoSys Wholesale is a product of EVO Integrated Solutions LLC, operated under the EvoSysPro platform. EvoSense is the acquisition and communication engine used within EvoSys Wholesale.</p>
        <p>We do not sell your information. Mobile numbers and SMS consent are never shared with third parties for their marketing.</p>
        <?php if ($publicPhone !== '' || $publicEmail !== ''): ?>
        <p>Questions? <?php if ($publicPhone !== ''): ?>Call <a href="tel:<?=htmlspecialchars(preg_replace('/[^0-9+]/','',$publicPhone))?>"><?=htmlspecialchars($publicPhone)?></a><?php endif; ?><?php if ($publicPhone !== '' && $publicEmail !== ''): ?> or email <?php elseif ($publicEmail !== ''): ?>Email <?php endif; ?><?php if ($publicEmail !== ''): ?><a href="mailto:<?=htmlspecialchars($publicEmail)?>"><?=htmlspecialchars($publicEmail)?></a><?php endif; ?>.</p>
        <?php endif; ?>
      </div>
    </aside>
  </div>
</main>

<footer class="s-foot" role="contentinfo">
  <div class="s-wrap">
    <nav aria-label="Legal">
      <a href="/privacy.html#sms">Privacy Policy</a>
      <a href="/terms.html#sms-wholesale">Terms</a>
      <a href="/">EvoSysPro</a>
    </nav>
    <div>EvoSys Wholesale is a product of EVO Integrated Solutions LLC, operated under the EvoSysPro platform.<br>&copy; <?=date('Y')?> EVO Integrated Solutions LLC. All rights reserved.</div>
  </div>
</footer>
</body>
</html>
