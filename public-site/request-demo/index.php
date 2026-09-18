<?php
declare(strict_types=1);
require __DIR__.'/../private/form-utils.php';
require __DIR__.'/../private/site.php';
header('Cache-Control: no-store');

function demo_json(int $status, array $body): never {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($body, JSON_UNESCAPED_SLASHES);
    exit;
}

function demo_input(): array {
    $raw = file_get_contents('php://input');
    if (!is_string($raw) || $raw === '') return [];
    $decoded = json_decode($raw, true);
    return is_array($decoded) ? $decoded : [];
}

function demo_submission_id($raw): string {
    $value = evosys_clean((string)$raw, 128);
    if ($value !== '' && preg_match('/^[A-Za-z0-9._:-]{8,128}$/', $value)) return $value;
    return bin2hex(random_bytes(16));
}

function demo_booking_code(): string {
    // Public salesperson booking codes are intentionally opaque. The website
    // never resolves a person locally; it only forwards the code to the
    // platform, which validates brand scope, active membership and revocation.
    $raw = $_GET['r'] ?? $_GET['booking_code'] ?? '';
    $value = evosys_clean((string)$raw, 128);
    if ($value === '') return '';
    return preg_match('/^[A-Za-z0-9._~-]{6,128}$/', $value) ? $value : '';
}

function demo_fallback_payload(array $in, string $bookingCode=""): array {
    $name = evosys_clean((string)($in['name'] ?? ''), 120);
    $company = evosys_clean((string)($in['company'] ?? ''), 160);
    $email = evosys_clean((string)($in['email'] ?? ''), 180);
    $phone = evosys_clean((string)($in['phone'] ?? ''), 40);
    $industry = evosys_clean((string)($in['industry'] ?? ''), 100);
    $pain = evosys_clean((string)($in['pain_point'] ?? ''), 180);
    $goals = evosys_clean((string)($in['goals'] ?? ''), 2500);

    $errors = [];
    if ($name === '') $errors[] = 'Name is required.';
    if ($company === '') $errors[] = 'Company is required.';
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) $errors[] = 'A valid email is required.';
    if (strlen(preg_replace('/\D/', '', $phone)) < 10) $errors[] = 'A valid phone number is required.';
    if ($industry === '') $errors[] = 'Industry is required.';
    if ($pain === '') $errors[] = 'Choose the business challenge that matters most right now.';
    if ($errors) demo_json(422, ['success'=>false, 'detail'=>implode(' ', $errors)]);

    $consent = 'Optional SMS consent: By checking this box, I agree to receive SMS/text messages from EvoSys Pro about my demo request, appointment reminders, service follow-ups, and related updates. Message frequency varies. Message & data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of purchase.';

    return [
        'submission_id' => demo_submission_id($in['submission_id'] ?? ''),
        'submitted_at' => gmdate('c'),
        'name' => $name,
        'company' => $company,
        'email' => $email,
        'phone' => $phone,
        'industry' => $industry,
        'pain_point' => $pain,
        'locations' => evosys_clean((string)($in['locations'] ?? ''), 60),
        'leads' => evosys_clean((string)($in['leads'] ?? ''), 60),
        'current_system' => evosys_clean((string)($in['current_system'] ?? ''), 120),
        'goals' => $goals,
        'visitor_timezone' => evosys_clean((string)($in['visitor_timezone'] ?? ''), 100),
        'sms_consent' => !empty($in['sms_consent']) && strtoupper((string)$in['sms_consent']) !== 'NO' ? 'YES' : 'NO',
        'sms_consent_text' => $consent,
        'booking_code' => $bookingCode !== '' ? $bookingCode : null,
        'source_url' => 'https://evosyspro.live/request-demo/',
        'referrer' => evosys_clean((string)($in['referrer'] ?? ''), 500),
        'ip' => evosys_ip(),
        'user_agent' => $_SERVER['HTTP_USER_AGENT'] ?? '',
    ];
}

/**
 * Exact public-booking POST contract from DISCOVERY_DEMO_PLATFORM_BOOKING_REPORT.md §19.3.
 * Keep this payload intentionally narrow: do not add website-only fields unless the
 * platform contract is changed first.
 */
