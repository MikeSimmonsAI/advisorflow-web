# DEV-3: owner default CSV includes name, email, phone, city, state

No new code change was needed. The requested amendment to DEV-2 / PR #597 is already on `main`
as commit e1f3718e (#598): owner default export order is Full name, Email, Phone, City, State,
then the remaining columns; first/last name stay selectable but are not duplicated defaults.
Backend no-`column` defaults and the frontend initial selection come from the same
`default_columns`; deselected fields and saved presets are respected. ACA-restricted staff
default only to owner-granted export fields (server-side).

Verified on branch base a470e248:
`python -m pytest -q -o addopts='' tests/test_volt_recruiting.py` -> 38 passed.
The frontend build was not run in this session.
Nothing here is merged, released or deployed; that needs owner approval for the exact tested commit.
