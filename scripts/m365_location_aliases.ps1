<#
  Add the SCI location email addresses as ALIASES on ONE central mailbox in
  Microsoft 365 (not 39 mailboxes). Mail to any of them lands in that mailbox,
  which EvoSys already reads and routes to the right family and location.

  DRY RUN BY DEFAULT - lists what it would add and changes nothing:
      powershell -ExecutionPolicy Bypass -File m365_location_aliases.ps1
  Then, to make the change:
      powershell -ExecutionPolicy Bypass -File m365_location_aliases.ps1 -Apply

  You sign in yourself in the Microsoft window that opens (an account that can
  manage Exchange - Global admin or Exchange admin). The script never sees or
  stores a password.

  It refuses to add an address that already belongs to ANOTHER mailbox or
  group, and changes nothing else on the mailbox.

  Input: a CSV with columns Alias,Location (sci_location_aliases.csv, or the
  live list from Family Service Center -> GET /program/aliases.csv).

  If your Microsoft 365 is managed through GoDaddy and PowerShell admin access
  is blocked, add the same addresses by hand: admin.microsoft.com -> Users ->
  Active users -> the central mailbox (support@evosyspro.live) -> Manage
  username and email -> Add an alias. The CSV is the list.
#>
param(
  [string]$Csv = (Join-Path $PSScriptRoot "sci_location_aliases.csv"),
  [string]$Mailbox = "support@evosyspro.live",
  [switch]$Apply
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Csv)) { throw "CSV not found: $Csv" }
$rows = Import-Csv $Csv
$wanted = @($rows | ForEach-Object { $_.Alias.Trim().ToLower() } | Where-Object { $_ })
if ($wanted.Count -eq 0) { throw "No aliases in $Csv" }
$domain = ($Mailbox -split "@")[1].ToLower()
$bad = @($wanted | Where-Object { ($_ -split "@")[1] -ne $domain })
if ($bad.Count) { throw "These are not on $domain : $($bad -join ', ')" }

if (-not (Get-Module -ListAvailable -Name ExchangeOnlineManagement)) {
  Write-Host "Installing the ExchangeOnlineManagement module for this user..."
  Install-Module ExchangeOnlineManagement -Scope CurrentUser -Force -AllowClobber
}
Import-Module ExchangeOnlineManagement
Connect-ExchangeOnline -ShowBanner:$false

try {
  $mbx = Get-Mailbox -Identity $Mailbox
  $have = @($mbx.EmailAddresses | ForEach-Object { ($_ -replace '^(?i)smtp:', '').ToLower() })
  $add = @(); $skip = @(); $conflict = @()
  foreach ($a in $wanted) {
    if ($have -contains $a) { $skip += $a; continue }
    $owner = Get-Recipient -Identity $a -ErrorAction SilentlyContinue
    if ($owner) { $conflict += "$a (belongs to $($owner.PrimarySmtpAddress))"; continue }
    $add += $a
  }
  Write-Host ""
  Write-Host "Central mailbox : $($mbx.PrimarySmtpAddress)"
  Write-Host "Already present : $($skip.Count)"
  Write-Host "To add          : $($add.Count)"
  $add | ForEach-Object { Write-Host "   + $_" }
  if ($conflict.Count) {
    Write-Host "NOT added - already used elsewhere:" -ForegroundColor Yellow
    $conflict | ForEach-Object { Write-Host "   ! $_" -ForegroundColor Yellow }
  }
  if (-not $Apply) {
    Write-Host ""
    Write-Host "DRY RUN - nothing changed. Re-run with -Apply to add them." -ForegroundColor Cyan
    return
  }
  if ($add.Count) {
    Set-Mailbox -Identity $Mailbox -EmailAddresses @{ Add = @($add | ForEach-Object { "smtp:$_" }) }
  }
  $after = @((Get-Mailbox -Identity $Mailbox).EmailAddresses | ForEach-Object { ($_ -replace '^(?i)smtp:', '').ToLower() })
  $missing = @($wanted | Where-Object { $after -notcontains $_ })
  Write-Host ""
  if ($missing.Count) {
    Write-Host "MISSING after the change: $($missing -join ', ')" -ForegroundColor Red
  } else {
    Write-Host "All $($wanted.Count) addresses are now on $Mailbox." -ForegroundColor Green
    Write-Host "Next: send one test email to any of them; EvoSys marks each address 'receiving' as mail arrives."
  }
} finally {
  Disconnect-ExchangeOnline -Confirm:$false | Out-Null
}
