<?php
declare(strict_types=1);

function careers_cfg(string $key, string $default = ''): string {
    if (function_exists('evosys_cfg')) return evosys_cfg($key, $default);
    $env = getenv($key);
    return $env !== false && trim((string)$env) !== '' ? trim((string)$env) : $default;
}

function careers_base_dir(): string {
    $configured = careers_cfg('CAREERS_STORAGE_DIR', '');
    if ($configured !== '') return rtrim($configured, '/\\');
    return dirname(__DIR__) . '/storage/careers';
}

function careers_ensure_storage(): void {
    $base = careers_base_dir();
    foreach ([$base, $base.'/applications', $base.'/resumes'] as $dir) {
        if (!is_dir($dir)) @mkdir($dir, 0755, true);
    }
}

function careers_default_config(): array {
    return [
        'site' => [
            'hero_kicker' => 'Careers at EvoSys Pro',
            'hero_title' => 'Find Your Next Opportunity.',
            'hero_copy' => 'Join a growing team building practical AI-powered business solutions. Explore current openings and apply directly online.',
            'visual_title' => 'Real roles. Real responsibility. Real growth.',
            'visual_copy' => 'Open positions are managed directly by EvoSys Pro. When a role closes, it disappears from this page. When a new one opens, it can be added without changing website code.',
            'visual_chips' => ['Sales','Remote Opportunities','Business Growth','AI + Automation'],
            'roles_title' => 'Find your next opportunity.',
            'roles_copy' => 'Explore current openings. Use the filters to narrow the list, then apply directly through the website.',
            'why_title' => 'More Than a Job. A Greater Opportunity.',
            'why_items' => [
                ['title'=>'Growth Opportunity','copy'=>'Build your career with a growing business and an ambitious vision.'],
                ['title'=>'Flexible Work Environment','copy'=>'Many roles are remote or flexible, depending on the position.'],
                ['title'=>'Performance-Driven Culture','copy'=>'Strong performance, ownership, and follow-through matter here.'],
                ['title'=>'Innovation That Matters','copy'=>'Help businesses turn AI and automation into practical operating results.'],
            ],
            'apply_kicker' => 'Apply Today',
            'apply_title' => 'Take the Next Step.',
            'apply_copy' => 'Choose an open role and tell us about your background. Your application goes directly into the private Careers Manager for review.',
        ],
        'income' => [
            'enabled' => true,
            'title' => 'Projected Income for a Sales Professional',
            'subtitle' => 'Performance-based earnings depend on activity, consistency, and deal mix.',
            'scenarios' => [
                ['label'=>'Getting Started','activity'=>'3–5 deals per month','income'=>'$3,000–$6,000/mo'],
                ['label'=>'Consistent Producer','activity'=>'6–10 deals per month','income'=>'$7,500–$15,000/mo'],
                ['label'=>'Top Performer','activity'=>'10+ deals per month','income'=>'$15,000+/mo'],
            ],
            'disclaimer' => 'Illustrative earnings scenarios only. Actual earnings vary based on individual performance, deal mix, collections, and applicable compensation terms. Sales Consultant is a 1099 independent-contractor, commission-based opportunity with no base salary.',
        ],
        'jobs' => [
            [
                'id'=>'sales-consultant',
                'title'=>'Sales Consultant',
                'department'=>'Sales',
                'location'=>'Remote (US)',
                'employment_type'=>'Independent Contractor (1099)',
                'status'=>'published',
                'featured'=>true,
                'sort'=>10,
                'summary'=>'Sell EvoSys Pro to businesses that need stronger lead follow-up, sales execution, automation, and growth systems.',
                'description'=>'Own the sales conversation from discovery through close. This role is built for self-directed sales professionals who can build relationships, explain business value, and consistently move opportunities forward.',
                'responsibilities'=>[
                    'Prospect, qualify, demonstrate, and close new B2B opportunities.',
                    'Conduct discovery conversations and live platform demonstrations.',
                    'Maintain clean follow-up and pipeline activity inside the sales workspace.',
                    'Build long-term relationships and identify expansion opportunities.',
                ],
                'requirements'=>[
                    'Strong communication and follow-up discipline.',
                    'Comfort selling business outcomes, not just software features.',
                    'Ability to work independently in a performance-driven environment.',
                    'B2B, SaaS, technology, or consultative sales experience is helpful but not required for every candidate.',
                    'Comfort with a commission-based 1099 independent-contractor structure.',
                ],
                'compensation_label'=>'Performance-based commission; projected-income examples shown below.',
            ],
        ],
    ];
}

