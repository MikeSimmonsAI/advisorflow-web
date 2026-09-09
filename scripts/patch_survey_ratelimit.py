"""IDENT-08 patch: add @limiter.limit("60/minute") to the three public
survey token endpoints that were previously unthrottled."""

f = r'C:/Dev/advisorflow-web/app/routers/survey_router.py'
with open(f, 'r', encoding='utf-8') as fh:
    c = fh.read()

orig = c

# 1. Add Request to fastapi import
c = c.replace(
    'from fastapi import APIRouter, Depends, HTTPException',
    'from fastapi import APIRouter, Depends, HTTPException, Request'
)

# 2. Add limiter import after router = APIRouter line
c = c.replace(
    'router = APIRouter(prefix="/survey", tags=["survey"])',
    'router = APIRouter(prefix="/survey", tags=["survey"])\nfrom app.limiter import limiter'
)

# 3. GET /{token}/context
c = c.replace(
    '@router.get("/{token}/context")\ndef get_survey_context_json(token: str,',
    '@limiter.limit("60/minute")\n@router.get("/{token}/context")\ndef get_survey_context_json(request: Request, token: str,'
)

# 4. GET /{token} HTML
c = c.replace(
    '@router.get("/{token}", response_class=HTMLResponse)\ndef get_survey_page(token: str,',
    '@limiter.limit("60/minute")\n@router.get("/{token}", response_class=HTMLResponse)\ndef get_survey_page(request: Request, token: str,'
)

# 5. POST /{token}
c = c.replace(
    '@router.post("/{token}")\ndef submit_survey(token: str,',
    '@limiter.limit("60/minute")\n@router.post("/{token}")\ndef submit_survey(request: Request, token: str,'
)

assert c != orig, "No replacements made — patterns did not match"

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(c)

print("DONE — 5 replacements applied to survey_router.py")
