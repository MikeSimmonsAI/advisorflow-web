# Twilio A2P 10DLC — Final CTA & Consent Language

---

## 1. "How do end-users consent?" Field (paste this exactly)

End users opt in through one of two methods:

(1) WEBSITE OPT-IN: Customers who schedule an appointment at https://advisorflow-booking.vercel.app complete a booking form that includes an unchecked consent checkbox stating: "I agree to receive SMS text messages from Restland Cemetery & Funeral Home regarding appointment scheduling, reminders, and follow-up communications. Message and data rates may apply. Message frequency varies. Reply STOP to opt out. Reply HELP for assistance." The form cannot be submitted without active checkbox selection. Privacy Policy: https://advisorflow-booking.vercel.app/privacy-policy. Terms: https://advisorflow-booking.vercel.app/terms.

(2) OFFLINE/VERBAL OPT-IN: Customers who provide their mobile number during an in-person consultation, phone inquiry, or scheduled file review appointment at Restland are verbally informed of SMS communications at the time of number collection. The advisor reads a standardized disclosure and records consent only upon affirmative verbal confirmation. Evidence of this process, including the full verbal script, is documented at https://advisorflow-booking.vercel.app/sms-consent-evidence. Message and data rates may apply. Message frequency varies. Reply STOP to opt out. Reply HELP for assistance. No mobile numbers are shared with third parties or affiliates for marketing or promotional purposes.

---

## 2. Booking Form Consent Checkbox (add to your booking form UI)

**Checkbox label text:**

> I agree to receive SMS text messages from Restland Cemetery & Funeral Home regarding appointment scheduling, appointment reminders, and follow-up communications related to my funeral or cemetery planning services. Message and data rates may apply. Message frequency varies. Reply **STOP** to opt out at any time. Reply **HELP** for assistance. View our [Privacy Policy](https://advisorflow-booking.vercel.app/privacy-policy) and [Terms & Conditions](https://advisorflow-booking.vercel.app/terms).

**Rules:**
- Checkbox must be **unchecked by default**
- Form submit button must be **disabled** until checkbox is checked
- Do NOT pre-check it for the user

---

## 3. Deploy Checklist Before Resubmitting

- [ ] Upload `sms-consent-evidence.html` to your site at `/sms-consent-evidence`
- [ ] Verify it loads publicly at `https://advisorflow-booking.vercel.app/sms-consent-evidence`
- [ ] Add the consent checkbox to your booking form (unchecked by default)
- [ ] Confirm `/privacy-policy` and `/terms` still load
- [ ] Paste the CTA text from section 1 above into the Twilio field
- [ ] Hit Update — do NOT create a new campaign (avoids another vetting fee)
