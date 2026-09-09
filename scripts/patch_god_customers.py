import re, sys

path = r"C:\Dev\advisorflow-web\frontend\src\pages\GodCustomers.jsx"
with open(path, encoding="utf-8") as f:
    src = f.read()

orig = src

# 1. Add useSearchParams to the react-router-dom import
src = src.replace(
    "import { useNavigate } from 'react-router-dom'",
    "import { useNavigate, useSearchParams } from 'react-router-dom'"
)

# 2. After `const nav = useNavigate()`, add searchParams hook
src = src.replace(
    "  const nav = useNavigate()\n  const [d, setD]",
    "  const nav = useNavigate()\n  const [searchParams] = useSearchParams()\n  const [d, setD]"
)

# 3. Seed status from URL param
src = src.replace(
    "  const [status, setStatus] = useState('')",
    "  const [status, setStatus] = useState(searchParams.get('status') || '')"
)

# 4. Seed platform from URL param
src = src.replace(
    "  const [platform, setPlatform] = useState('')",
    "  const [platform, setPlatform] = useState(searchParams.get('platform_id') || '')"
)

# 5. Seed showArchived from URL param
src = src.replace(
    "  const [showArchived, setShowArchived] = useState(false)",
    "  const [showArchived, setShowArchived] = useState(searchParams.get('include_archived') === 'true')"
)

if src == orig:
    print("ERROR: no changes made — patterns not matched")
    sys.exit(1)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)

print("DONE: GodCustomers.jsx patched with URL-param seeding")
