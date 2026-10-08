<?php
declare(strict_types=1);
/*
 * EvoSys Pro Universal SMS Consent Center - program registry for /sms-optin/.
 *
 *   /sms-optin/                    general    (the approved EvoSys Pro program - unchanged)
 *   /sms-optin/?program=wholesale  wholesale  (EvoSys Wholesale seller messages)
 *   /sms-optin/?program=sci        sci        (funeral home / cemetery proof of concept)
 *
 * The 'disclosure' strings are compliance text. Each is mirrored EXACTLY in the
 * platform (app/services/sms_programs.py DISCLOSURES) and a test compares them
 * character for character. Change both together, with a new 'version', or not
 * at all.
 *
 * 'status' => 'pending' means the wording has not been signed off. A pending
 * program shows its page structure but accepts NO opt-in: no checkbox, no
 * submit, nothing stored.
 */

function sms_programs(): array {
    return [
        'general' => [
            'status' => 'final',
            'version' => '2026-09',
            'form_version' => 'optin-general-v1',
            'form_id' => 'evosys_sms_optin',
            'sender' => 'EvoSys Pro (EVO Integrated Solutions LLC)',
            'eyebrow' => 'SMS Opt-In',
            'title' => 'Stay connected.',
            'intro' => 'Opt in to receive SMS updates from EvoSys Pro, including appointment confirmations, reminders, and related service follow-ups.',
            'meta_title' => 'SMS Opt-In | EvoSys Pro',
            'meta_desc' => 'Opt in to EvoSys Pro SMS appointment confirmations, reminders and related service follow-ups.',
            'disclosure' => 'By checking this box, I agree to receive SMS/text messages from EvoSys Pro including appointment confirmations, reminders, and related service follow-ups. Message frequency varies. Message & data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of any purchase or service.',
            'links_suffix' => true,
            'button' => 'Yes, I Want SMS Updates →',
            'side_title' => 'Clear consent. Clear control.',
            'side_text' => 'Messages may include appointment confirmations, reminders and related service follow-ups. Message frequency varies. Message & data rates may apply.',
            'csv' => 'sms_optins.csv',
        ],
        'wholesale' => [
            'status' => 'final',
            'version' => 'evo-wholesale-sell-2026-09-25',
            'form_version' => 'optin-wholesale-v1',
            'form_id' => 'evosys_sms_optin_wholesale',
            'sender' => 'EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro)',
            'eyebrow' => 'EvoSys Wholesale · SMS Opt-In',
            'title' => 'Text updates about your property.',
            'intro' => 'Opt in to receive text messages from EvoSys Wholesale about your property inquiry: follow-up questions, appointment scheduling and transaction updates.',
            'meta_title' => 'EvoSys Wholesale SMS Opt-In | EvoSys Pro',
            'meta_desc' => 'Opt in to EvoSys Wholesale text messages about your property inquiry.',
            // Same wording, same version as the /sell form: one Wholesale program.
            'disclosure' => 'By checking this box, I agree to receive SMS text messages from EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) regarding my property inquiry, including follow-up questions, appointment scheduling, and transaction updates. Message frequency varies. Message and data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of any service. View our Privacy Policy and Terms.',
            'links_suffix' => false,
            'button' => 'Yes, Text Me About My Property →',
            'side_title' => 'A separate program.',
            'side_text' => 'EvoSys Wholesale seller messages are their own SMS program. Opting in here does not opt you in to any other EvoSys Pro messages.',
            'csv' => 'sms_optins_wholesale.csv',
        ],
        'sci' => [
            // SCI-SPECIFIC WORDING, PENDING FINAL REVIEW (Grok package 2026-10-08,
            // Mike's sign-off outstanding). While 'pending' the page shows this
            // wording but takes NO opt-in. Going live = set 'status' => 'final'
            // here AND copy_status="final" in app/services/sms_programs.py.
            // Never replace it with generic wording.
            'status' => 'pending',
            'version' => 'sci-poc-2026-10-08',
            'form_version' => 'optin-sci-v1',
            'form_id' => 'evosys_sms_optin_sci',
            'sender' => 'EvoSys Pro (EVO Integrated Solutions LLC), on behalf of participating funeral home and cemetery locations',
            'eyebrow' => 'Pre-Planning Follow-Up · SMS Opt-In',
            'title' => 'Text follow-up on your planning information.',
            'intro' => 'Opt in to receive text messages from EvoSys Pro on behalf of participating funeral home and cemetery locations: follow-ups on pre-planning information you requested, appointment confirmations and reminders.',
            'meta_title' => 'Pre-Planning SMS Opt-In | EvoSys Pro',
            'meta_desc' => 'Opt in to text follow-ups on pre-planning information from participating funeral home and cemetery locations, sent via EvoSys Pro.',
            'disclosure' => 'I agree to receive SMS messages from EvoSys Pro (operated by EVO Integrated Solutions LLC), including follow-ups on pre-planning information I requested, appointment confirmations, reminders, and related service updates from participating funeral home and cemetery locations. Message frequency varies. Msg & data rates may apply. Reply STOP to opt out or HELP for help. Consent is not a condition of any purchase. View our Terms and Privacy Policy.',
            'links_suffix' => false,
            'button' => 'Yes, Text Me →',
            'side_title' => 'Sent via EvoSys Pro.',
            'side_text' => 'Messages come from a named representative of a specific location, sent through the EvoSys Pro platform. Opting in here does not opt you in to any other program.',
            'csv' => 'sms_optins_sci.csv',
        ],
    ];
}

/** The requested program key, 'general' when none is given, null when unknown.
 *  An unknown ?program= never falls back to another program's consent. */
function sms_program_key(): ?string {
    $raw = $_GET['program'] ?? $_POST['program'] ?? '';
    $k = strtolower(trim(is_string($raw) ? $raw : ''));
    if ($k === '') return 'general';
    return array_key_exists($k, sms_programs()) ? $k : null;
}

function sms_program_open(array $p): bool {
    return $p['status'] === 'final' && $p['version'] !== '' && $p['disclosure'] !== '';
}