function demo_booking_payload(array $in, string $bookingCode=""): array {
    $fullName = evosys_clean((string)($in['name'] ?? ''), 120);
    $company = evosys_clean((string)($in['company'] ?? ''), 160);
    $email = evosys_clean((string)($in['email'] ?? ''), 180);
    $phone = evosys_clean((string)($in['phone'] ?? ''), 40);
    $industry = evosys_clean((string)($in['industry'] ?? ''), 100);
    $challenge = evosys_clean((string)($in['pain_point'] ?? ''), 180);
    $goals = evosys_clean((string)($in['goals'] ?? ''), 2500);
    $startUtc = evosys_clean((string)($in['starts_at'] ?? ''), 80);
    $meetingType = evosys_clean((string)($in['meeting_type'] ?? 'discovery_demo'), 100);
    $timezone = evosys_clean((string)($in['visitor_timezone'] ?? ''), 100);

    $errors = [];
    if ($fullName === '') $errors[] = 'Name is required.';
    if ($company === '') $errors[] = 'Company is required.';
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) $errors[] = 'A valid email is required.';
    if (strlen(preg_replace('/\D/', '', $phone)) < 10) $errors[] = 'A valid phone number is required.';
    if ($industry === '') $errors[] = 'Industry is required.';
    if ($challenge === '') $errors[] = 'Choose the business challenge that matters most right now.';
    if ($startUtc === '') $errors[] = 'Choose an available Discovery + Demo time.';
    if ($meetingType === '') $errors[] = 'The meeting type is unavailable. Please refresh the page.';
    if ($errors) demo_json(422, ['success'=>false, 'detail'=>implode(' ', $errors)]);

    $challengeDetail = '';
    $notes = $goals;
    if (strcasecmp($challenge, 'Something else') === 0 || $challenge === 'other') {
        $challengeDetail = $goals;
        $notes = '';
    }

    return [
        'code' => $bookingCode !== '' ? $bookingCode : null,
        'meeting_type' => $meetingType,
        'start_utc' => $startUtc,
        'full_name' => $fullName,
        'company' => $company,
        'email' => $email,
        'phone' => $phone,
        'industry' => $industry,
        'primary_challenge' => $challenge,
        'primary_challenge_detail' => $challengeDetail,
        'current_system' => evosys_clean((string)($in['current_system'] ?? ''), 120),
        'locations' => evosys_clean((string)($in['locations'] ?? ''), 60),
        'lead_volume' => evosys_clean((string)($in['leads'] ?? ''), 60),
        'notes' => $notes,
        'timezone' => $timezone,
        'submission_id' => demo_submission_id($in['submission_id'] ?? ''),
        'page_url' => evosys_clean((string)($in['page_url'] ?? 'https://evosyspro.live/request-demo/'), 500),
        'referrer' => evosys_clean((string)($in['referrer'] ?? ''), 500),
    ];
}

