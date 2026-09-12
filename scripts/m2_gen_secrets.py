"""Emit `set` lines for a throwaway JWT secret and test password.

Called by run_m2_local_verify.bat. Exists so that the local verification run
needs no credential to be written down anywhere: both values live for the
length of one `cmd` session and are never the same twice.
"""
import secrets

print("set JWT_SECRET=%s" % secrets.token_hex(32))
print("set M2_VERIFY_PASSWORD=Verify-%s!9" % secrets.token_hex(8))
