import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.deps import SessionLocal
from app.models.models import User, Organization
from app.services.auth_service import hash_password

db = SessionLocal()
try:
    target = "mike@simmonsstrong.com"
    u = db.query(User).filter(User.email == target).first()
    if u:
        print(f"User found: {u.email}  role={u.role}  active={u.is_active}")
        if u.role != "god_admin":
            u.role = "god_admin"
            db.commit()
            print("  -> role upgraded to god_admin")
    else:
        print(f"User NOT found. Creating god_admin account for {target}...")
        new_user = User(
            email=target,
            password_hash=hash_password("GodMode2024!"),
            full_name="Mike Simmons",
            role="god_admin",
            is_active=True,
            must_change_password=False,
            organization_id=None,
        )
        db.add(new_user)
        db.commit()
        print(f"  -> Created! Login: {target} / GodMode2024!")

    orgs = db.query(Organization).all()
    print(f"\nOrganizations in DB ({len(orgs)}):")
    for o in orgs:
        print(f"  {o.name:<40} plan={o.plan:<12} active={o.is_active}")
finally:
    db.close()
