<?php
return [
  'NOTIFY_EMAIL' => 'support@evosyspro.live',
  'OPENAI_API_KEY' => '',
  'OPENAI_MODEL' => 'gpt-5.6-luna',
  'DEMO_WEBHOOK_URL' => '',
  'PUBLIC_BOOKING_BASE_URL' => '', // optional; e.g. https://api.example.com/public-booking/evosyspro
  // Applicant storage. STRONGLY RECOMMENDED in production: an absolute path
  // ABOVE the document root, e.g. '/home/<account>/evosys-private/careers'.
  // Left empty it defaults to storage/careers inside this tree, which stays
  // private only for as long as the web server honours .htaccess.
  'CAREERS_STORAGE_DIR' => '',
  // Careers Manager bootstrap login: output of PHP password_hash(). Set once
  // at deployment; after the first sign-in the password is changed from
  // Settings and the working hash lives in admin-auth.json.
  'CAREERS_ADMIN_PASSWORD_HASH' => '',
  'SMS_OPTIN_WEBHOOK_URL' => '',
  'SUPPORT_WEBHOOK_URL' => ''
];
