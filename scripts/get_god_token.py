import os, requests
from dotenv import load_dotenv
load_dotenv()

API = "https://advisorflow-backend.onrender.com"
email = os.environ.get("GOD_EMAIL") or input("god email: ")
password = os.environ.get("GOD_PASSWORD") or input("god password: ")

r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
r.raise_for_status()
token = r.json()["access_token"]
print("TOKEN:", token)

# Hit job-runs/latest
headers = {"Authorization": f"Bearer {token}"}
j = requests.get(f"{API}/god/job-runs/latest", headers=headers, timeout=15)
print("Status:", j.status_code)
import json
print(json.dumps(j.json(), indent=2, default=str))
