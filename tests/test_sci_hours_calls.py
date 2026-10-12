"""Texting hours 8:30-7:45, bad numbers blocked, the first-text call line."""
from datetime import time
from types import SimpleNamespace

from app.services import contact_hours
from app.services.programs import identity, send_approval


def test_window():
    assert contact_hours.WINDOW_START == time(8, 30) and contact_hours.WINDOW_END == time(19, 45)


def test_real_numbers():
    assert send_approval._real_us_number("13342150180")
    assert not send_approval._real_us_number("11662458988")
    assert not send_approval._real_us_number("11850207338")
    assert not send_approval._real_us_number("334215018")


def test_call_line():
    assert identity._call_line("+13342150180") == "Just reply to this text, or call us at (334) 215-0180."
    assert identity._call_line(None) == "Just reply to this text."


def test_unassigned_area_code():
    assert not send_approval._real_us_number('17513490943')

