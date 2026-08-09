@echo off
echo Triggering Render backend deploy...
powershell -Command "$key = $env:RENDER_API_KEY; $headers = @{'Authorization'=\"Bearer $key\"; 'Content-Type'='application/json'}; Invoke-RestMethod -Uri 'https://api.render.com/v1/services/srv-cus28q3tq21c73bkkklg/deploys' -Method POST -Headers $headers -Body '{}' | ConvertTo-Json"
echo.
echo Triggering Render frontend deploy...
powershell -Command "$key = $env:RENDER_API_KEY; $headers = @{'Authorization'=\"Bearer $key\"; 'Content-Type'='application/json'}; Invoke-RestMethod -Uri 'https://api.render.com/v1/services/srv-cus2b53tq21c73bkkkp0/deploys' -Method POST -Headers $headers -Body '{}' | ConvertTo-Json"
echo Done.
