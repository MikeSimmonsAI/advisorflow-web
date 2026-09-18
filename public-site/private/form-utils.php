<?php
declare(strict_types=1);

function evosys_cfg(string $key, string $default=''): string {
    $env = getenv($key);
    if ($env !== false && trim((string)$env) !== '') return trim((string)$env);
    $configFile = __DIR__.'/config.php';
    if (is_file($configFile)) {
        $cfg = include $configFile;
        if (is_array($cfg) && isset($cfg[$key]) && trim((string)$cfg[$key]) !== '') {
            return trim((string)$cfg[$key]);
        }
    }
    return $default;
}

function evosys_clean(string $value, int $max=2000): string {
    $value = trim(str_replace(["\r\n", "\r"], "\n", $value));
    return function_exists('mb_substr') ? mb_substr($value, 0, $max) : substr($value, 0, $max);
}

function evosys_ip(): string {
    return $_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['HTTP_X_FORWARDED_FOR'] ?? $_SERVER['REMOTE_ADDR'] ?? '';
}

function evosys_store(string $file, array $headers, array $row): bool {
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0755, true);
    $isNew = !is_file($file) || filesize($file) === 0;
    $fh = @fopen($file, 'ab');
    if (!$fh) return false;
    if (!flock($fh, LOCK_EX)) { fclose($fh); return false; }
    if ($isNew) fputcsv($fh, $headers);
    fputcsv($fh, $row);
    fflush($fh);
    flock($fh, LOCK_UN);
    fclose($fh);
    return true;
}

function evosys_notify(string $subject, string $body, string $replyTo=''): bool {
    $to = evosys_cfg('NOTIFY_EMAIL', 'support@evosyspro.live');
    $headers = [
        'From: EvoSys Pro Website <support@evosyspro.live>',
        'Content-Type: text/plain; charset=UTF-8'
    ];
    if ($replyTo !== '' && filter_var($replyTo, FILTER_VALIDATE_EMAIL)) {
        $headers[] = 'Reply-To: '.$replyTo;
    }
    return @mail($to, $subject, $body, implode("\r\n", $headers));
}


/**
 * Issue one JSON HTTP request. Supports GET and POST only.
 * There is intentionally no retry loop; idempotency belongs to the backend.
 */
function evosys_json_request(string $method, string $url, ?array $payload=null, int $timeout=12): array {
    $method = strtoupper($method);
    if (!in_array($method, ['GET','POST'], true)) {
        return ['ok'=>false, 'status'=>0, 'data'=>null, 'raw'=>'', 'error'=>'unsupported_method'];
    }
    if ($url === '') {
        return ['ok'=>false, 'status'=>0, 'data'=>null, 'raw'=>'', 'error'=>'endpoint_not_configured'];
    }

    $raw = '';
    $error = null;
    $status = 0;
    $json = $payload !== null ? json_encode($payload, JSON_UNESCAPED_SLASHES) : null;

    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        if ($ch === false) {
            return ['ok'=>false, 'status'=>0, 'data'=>null, 'raw'=>'', 'error'=>'curl_init_failed'];
        }
        $headers = ['Accept: application/json'];
        $opts = [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => min(5, $timeout),
            CURLOPT_TIMEOUT => $timeout,
            CURLOPT_HTTPHEADER => $headers,
        ];
        if ($method === 'POST') {
            $headers[] = 'Content-Type: application/json';
            $opts[CURLOPT_POST] = true;
            $opts[CURLOPT_POSTFIELDS] = $json ?? '{}';
            $opts[CURLOPT_HTTPHEADER] = $headers;
        } else {
            $opts[CURLOPT_HTTPGET] = true;
        }
        curl_setopt_array($ch, $opts);
        $result = curl_exec($ch);
        $raw = is_string($result) ? $result : '';
        $curlError = curl_error($ch);
        $error = $curlError !== '' ? $curlError : null;
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
    } else {
        $headers = ['Accept: application/json', 'Connection: close'];
        $http = [
            'method'=>$method,
            'header'=>implode("\\r\\n", $headers),
            'timeout'=>$timeout,
            'ignore_errors'=>true,
        ];
        if ($method === 'POST') {
            $headers[] = 'Content-Type: application/json';
            $http['header'] = implode("\\r\\n", $headers);
            $http['content'] = $json ?? '{}';
        }
        $ctx = stream_context_create(['http'=>$http]);
        $result = @file_get_contents($url, false, $ctx);
        $raw = is_string($result) ? $result : '';
        $responseHeaders = $http_response_header ?? [];
        foreach ($responseHeaders as $line) {
            if (preg_match('#^HTTP/\\S+\\s+(\\d{3})#i', $line, $m)) {
                $status = (int)$m[1];
                break;
            }
        }
        if ($result === false && $status === 0) $error = 'http_request_failed';
    }

    $decoded = json_decode($raw, true);
    return [
        'ok'=>$status >= 200 && $status < 300,
        'status'=>$status,
        'data'=>is_array($decoded) ? $decoded : null,
        'raw'=>$raw,
        'error'=>$error !== '' ? $error : null,
    ];
}

