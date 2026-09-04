$env:JWT_SECRET = "ceb03e6664e13367dbd6b89d2bb200c6fcb6bf82f8b09c39958648c5d8f6656e"
$env:DATABASE_URL = "sqlite:///./advisorflow.db"
Set-Location "C:\Dev\advisorflow-web"
python app/scripts/check_god_admin.py
