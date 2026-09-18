<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); echo json_encode(['answer'=>'POST requests only.']); exit; }
$raw=file_get_contents('php://input');$data=json_decode($raw?:'{}',true);$message=trim((string)($data['message']??''));
if($message===''){http_response_code(400);echo json_encode(['answer'=>'Ask me about EvoSys Pro, AI, sales workflows, customer launch, packages, compliance, or requesting a demo.']);exit;}
$m=strtolower($message);
if(str_contains($m,'package')||str_contains($m,'price')||str_contains($m,'cost'))$a='EvoSys Pro offers Starter / Launch, Growth / Accelerate, Professional / Scale, and Custom / Enterprise packages. Public pricing is not posted; package scope and commercial terms are confirmed directly based on users, lead capacity, messaging volume, integrations, and deployment needs.';
elseif(str_contains($m,'website agent')||str_contains($m,'chatbot')||str_contains($m,'agent'))$a='The EvoSys Pro AI Website Agent works from approved business knowledge, captures visitor intent, helps qualify the opportunity, can route to a human, and can move qualified prospects toward the next action such as a demo or scheduling workflow.';
elseif(str_contains($m,'workforce')||str_contains($m,'ai employee'))$a='AI Workforce is the controlled job-role layer for AI-assisted work. Roles are deployed through staged activation with tool permissions, supervision, human handoff, and operational controls rather than unrestricted automation.';
elseif(str_contains($m,'sales'))$a='The Sales Operating System keeps prospects, opportunities, proposals, team coordination, follow-up, compensation context, and sold-customer handoff connected in one workflow.';
elseif(str_contains($m,'launch')||str_contains($m,'onboard'))$a='Customer Launch gives a sold customer a branded path through intake, files, integrations, implementation, testing, training, and go-live while the internal team sees the real status.';
elseif(str_contains($m,'sms')||str_contains($m,'compliance')||str_contains($m,'consent'))$a='EvoSys Pro keeps SMS enrollment and messaging controls visible. The public SMS Opt-In flow includes explicit consent language, variable-frequency disclosure, message/data-rate disclosure, STOP/HELP instructions, and links to SMS Terms and Privacy.';
elseif(str_contains($m,'demo'))$a='You can request a live EvoSys Pro demo from the Request Demo page. The form asks about your business, current systems, lead volume, goals, and the package area you want to explore so the demo can be focused.';
else $a='EvoSys Pro is an AI-powered business operating system connecting customer acquisition, sales execution, customer launch, communications, commercial control, and AI-assisted operations in one environment. Ask me about AI, sales, customer launch, packages, compliance, or requesting a demo.';
echo json_encode(['answer'=>$a],JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE);