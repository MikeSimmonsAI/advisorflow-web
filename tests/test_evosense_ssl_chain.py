"""Public-record sites that send an incomplete certificate chain (the tax-sale
list at taxsales.lgbs.com) must still be fetched WITH full verification: the
shared trust store carries the missing public intermediate, and checking is
never switched off."""
import ssl

from app.services.evosense.sources import base as B


def test_context_verifies_and_checks_hostname():
    B._SSL_CTX = None
    ctx = B.ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_shipped_intermediate_is_trusted():
    # Checked on a bare context so a machine whose own store already holds the
    # intermediate (Windows does) cannot hide a missing or unreadable file.
    import glob
    import os
    files = glob.glob(os.path.join(B.CERT_DIR, "*.crt"))
    assert files, "no shipped intermediates"
    bare = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for f in files:
        bare.load_verify_locations(cafile=f)
    names = [dict(x[0] for x in c["subject"]).get("commonName", "") for c in bare.get_ca_certs()]
    assert "DigiCert Global G2 TLS RSA SHA256 2020 CA1" in names


def test_requests_use_the_shared_context(monkeypatch):
    seen = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b'{"results": [], "next": null}'

    def fake_urlopen(req, timeout=None, context=None):
        seen["ctx"] = context
        return _R()

    monkeypatch.setattr(B.urllib.request, "urlopen", fake_urlopen)
    assert B.get_json("https://taxsales.lgbs.com/api/property_sales/", {"limit": 1}) == {"results": [], "next": None}
    assert seen["ctx"] is B.ssl_context()
