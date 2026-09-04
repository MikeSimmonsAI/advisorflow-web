$env:JWT_SECRET = "ceb03e6664e13367dbd6b89d2bb200c6fcb6bf82f8b09c39958648c5d8f6656e"
$env:DATABASE_URL = "sqlite:///./advisorflow.db"
Set-Location "C:\Dev\advisorflow-web"
python app/scripts/seed_demo_org.py --org-id 48dd74cb-97c2-45be-92e8-f8cf6270d533
Write-Host "Done."
