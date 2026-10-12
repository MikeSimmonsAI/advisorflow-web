<?php
/** EvoSys Pro Wholesale: investor buy-box inquiry intake.
 * PHP 8+, independent of AdvisorFlow production API. Stores records in private
 * server-side storage ABOVE the document root. Nothing is sent by SMS.
 */
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: strict-origin-when-cross-origin');
function reply(int $code, string $message, array $extra=[]): never {
  http_response_code($code);
  echo json_encode(['success'=>$code<300,'message'=>$message]+$extra,JSON_UNESCAPED_SLASHES);
  exit;
}
if ($_SERVER['REQUEST_METHOD']!=='POST') { header('Allow: POST'); reply(405,'Use the investor registration form.'); }
if ((int)($_SERVER['CONTENT_LENGTH']??0)>20000) reply(413,'Form is too large.');
$origin=(string)($_SERVER['HTTP_ORIGIN']??'');
$host=(string)($_SERVER['HTTP_HOST']??'');
if ($origin!=='') {
  $url=parse_url($origin);
  if (!is_array($url)||strcasecmp((string)($url['host']??''),explode(':',$host)[0])!==0) reply(403,'Please submit from this website.');
}
if (!empty($_POST['company_url'])) reply(200,'Thank you.',['reference'=>'RECEIVED']);
if (($_POST['intent']??'')!=='buyer') reply(422,'Unknown inquiry type.');
if (($_POST['contact_permission']??'')!=='yes') reply(422,'Please confirm you agree to be contacted about this inquiry.');
function cleaned(string $key,int $max): string {
  $raw=$_POST[$key]??'';
  if (!is_string($raw)) return '';
  $v=trim(preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u','',$raw)??'');
  return function_exists('mb_substr') ? mb_substr($v,0,$max) : substr($v,0,$max);
}
$fields=[
  'full_name'=>cleaned('full_name',120),'company'=>cleaned('company',120),
  'email'=>strtolower(cleaned('email',160)),'phone'=>cleaned('phone',35),
  'markets'=>cleaned('markets',250),'strategy'=>cleaned('strategy',60),
  'budget'=>cleaned('budget',70),'notes'=>cleaned('notes',1200)
];
if (strlen($fields['full_name'])<2||!filter_var($fields['email'],FILTER_VALIDATE_EMAIL)
    ||strlen(preg_replace('/\D/','',$fields['phone']))<10
    ||strlen($fields['markets'])<2) reply(422,'Please enter your name, email, phone, and target markets.');
if (!in_array($fields['strategy'],['Fix & Flip','Buy & Hold','Land','Multifamily','Multiple Strategies'],true)) reply(422,'Please select a buying strategy.');
if (!in_array($fields['budget'],['Under $100,000','$100,000–$250,000','$250,000–$500,000','$500,000–$1 million','Over $1 million','Flexible / Deal Dependent'],true)) reply(422,'Please select a budget range.');
$dir=getenv('WHOLESALE_INTAKE_DIR')?:dirname(__DIR__,2).DIRECTORY_SEPARATOR.'evosys-wholesale-private';
$docroot=realpath((string)($_SERVER['DOCUMENT_ROOT']??''));
if (!is_dir($dir) && !@mkdir($dir,0700,true)) { error_log('wholesale intake: cannot create private storage');reply(503,'Registration is temporarily unavailable. Please try again later.'); }
$real=realpath($dir);
if (!$real||($docroot&&($real===$docroot||str_starts_with($real,$docroot.DIRECTORY_SEPARATOR)))) {
  error_log('wholesale intake: storage must be outside DOCUMENT_ROOT');reply(503,'Registration is temporarily unavailable. Please try again later.');
}
@chmod($real,0700);
$ip=(string)($_SERVER['REMOTE_ADDR']??'unknown');
$hash=hash('sha256',$ip.'|'.gmdate('Y-m-d'));
$rate=$real.'/rate-'.$hash.'.txt';
$fp=@fopen($rate,'c+');
if ($fp) {
  if (flock($fp,LOCK_EX)) {
    $last=(int)trim((string)stream_get_contents($fp));
    if ($last>time()-30) { flock($fp,LOCK_UN);fclose($fp);reply(429,'Please wait 30 seconds before sending another inquiry.'); }
    rewind($fp);ftruncate($fp,0);fwrite($fp,(string)time());fflush($fp);flock($fp,LOCK_UN);
  }
  fclose($fp);@chmod($rate,0600);
}
$reference='BUY-'.gmdate('Ymd').'-'.strtoupper(bin2hex(random_bytes(4)));
$record=['reference'=>$reference,'created_at'=>gmdate('c'),'type'=>'buyer','source'=>'wholesale.evosyspro.live',
  'fields'=>$fields,'contact_permission'=>true,'sms_marketing_consent'=>false,'ip_hash'=>hash('sha256',$ip)];
$file=$real.'/'.$reference.'.json';
$encoded=json_encode($record,JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES|JSON_INVALID_UTF8_SUBSTITUTE);
$ok=@file_put_contents($file,$encoded,LOCK_EX);
if ($ok===false) { error_log('wholesale intake: write failed');reply(503,'Registration is temporarily unavailable. Please try again later.'); }
@chmod($file,0600);
$notify=getenv('WHOLESALE_NOTIFY_EMAIL');
if ($notify&&filter_var($notify,FILTER_VALIDATE_EMAIL)) {
  // Notification contains NO personal data; private record remains on the server.
  @mail($notify,'New EvoSys Wholesale buyer inquiry: '.$reference,"A new buyer profile was submitted. Reference: ".$reference."\nReview private wholesale intake storage.");
}
reply(200,'Your investor profile has been received.',['reference'=>$reference]);
