# Read-only post-deploy check. Fetches public URLs and prints status.
# Makes no change to anything, local or remote.

Write-Host "-- backend health --"
try {
    $r = Invoke-WebRequest -Uri "https://advisorflow-backend.onrender.com/health" -TimeoutSec 90 -UseBasicParsing
    Write-Host ("  " + $r.StatusCode + " " + $r.Content)
} catch { Write-Host ("  ERROR " + $_.Exception.Message) }

Write-Host "-- deal-room route registered? (bogus token must fail closed) --"
try {
    $r = Invoke-WebRequest -Uri "https://advisorflow-backend.onrender.com/deal-room/not-a-real-token" -TimeoutSec 90 -UseBasicParsing
    Write-Host ("  UNEXPECTED " + $r.StatusCode + " " + $r.Content)
} catch {
    $resp = $_.Exception.Response
    if ($resp) {
        $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
        Write-Host ("  " + [int]$resp.StatusCode + " " + $sr.ReadToEnd())
    } else { Write-Host ("  ERROR " + $_.Exception.Message) }
}

Write-Host "-- frontend bundle --"
try {
    $f = Invoke-WebRequest -Uri "https://app.evosyspro.live/" -TimeoutSec 90 -UseBasicParsing
    $m = [regex]::Matches($f.Content, "index-[A-Za-z0-9_-]+\.(js|css)")
    Write-Host ("  " + $f.StatusCode + " assets: " + (($m | ForEach-Object { $_.Value }) -join ", "))
} catch { Write-Host ("  ERROR " + $_.Exception.Message) }
