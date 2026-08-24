$env:JWT_SECRET = "ceb03e6664e13367dbd6b89d2bb200c6fcb6bf82f8b09c39958648c5d8f6656e"
$env:DATABASE_URL = "sqlite:///./advisorflow.db"
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
python app/scripts/seed_god_mode_orgs.py
Write-Host "Exit code: $LASTEXITCODE"