function careers_config_path(): string { return careers_base_dir().'/careers.json'; }
function careers_auth_path(): string { return careers_base_dir().'/admin-auth.json'; }

function careers_atomic_json_write(string $path, array $data): bool {
    careers_ensure_storage();
    $dir = dirname($path);
    if (!is_dir($dir) && !@mkdir($dir, 0755, true) && !is_dir($dir)) return false;
    $tmp = $path.'.tmp.'.bin2hex(random_bytes(5));
    $json = json_encode($data, JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE);
    if ($json === false || file_put_contents($tmp, $json, LOCK_EX) === false) return false;
    @chmod($tmp, 0640);
    return @rename($tmp, $path);
}

function careers_load_config(): array {
    careers_ensure_storage();
    $path = careers_config_path();
    if (!is_file($path)) {
        $default = careers_default_config();
        careers_atomic_json_write($path, $default);
        return $default;
    }
    $raw = @file_get_contents($path);
    $decoded = is_string($raw) ? json_decode($raw, true) : null;
    return is_array($decoded) ? array_replace_recursive(careers_default_config(), $decoded) : careers_default_config();
}

function careers_save_config(array $config): bool { return careers_atomic_json_write(careers_config_path(), $config); }

function careers_slug(string $value): string {
    $value = strtolower(trim($value));
    $value = preg_replace('/[^a-z0-9]+/', '-', $value) ?? '';
    return trim($value, '-');
}

function careers_public_jobs(array $config): array {
    $jobs = array_values(array_filter($config['jobs'] ?? [], fn($j)=>is_array($j) && (($j['status'] ?? '') === 'published')));
    usort($jobs, function($a,$b){
        $fa = !empty($a['featured']) ? 0 : 1; $fb = !empty($b['featured']) ? 0 : 1;
        if ($fa !== $fb) return $fa <=> $fb;
        return ((int)($a['sort'] ?? 100)) <=> ((int)($b['sort'] ?? 100));
    });
    return $jobs;
}

function careers_find_job(array $config, string $id): ?array {
    foreach (($config['jobs'] ?? []) as $job) {
        if (is_array($job) && (string)($job['id'] ?? '') === $id) return $job;
    }
    return null;
}

function careers_clean(string $value, int $max = 1000): string {
    $value = trim(preg_replace('/\s+/u', ' ', $value) ?? $value);
    return function_exists('mb_substr') ? mb_substr($value, 0, $max) : substr($value, 0, $max);
}

/**
 * Is this string shaped like an application id we issued?
 *
 * SECURITY: `careers_clean()` collapses whitespace and truncates. It does NOT
 * remove `/` or `..`, so passing an unchecked `?resume=` value into a path
 * built by string concatenation let an authenticated Careers Manager session
 * read any .json file the web user can reach - including this feature's own
 * admin-auth.json, which holds the password hash.
 *
 * Ids are minted in careers_store_application() as APP-<YYYYMMDD>-<8 hex>, so
 * the safe check is to require exactly that shape rather than to try to strip
 * dangerous characters. An allowlist cannot be bypassed by an encoding nobody
 * thought of; a denylist can.
 */
function careers_is_application_id(string $id): bool {
    return (bool)preg_match('/^APP-[0-9]{8}-[0-9A-F]{8}$/', $id);
}

function careers_application_path(string $id): string {
    if (!careers_is_application_id($id)) return '';
    return careers_base_dir().'/applications/'.$id.'.json';
}

