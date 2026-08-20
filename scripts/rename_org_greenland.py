"""
One-time migration: rename Restland Cemetery & Funeral Home
→ Greenland Cemetery and Funeral Home

Run on Render Shell:
  python scripts/rename_org_greenland.py

Or locally with DATABASE_URL set:
  DATABASE_URL=<url> python scripts/rename_org_greenland.py
"""
import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

OLD_NAME = "Restland Cemetery & Funeral Home"
NEW_NAME = "Greenland Cemetery and Funeral Home"

async def main():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT id, name FROM organizations WHERE name = :old"),
            {"old": OLD_NAME}
        )
        rows = result.fetchall()
        if not rows:
            result2 = await conn.execute(
                text("SELECT id, name FROM organizations WHERE name ILIKE :pattern"),
                {"pattern": "%Restland%"}
            )
            rows = result2.fetchall()

        if not rows:
            print("No matching org found. All current orgs:")
            all_orgs = await conn.execute(text("SELECT id, name FROM organizations ORDER BY name"))
            for r in all_orgs.fetchall():
                print(f"  id={r[0]}  name={r[1]}")
            return

        for row in rows:
            print(f"Updating org id={row[0]}: '{row[1]}' → '{NEW_NAME}'")
            await conn.execute(
                text("UPDATE organizations SET name = :new WHERE id = :id"),
                {"new": NEW_NAME, "id": row[0]}
            )
        print(f"Done — {len(rows)} org(s) updated.")

asyncio.run(main())