$action = evosys_clean((string)($_GET['action'] ?? ''), 40);
$bookingCode = demo_booking_code();
if ($action !== '') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        demo_json(405, ['success'=>false, 'detail'=>'Method not allowed.']);
    }

    $in = demo_input();
    if (trim((string)($in['website_url'] ?? '')) !== '') {
        demo_json(200, ['success'=>true]);
    }
    $started = (int)($in['form_started_at'] ?? 0);
    if ($action !== 'availability' && $started > 0 && time() - $started < 2) {
        demo_json(429, ['success'=>false, 'detail'=>'Please wait a moment and submit again.']);
    }

    if ($action === 'availability') {
        $timezone = evosys_clean((string)($in['visitor_timezone'] ?? ''), 100);
        $meetingType = evosys_clean((string)($in['meeting_type'] ?? ''), 100);
        $from = evosys_clean((string)($in['from'] ?? ''), 10);
        $to = evosys_clean((string)($in['to'] ?? ''), 10);
        $params = [
            'code' => $bookingCode !== '' ? $bookingCode : null,
            'meeting_type' => $meetingType !== '' ? $meetingType : null,
            'timezone' => $timezone !== '' ? $timezone : null,
        ];

        // The public booking API deliberately exposes meeting metadata and
        // availability as separate GETs. Fetch meeting first so a disabled or
        // invalid booking configuration fails before doing the slots query.
        $meetingResult = evosys_get_json_url(evosys_public_booking_url('meeting', $params), 10);
        if (!$meetingResult['ok'] || !is_array($meetingResult['data'])) {
            $result = $meetingResult;
        } elseif (($meetingResult['data']['bookable'] ?? true) === false) {
            $result = $meetingResult;
        } else {
            $slotParams = $params;
            if ($from !== '') $slotParams['from'] = $from;
            if ($to !== '') $slotParams['to'] = $to;
            $slotsResult = evosys_get_json_url(evosys_public_booking_url('slots', $slotParams), 12);
            if (!$slotsResult['ok'] || !is_array($slotsResult['data'])) {
                $result = $slotsResult;
            } else {
                // Preserve the backend contract but attach meeting metadata so
                // the browser needs only one same-origin request.
                $combined = $slotsResult['data'];
                foreach (['brand','meeting','salesperson','assigned_via','bookable','visitor_timezone','types','form'] as $key) {
                    if (array_key_exists($key, $meetingResult['data'])) {
                        $combined[$key] = $meetingResult['data'][$key];
                    }
                }
                $result = ['ok'=>true, 'status'=>200, 'data'=>$combined, 'raw'=>'', 'error'=>null];
            }
        }
    } elseif ($action === 'book') {
        // IMPORTANT: the final POST body is intentionally built in one helper.
        // It will be locked to Claude's §19.3 schema before production deploy.
        $url = evosys_public_booking_url('book');
        $result = evosys_post_json_url($url, demo_booking_payload($in, $bookingCode));
    } elseif ($action === 'request') {
        // Contact-me fallback continues through the existing intake endpoint.
        $url = evosys_demo_endpoint('request');
        $result = evosys_post_json_url($url, demo_fallback_payload($in, $bookingCode));
    } else {
        demo_json(404, ['success'=>false, 'detail'=>'Unknown action.']);
    }

    if ($result['status'] > 0 && is_array($result['data'])) {
        demo_json($result['status'], $result['data']);
    }
    demo_json(503, [
        'success'=>false,
        'detail'=>'We could not reach online scheduling right now. Please try again shortly or email support@evosyspro.live.'
    ]);
}