/**
 * Resolve a stored resume's relative path to a real file INSIDE the resumes
 * directory, or return '' .
 *
 * Defence in depth. `resume.path` is written by careers_store_resume() from a
 * server-generated name and is never user input, so today this cannot be
 * abused - but it is read back out of a JSON file and concatenated onto a
 * directory, which is the shape that becomes an arbitrary-file-read the moment
 * anything upstream changes. Verified with realpath() so that a symlink cannot
 * point out of the directory either.
 */
function careers_resume_file(string $rel): string {
    if ($rel === '' || strpos($rel, "\0") !== false) return '';
    $base = careers_base_dir().'/resumes';
    $baseReal = realpath($base);
    if ($baseReal === false) return '';
    $candidate = realpath(careers_base_dir().'/'.$rel);
    if ($candidate === false || !is_file($candidate)) return '';
    $prefix = rtrim($baseReal, '/\\').DIRECTORY_SEPARATOR;
    if (strncmp($candidate, $prefix, strlen($prefix)) !== 0) return '';
    return $candidate;
}

function careers_application_dedupe(string $email, string $jobId, int $seconds = 86400): ?array {
    $dir = careers_base_dir().'/applications';
    if (!is_dir($dir)) return null;
    foreach (glob($dir.'/*.json') ?: [] as $file) {
        if (@filemtime($file) < time()-$seconds) continue;
        $raw = @file_get_contents($file); $app = is_string($raw) ? json_decode($raw, true) : null;
        if (!is_array($app)) continue;
        if (strcasecmp((string)($app['email'] ?? ''), $email) === 0 && (string)($app['job_id'] ?? '') === $jobId) return $app;
    }
    return null;
}