function evosys_get_json_url(string $url, int $timeout=12): array {
    return evosys_json_request('GET', $url, null, $timeout);
}

/**
 * Public booking base for the EvoSys Pro website.
 *
 * Preferred config:
 *   PUBLIC_BOOKING_BASE_URL=https://<api-host>/public-booking/evosyspro
 *
 * For backwards compatibility, when that value is absent we derive the same
 * API host from DEMO_WEBHOOK_URL and append /public-booking/evosyspro.
 */
function evosys_public_booking_base(): string {
    $explicit = rtrim(evosys_cfg('PUBLIC_BOOKING_BASE_URL', ''), '/');
    if ($explicit !== '') return $explicit;

    $legacy = evosys_cfg('DEMO_WEBHOOK_URL', '');
    if ($legacy === '') return '';
    $parts = parse_url($legacy);
    if (!is_array($parts) || empty($parts['scheme']) || empty($parts['host'])) return '';
    $origin = $parts['scheme'].'://'.$parts['host'];
    if (!empty($parts['port'])) $origin .= ':'.(int)$parts['port'];
    return $origin.'/public-booking/evosyspro';
}

function evosys_public_booking_url(string $kind, array $params=[]): string {
    if (!in_array($kind, ['meeting','slots','book'], true)) return '';
    $base = evosys_public_booking_base();
    if ($base === '') return '';
    $url = $base.'/'.$kind;
    $filtered = [];
    foreach ($params as $k=>$v) {
        if ($v === null || $v === '') continue;
        $filtered[$k] = $v;
    }
    if ($filtered) $url .= '?'.http_build_query($filtered, '', '&', PHP_QUERY_RFC3986);
    return $url;
}

/**
 * Post JSON to an exact URL and return the HTTP status + decoded body.
 * No automatic retries: POST idempotency is owned by the backend receipt.
 */
function evosys_post_json_url(string $url, array $payload, int $timeout=12): array {
    return evosys_json_request('POST', $url, $payload, $timeout);
}

/**
 * The existing DEMO_WEBHOOK_URL remains the single required config value.
 * New explicit keys may override the derived URLs, but no config change is
 * required when DEMO_WEBHOOK_URL ends in /demo-request.
 */
function evosys_demo_endpoint(string $kind): string {
    $map = [
        'request' => ['DEMO_WEBHOOK_URL', 'demo-request'],
        'availability' => ['DEMO_AVAILABILITY_URL', 'demo-availability'],
        'book' => ['DEMO_BOOK_URL', 'demo-book'],
    ];
    if (!isset($map[$kind])) return '';

    [$specificKey, $suffix] = $map[$kind];
    $specific = evosys_cfg($specificKey, '');
    if ($kind === 'request' && $specific !== '') return $specific;
    if ($kind !== 'request' && $specific !== '') return $specific;

    $base = evosys_cfg('DEMO_WEBHOOK_URL', '');
    if ($base === '') return '';
    $derived = preg_replace('#/demo-request/?(?:\?.*)?$#', '/'.$suffix, $base);
    if (!is_string($derived) || $derived === $base) return '';
    return $derived;
}

function evosys_webhook(string $envKey, array $payload): bool {
    $url = evosys_cfg($envKey, '');
    $result = evosys_post_json_url($url, $payload, 8);
    return (bool)$result['ok'];
}

function evosys_honeypot(): bool {
    return trim((string)($_POST['website_url'] ?? '')) !== '';
}

function evosys_too_fast(): bool {
    $s = (int)($_POST['form_started_at'] ?? 0);
    return $s > 0 && time() - $s < 2;
}
?>
