import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.deps import SessionLocal
from app.models.models import User
from app.services.auth_service import hash_password

db = SessionLocal()
try:
    u = db.query(User).filter(User.email == "mike@simmonsstrong.com").first()
    if u:
        u.password_hash = hash_password("GodMode2024!")
        u.must_change_password = False
        db.commit()
        print("Password reset to: GodMode2024!")
    else:
        print("User not found.")
finally:
    db.close()
