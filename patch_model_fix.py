"""patch_model_fix.py — add enabled_features column to Organization model"""
import os

BASE = r"C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"

def path(rel):
    return os.path.join(BASE, rel)

def read(rel):
    with open(path(rel), 'r', encoding='utf-8') as f:
        return f.read()

def write(rel, content):
    with open(path(rel), 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  saved: {rel}")

print("[fix] models.py — add enabled_features to Organization model")

old = "    # Industry-agnostic tier config — JSON array of tier definitions\n    # e.g. [{\"value\": \"pre_need\", \"label\": \"Pre-Need\", \"color\": \"blue\"}, ...]\n    tier_config = Column(Text, nullable=True)\n\n    users = relationship"

new = "    # Industry-agnostic tier config — JSON array of tier definitions\n    # e.g. [{\"value\": \"pre_need\", \"label\": \"Pre-Need\", \"color\": \"blue\"}, ...]\n    tier_config = Column(Text, nullable=True)\n\n    # Per-org feature flags (super admin only). JSON array of feature keys.\n    # null = all features enabled (backward-compatible default).\n    # [] = no optional features. [\"campaigns\", \"reports\", ...] = explicit allow-list.\n    enabled_features = Column(Text, nullable=True)\n\n    users = relationship"

content = read("app/models/models.py")
if "enabled_features = Column" in content:
    print("  skip: enabled_features already in model")
elif old in content:
    content = content.replace(old, new, 1)
    write("app/models/models.py", content)
    print("  patched: Organization.enabled_features added to SQLAlchemy model")
else:
    print("  WARNING: target not found — dumping context:")
    i = content.find("tier_config = Column")
    print(repr(content[i-5:i+200]))
