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
$PTYPES = ['single_family'=>'Single-family home','multi_family'=>'Multi-family (2-4 units)','condo_townhome'=>'Condo or townhome','mobile_home'=>'Mobile or manufactured home','land'=>'Vacant land','other'=>'Other'];

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
        // Property type and asking price are optional page fields. The intake
        // contract has no columns for them, so they travel inside `notes` -
        // the payload keys, the endpoint and the backend are unchanged.
        $ptype = (string)($_POST['property_type'] ?? '');
        $asking = evosys_clean((string)($_POST['asking_price'] ?? ''), 40);
        if ($ptype !== '' && !isset($PTYPES[$ptype])) $errors['property_type'] = 'Please choose a property type.';
        $extra = [];
        if ($ptype !== '' && isset($PTYPES[$ptype])) $extra[] = 'Property type: '.$PTYPES[$ptype];
        if ($asking !== '') $extra[] = 'Asking price: '.$asking;
        if ($extra) $in['notes'] = mb_substr(implode("\n", $extra).($in['notes'] !== '' ? "\n\n".$in['notes'] : ''), 0, 2000);
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
$labels = ['full_name'=>'Full name','phone'=>'Mobile phone','email'=>'Email','street_address'=>'Property address','city'=>'City','state'=>'State','zip_code'=>'ZIP','property_condition'=>'Property condition','timeline'=>'Selling timeline','preferred_contact_method'=>'Preferred contact method','sms_consent'=>'SMS consent','property_type'=>'Property type'];
?><!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sell Your Property | EvoSys Wholesale</title>
<meta name="description" content="Thinking about selling a property? Tell EvoSys Wholesale about it. No obligation, any condition, and you choose how we contact you. EvoSys Wholesale is operated by EVO Integrated Solutions LLC.">
<link rel="canonical" href="https://evosyspro.live/sell">
<link rel="icon" type="image/png" href="/assets/favicon.png">
<meta name="theme-color" content="#0b1f3a">
<link rel="preload" as="image" type="image/webp" href="/assets/sell/sell-hero-1600.webp"
      imagesrcset="/assets/sell/sell-hero-m.webp 800w, /assets/sell/sell-hero-960.webp 960w, /assets/sell/sell-hero-1280.webp 1280w, /assets/sell/sell-hero-1600.webp 1600w"
      imagesizes="100vw" fetchpriority="high">
<style>
:root{
  --navy:#0b1f3a;--navy-2:#122b4f;--navy-3:#1c3a66;--ink:#16233b;--ink-2:#3d4b63;--ink-3:#56637a;
  --paper:#ffffff;--mist:#f5f3ee;--mist-2:#ece8df;--line:#e3ded3;--line-2:#c9c2b3;
  --gold:#d4ae5a;--gold-2:#e6c784;--gold-ink:#7a5a12;--gold-soft:#faf3e3;
  --danger:#a61b33;--danger-soft:#fdecef;--ok:#14663a;--ok-soft:#e7f5ec;--focus:#1d5fd1;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --r:14px;--shadow:0 1px 2px rgba(11,31,58,.05),0 12px 32px rgba(11,31,58,.08);
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-padding-top:84px}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.6 var(--sans);overflow-x:hidden}
img{max-width:100%;display:block}
a{color:var(--navy-3)}
:focus-visible{outline:3px solid var(--focus);outline-offset:3px;border-radius:4px}
.dark :focus-visible{outline-color:var(--gold-2)}
.wrap{width:100%;max-width:1320px;margin:0 auto;padding:0 32px}
.skip{position:absolute;left:-9999px;top:0;background:#fff;color:var(--navy);padding:10px 14px;z-index:100;font-weight:700}
.skip:focus{left:12px;top:12px}
.hp{position:absolute!important;left:-10000px!important;width:1px;height:1px;overflow:hidden}
.eyebrow{display:inline-flex;align-items:center;gap:10px;font-size:13px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--gold-ink)}
.eyebrow::before{content:"";width:28px;height:2px;background:var(--gold)}
.dark .eyebrow{color:var(--gold-2)}
h1,h2,h3{font-family:var(--serif);color:var(--navy);letter-spacing:-.01em;margin:0}
h2{font-size:clamp(30px,3.2vw,44px);line-height:1.12}
.lede{color:var(--ink-2);font-size:19px;max-width:660px;margin:14px 0 0}
.sec{padding:96px 0}
.sec-head{max-width:760px;margin:0 0 44px}
.sec-head h2{margin-top:12px}

