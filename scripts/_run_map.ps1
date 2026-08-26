Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
Get-ChildItem app\routers\*.py | ForEach-Object {
    $out = python scripts\_routemap.py $_.FullName 2>&1
    if ($out) {
        Write-Host ("=== " + $_.Name)
        $out | ForEach-Object { Write-Host $_ }
    }
}
