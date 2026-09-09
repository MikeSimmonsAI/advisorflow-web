
import re

path = r"C:\Dev\advisorflow-web\app\models\registry.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = "import app.models.device_models  # noqa: F401  (imported for side effects)"
new = """import app.models.device_models  # noqa: F401  (imported for side effects)
# Background job records (background_jobs). Same Base, same reason. Without this
# import the table is never created and job persistence silently has nowhere to
# write — jobs appear to enqueue but leave no durable record.
import app.models.job_models  # noqa: F401  (imported for side effects)"""

if "job_models" in content:
    print("job_models already present — no change needed")
else:
    # Replace last occurrence only
    idx = content.rfind(old)
    if idx == -1:
        print("ERROR: anchor text not found")
    else:
        content = content[:idx] + new + content[idx+len(old):]
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("DONE: job_models added")
