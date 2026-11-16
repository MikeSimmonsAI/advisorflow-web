"""Staff alert texts name the program they came from (Nov 16: a Restland
reply alert read "SCI Appointment Request")."""
from app.services.programs import alert_ledger as L


def test_sci_keeps_sci_label():
    assert L.sms_line("hot", "A Family", words="hi").startswith("SCI Appointment Request | A Family")
    assert L.sms_line("booked", "A Family", detail="booked a visit", program="SCI").startswith("SCI Booked |")


def test_other_program_uses_its_own_name():
    for kind, label in [("hot", "Appointment Request"), ("handoff", "HUMAN HANDOFF"), ("booked", "Booked"),
                        ("sla_breach", "Still Waiting"), ("home_visit", "Home Visit Request"), ("reply", "Reply")]:
        out = L.sms_line(kind, "A Family", words="hi", program="Restland")
        assert out.startswith("Restland %s | A Family" % label), out
        assert "SCI" not in out
    assert L.sms_line("hot", "A Family", words="hi", program="Restland", test=True).startswith(
        "TEST Restland Appointment Request")


def test_label_never_breaks_on_bad_program():
    assert L.program_label(None, None) == "SCI"
    assert L.sms_line("hot", "A Family", words="hi", program="  ").startswith("SCI Appointment Request")
