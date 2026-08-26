# Proves PRODUCTION has no demo surface. Read-only; every call below is a GET
# or an unauthenticated POST whose only correct answer is a refusal.
function Probe($label, $url, $method = "GET") {
    try {
        $r = if ($method -eq "POST") {
            Invoke-WebRequest -Uri $url -Method POST -ContentType "application/json" -Body '{}' -TimeoutSec 90 -UseBasicParsing
        } else {
            Invoke-WebRequest -Uri $url -TimeoutSec 90 -UseBasicParsing
        }
        Write-Host ("{0}: {1} {2}" -f $label, $r.StatusCode, $r.Content.Substring(0, [Math]::Min(120, $r.Content.Length)))
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            $b = $sr.ReadToEnd()
            Write-Host ("{0}: {1} {2}" -f $label, [int]$resp.StatusCode, $b.Substring(0, [Math]::Min(120, $b.Length)))
        } else { Write-Host ("{0}: ERROR {1}" -f $label, $_.Exception.Message) }
    }
}

$api = "https://advisorflow-backend.onrender.com"
Probe "demo environment " "$api/demo/environment"
Probe "demo state       " "$api/demo/state"
Probe "demo scenarios   " "$api/demo/scenarios"
Probe "demo seed        " "$api/demo/seed" "POST"
Probe "demo advance     " "$api/demo/advance" "POST"
Probe "demo reset       " "$api/demo/reset" "POST"
