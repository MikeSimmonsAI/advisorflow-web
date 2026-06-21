# AdvisorFlow Web — What We Built (Plain English Summary)

This is written so anyone — even someone with zero coding background — can
understand what exists, why it exists, and what to do next.

---

## The Big Picture

Mike runs a sales team that texts people about cemetery and funeral planning
(Pre-Need, At-Need, etc.) for Restland Cemetery. He had a Python program
running on his desktop computer that did this, but it kept breaking and only
worked on one computer at a time.

**The goal:** build a website version instead, so 5+ salespeople can log in
from any device (laptop, phone, Chromebook — doesn't matter) and all use the
same system at the same time, without stepping on each other's toes.

Think of it like building a car. We built the engine, the transmission, the
wheels, and the dashboard. The car runs — we tested it. But it's still sitting
in the garage. It hasn't been driven out onto a real road (the internet) yet.

---

## The Two Big Pieces

A website always has two halves:

1. **The Backend** — the "brain." Lives on a server, handles all the
   thinking: storing leads, checking for duplicates, sending texts, deciding
   who gets what message. The person using the site never sees this part
   directly.
2. **The Frontend** — the "face." This is what you actually see and click on
   in your browser — buttons, lists, login screen.

We built BOTH. They're not connected to the internet yet, but they work
together when tested.

---

## What The Backend (the brain) Can Do

- **Log people in** — each salesperson gets their own account and password.
- **Import leads from an Excel file** — someone uploads a spreadsheet of
  names/phones/emails, and the system reads it automatically.
- **Stop duplicate contacts** — if two salespeople accidentally have the same
  person in their lists, the system catches it and only lets ONE person text
  them, not both. We actually tested this against Mike's real lead file and
  it worked correctly.
- **Sort leads into the right bucket automatically** — someone who already
  bought a plot gets a different message ("want a headstone too?") than
  someone who hasn't bought anything yet ("lock in today's price"). Nobody
  gets ignored anymore — every type of lead gets contacted, just with the
  right message for their situation.
- **Send text messages** — through Twilio (the texting service Mike already
  uses), with a "book an appointment" link built into the message.
- **Follow up automatically 9 times over 60 days** — if someone doesn't
  reply, the system automatically texts them again on a schedule (day 1, 3,
  7, 10, 14, 21, 30, 45, 60), so nobody has to remember to follow up by hand.
  The moment someone replies, it stops bothering them — no more texts.
  texts go out.
- **Email people who don't have a phone number** — some leads only have an
  email. The system handles those separately through email instead of text.
- **Notice when someone sounds interested** — if a reply contains words like
  "yes" or "interested," the system flags it as a HOT lead and emails the
  salesperson immediately so they don't miss it.
- **Give Mike a master view** — one screen shows what all 5 salespeople are
  doing at once: how many leads, how many texts sent, how many hot replies.

## What The Frontend (the face) Looks Like

We designed it to look like a sleek, dark control panel — Mike specifically
asked for "Tesla dashboard" vibes, not a boring spreadsheet look. Built
screens for:

- **Login page**
- **Overview page** — quick snapshot: new leads, hot replies, booked
  appointments
- **Leads page** — upload your Excel file here, see a preview of what will
  happen BEFORE you confirm anything
- **Replies page** — see everyone who texted back
- **Cadence page** — see the 9-touch follow-up schedule status
- **Email Queue page** — for the leads with no phone number
- **Master Dashboard** — Mike's "see everything" screen

---

## What We Tested (Proof It Actually Works)

We didn't just write code and hope — we ran real tests:

- Uploaded Mike's actual 1,000-lead Excel file from Restland. Out of 1,000
  rows, the system correctly sorted: 775 ready to text, 55 email-only, 340
  already-bought-but-still-worth-contacting, 368 that need a human to
  double check what type of lead they are, and caught 1 duplicate.
- Simulated two different salespeople uploading lists that secretly
  contained the same person — the system correctly blocked the second
  person from texting them.
- Simulated someone replying "yes" to a text — the system correctly flagged
  it as HOT and tried to send an email alert.
- Built the actual website pages and confirmed they compile/build with zero
  errors.

---

## What's NOT Done Yet (Be Honest About This)

1. **It's not on the internet yet.** Nobody outside this conversation can
   visit it. It needs to be "deployed" — basically uploaded to a hosting
   service (we recommend Render or Railway, both cheap, around $20-30/month
   total).
2. **There's no "type a message and hit send" button yet.** Right now,
   texting only happens automatically through the 9-touch schedule. If Mike
   wants to manually write and send a one-off text to someone, that screen
   doesn't exist yet.
3. **No "forgot my password" feature.** Salespeople log in with a temporary
   password and there's currently no way for them to change it themselves.
4. **Calendar booking isn't fully connected.** The code to put appointments
   on Google Calendar is written, but it needs Mike to go set up a Google
   account connection first (a 10-minute task only Mike can do, not
   something that can be coded around).
5. **Caller ID name** (showing "Restland Cemetery" instead of a random phone
   number) needs Mike to register that directly with Twilio — also something
   only Mike can do, not a coding task.

---

## If You're Picking This Up Cold, Do This Next (In Order)

1. **Get it online.** Follow the `DEPLOY.md` file in the backend folder — it
   walks through putting the backend on Render.com step by step.
2. **Connect the frontend to the backend.** The frontend needs one setting
   (`VITE_API_BASE_URL`) pointed at wherever the backend ends up living.
3. **Create real accounts for the 5 salespeople.** There's a script called
   `seed.py` that creates accounts — just put in real names and emails.
4. **Test it live with one real salesperson before rolling out to all 5.**
5. **THEN worry about the missing pieces** (manual send button, password
   reset, calendar, caller ID) — those are improvements, not blockers to
   getting started.

---

## One-Sentence Summary

We built a complete, tested replacement for Mike's broken desktop program —
shaped like a real website that 5+ people can use at once — and it's ready
to be put online, just not online yet.
