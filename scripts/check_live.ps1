
# Read-only post-deploy check. GETs public URLs and prints status.
# Makes no change to anything, local or remote. No real record ids are used.
# The POST probes below are sent WITHOUT a credential on purpose: the only
# correct answer is a refusal, so nothing is ever created by running this.

function Probe($label, $url) {
    try {
        $r = Invoke-WebRequest -Uri $url -TimeoutSec 90 -UseBasicParsing
        Write-Host ("{0}: {1} {2}" -f $label, $r.StatusCode, $r.Content.Substring(0, [Math]::Min(160, $r.Content.Length)))
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            $b = $sr.ReadToEnd()
            Write-Host ("{0}: {1} {2}" -f $label, [int]$resp.StatusCode, $b.Substring(0, [Math]::Min(160, $b.Length)))
        } else { Write-Host ("{0}: ERROR {1}" -f $label, $_.Exception.Message) }
    }
}

function ProbePost($label, $url, $body) {
    try {
        $r = Invoke-WebRequest -Uri $url -Method POST -ContentType "application/json" -Body $body -TimeoutSec 90 -UseBasicParsing
        Write-Host ("{0}: {1} {2}" -f $label, $r.StatusCode, $r.Content.Substring(0, [Math]::Min(160, $r.Content.Length)))
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            $b = $sr.ReadToEnd()
            Write-Host ("{0}: {1} {2}" -f $label, [int]$resp.StatusCode, $b.Substring(0, [Math]::Min(160, $b.Length)))
        } else { Write-Host ("{0}: ERROR {1}" -f $label, $_.Exception.Message) }
    }
}

$api = "https://advisorflow-backend.onrender.com"
Probe "health         " "$api/health"
Probe "mgr overview   " "$api/sales/manager/overview"
Probe "mgr approvals  " "$api/sales/manager/approvals"

# Retell question: is the legacy availability endpoint reachable with NO credential?
# A deliberately fake UUID, so nothing real is read.
Probe "avail (no auth)" "$api/availability/slots/00000000-0000-0000-0000-000000000000"
Probe "cal slots      " "$api/calendar/slots?advisor_id=x&date=2026-09-01&token=x"

# The new Retell bridge. Every one of these MUST refuse: a 401 proves the route
# is deployed AND fails closed. A 404 would mean it did not ship; a 200 would
# mean the credential check is not wired.
Probe     "retell ping    " "$api/integrations/retell/ping"
ProbePost "retell avail   " "$api/integrations/retell/availability" '{"date_from":"2026-09-01"}'
ProbePost "retell book    " "$api/integrations/retell/book" '{"external_ref":"probe-no-auth","starts_at":"2026-09-01T09:00:00"}'
