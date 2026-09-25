"""One-time, idempotent edit of the public site's Privacy Policy and Terms.

Adds the EvoSys Wholesale SMS program sections WITHOUT touching any other
policy text. Every edit is an exact-anchor replacement that must match exactly
once; if an anchor is missing or already replaced the script says so and
changes nothing, so re-running it is safe.

    python scripts/review/apply_wholesale_sms_legal.py
"""
from pathlib import Path

SITE = Path(__file__).resolve().parents[2] / "public-site"
MARK = "EvoSys Wholesale SMS program"

PRIVACY_OLD_3 = (
    '<h2>3. SMS Communications</h2><p>If you opt in to SMS, your mobile number and consent record '
    'are used to support the messaging program described at the time of opt-in. Message frequency '
    'varies. Message and data rates may apply. Reply STOP to opt out and HELP for help. Consent is '
    'not a condition of purchase. Text messaging originator opt-in data and consent will not be '
    'sold or shared with third parties for their own marketing purposes. See our '
    '<a href="sms-terms.html">SMS Terms</a>.</p>')

PRIVACY_NEW_3 = (
    '<h2 id="sms">3. SMS Communications</h2><p>If you opt in to SMS, your mobile number and consent '
    'record are used to support the messaging program described at the time of opt-in. Message '
    'frequency varies. Message and data rates may apply. Reply STOP to opt out and HELP for help. '
    'Consent is not a condition of purchase. See our <a href="sms-terms.html">SMS Terms</a>.</p>'
    '<p><strong>Text messaging originator opt-in data and consent will not be shared with any third '
    'parties. Mobile information will not be shared with third parties or affiliates for marketing '
    'or promotional purposes.</strong></p>'
    '<h3 id="sms-wholesale">EvoSys Wholesale SMS program</h3>'
    '<p>EvoSys Wholesale is a product of EVO Integrated Solutions LLC, operated under the EvoSysPro '
    'platform. EvoSense is the acquisition and communication engine used within EvoSys Wholesale. '
    'Property owners who submit the seller inquiry form at <a href="/sell">evosyspro.live/sell</a> '
    'and check the optional, unchecked SMS consent box agree to receive text messages from EVO '
    'Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) about their property '
    'inquiry.</p><ul>'
    '<li><strong>Types of messages:</strong> follow-up on your property inquiry; questions about the '
    'property, such as its condition, your timeline, your reasons for selling and your asking '
    'price; appointment scheduling; and updates on the status of your property discussion or '
    'transaction.</li>'
    '<li><strong>Message frequency:</strong> Message frequency varies. Message and data rates may '
    'apply.</li>'
    '<li><strong>STOP:</strong> Reply STOP at any time to stop receiving messages. We record the '
    'date, time and keyword of every opt-out and stop program messages to that number.</li>'
    '<li><strong>HELP:</strong> Reply HELP for help.</li>'
    '<li><strong>Consent handling:</strong> SMS consent is optional and is not a condition of any '
    'service. Only people who actively check the SMS box receive program messages; submitting the '
    'form without checking it does not enroll you. We keep a record of each consent: the date and '
    'time we received it, the page and the exact wording shown, and the IP address and browser '
    'information sent with the form. A phone number obtained from public records, data providers or '
    'any other source is never treated as consent to receive text messages.</li>'
    '<li><strong>Mobile data handling:</strong> Mobile numbers and consent records are used only to '
    'operate the program, honor opt-outs and keep the records described above. Access is limited to '
    'personnel and service providers who need it to deliver the messages you asked for.</li></ul>'
    '<p><strong>Text messaging originator opt-in data and consent will not be shared with any third '
    'parties. Mobile information will not be shared with third parties or affiliates for marketing '
    'or promotional purposes.</strong></p>')

PRIVACY_OLD_4 = (
    'as part of a business transaction such as a merger or acquisition.</p>')
PRIVACY_NEW_4 = (
    'as part of a business transaction such as a merger or acquisition. None of this sharing '
    'includes mobile phone numbers or SMS opt-in data and consent, which are governed by '
    '<a href="#sms">Section 3</a>.</p>')

TERMS_OLD_10 = '<h2>10. Changes & Contact</h2>'
TERMS_NEW_10 = (
    '<h2 id="sms-wholesale">10. SMS Messaging &ndash; EvoSys Wholesale</h2>'
    '<p><strong>Program and business.</strong> The EvoSys Wholesale SMS program is operated by EVO '
    'Integrated Solutions LLC. EvoSys Wholesale is a product of EVO Integrated Solutions LLC, '
    'operated under the EvoSysPro platform. EvoSense is the acquisition and communication engine '
    'used within EvoSys Wholesale.</p>'
    '<p><strong>How you join.</strong> You join only by submitting the seller inquiry form at '
    '<a href="/sell">evosyspro.live/sell</a> and checking the optional SMS consent box, which is '
    'unchecked by default. Submitting the form without checking the box does not enroll you. '
    'Consent is not a condition of any service.</p>'
    '<p><strong>Message types.</strong> Messages about your property inquiry: follow-up questions; '
    'questions about the property such as condition, timeline, motivation and asking price; '
    'appointment scheduling; and transaction or status updates after a relationship is '
    'established.</p>'
    '<p><strong>Frequency and cost.</strong> Message frequency varies. Message and data rates may '
    'apply. Carriers are not liable for delayed or undelivered messages.</p>'
    '<p><strong>STOP and opt-out.</strong> Reply STOP to any message to unsubscribe. You may receive '
    'one message confirming the opt-out, and no further program messages will be sent to that '
    'number. An opt-out is recorded with its date, time and keyword.</p>'
    '<p><strong>HELP and support.</strong> Reply HELP for help, or contact us using the details in '
    'Section 11.</p>'
    '<p>Our <a href="privacy.html#sms">Privacy Policy</a> explains how mobile information and '
    'consent are handled. Text messaging originator opt-in data and consent will not be shared with '
    'any third parties.</p>'
    '<h2>11. Changes & Contact</h2>')


def edit(name, pairs):
    path = SITE / name
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        print("%s: already applied - unchanged" % name)
        return
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            raise SystemExit("%s: anchor found %d times, expected 1:\n%s" % (name, n, old[:120]))
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline="")
    print("%s: updated" % name)


if __name__ == "__main__":
    edit("privacy.html", [(PRIVACY_OLD_3, PRIVACY_NEW_3), (PRIVACY_OLD_4, PRIVACY_NEW_4)])
    edit("terms.html", [(TERMS_OLD_10, TERMS_NEW_10)])
