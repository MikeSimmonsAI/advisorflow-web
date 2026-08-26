# Local dev helper: report (and optionally stop) whatever is listening on the
# demo ports. Touches nothing but local processes on those three ports.
param([switch]$Stop)

$ports = 8000, 8099, 5173
foreach ($p in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        Write-Host ("port {0}: pid {1} ({2})" -f $p, $c.OwningProcess, $proc.ProcessName)
        if ($Stop) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Host ("  stopped pid {0}" -f $c.OwningProcess)
        }
    }
    if (-not $conns) { Write-Host ("port {0}: free" -f $p) }
}
