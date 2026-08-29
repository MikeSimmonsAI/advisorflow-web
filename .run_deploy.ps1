Set-Location -LiteralPath $PSScriptRoot
$msg = 'customer-ready closeout: branded public pages resolved from the shared identity resolver (booking_links has no organization_id, so the org came from the lead - a family saw a blank header and EvoSys Pro in the tab); the booking stays on the lead EvoSys called and a spoken callback number is stored beside the primary rather than rekeying the appointment onto another record; the 3-attempt cap becomes campaign to use-case to organization to system configuration with hard ceilings; and a voicemail is recorded as a dial, not as a live conversation.'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
