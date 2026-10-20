<?php
/** Retry investor registrations whose hand-off to EvoSys did not go through
 * (platform down, key changed, URL set after they arrived).
 *
 * COMMAND LINE ONLY:  php resend.php            (list what would be sent)
 *                     php resend.php --send     (send them)
 * Over the web it answers 404. Uses the same WHOLESALE_INTAKE_DIR and
 * WHOLESALE_BUYER_INTAKE_URL as intake.php. A record is re-sent with its
 * original reference, which the platform treats as the same submission, so
 * running this twice never creates a buyer twice. Sends nothing to investors.
 * Suitable for an hourly cron job on the host.
 */
declare(strict_types=1);
if (PHP_SAPI!=='cli') { http_response_code(404); exit; }
require_once __DIR__.'/bridge.php';

$send=in_array('--send',$argv,true);
$dir=getenv('WHOLESALE_INTAKE_DIR')?:dirname(__DIR__,2).DIRECTORY_SEPARATOR.'evosys-wholesale-private';
$url=evosys_bridge_url();
if (!is_dir($dir)) { fwrite(STDERR,"No private storage at $dir\n"); exit(1); }
if (!$url) { fwrite(STDERR,"WHOLESALE_BUYER_INTAKE_URL is not set (or not https).\n"); exit(1); }

$pending=0;$sent=0;$failed=0;
foreach (glob(rtrim($dir,'/').'/BUY-*.json')?:[] as $file) {
  $record=json_decode((string)file_get_contents($file),true);
  if (!is_array($record)||($record['type']??'')!=='buyer'||!is_array($record['fields']??null)) continue;
  if (!empty($record['evosys']['sent'])) continue;
  $pending++;
  $ref=(string)$record['reference'];
  if (!$send) { echo "would send $ref\n"; continue; }
  $host=(string)($record['host']??'wholesale.evosyspro.live');
  $result=evosys_send($url,$record['fields'],$ref,$host);
  $result['attempts']=(int)($record['evosys']['attempts']??1)+1;
  $record['evosys']=$result;
  file_put_contents($file,json_encode($record,JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES|JSON_INVALID_UTF8_SUBSTITUTE),LOCK_EX);
  @chmod($file,0600);
  if ($result['sent']) { $sent++; echo "sent $ref\n"; } else { $failed++; echo "failed $ref (status {$result['status']})\n"; }
}
echo $send ? "pending $pending, sent $sent, failed $failed\n" : "pending $pending (run with --send to send)\n";
exit($failed>0?2:0);
