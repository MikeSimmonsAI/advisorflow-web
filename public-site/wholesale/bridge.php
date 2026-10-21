<?php
/** EvoSys bridge: hand one investor registration to EvoSys Wholesale > Buyers.
 * Used by intake.php (at submission) and resend.php (command line retry).
 * Defines a function only; requested directly over the web it answers 404.
 */
declare(strict_types=1);
if (realpath((string)($_SERVER['SCRIPT_FILENAME']??''))===__FILE__) { http_response_code(404); exit; }

/** The configured platform URL, or null when unset or not https (http only to localhost). */
function evosys_bridge_url(): ?string {
  $u=getenv('WHOLESALE_BUYER_INTAKE_URL');
  if (!$u) return null;
  return (str_starts_with($u,'https://')||preg_match('#^http://(127\.0\.0\.1|localhost)(:\d+)?/#',$u)) ? $u : null;
}

/** POST the fields server-to-server; never follows redirects. Returns the
 * 'evosys' block stored in the private record: sent true + platform reference,
 * or sent false + HTTP status (0 = no answer). */
function evosys_send(string $url, array $fields, string $reference, string $host): array {
  $body=json_encode($fields+['contact_permission'=>'yes','submission_id'=>$reference,
    'source_url'=>'https://'.$host.'/buyers.html','form_version'=>'buyers-v1'],JSON_UNESCAPED_SLASHES);
  $status=0;$resp='';
  if (function_exists('curl_init')) {
    $ch=curl_init($url);
    curl_setopt_array($ch,[CURLOPT_POST=>true,CURLOPT_POSTFIELDS=>$body,CURLOPT_RETURNTRANSFER=>true,
      CURLOPT_TIMEOUT=>20,CURLOPT_CONNECTTIMEOUT=>8,CURLOPT_FOLLOWLOCATION=>false,
      CURLOPT_HTTPHEADER=>['Content-Type: application/json','Accept: application/json']]);
    $resp=(string)curl_exec($ch);$status=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);curl_close($ch);
  } else {
    $ctx=stream_context_create(['http'=>['method'=>'POST','timeout'=>20,'ignore_errors'=>true,'follow_location'=>0,
      'header'=>"Content-Type: application/json\r\nAccept: application/json\r\n",'content'=>$body]]);
    $resp=(string)@file_get_contents($url,false,$ctx);
    if (isset($http_response_header[0])&&preg_match('/\s(\d{3})\s/',$http_response_header[0],$m)) $status=(int)$m[1];
  }
  $data=json_decode($resp,true);
  return ($status>=200&&$status<300&&is_array($data)&&!empty($data['success']))
    ? ['sent'=>true,'reference'=>(string)($data['reference']??''),'at'=>gmdate('c')]
    : ['sent'=>false,'status'=>$status,'at'=>gmdate('c')];
}
