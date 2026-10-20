# Atlantis — New Requests and Team & Alerts (manager guide)

Everything here is done in the app. Nobody edits the website, a server setting or code when someone joins or leaves the team.

## The two screens
| Screen | Where | Who |
|---|---|---|
| **New Requests** | left menu → *New Requests* (`/energy/follow-up?view=new`) | managers, and teammates who *can be assigned* |
| **Team & Alerts** | left menu → *Team & Alerts* (`/energy/follow-up?view=team`) | managers |

## How a request flows
1. A customer sends the website form, or a field rep logs an interested resident with their permission.
2. The request appears **once** in **New Requests → Unclaimed**. It is also on Rate Requests under New, and it has a first-contact task.
3. The people you selected get **one** in-app alert. A retry, double click or repeat form while it is still open does **not** alert again.
4. An advisor presses **Claim**, or a manager picks a name under **Assign**. That person now owns the customer and their first-contact task. Only the owner (and managers) see the phone number and email.
5. The owner works the request from the lead page (Overview: energy details and next step) and their task list. When the first contact is done, the request leaves New Requests.

If you set a **default advisor**, new requests go straight to that person (they are always alerted), and the rest of the selected team is alerted too.

## Team & Alerts, step by step
1. Open **Team & Alerts**. Each teammate has three switches:
   - **In-app alert**: gets the bell notification for every new request.
   - **Email alert**: also gets an email, once team email is set up (below).
   - **Can be assigned**: may claim requests and can be assigned them.

   New teammates start with alerts off and *can be assigned* on. By default, admins get in-app alerts.
2. **Default advisor**: choose a person, or "Nobody — the team claims them".
   - It does not change the website.
   - If you switch that person to *cannot be assigned*, or they leave, new requests go back to the shared inbox automatically.
3. **Add teammate** (workspace admins):
   1. Enter name, email and role, then press *Add teammate*.
   2. Copy the one-time setup link and send it to them yourself. The app does not email it.
   3. Then set their switches.
4. **Someone leaves**: deactivate them (Team & Access) or switch off their access. They stop receiving alerts and can't be assigned. Reassign their open requests from **New Requests → All → Reassign**.

If nobody has in-app alerts turned on, managers are alerted anyway, so a request is never unseen.

## Team email (shared mailbox)
Until all of the following are true, the screen shows **"Email not configured"** with the exact reasons, and no email is sent or reported:
1. The workspace has a working sending address (Org Settings → Email Sender).
2. The server switch `OUTBOUND_EMAIL_ENERGY_TEAM_ALERT` is on. It is off by default, and it is a one-time platform setting, not per teammate.
3. You entered the shared mailbox (e.g. `alerts@…`), pressed **Send test code**, and typed the 6-digit code from that email back in. Codes expire after 30 minutes and allow 5 tries.

After that, each new request emails the shared mailbox plus teammates with *Email alert* on.
- Emails go only to your team, **never** to the customer, and never for test records.
- *Recent alerts* shows what happened to each email (sent or failed).
- Changing the mailbox address requires verifying again.

## Demonstration (synthetic, as tested)
1. Manager → Team & Alerts → turn on **Ava: In-app alert**. The email section reads *Email not configured*.
2. A website request arrives. The manager and Ava get one alert each.
3. Ava → New Requests → **Claim**. "…is yours". Under **Mine** she sees the phone and email, and the first-contact task is on her list.
4. A field rep logs an interested resident. It appears in the same New Requests list. The manager → **Assign → Ben**. Ben is alerted, opens the lead and records details.
5. Manager → Team & Alerts → Ava **off**, Ben **on**. The next website request alerts the manager and Ben, not Ava.
