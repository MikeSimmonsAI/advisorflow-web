"""A 401/403 says who refused (S3, a CloudFront/WAF page, a login wall), so the
fix can be chosen from the Controls screen instead of guessed."""
import io
import urllib.error
from email.message import Message

import pytest

from app.services.evosense.sources import base as B


def _raise(code, server, body):
    def opener(req, timeout=None, context=None):
        h = Message()
        if server:
            h["Server"] = server
        raise urllib.error.HTTPError(req.full_url, code, "Forbidden", h, io.BytesIO(body))
    return opener


def test_cloudfront_block_page_is_named(monkeypatch):
    monkeypatch.setattr(B.urllib.request, "urlopen", _raise(
        403, "CloudFront", b"<html><h1>403 ERROR</h1><p>The request could not be satisfied. Request blocked.</p>"))
    with pytest.raises(B.SourceError) as e:
        B.get_json("https://example.test/x.zip")
    assert e.value.code == B.AUTH_FAILED
    assert "server=CloudFront" in e.value.message and "Request blocked" in e.value.message


def test_empty_refusal_keeps_the_plain_message(monkeypatch):
    monkeypatch.setattr(B.urllib.request, "urlopen", _raise(401, None, b""))
    with pytest.raises(B.SourceError) as e:
        B.get_json("https://example.test/a")
    assert e.value.message == "example.test refused the request (401)"