function careers_store_resume(array $file, string $applicationId): array {
    if (($file['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE) return ['name'=>'','path'=>'','mime'=>''];
    if (($file['error'] ?? UPLOAD_ERR_OK) !== UPLOAD_ERR_OK) throw new RuntimeException('Resume upload failed.');
    if ((int)($file['size'] ?? 0) > 5*1024*1024) throw new RuntimeException('Resume must be 5 MB or smaller.');
    $tmp = (string)($file['tmp_name'] ?? '');
    if ($tmp === '' || !is_uploaded_file($tmp)) throw new RuntimeException('Resume upload was not valid.');
    $finfo = new finfo(FILEINFO_MIME_TYPE);
    $mime = (string)$finfo->file($tmp);
    $allowed = [
        'application/pdf'=>'pdf',
        'application/msword'=>'doc',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'=>'docx',
    ];
    if (!isset($allowed[$mime])) throw new RuntimeException('Resume must be a PDF, DOC, or DOCX file.');
    $rel = 'resumes/'.$applicationId.'.'.$allowed[$mime];
    $dest = careers_base_dir().'/'.$rel;
    if (!move_uploaded_file($tmp, $dest)) throw new RuntimeException('Resume could not be saved.');
    @chmod($dest, 0640);
    return ['name'=>careers_clean((string)($file['name'] ?? ''), 180),'path'=>$rel,'mime'=>$mime];
}

function careers_store_application(array $data, array $resume = []): array {
    careers_ensure_storage();
    $email = careers_clean((string)($data['email'] ?? ''), 180);
    $jobId = careers_clean((string)($data['job_id'] ?? ''), 100);
    $existing = careers_application_dedupe($email, $jobId);
    if ($existing) return ['application'=>$existing,'duplicate'=>true];

    $id = 'APP-'.gmdate('Ymd').'-'.strtoupper(bin2hex(random_bytes(4)));
    $record = [
        'application_id'=>$id,
        'submitted_at'=>gmdate('c'),
        'status'=>'New',
        'job_id'=>$jobId,
        'job_title'=>careers_clean((string)($data['job_title'] ?? ''), 160),
        'full_name'=>careers_clean((string)($data['full_name'] ?? ''), 160),
        'email'=>$email,
        'phone'=>careers_clean((string)($data['phone'] ?? ''), 50),
        'city_state'=>careers_clean((string)($data['city_state'] ?? ''), 120),
        'linkedin'=>careers_clean((string)($data['linkedin'] ?? ''), 300),
        'current_role'=>careers_clean((string)($data['current_role'] ?? ''), 180),
        'sales_experience'=>careers_clean((string)($data['sales_experience'] ?? ''), 80),
        'b2b_experience'=>careers_clean((string)($data['b2b_experience'] ?? ''), 20),
        'saas_experience'=>careers_clean((string)($data['saas_experience'] ?? ''), 20),
        'profile_type'=>careers_clean((string)($data['profile_type'] ?? ''), 80),
        'source'=>careers_clean((string)($data['source'] ?? ''), 80),
        'start_timing'=>careers_clean((string)($data['start_timing'] ?? ''), 80),
        'interest'=>careers_clean((string)($data['interest'] ?? ''), 3000),
        'ack_1099'=>!empty($data['ack_1099']),
        'resume'=>$resume,
        'internal_notes'=>'',
        'ip'=>function_exists('evosys_ip') ? evosys_ip() : ($_SERVER['REMOTE_ADDR'] ?? ''),
        'user_agent'=>careers_clean((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 500),
    ];
    if (!careers_atomic_json_write(careers_application_path($id), $record)) throw new RuntimeException('Application could not be stored.');
    return ['application'=>$record,'duplicate'=>false];
}

function careers_all_applications(): array {
    careers_ensure_storage();
    $rows=[];
    foreach (glob(careers_base_dir().'/applications/*.json') ?: [] as $file) {
        $raw=@file_get_contents($file); $app=is_string($raw)?json_decode($raw,true):null;
        if (is_array($app)) $rows[]=$app;
    }
    usort($rows, fn($a,$b)=>strcmp((string)($b['submitted_at']??''),(string)($a['submitted_at']??'')));
    return $rows;
}

function careers_update_application(string $id, string $status, string $notes): bool {
    if (!preg_match('/^APP-[A-Z0-9-]+$/', $id)) return false;
    $path = careers_application_path($id);
    if ($path === '' || !is_file($path)) return false;
    $raw=@file_get_contents($path); $app=is_string($raw)?json_decode($raw,true):null;
    if (!is_array($app)) return false;
    $allowed=['New','Review','Interview','Hold','Rejected','Hired'];
    if (!in_array($status,$allowed,true)) $status='Review';
    $app['status']=$status;
    $app['internal_notes']=careers_clean($notes,5000);
    $app['updated_at']=gmdate('c');
    return careers_atomic_json_write($path,$app);
}

function careers_admin_hash(): string {
    $authPath=careers_auth_path();
    if (is_file($authPath)) {
        $raw=@file_get_contents($authPath); $data=is_string($raw)?json_decode($raw,true):null;
        if (is_array($data) && !empty($data['password_hash'])) return (string)$data['password_hash'];
    }
    return careers_cfg('CAREERS_ADMIN_PASSWORD_HASH','');
}

function careers_admin_set_password(string $password): bool {
    if (strlen($password) < 10) return false;
    return careers_atomic_json_write(careers_auth_path(), ['password_hash'=>password_hash($password,PASSWORD_DEFAULT),'updated_at'=>gmdate('c')]);
}

function careers_csrf(): string {
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    if (empty($_SESSION['careers_csrf'])) $_SESSION['careers_csrf']=bin2hex(random_bytes(24));
    return (string)$_SESSION['careers_csrf'];
}

function careers_check_csrf(string $token): bool {
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    return isset($_SESSION['careers_csrf']) && hash_equals((string)$_SESSION['careers_csrf'],$token);
}

function careers_admin_logged_in(): bool {
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    return !empty($_SESSION['careers_admin_ok']);
}

/**
 * Failed-login throttling for the Careers Manager.
 *
 * WHY THIS IS NOT OPTIONAL. /careers/admin/ is a publicly reachable login form
 * protecting applicants' names, emails, phone numbers and resumes. Without a
 * limit, an unauthenticated script can post passwords at it for as long as it
 * likes, and the only thing standing in the way is how good the password is.
 *
 * Deliberately simple: a JSON file of recent failures per IP, a fixed window,
 * and a refusal past the threshold. No dependency, no database, and it fails
 * OPEN only if the storage is unwritable - a broken throttle must not lock the
 * owner out of his own hiring pipeline, and an unwritable storage directory is
 * already a bigger problem that Settings reports.
 */
const CAREERS_LOGIN_MAX_ATTEMPTS = 8;
const CAREERS_LOGIN_WINDOW_SECONDS = 900;

function careers_login_throttle_path(): string { return careers_base_dir().'/login-attempts.json'; }

function careers_login_key(): string {
    $ip = function_exists('evosys_ip') ? evosys_ip() : ($_SERVER['REMOTE_ADDR'] ?? '');
    return substr(hash('sha256', (string)$ip), 0, 32);
}

function careers_login_attempts(): array {
    $path = careers_login_throttle_path();
    if (!is_file($path)) return [];
    $raw = @file_get_contents($path);
    $data = is_string($raw) ? json_decode($raw, true) : null;
    if (!is_array($data)) return [];
    $cutoff = time() - CAREERS_LOGIN_WINDOW_SECONDS;
    $out = [];
    foreach ($data as $key => $stamps) {
        if (!is_array($stamps)) continue;
        $kept = array_values(array_filter($stamps, static fn($t) => (int)$t >= $cutoff));
        if ($kept) $out[$key] = $kept;
    }
    return $out;
}

function careers_login_blocked(): bool {
    $attempts = careers_login_attempts();
    return count($attempts[careers_login_key()] ?? []) >= CAREERS_LOGIN_MAX_ATTEMPTS;
}

function careers_login_record_failure(): void {
    $attempts = careers_login_attempts();
    $key = careers_login_key();
    $attempts[$key][] = time();
    careers_atomic_json_write(careers_login_throttle_path(), $attempts);
}

function careers_login_clear(): void {
    $attempts = careers_login_attempts();
    unset($attempts[careers_login_key()]);
    careers_atomic_json_write(careers_login_throttle_path(), $attempts);
}

function careers_admin_login(string $password): bool {
    if (careers_login_blocked()) return false;
    $hash=careers_admin_hash();
    if ($hash==='' || !password_verify($password,$hash)) { careers_login_record_failure(); return false; }
    careers_login_clear();
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    session_regenerate_id(true); $_SESSION['careers_admin_ok']=true; return true;
}

/**
 * Neutralise a spreadsheet formula in an exported cell.
 *
 * THE ATTACK THIS STOPS. Every value in this export was typed by a stranger on
 * a public form. A cell beginning `=`, `+`, `-` or `@` is executed as a formula
 * when the file is opened in Excel or Sheets - so an applicant can put
 * something like `=HYPERLINK(...)` in their name and have it run on the machine
 * of whoever reviews applications. The export is the one place this data leaves
 * the escaped safety of an HTML page, and the person opening it is the owner.
 *
 * A leading apostrophe is the standard neutraliser: the spreadsheet shows the
 * original text and does not evaluate it. Tab, CR and LF are stripped first,
 * because a leading whitespace character hides the trigger from this check
 * while some spreadsheets still evaluate what follows.
 */
function careers_csv_cell(string $value): string {
    $value = str_replace(["\t", "\r", "\n"], ' ', $value);
    if ($value !== '' && strpbrk(substr($value, 0, 1), "=+-@") !== false) return "'".$value;
    return $value;
}

function careers_export_csv(array $apps): string {
    $fh=fopen('php://temp','w+');
    $headers=['application_id','submitted_at','status','job_title','full_name','email','phone','city_state','linkedin','current_role','sales_experience','b2b_experience','saas_experience','profile_type','source','start_timing','interest','ack_1099','internal_notes'];
    fputcsv($fh,$headers);
    foreach($apps as $a){ $row=[]; foreach($headers as $h)$row[]=careers_csv_cell((string)($a[$h]??'')); fputcsv($fh,$row); }
    rewind($fh); return (string)stream_get_contents($fh);
}