$submissionId = bin2hex(random_bytes(16));
site_head(
    'Book a Discovery + Demo | EvoSys Pro',
    'Book a live EvoSys Pro discovery and product demonstration tailored to your business.'
);
site_nav();
?>
<style>
.demo-booking-shell{align-items:start}.demo-step{border-top:1px solid rgba(112,229,222,.16);padding-top:20px;margin-top:20px}.demo-step:first-child{border-top:0;padding-top:0;margin-top:0}.demo-step-kicker{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#70e5de;font-weight:800;margin-bottom:5px}.demo-step-title{font-size:18px;font-weight:800;margin:0 0 6px;color:#f6f9fc}.demo-step-copy{font-size:13px;line-height:1.55;color:#91a6bd;margin:0 0 16px}.booking-status{border:1px solid rgba(112,229,222,.18);background:rgba(5,20,32,.55);border-radius:12px;padding:13px 14px;color:#a9bdd0;font-size:13px}.booking-status.error{border-color:rgba(255,116,116,.35);color:#ffd0d0}.booking-status.good{border-color:rgba(112,229,222,.34);color:#dffefa}.date-strip{display:flex;gap:8px;overflow:auto;padding:4px 1px 9px;margin-top:10px}.date-choice,.time-choice{appearance:none;border:1px solid #29445c;background:#071726;color:#dbe8f3;border-radius:10px;cursor:pointer;font:inherit;transition:.16s ease}.date-choice{min-width:92px;padding:10px 12px;text-align:left}.date-choice small{display:block;color:#7f96aa;font-size:11px;text-transform:uppercase;letter-spacing:.06em}.date-choice strong{display:block;font-size:14px;margin-top:2px}.date-choice:hover,.time-choice:hover,.date-choice[aria-pressed="true"],.time-choice[aria-pressed="true"]{border-color:#f2b53f;box-shadow:0 0 0 1px rgba(242,181,63,.25);background:#102235}.time-grid{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.time-choice{padding:9px 13px;font-weight:700;font-size:13px}.booking-label-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:4px}.timezone-badge{font-size:11px;color:#91a6bd;border:1px solid #29445c;border-radius:999px;padding:4px 8px;white-space:nowrap}.selected-slot{font-size:12px;color:#70e5de;margin:10px 0 0;min-height:18px}.demo-benefits{padding:0;margin:14px 0 0;list-style:none}.demo-benefits li{position:relative;padding-left:22px;margin:9px 0;color:#a9bdd0;line-height:1.45}.demo-benefits li:before{content:'✓';position:absolute;left:0;color:#70e5de;font-weight:900}.next-open{margin-top:15px;padding:12px;border:1px solid rgba(242,181,63,.28);border-radius:10px;background:rgba(242,181,63,.06)}.next-open small{display:block;text-transform:uppercase;letter-spacing:.09em;color:#d9a93f;font-size:10px;font-weight:800}.next-open strong{display:block;color:#f6f9fc;margin-top:4px}.booking-success{border:1px solid rgba(112,229,222,.35);background:rgba(26,136,129,.08);border-radius:14px;padding:22px}.booking-success h2{margin:0 0 8px;font-size:25px}.booking-success .when{font-size:18px;font-weight:800;color:#fff;margin:15px 0 6px}.booking-success p{color:#a9bdd0;line-height:1.6}.booking-success .btn{display:inline-flex;margin-top:8px}.fallback-action{margin-top:10px}.form-alert{display:none;margin:0 0 14px}.form-alert.show{display:block}.btn[disabled]{opacity:.62;cursor:not-allowed}.schedule-disabled{opacity:.7}.field-help{font-size:11px;color:#71879a;margin-top:6px}.more-dates{border:0;background:transparent;color:#70e5de;padding:5px 0;cursor:pointer;font-weight:700;font-size:12px}.date-choice:focus-visible,.time-choice:focus-visible,.more-dates:focus-visible,.btn:focus-visible{outline:3px solid rgba(112,229,222,.55);outline-offset:2px}.booking-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
@media(max-width:720px){.booking-label-row{align-items:flex-start;flex-direction:column}.date-choice{min-width:84px}.time-choice{flex:1 0 calc(33.333% - 8px)}}
</style>
<main>
<section class="page-hero"><div class="wrap"><div class="eyebrow">Discovery + Demo</div><h1>Book your EvoSys Pro Discovery + Demo.</h1><p>Tell us a little about your business, choose a real available time, and we’ll tailor the conversation around your operation and the problems you want to solve.</p></div></section>
<section><div class="wrap form-shell demo-booking-shell"><div class="form-card">
<div id="formAlert" class="error form-alert" role="alert" aria-live="assertive"></div>
<div id="bookingSuccess" class="booking-success" role="status" aria-live="polite" hidden></div>
<form id="demoBookingForm" novalidate>
<input class="hp" name="website_url" tabindex="-1" autocomplete="off">
<input type="hidden" name="form_started_at" value="<?=time()?>">
<input type="hidden" name="submission_id" value="<?=htmlspecialchars($submissionId)?>">
<input type="hidden" name="starts_at" id="startsAt" value="">
<input type="hidden" name="meeting_type" id="meetingType" value="discovery_demo">

<div class="demo-step">
<div class="demo-step-kicker">Step 1</div><h2 class="demo-step-title">Tell us who you are</h2><p class="demo-step-copy">Just enough information for us to prepare for the conversation.</p>
<div class="form-grid">
<div class="field"><label for="demoName">Full name *</label><input id="demoName" name="name" required autocomplete="name"></div>
<div class="field"><label for="demoCompany">Company *</label><input id="demoCompany" name="company" required autocomplete="organization"></div>
<div class="field"><label for="demoEmail">Email *</label><input id="demoEmail" type="email" name="email" required autocomplete="email"></div>
<div class="field"><label for="demoPhone">Phone *</label><input id="demoPhone" name="phone" required autocomplete="tel" inputmode="tel" placeholder="(214) 555-0100"></div>
<div class="field"><label for="demoIndustry">Industry *</label><select id="demoIndustry" name="industry" required><option value="">Choose one</option><?php foreach(['Pest Control','Cleaning Services','Home Services / Field Services','Roofing / Construction','Insurance','Healthcare / Wellness','Automotive','Real Estate','Professional Services','Energy / Utilities','Retail / Local','Other'] as $o):?><option><?=htmlspecialchars($o)?></option><?php endforeach;?></select></div>
<div class="field"><label for="demoLocations">Number of locations</label><select id="demoLocations" name="locations"><option value="">Select</option><?php foreach(['1','2–5','6–20','21+'] as $o):?><option><?=htmlspecialchars($o)?></option><?php endforeach;?></select></div>
<div class="field"><label for="demoLeads">Active / available leads</label><select id="demoLeads" name="leads"><option value="">Select</option><?php foreach(['Under 2,500','2,500–5,000','5,001–10,000','10,001–50,000','50,000+'] as $o):?><option><?=htmlspecialchars($o)?></option><?php endforeach;?></select></div>
<div class="field"><label for="demoSystem">Current system</label><input id="demoSystem" name="current_system" placeholder="CRM, spreadsheets, other tools"></div>
</div></div>

<div class="demo-step">
<div class="demo-step-kicker">Step 2</div><h2 class="demo-step-title">What is the biggest challenge you want to improve?</h2><p class="demo-step-copy">Give us the pain point. We’ll use it to make the discovery and demo relevant to your business.</p>
<div class="form-grid">
<div class="field full"><label for="demoPain">Primary pain point *</label><select id="demoPain" name="pain_point" required><option value="">Choose the one that matters most right now</option><?php foreach([
'Following up with leads consistently','Responding to new leads fast enough','Getting more appointments / booked meetings','Re-engaging old or inactive leads','Managing the sales process / pipeline','Keeping the team accountable and organized','Customer communication and follow-up','Too much manual work / not enough automation','Reporting / knowing what’s actually happening','Managing multiple locations / teams','Our current CRM or tools aren’t working well','Something else'
] as $o):?><option><?=htmlspecialchars($o)?></option><?php endforeach;?></select></div>
<div class="field full"><label for="demoGoals">Anything else you want us to know?</label><textarea id="demoGoals" name="goals" placeholder="Tell us what’s frustrating you, what you’re trying to improve, or what you wish your current system did better."></textarea><div class="field-help">Optional — a sentence or two is enough.</div></div>
</div></div>

<div class="demo-step" id="scheduleStep">
<div class="demo-step-kicker">Step 3</div><div class="booking-label-row"><div><h2 class="demo-step-title">Choose your Discovery + Demo time</h2><p class="demo-step-copy" style="margin-bottom:0">Only times that are actually available will be shown.</p></div><span class="timezone-badge" id="timezoneBadge">Detecting timezone…</span></div>
<div id="bookingStatus" class="booking-status" role="status" aria-live="polite">Checking the EvoSys appointment calendar…</div>
<div id="dateStrip" class="date-strip" role="group" aria-label="Available appointment dates" hidden></div>
<button id="moreDates" type="button" class="more-dates" aria-controls="dateStrip" hidden>Show more dates</button>
<div id="timeGrid" class="time-grid" role="group" aria-label="Available appointment times" hidden></div>
<div id="selectedSlot" class="selected-slot" aria-live="polite"></div>
<div id="fallbackAction" class="fallback-action" hidden><div class="booking-actions"><button type="button" class="btn btn-gold" id="requestInstead">Send My Information Instead →</button><button type="button" class="btn btn-ghost-gold" id="retryAvailability">Check Availability Again</button></div><div class="field-help">Online scheduling is unavailable right now. Everything you have typed is kept exactly as it is — send it and our team will contact you directly to arrange your Discovery + Demo.</div></div>
</div>

<div class="demo-step">
<div class="form-grid"><div class="field full"><label class="check"><input type="checkbox" name="sms_consent" value="YES"><span><strong>Optional SMS consent.</strong> By checking this box, I agree to receive SMS/text messages from EvoSys Pro about my demo request, appointment reminders, service follow-ups, and related updates. Message frequency varies. Message &amp; data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of purchase. See <a href="../sms-terms.html">SMS Terms</a> and <a href="../privacy.html">Privacy Policy</a>.</span></label></div>
<div class="field full" id="bookAction"><button class="btn btn-gold" type="submit" id="bookButton" disabled>Book My Discovery + Demo →</button><div class="form-meta">By booking, you agree that EvoSys Pro may contact you by email or phone regarding this meeting. SMS requires the separate optional consent checkbox above.</div></div></div>
</div>
</form></div>

<aside class="form-side"><div class="card"><div class="eyebrow">Discovery + Demo</div><h3 id="meetingLength">A real working session — not a generic sales deck.</h3><ul class="demo-benefits"><li>Conversation focused on your operation</li><li>Live EvoSys Pro walkthrough</li><li>Real workflows tied to your pain point</li><li>Q&amp;A with our team</li></ul><div class="next-open" id="nextOpen" hidden><small>Next available</small><strong id="nextOpenValue"></strong></div></div><div class="card"><div class="eyebrow">What Happens On The Call</div><p>We’ll understand what you’re doing today, identify where leads or customers are falling through the cracks, and show the EvoSys workflows that fit the opportunity.</p></div><div class="card"><div class="eyebrow">Need Help?</div><p>Email <a href="mailto:support@evosyspro.live" style="color:#70e5de">support@evosyspro.live</a> or use our <a href="../support/" style="color:#70e5de">support form</a>.</p></div></aside>
</div></section></main>
<script>
(function(){
'use strict';
const form=document.getElementById('demoBookingForm');
const bookButton=document.getElementById('bookButton');
const startsAt=document.getElementById('startsAt');
const meetingType=document.getElementById('meetingType');
const statusBox=document.getElementById('bookingStatus');
const dateStrip=document.getElementById('dateStrip');
const timeGrid=document.getElementById('timeGrid');
const selectedSlot=document.getElementById('selectedSlot');
const timezoneBadge=document.getElementById('timezoneBadge');
const formAlert=document.getElementById('formAlert');
const successBox=document.getElementById('bookingSuccess');
const nextOpen=document.getElementById('nextOpen');
const nextOpenValue=document.getElementById('nextOpenValue');
const meetingLength=document.getElementById('meetingLength');
const fallbackAction=document.getElementById('fallbackAction');const bookAction=document.getElementById('bookAction');const retryAvailability=document.getElementById('retryAvailability');
const requestInstead=document.getElementById('requestInstead');
const moreDates=document.getElementById('moreDates');
const tz=(Intl.DateTimeFormat().resolvedOptions().timeZone||'America/Chicago');
const bookingCode=<?=json_encode($bookingCode, JSON_HEX_TAG|JSON_HEX_AMP|JSON_HEX_APOS|JSON_HEX_QUOT)?>;
const actionUrl=(action)=>'./?action='+encodeURIComponent(action)+(bookingCode?'&r='+encodeURIComponent(bookingCode):'');
let slots=[]; let groups=[]; let selectedDate=''; let availabilityReady=false; let showAllDates=false;

timezoneBadge.textContent='Times shown in '+tz.replaceAll('_',' ');

function escHtml(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));}
function alertMessage(message){formAlert.textContent=message;formAlert.classList.add('show');formAlert.scrollIntoView({behavior:'smooth',block:'center'});}
function clearAlert(){formAlert.textContent='';formAlert.classList.remove('show');}
function apiMessage(data,fallback){return String((data&&((data.detail)||(data.message)||(data.error_message)))||fallback);}
function validDate(value){const d=new Date(value);return typeof value==='string'&&value!==''&&!Number.isNaN(d.getTime());}
function normalizedSlots(data){const raw=Array.isArray(data?.slots)?data.slots:[];return raw.map(s=>typeof s==='string'?{starts_at:s}:{...s,starts_at:s.start_utc||s.starts_at||s.start_at||s.start}).filter(s=>validDate(s.starts_at)).sort((a,b)=>new Date(a.starts_at)-new Date(b.starts_at));}
function normalizedBooking(data){const status=String(data?.status||'').toLowerCase();const confirmationStatus=String(data?.confirmation_email?.status||'').toLowerCase();const joinUrl=String(data?.join_url||'');return {booked:status==='booked'||status==='already_booked'||data?.already_booked===true,starts_at:data?.start_utc||data?.start_visitor_local||data?.start_meeting_local||'',meeting_url:joinUrl,reference:data?.reference||'',already_booked:data?.already_booked===true||status==='already_booked',confirmation_status:confirmationStatus,message:data?.message||data?.detail||''};}
async function postAction(action,payload,timeoutMs){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeoutMs||15000);try{const r=await fetch(actionUrl(action),{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},credentials:'same-origin',cache:'no-store',signal:controller.signal,body:JSON.stringify(payload)});const data=await r.json().catch(()=>({}));return {response:r,data};}catch(e){if(e&&e.name==='AbortError')throw new Error('Online scheduling took too long to respond. Please try again or send your information instead.');throw e;}finally{clearTimeout(timer);}}
function dateKey(iso){const parts=new Intl.DateTimeFormat('en-US',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(iso));const get=t=>parts.find(p=>p.type===t)?.value||'';return get('year')+'-'+get('month')+'-'+get('day');}
function dateLabel(iso){return new Intl.DateTimeFormat('en-US',{timeZone:tz,weekday:'short',month:'short',day:'numeric'}).format(new Date(iso));}
function dayParts(iso){const parts=new Intl.DateTimeFormat('en-US',{timeZone:tz,weekday:'short',month:'short',day:'numeric'}).formatToParts(new Date(iso));const get=t=>parts.find(p=>p.type===t)?.value||'';return {dow:get('weekday'),day:get('month')+' '+get('day')};}
function timeLabel(iso){return new Intl.DateTimeFormat('en-US',{timeZone:tz,hour:'numeric',minute:'2-digit'}).format(new Date(iso));}
function fullWhen(iso){return new Intl.DateTimeFormat('en-US',{timeZone:tz,weekday:'long',month:'long',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}).format(new Date(iso));}
function groupSlots(){const map=new Map();slots.forEach(s=>{const k=dateKey(s.starts_at);if(!map.has(k))map.set(k,[]);map.get(k).push(s);});groups=[...map.entries()].map(([key,items])=>({key,items}));}
function renderDates(){dateStrip.innerHTML='';const visible=showAllDates?groups:groups.slice(0,5);visible.forEach(g=>{const p=dayParts(g.items[0].starts_at);const b=document.createElement('button');b.type='button';b.className='date-choice';b.setAttribute('aria-pressed',g.key===selectedDate?'true':'false');b.innerHTML='<small>'+p.dow+'</small><strong>'+p.day+'</strong>';b.addEventListener('click',()=>selectDate(g.key));dateStrip.appendChild(b);});dateStrip.hidden=false;moreDates.hidden=groups.length<=5;moreDates.textContent=showAllDates?'Show fewer dates':'Show more dates';}
function selectDate(key){selectedDate=key;startsAt.value='';selectedSlot.textContent='';bookButton.disabled=true;renderDates();timeGrid.innerHTML='';const group=groups.find(g=>g.key===key);if(!group){timeGrid.hidden=true;return;}group.items.forEach(slot=>{const b=document.createElement('button');b.type='button';b.className='time-choice';b.textContent=timeLabel(slot.starts_at);b.setAttribute('aria-pressed','false');b.addEventListener('click',()=>{[...timeGrid.querySelectorAll('.time-choice')].forEach(x=>x.setAttribute('aria-pressed','false'));b.setAttribute('aria-pressed','true');startsAt.value=slot.starts_at;selectedSlot.textContent='Selected: '+fullWhen(slot.starts_at);bookButton.disabled=false;clearAlert();});timeGrid.appendChild(b);});timeGrid.hidden=false;}
function setBookingAvailable(on){if(bookAction)bookAction.hidden=!on;fallbackAction.hidden=!!on;if(retryAvailability)retryAvailability.disabled=false;}
function offline(message){availabilityReady=false;statusBox.textContent=message||'Online scheduling is temporarily unavailable.';statusBox.className='booking-status error';dateStrip.hidden=true;timeGrid.hidden=true;moreDates.hidden=true;nextOpen.hidden=true;startsAt.value='';selectedSlot.textContent='';bookButton.disabled=true;setBookingAvailable(false);}
function ymd(d){const y=d.getFullYear();const m=String(d.getMonth()+1).padStart(2,'0');const day=String(d.getDate()).padStart(2,'0');return y+'-'+m+'-'+day;}
async function loadAvailability(){statusBox.textContent='Checking the EvoSys appointment calendar…';statusBox.className='booking-status';dateStrip.hidden=true;timeGrid.hidden=true;moreDates.hidden=true;nextOpen.hidden=true;fallbackAction.hidden=true;try{const start=new Date();const end=new Date(start.getTime());end.setDate(end.getDate()+30);const {response:r,data}=await postAction('availability',{visitor_timezone:tz,meeting_type:meetingType.value||'discovery_demo',from:ymd(start),to:ymd(end)},12000);if(!r.ok||data.bookable===false)throw new Error(apiMessage(data,'Online scheduling is temporarily unavailable.'));if(data?.meeting?.key){meetingType.value=String(data.meeting.key);}slots=normalizedSlots(data);if(!slots.length)throw new Error(apiMessage(data,'No online openings are available in the next 30 days.'));availabilityReady=true;setBookingAvailable(true);statusBox.textContent='Choose a date, then pick an available time.';statusBox.className='booking-status good';const duration=data?.meeting?.duration_minutes||data?.duration_minutes;if(duration){meetingLength.textContent=duration+'-Minute Discovery + Demo — a real working session, not a generic sales deck.';}groupSlots();selectedDate=groups[0]?.key||'';renderDates();selectDate(selectedDate);if(slots[0]){nextOpen.hidden=false;nextOpenValue.textContent=fullWhen(slots[0].starts_at);}}catch(e){offline(e.message);}}
function formPayload(){const fd=new FormData(form);const out={};fd.forEach((v,k)=>out[k]=v);out.sms_consent=fd.has('sms_consent')?'YES':'NO';out.visitor_timezone=tz;out.page_url=window.location.origin+window.location.pathname;out.referrer=document.referrer||'';return out;}
function setBusy(on,label){form.dataset.submitting=on?'1':'0';bookButton.disabled=on||!startsAt.value;requestInstead.disabled=on;if(on){bookButton.dataset.label=bookButton.textContent;bookButton.textContent=label||'Booking…';}else if(bookButton.dataset.label){bookButton.textContent=bookButton.dataset.label;}}
function confirmationCopy(status){switch(String(status||'').toLowerCase()){case 'sent':return 'Check your inbox for the details.';case 'pending_meeting_link':case 'delivery_disabled':return 'You’re booked — your meeting link is on its way.';case 'no_recipient':case 'failed':return 'You’re booked. We’ll be in touch to confirm.';default:return 'You’re booked. We’ll be in touch with your meeting details.';}}
function success(data,requestOnly){form.hidden=true;clearAlert();successBox.hidden=false;successBox.focus?.();if(requestOnly){successBox.innerHTML='<div class="eyebrow">Request received</div><h2>We have your information.</h2><p>Online scheduling was unavailable, but your information was recorded. Our team will contact you to arrange your Discovery + Demo.</p>';return;}const b=normalizedBooking(data);const when=b.starts_at&&validDate(b.starts_at)?fullWhen(b.starts_at):'Your selected time';const notice=confirmationCopy(b.confirmation_status);let actions='';if(/^https:\/\//i.test(String(b.meeting_url))){actions+='<a class="btn btn-ghost-gold" href="'+escHtml(b.meeting_url)+'" target="_blank" rel="noopener noreferrer">Join Zoom</a>';}const ref=b.reference?'<p style="font-size:12px">Booking reference: <strong>'+escHtml(b.reference)+'</strong></p>':'';const eyebrow=b.already_booked?'Already booked':'You’re booked';const title=b.already_booked?'Your existing EvoSys Pro Discovery + Demo is confirmed.':'Your EvoSys Pro Discovery + Demo is scheduled.';successBox.innerHTML='<div class="eyebrow">'+escHtml(eyebrow)+'</div><h2>'+escHtml(title)+'</h2><div class="when">'+escHtml(when)+'</div><p>'+escHtml(notice)+'</p>'+ref+(actions?'<div class="booking-actions">'+actions+'</div>':'');}
async function submit(action){if(form.dataset.submitting==='1')return;if(!form.reportValidity())return;clearAlert();if(action==='book'&&!startsAt.value){alertMessage('Choose an available Discovery + Demo time.');return;}setBusy(true,action==='book'?'Booking your Discovery + Demo…':'Sending your information…');try{const {response:r,data}=await postAction(action,formPayload(),15000);if(r.status===409&&action==='book'){startsAt.value='';selectedSlot.textContent='';bookButton.disabled=true;alertMessage(apiMessage(data,'That time was just booked. Please choose another opening.'));await loadAvailability();return;}if(!r.ok)throw new Error(apiMessage(data,'We could not complete your request. Please try again.'));if(action==='book'){const b=normalizedBooking(data);if(b.booked){success(data,false);return;}throw new Error('The meeting was not confirmed. Please choose another time or contact support.');}success(data,true);}catch(e){alertMessage(e.message||'We could not complete your request.');if(action==='book'&&(String(e.message).toLowerCase().includes('scheduling')||String(e.message).toLowerCase().includes('calendar')||String(e.message).toLowerCase().includes('too long'))){offline(e.message);}}finally{setBusy(false);}}
form.addEventListener('submit',e=>{e.preventDefault();submit('book');});requestInstead.addEventListener('click',()=>submit('request'));if(retryAvailability){retryAvailability.addEventListener('click',()=>{retryAvailability.disabled=true;loadAvailability();});}moreDates.addEventListener('click',()=>{showAllDates=!showAllDates;renderDates();});loadAvailability();
})();
</script>
<?php site_footer(); ?>