/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:10px;min-height:52px;padding:14px 26px;border-radius:10px;font:700 16px/1.2 var(--sans);letter-spacing:.02em;text-decoration:none;border:2px solid transparent;cursor:pointer;transition:background .15s,border-color .15s,color .15s}
.btn-gold{background:var(--gold);color:var(--navy);border-color:var(--gold)}
.btn-gold:hover{background:var(--gold-2);border-color:var(--gold-2)}
.btn-line{background:transparent;color:#fff;border-color:rgba(255,255,255,.55)}
.btn-line:hover{border-color:#fff;background:rgba(255,255,255,.08)}
.btn-navy{background:var(--navy);color:#fff;border-color:var(--navy)}
.btn-navy:hover{background:var(--navy-3);border-color:var(--navy-3)}
.btn svg{width:18px;height:18px;flex:none}

/* header */
.top{position:sticky;top:0;z-index:50;background:var(--navy);color:#fff;border-bottom:1px solid rgba(212,174,90,.35)}
.top .wrap{display:flex;align-items:center;gap:28px;min-height:72px}
.brand{display:flex;align-items:center;gap:12px;color:#fff;text-decoration:none;margin-right:auto}
.brand svg{width:34px;height:34px;flex:none}
.brand b{display:block;font:700 19px/1 var(--serif);letter-spacing:.02em}
.brand small{display:block;font:700 11px/1 var(--sans);letter-spacing:.32em;color:var(--gold-2);margin-top:5px}
.top nav ul{display:flex;gap:28px;list-style:none;margin:0;padding:0}
.top nav a{color:#dfe6f1;text-decoration:none;font-size:15px;font-weight:600}
.top nav a:hover{color:#fff;text-decoration:underline;text-underline-offset:6px;text-decoration-color:var(--gold)}
.top .btn{min-height:44px;padding:10px 20px;font-size:14px;letter-spacing:.08em;text-transform:uppercase}

/* hero */
.hero{position:relative;background:var(--navy);color:#fff;overflow:hidden}
.hero-media{position:absolute;inset:0}
.hero-media picture{display:block;height:100%}
.hero-media img{width:100%;height:100%;object-fit:cover;object-position:62% 55%}
.hero-media::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(8,22,42,.96) 0%,rgba(8,22,42,.88) 34%,rgba(8,22,42,.45) 62%,rgba(8,22,42,.12) 100%)}
.hero .wrap{position:relative;padding-top:112px;padding-bottom:112px;min-height:600px;display:flex;flex-direction:column;justify-content:center}
.hero-copy{max-width:640px}
.hero h1{color:#fff;font-size:clamp(40px,5.4vw,72px);line-height:1.04;margin:18px 0 0}
.hero h1 span{display:block;color:var(--gold-2)}
.hero p{color:#dbe3ef;font-size:20px;line-height:1.55;margin:22px 0 0;max-width:560px}
.hero-ctas{display:flex;flex-wrap:wrap;gap:14px;margin-top:34px}
.trust{background:var(--navy-2);color:#fff;border-top:1px solid rgba(212,174,90,.3)}
.trust ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}
.trust li{display:flex;align-items:center;gap:14px;padding:24px 20px;font-weight:700;font-size:15px;letter-spacing:.06em;text-transform:uppercase;border-left:1px solid rgba(255,255,255,.1)}
.trust li:first-child{border-left:0;padding-left:0}
.trust svg{width:28px;height:28px;flex:none;color:var(--gold)}

/* how it works */
.steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px;list-style:none;margin:0;padding:0;counter-reset:s}
.step{position:relative;background:var(--paper);border:1px solid var(--line);border-radius:var(--r);padding:34px 30px 32px;box-shadow:var(--shadow)}
.step-n{display:grid;place-items:center;width:52px;height:52px;border-radius:50%;background:var(--navy);color:var(--gold-2);font:700 22px/1 var(--serif);margin-bottom:22px}
.step h3{font-size:24px;line-height:1.2}
.step p{color:var(--ink-2);margin:10px 0 0}
.step:not(:last-child)::after{content:"";position:absolute;top:60px;right:-29px;width:30px;height:2px;background:var(--gold)}

/* situations */
.sit{background:var(--mist)}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;list-style:none;margin:0;padding:0}
.card{background:var(--paper);border:1px solid var(--line);border-radius:var(--r);padding:26px 24px;display:flex;flex-direction:column;gap:10px;transition:border-color .15s,box-shadow .15s}
.card:hover{border-color:var(--gold);box-shadow:var(--shadow)}
.card svg{width:40px;height:40px;color:var(--gold-ink)}
.card h3{font-size:21px;line-height:1.2}
.card p{margin:0;color:var(--ink-2);font-size:15.5px}
.sit-note{margin:26px 0 0;color:var(--ink-3);font-size:15px}

/* form section */
.get{background:linear-gradient(180deg,var(--paper) 0,var(--mist) 100%)}
.get-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.55fr);gap:48px;align-items:start}
.get-side{position:sticky;top:104px;display:grid;gap:22px}
.side-photo{border-radius:var(--r);overflow:hidden;position:relative;aspect-ratio:3/2;background:var(--mist-2)}
.side-photo img{width:100%;height:100%;object-fit:cover}
.side-photo span{position:absolute;left:12px;bottom:12px;background:rgba(11,31,58,.78);color:#fff;font-size:12px;padding:4px 10px;border-radius:999px}
.panel{background:var(--paper);border:1px solid var(--line);border-radius:var(--r);padding:26px 26px 22px}
.panel h3{font-size:20px;margin-bottom:10px}
.panel p{margin:0 0 10px;color:var(--ink-2);font-size:15.5px}
.panel.who{border-top:4px solid var(--gold)}
.who .phone{display:inline-flex;align-items:center;gap:8px;font:700 22px/1.2 var(--serif);color:var(--navy);text-decoration:none;margin:2px 0 10px}
.who .phone svg{width:20px;height:20px;color:var(--gold-ink)}
.checks{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.checks li{display:grid;grid-template-columns:22px minmax(0,1fr);gap:10px;color:var(--ink-2);font-size:15.5px}
.checks svg{width:20px;height:20px;color:var(--ok);margin-top:3px}

.formcard{background:var(--paper);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);padding:40px 40px 34px}
.formcard h2{font-size:clamp(28px,2.6vw,36px)}
.sub{color:var(--ink-3);margin:8px 0 26px;font-size:15.5px}
fieldset{border:0;margin:0;padding:0;min-width:0}
fieldset+fieldset{margin-top:30px;padding-top:28px;border-top:1px solid var(--line)}
legend{float:left;width:100%;display:flex;align-items:center;gap:12px;font:700 13px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--navy);padding:0;margin:0 0 18px}
.fields{clear:both}
legend i{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:var(--gold-soft);color:var(--gold-ink);font:700 14px/1 var(--serif);font-style:normal;letter-spacing:0}
.fields{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:18px 18px}
.f{grid-column:span 6;min-width:0}.f.h{grid-column:span 3}.f.t{grid-column:span 2}
label.l{display:block;font-weight:650;font-size:15px;margin:0 0 7px;color:var(--ink)}
.req{color:var(--danger);font-weight:700}
.opt{color:var(--ink-3);font-weight:400;font-size:14px}
input.i,select.i,textarea.i{width:100%;max-width:100%;font:inherit;font-size:16px;color:var(--ink);background:#fff;border:1.5px solid var(--line-2);border-radius:10px;padding:12px 14px;min-height:50px;transition:border-color .15s,box-shadow .15s}
input.i:hover,select.i:hover,textarea.i:hover{border-color:#a99f8b}
input.i:focus,select.i:focus,textarea.i:focus{border-color:var(--navy-3);box-shadow:0 0 0 3px rgba(29,95,209,.18)}
textarea.i{min-height:110px;resize:vertical}
.i[aria-invalid="true"]{border-color:var(--danger);background:var(--danger-soft)}
.hint{font-size:14px;color:var(--ink-3);margin:7px 0 0}
.s-err{font-size:14.5px;color:var(--danger);margin:7px 0 0;font-weight:650}
.consent{margin-top:30px;background:var(--mist);border:1px solid var(--line);border-radius:12px;padding:20px 20px 18px}
.consent legend{margin-bottom:14px}
.consent .check{clear:both}
.consent .tag{display:inline-block;font:700 11px/1 var(--sans);letter-spacing:.12em;background:#fff;border:1px solid var(--line-2);color:var(--ink-3);border-radius:999px;padding:5px 9px;margin-left:4px}
.check{display:grid;grid-template-columns:26px minmax(0,1fr);gap:12px;align-items:start;font-size:15px;color:var(--ink-2);line-height:1.55}
.check input{width:22px;height:22px;margin:2px 0 0;accent-color:var(--navy)}
/* SMS opt-in: the plain-language summary sits ABOVE the box, the verbatim
   registered disclosure is the box's own label, and the box is never checked
   for anyone. The panel only changes look once a person ticks it. */
.consent{transition:border-color .15s,box-shadow .15s,background .15s}
.consent:has(input:checked){background:#fff;border-color:var(--navy-3);box-shadow:0 0 0 3px rgba(29,95,209,.14)}
.consent-lead{font-size:16px;color:var(--ink);margin:0 0 12px;font-weight:600}
.consent-facts{list-style:none;margin:0 0 16px;padding:0;display:grid;gap:8px;font-size:15px;color:var(--ink-2)}
.consent-facts li{display:grid;grid-template-columns:14px minmax(0,1fr);gap:10px}
.consent-facts li::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--navy-3);margin-top:10px}
.consent .check{background:#fff;border:1.5px solid var(--line-2);border-radius:10px;padding:14px 14px 14px 12px;color:var(--ink)}
.consent .check label{cursor:pointer}
.consent .check input{cursor:pointer}
.actions{margin-top:28px;display:flex;flex-wrap:wrap;gap:14px 22px;align-items:center}
.actions .btn{min-width:260px}
.fine{font-size:14px;color:var(--ink-3);margin:0;max-width:420px}
.alert{border-radius:12px;padding:16px 18px;margin:0 0 22px;font-size:16px}
.alert.err{background:var(--danger-soft);border:1px solid #f1b3bf;color:#6f1024}
.alert.ok{background:var(--ok-soft);border:1px solid #b3dfc3;color:#0d4a28}
.alert ul{margin:8px 0 0;padding-left:20px}
.alert a{color:inherit}

/* final CTA */
.final{position:relative;color:#fff;background:var(--navy);overflow:hidden}
.final img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.28}
.final::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(8,22,42,.92),rgba(8,22,42,.7))}
.final .wrap{position:relative;z-index:1;padding-top:96px;padding-bottom:96px;display:flex;align-items:center;justify-content:space-between;gap:32px;flex-wrap:wrap}
.final h2{color:#fff;font-size:clamp(32px,3.6vw,50px)}
.final p{color:#dbe3ef;font-size:19px;margin:12px 0 0}

/* footer */
.foot{background:#081629;color:#b7c3d6;font-size:15px}
.foot .wrap{padding-top:48px;padding-bottom:40px;display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr);gap:32px}
.foot h2{font:700 13px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--gold-2);margin:0 0 14px}
.foot a{color:#fff}
.foot ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.foot .brand{margin:0 0 14px}
.foot .legal{grid-column:1/-1;border-top:1px solid rgba(255,255,255,.1);padding-top:22px;font-size:14px;color:#94a3b8}

/* responsive */
@media (max-width:1180px){
  .cards{grid-template-columns:repeat(2,minmax(0,1fr))}
  .get-grid{grid-template-columns:minmax(0,1fr) minmax(0,1.7fr);gap:32px}
}
@media (max-width:1024px){
  .wrap{padding:0 28px}
  .top nav{display:none}
  .trust ul{grid-template-columns:repeat(2,minmax(0,1fr))}
  .trust li{border-left:0;padding-left:0}
  .steps{grid-template-columns:minmax(0,1fr);gap:18px}
  .step:not(:last-child)::after{display:none}
  .step{display:grid;grid-template-columns:52px minmax(0,1fr);gap:0 22px;padding:26px}
  .step-n{margin:0;grid-row:span 2}
  .get-grid{grid-template-columns:minmax(0,1fr)}
  .get-side{position:static;grid-template-columns:minmax(0,1fr) minmax(0,1fr);order:2}
  .side-photo{display:none}
  .foot .wrap{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .foot .fbrand{grid-column:1/-1}
}
@media (max-width:820px){
  .sec{padding:72px 0}
  .hero .wrap{min-height:0;padding-top:0;padding-bottom:48px}
  .hero-media{position:relative;width:100%;height:clamp(220px,50vw,380px)}
  .hero-media::after{background:linear-gradient(180deg,rgba(8,22,42,0) 45%,rgba(11,31,58,1) 100%)}
  .hero-media img{object-position:50% 60%}
  .hero h1{margin-top:14px}
  .get-side{grid-template-columns:minmax(0,1fr)}
  .formcard{padding:30px 24px 26px}
}
@media (max-width:600px){
  body{font-size:16px}
  .wrap{padding:0 16px}
  .top .wrap{min-height:64px;gap:12px}
  .brand svg{width:30px;height:30px}
  .brand b{font-size:17px}
  .brand small{letter-spacing:.26em}
  .top .btn{padding:9px 14px;min-height:42px;font-size:13px}
  .hero p{font-size:17.5px}
  .hero-ctas .btn{width:100%}
  .trust li{padding:16px 0;font-size:13px;gap:10px;letter-spacing:.04em}
  .trust svg{width:24px;height:24px}
  .cards{grid-template-columns:minmax(0,1fr)}
  .card{flex-direction:row;align-items:flex-start;gap:16px;padding:20px}
  .card svg{width:34px;height:34px;flex:none}
  .card div{display:grid;gap:4px}
  .formcard{padding:24px 16px 22px;border-radius:14px}
  .f.h,.f.t{grid-column:span 6}
  .actions .btn{width:100%;min-width:0}
  .final .wrap{padding-top:64px;padding-bottom:64px}
  .final .btn{width:100%}
  .foot .wrap{grid-template-columns:minmax(0,1fr)}
}
</style>
</head>
<body>
<a class="skip" href="#seller-form">Skip to the seller form</a>
<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">
  <symbol id="i-check" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M5 12.5l4.5 4.5L19 7.5"/></symbol>
  <symbol id="i-arrow" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M5 12h14M13 6l6 6-6 6"/></symbol>
  <symbol id="i-phone" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M6.6 3.5h3l1.5 4-2 1.3a11 11 0 005.9 5.9l1.3-2 4 1.5v3a2 2 0 01-2.2 2A17 17 0 014.6 5.7a2 2 0 012-2.2z"/></symbol>
</svg>

<header class="top dark" role="banner">
  <div class="wrap">
    <a class="brand" href="/sell" aria-label="EvoSys Wholesale, home">
      <svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><rect width="40" height="40" rx="9" fill="#122b4f"/><path d="M8 21.5L20 11l12 10.5" fill="none" stroke="#d4ae5a" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M12.5 19.5V29h15v-9.5" fill="none" stroke="#fff" stroke-width="2.4" stroke-linejoin="round"/><path d="M18 29v-5.5h4V29" fill="none" stroke="#fff" stroke-width="2.2" stroke-linejoin="round"/></svg>
      <span><b>EvoSys</b><small>WHOLESALE</small></span>
    </a>
    <nav aria-label="Page sections">
      <ul>
        <li><a href="#how">How It Works</a></li>
        <li><a href="#situations">Situations We Help</a></li>
        <li><a href="#seller-form" data-focus-form>Get Started</a></li>
      </ul>
    </nav>
    <a class="btn btn-gold" href="#seller-form" data-focus-form>Get started</a>
  </div>
</header>

<main id="main">
<section class="hero dark" aria-labelledby="hero-title">
  <div class="hero-media">
    <picture>
      <source media="(max-width:820px)" type="image/webp" srcset="/assets/sell/sell-hero-m.webp">
      <img src="/assets/sell/sell-hero-1600.webp" srcset="/assets/sell/sell-hero-960.webp 960w, /assets/sell/sell-hero-1280.webp 1280w, /assets/sell/sell-hero-1600.webp 1600w" sizes="100vw" width="1600" height="560" alt="" fetchpriority="high" decoding="async">
    </picture>
  </div>
  <div class="wrap">
    <div class="hero-copy">
      <span class="eyebrow">EvoSys Wholesale &middot; For property owners</span>
      <h1 id="hero-title">Sell your property <span>without the stress.</span></h1>
      <p>Tell us about your property and what you&rsquo;re hoping to accomplish. We&rsquo;ll review the information and contact you using the method you choose.</p>
      <div class="hero-ctas">
        <a class="btn btn-gold" href="#seller-form" data-focus-form>Tell us about your property <svg aria-hidden="true" focusable="false"><use href="#i-arrow"/></svg></a>
        <a class="btn btn-line" href="#how">How it works</a>
      </div>
    </div>
  </div>
</section>

<div class="trust dark">
  <div class="wrap">
    <ul aria-label="What to expect">
      <li><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M12 3l7 3v5.5c0 4.3-2.9 8.1-7 9.5-4.1-1.4-7-5.2-7-9.5V6z"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M9 12l2.2 2.2L15.5 10"/></svg>No obligation</li>
      <li><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M3.5 11L12 4l8.5 7M6 9.5V20h12V9.5"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M14.5 13.5l-3 3M11 13l1 1"/></svg>Any condition</li>
      <li><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="5" cy="12" r="2" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="2" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="19" cy="12" r="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path fill="none" stroke="currentColor" stroke-width="1.8" d="M7 12h3M14 12h3"/></svg>Simple process</li>
      <li><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M4 5.5h16v10H9l-5 4z"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M8 9.5h8M8 12.5h5"/></svg>You choose how we contact you</li>
    </ul>
  </div>
</div>

<section class="sec" id="how" aria-labelledby="how-title">
  <div class="wrap">
    <div class="sec-head">
      <span class="eyebrow">How it works</span>
      <h2 id="how-title">Three simple steps.</h2>
      <p class="lede">No pressure and nothing to sign to get started. Nothing is agreed unless you decide to move forward.</p>
    </div>
    <ol class="steps">
      <li class="step"><span class="step-n" aria-hidden="true">1</span><h3>Tell us about the property</h3><p>Complete the short property inquiry below. It takes a few minutes.</p></li>
      <li class="step"><span class="step-n" aria-hidden="true">2</span><h3>We review the details</h3><p>Our team reviews the property and your situation.</p></li>
      <li class="step"><span class="step-n" aria-hidden="true">3</span><h3>Discuss your options</h3><p>If it looks like a potential fit, we&rsquo;ll contact you using the contact preference you selected.</p></li>
    </ol>
  </div>
</section>

<section class="sec sit" id="situations" aria-labelledby="sit-title">
  <div class="wrap">
    <div class="sec-head">
      <span class="eyebrow">Situations we can help with</span>
      <h2 id="sit-title">Every property has a story.</h2>
      <p class="lede">Owners reach out for many different reasons. Whatever yours is, you can tell us about it and talk through your options.</p>
    </div>
    <ul class="cards">
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M6 19L20 7l14 12M10 16v17h20V16"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M16 25a4 4 0 118 0v8h-8z"/></svg><div><h3>Inherited property</h3><p>A home that came to you through a family estate.</p></div></li>
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M24 8a6 6 0 00-7.6 7.6L7 25l4 4 9.4-9.4A6 6 0 0028 12l-3.5 3.5-2.8-.7-.7-2.8z"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M22 27l6 6M26 24l6 6"/></svg><div><h3>Repairs needed</h3><p>Homes that need updates or significant work.</p></div></li>
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><rect x="6" y="9" width="28" height="24" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M13 6v6M27 6v6M6 16h28M12 22h6M12 27h10"/></svg><div><h3>Tired landlord</h3><p>A rental you&rsquo;re ready to step away from.</p></div></li>
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M6 19L20 7l14 12M10 16v17h20V16"/><rect x="15" y="21" width="10" height="7" fill="none" stroke="currentColor" stroke-width="2"/><path fill="none" stroke="currentColor" stroke-width="2" d="M20 21v7M15 24.5h10"/></svg><div><h3>Vacant property</h3><p>A house that&rsquo;s sitting empty.</p></div></li>
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20 34s-10-9.3-10-17a10 10 0 0120 0c0 7.7-10 17-10 17z"/><circle cx="20" cy="17" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/></svg><div><h3>Relocating</h3><p>Moving for work, family or a fresh start.</p></div></li>
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M4 21L14 12l10 9M7 19v13h14V19"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M24 25l6-5 6 5M26 23.5V32h8v-8.5"/></svg><div><h3>Downsizing</h3><p>Ready for a smaller or simpler home.</p></div></li>
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><rect x="7" y="7" width="26" height="26" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M13 15h14M13 20h14M13 25h8"/></svg><div><h3>Behind on payments</h3><p>It can help to understand your options early. We&rsquo;ll listen, with no pressure.</p></div></li>
      <li class="card"><svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M6 9h28v18H16l-8 6v-6H6z"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M13 16h14M13 21h9"/></svg><div><h3>Other situations</h3><p>Every situation is different. Tell us about yours.</p></div></li>
    </ul>
    <p class="sit-note">Every inquiry is reviewed individually. Submitting one does not commit you to anything.</p>
  </div>
</section>

<section class="sec get" id="get-started" aria-labelledby="form-title">
  <div class="wrap get-grid">
    <aside class="get-side" aria-label="About EvoSys Wholesale">
      <div class="side-photo"><img src="/assets/sell/sell-home-720.webp" width="720" height="480" alt="" loading="lazy" decoding="async"><span>Representative photo</span></div>
      <div class="panel">
        <h3>What to expect</h3>
        <ul class="checks">
          <li><svg aria-hidden="true" focusable="false"><use href="#i-check"/></svg><span>A person on our team reviews every inquiry.</span></li>
          <li><svg aria-hidden="true" focusable="false"><use href="#i-check"/></svg><span>We contact you only the way you choose.</span></li>
          <li><svg aria-hidden="true" focusable="false"><use href="#i-check"/></svg><span>Text messages only if you check the optional SMS box.</span></li>
          <li><svg aria-hidden="true" focusable="false"><use href="#i-check"/></svg><span>Nothing is agreed unless you choose to move forward, in writing.</span></li>
        </ul>
      </div>
      <div class="panel who">
        <h3>Who you&rsquo;re contacting</h3>
        <p>EvoSys Wholesale is operated by EVO Integrated Solutions LLC.</p>
        <?php if ($publicPhone !== ''): ?>
        <p style="margin:0">Questions?</p>
        <a class="phone" href="tel:<?=htmlspecialchars(preg_replace('/[^0-9+]/','',$publicPhone))?>"><svg aria-hidden="true" focusable="false"><use href="#i-phone"/></svg><?=htmlspecialchars($publicPhone)?></a>
        <?php endif; ?>
        <?php if ($publicEmail !== ''): ?><p><a href="mailto:<?=htmlspecialchars($publicEmail)?>"><?=htmlspecialchars($publicEmail)?></a></p><?php endif; ?>
        <p>We do not sell your information. Mobile numbers and SMS consent are never shared with third parties for their marketing.</p>
      </div>
    </aside>

    <div class="formcard" id="seller-form">
<?php if ($result !== null): ?>
      <div class="alert ok" role="status" aria-live="polite" tabindex="-1" id="s-done">
        <strong>Thank you - we received your property inquiry.</strong>
        <?php if ($result['reference']): ?><br>Your reference: <strong><?=htmlspecialchars($result['reference'])?></strong><?php endif; ?>
      </div>
      <h2 id="form-title">What happens next</h2>
      <p class="sub">A member of our team will review your property details and contact you using the method you chose.</p>
      <?php if ($result['sms']): ?>
      <p>You asked to receive text messages about your property inquiry. You may receive a text confirming your subscription. Message frequency varies. Message and data rates may apply. Reply <strong>STOP</strong> at any time to opt out, or <strong>HELP</strong> for help.</p>
      <?php else: ?>
      <p>You did not sign up for text messages, so we will not text you about this inquiry. We will follow up by phone or email.</p>
      <?php endif; ?>
      <p class="fine">See our <a href="/privacy.html#sms">Privacy Policy</a> and <a href="/terms.html#sms-wholesale">Terms</a>.</p>
      <script>document.getElementById('s-done').focus();</script>
<?php else: ?>
      <span class="eyebrow">Get started</span>
      <h2 id="form-title" style="margin-top:10px">Tell us about your property</h2>
      <p class="sub">It only takes a few minutes. Fields marked <span class="req" aria-hidden="true">*</span><span class="hp">with an asterisk</span> are required.</p>
      <?php if ($fatal): ?><div class="alert err" role="alert"><?=htmlspecialchars($fatal)?></div><?php endif; ?>
      <?php if ($errors): ?>
      <div class="alert err" role="alert" tabindex="-1" id="s-errs">
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
          <legend><i aria-hidden="true">1</i>Property</legend>
          <div class="fields">
            <div class="f"><label class="l" for="f-street_address">Street address <span class="req" aria-hidden="true">*</span></label>
              <input class="i" id="f-street_address" name="street_address" autocomplete="street-address" required maxlength="200" value="<?=sell_val('street_address')?>"<?=sell_aria($errors,'street_address')?>><?=sell_err($errors,'street_address')?></div>
            <div class="f t"><label class="l" for="f-city">City <span class="req" aria-hidden="true">*</span></label>
              <input class="i" id="f-city" name="city" autocomplete="address-level2" required maxlength="100" value="<?=sell_val('city')?>"<?=sell_aria($errors,'city')?>><?=sell_err($errors,'city')?></div>
            <div class="f t"><label class="l" for="f-state">State <span class="req" aria-hidden="true">*</span></label>
              <select class="i" id="f-state" name="state" autocomplete="address-level1" required<?=sell_aria($errors,'state')?>>
                <option value="">Choose…</option>
                <?php foreach ($STATES as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('state',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'state')?></div>
            <div class="f t"><label class="l" for="f-zip_code">ZIP <span class="req" aria-hidden="true">*</span></label>
              <input class="i" id="f-zip_code" name="zip_code" inputmode="numeric" autocomplete="postal-code" required maxlength="10" value="<?=sell_val('zip_code')?>"<?=sell_aria($errors,'zip_code')?>><?=sell_err($errors,'zip_code')?></div>
            <div class="f"><label class="l" for="f-property_type">Property type <span class="opt">(optional)</span></label>
              <select class="i" id="f-property_type" name="property_type"<?=sell_aria($errors,'property_type')?>>
                <option value="">Choose…</option>
                <?php foreach ($PTYPES as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('property_type',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'property_type')?></div>
          </div>
        </fieldset>
        <fieldset>
          <legend><i aria-hidden="true">2</i>Your situation</legend>
          <div class="fields">
            <div class="f h"><label class="l" for="f-property_condition">Property condition <span class="opt">(optional)</span></label>
              <select class="i" id="f-property_condition" name="property_condition"<?=sell_aria($errors,'property_condition')?>>
                <option value="">Choose…</option>
                <?php foreach ($CONDITIONS as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('property_condition',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'property_condition')?></div>
            <div class="f h"><label class="l" for="f-timeline">Selling timeline <span class="opt">(optional)</span></label>
              <select class="i" id="f-timeline" name="timeline"<?=sell_aria($errors,'timeline')?>>
                <option value="">Choose…</option>
                <?php foreach ($TIMELINES as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('timeline',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'timeline')?></div>
            <div class="f h"><label class="l" for="f-asking_price">Asking price <span class="opt">(optional)</span></label>
              <input class="i" id="f-asking_price" name="asking_price" inputmode="decimal" maxlength="40" value="<?=sell_val('asking_price')?>" aria-describedby="h-asking"><p class="hint" id="h-asking">If you have a number in mind. It&rsquo;s fine to leave this blank.</p></div>
            <div class="f h"><label class="l" for="f-reason_for_selling">Why are you considering selling? <span class="opt">(optional)</span></label>
              <input class="i" id="f-reason_for_selling" name="reason_for_selling" maxlength="500" value="<?=sell_val('reason_for_selling')?>" aria-describedby="h-reason"><p class="hint" id="h-reason">For example: relocating, inherited property, downsizing.</p></div>
            <div class="f"><label class="l" for="f-notes">Anything else we should know? <span class="opt">(optional)</span></label>
              <textarea class="i" id="f-notes" name="notes" maxlength="1900"><?=sell_val('notes')?></textarea></div>
          </div>
        </fieldset>
        <fieldset>
          <legend><i aria-hidden="true">3</i>Your contact information</legend>
          <div class="fields">
            <div class="f"><label class="l" for="f-full_name">Full name <span class="req" aria-hidden="true">*</span></label>
              <input class="i" id="f-full_name" name="full_name" autocomplete="name" required maxlength="120" value="<?=sell_val('full_name')?>"<?=sell_aria($errors,'full_name')?>><?=sell_err($errors,'full_name')?></div>
            <div class="f h"><label class="l" for="f-phone">Phone <span class="req" aria-hidden="true">*</span></label>
              <input class="i" id="f-phone" name="phone" type="tel" inputmode="tel" autocomplete="tel" required maxlength="40" value="<?=sell_val('phone')?>"<?=sell_aria($errors,'phone')?>><?=sell_err($errors,'phone')?></div>
            <div class="f h"><label class="l" for="f-email">Email <span class="opt">(optional)</span></label>
              <input class="i" id="f-email" name="email" type="email" autocomplete="email" maxlength="180" value="<?=sell_val('email')?>"<?=sell_aria($errors,'email')?>><?=sell_err($errors,'email')?></div>
            <div class="f"><label class="l" for="f-preferred_contact_method">Preferred contact method <span class="req" aria-hidden="true">*</span></label>
              <select class="i" id="f-preferred_contact_method" name="preferred_contact_method" required<?=sell_aria($errors,'preferred_contact_method')?>>
                <?php foreach ($METHODS as $v=>$t): ?><option value="<?=$v?>"<?=sell_sel('preferred_contact_method',$v)?>><?=htmlspecialchars($t)?></option><?php endforeach; ?>
              </select><?=sell_err($errors,'preferred_contact_method')?></div>
          </div>
        </fieldset>

        <fieldset class="consent">
          <legend><i aria-hidden="true">4</i>Text messages <span class="tag">OPTIONAL</span></legend>
          <p class="consent-lead">Would you like text updates about this property? It is completely optional.</p>
          <ul class="consent-facts" id="sms-facts">
            <li><span>Only about this property inquiry: follow-up questions, scheduling and transaction updates. No unrelated marketing.</span></li>
            <li><span>Message frequency varies. Message and data rates may apply.</span></li>
            <li><span>Reply <strong>STOP</strong> at any time to opt out, or <strong>HELP</strong> for help.</span></li>
            <li><span>Leave the box unchecked and we will reach you by phone or email only. Your inquiry is sent either way.</span></li>
          </ul>
          <div class="check">
            <input type="checkbox" id="f-sms_consent" name="sms_consent" value="yes"<?=(($_POST['sms_consent'] ?? '') === 'yes') ? ' checked' : ''?><?=sell_aria($errors,'sms_consent','sms-disclosure')?>>
            <label for="f-sms_consent" id="sms-disclosure">By checking this box, I agree to receive SMS text messages from EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) regarding my property inquiry, including follow-up questions, appointment scheduling, and transaction updates. Message frequency varies. Message and data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of any service. View our <a href="/privacy.html#sms">Privacy Policy</a> and <a href="/terms.html#sms-wholesale">Terms</a>.</label>
          </div>
          <?=sell_err($errors,'sms_consent')?>
        </fieldset>

        <div class="actions">
          <button class="btn btn-navy" type="submit">Send my property inquiry</button>
          <p class="fine">Submitting this form does not sign you up for text messages. Only the optional box above does.</p>
        </div>
      </form>
<?php endif; ?>
    </div>
  </div>
</section>

<section class="final dark" aria-labelledby="final-title">
  <img src="/assets/sell/sell-cta-1600.webp" width="1600" height="560" alt="" loading="lazy" decoding="async">
  <div class="wrap">
    <div>
      <h2 id="final-title">Ready to talk about your property?</h2>
      <p>Tell us a little about it and we&rsquo;ll review the details.</p>
    </div>
    <a class="btn btn-gold" href="#seller-form" data-focus-form>Tell us about your property <svg aria-hidden="true" focusable="false"><use href="#i-arrow"/></svg></a>
  </div>
</section>
</main>

<footer class="foot dark" role="contentinfo">
  <div class="wrap">
    <div class="fbrand">
      <a class="brand" href="/sell" aria-label="EvoSys Wholesale, home">
        <svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><rect width="40" height="40" rx="9" fill="#122b4f"/><path d="M8 21.5L20 11l12 10.5" fill="none" stroke="#d4ae5a" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M12.5 19.5V29h15v-9.5" fill="none" stroke="#fff" stroke-width="2.4" stroke-linejoin="round"/><path d="M18 29v-5.5h4V29" fill="none" stroke="#fff" stroke-width="2.2" stroke-linejoin="round"/></svg>
        <span><b>EvoSys</b><small>WHOLESALE</small></span>
      </a>
      <p style="margin:0">EvoSys Wholesale is operated by EVO Integrated Solutions LLC.</p>
    </div>
    <div>
      <h2>Contact</h2>
      <ul>
        <?php if ($publicPhone !== ''): ?><li><a href="tel:<?=htmlspecialchars(preg_replace('/[^0-9+]/','',$publicPhone))?>"><?=htmlspecialchars($publicPhone)?></a></li><?php endif; ?>
        <?php if ($publicEmail !== ''): ?><li><a href="mailto:<?=htmlspecialchars($publicEmail)?>"><?=htmlspecialchars($publicEmail)?></a></li><?php endif; ?>
        <li><a href="#seller-form" data-focus-form>Send a property inquiry</a></li>
      </ul>
    </div>
    <nav aria-label="Legal">
      <h2>Legal</h2>
      <ul>
        <li><a href="/privacy.html#sms">Privacy Policy</a></li>
        <li><a href="/terms.html#sms-wholesale">Terms</a></li>
        <li><a href="/">EvoSysPro</a></li>
      </ul>
    </nav>
    <div class="legal">EvoSys Wholesale is a product of EVO Integrated Solutions LLC, operated under the EvoSysPro platform.<br>&copy; <?=date('Y')?> EVO Integrated Solutions LLC. All rights reserved.</div>
  </div>
</footer>
<script>
/* Progressive enhancement only: the page and form work without it. A "tell
   us about your property" link scrolls to the form (native anchor) and then
   puts the keyboard focus on its first field. */
document.addEventListener('click',function(e){
  var a=e.target.closest&&e.target.closest('[data-focus-form]');
  if(!a)return;
  var f=document.getElementById('f-street_address');
  if(!f)return;
  setTimeout(function(){try{f.focus({preventScroll:true})}catch(_){f.focus()}},
    window.matchMedia('(prefers-reduced-motion: reduce)').matches?0:450);
});
</script>
</body>
</html>
