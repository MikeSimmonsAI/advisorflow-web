# Sales Workspace redesign — screenshots (Lane F, PR #287)

Real headless-Chromium captures of the app running locally against synthetic test records (no production data). *Before* = unmodified `main`; *After* = `redesign/sales-workspace-v1`. Refreshed 2026-10-21 after the final refinements.

## Final review set (desktop)

### 1. My Day — salesperson

![1. My Day — salesperson](screenshots/sales-workspace/my_day-desktop.png)

### 2. My Day — manager (Your numbers vs Your team)

![2. My Day — manager (Your numbers vs Your team)](screenshots/sales-workspace/e2e-11-manager-my-day.png)

### 3. Follow Ups — Due Now | Overdue | Today | Completed

![3. Follow Ups — Due Now | Overdue | Today | Completed](screenshots/sales-workspace/follow_ups-desktop.png)

### 4. Lead Details

![4. Lead Details](screenshots/sales-workspace/lead_details-desktop.png)

### 5. Book a Demo

![5. Book a Demo](screenshots/sales-workspace/book_a_demo-desktop.png)

### 6. Pipeline — one Add Lead, exact stage on every card

![6. Pipeline — one Add Lead, exact stage on every card](screenshots/sales-workspace/pipeline-desktop.png)

### 7. Appointments

![7. Appointments](screenshots/sales-workspace/appointments-desktop.png)

### 8. Post-call "What happened?"

![8. Post-call "What happened?"](screenshots/sales-workspace/e2e-03-what-happened.png)

### 9. Not Interested → Close as Lost / Follow up later / Asked not to be contacted

![9. Not Interested choices](screenshots/sales-workspace/e2e-09-not-interested-options.png)

### Reports

![Reports](screenshots/sales-workspace/reports-desktop.png)

## Phone (390 px) — main workflows

| My Day | Follow Ups | Lead Details |
|---|---|---|
| ![](screenshots/sales-workspace/my_day-mobile.png) | ![](screenshots/sales-workspace/follow_ups-mobile.png) | ![](screenshots/sales-workspace/lead_details-mobile.png) |

| Book a Demo | Pipeline | Appointments |
|---|---|---|
| ![](screenshots/sales-workspace/book_a_demo-mobile.png) | ![](screenshots/sales-workspace/pipeline-mobile.png) | ![](screenshots/sales-workspace/appointments-mobile.png) |

| "What happened?" (Not Interested) |
|---|
| ![](screenshots/sales-workspace/e2e-m-what-happened.png) |

## Phone closeout — More menu, call-result dialog

The phone top bar keeps the seven main items; everything else (and Sign out) is under **More**.

| Rep | Rep allowed the Demo Suite | Manager (MY TEAM stays in the bar) |
|---|---|---|
| ![](screenshots/sales-workspace/e2e-m-more-rep.png) | ![](screenshots/sales-workspace/e2e-m-more-rep-with-demo.png) | ![](screenshots/sales-workspace/e2e-m-more-manager.png) |

Longest Not Interested form (2,000-character note): header and Save/Cancel stay on screen while the middle scrolls.

| 390×844 | 390×600 | 375×480 (keyboard open) |
|---|---|---|
| ![](screenshots/sales-workspace/e2e-m-dialog-390x844.png) | ![](screenshots/sales-workspace/e2e-m-dialog-390x600.png) | ![](screenshots/sales-workspace/e2e-m-dialog-375x480-keyboard.png) |

## Do Not Contact

### Recorded by the rep from a call — Call, Text and Book Demo removed

![Do Not Contact on Lead Details](screenshots/sales-workspace/e2e-dnc-lead-rep.png)

### Manager correction needs a reason

![Manager lift](screenshots/sales-workspace/e2e-dnc-manager-lift.png)

### After the lift — history kept, nothing rescheduled (phone)

![After lift](screenshots/sales-workspace/e2e-m-dnc-lifted-manager.png)

## Tablet (820 px)

| My Day | Follow Ups | Lead Details |
|---|---|---|
| ![](screenshots/sales-workspace/my_day-tablet.png) | ![](screenshots/sales-workspace/follow_ups-tablet.png) | ![](screenshots/sales-workspace/lead_details-tablet.png) |

| Book a Demo | Pipeline | Appointments |
|---|---|---|
| ![](screenshots/sales-workspace/book_a_demo-tablet.png) | ![](screenshots/sales-workspace/pipeline-tablet.png) | ![](screenshots/sales-workspace/appointments-tablet.png) |

## Walkthrough moments

### Follow Ups after login (Due Now = badge)

![Follow Ups after login (Due Now = badge)](screenshots/sales-workspace/e2e-02-follow-ups-due-now.png)

### Book a Demo with an open time picked

![Book a Demo with an open time picked](screenshots/sales-workspace/e2e-04-book-a-demo.png)

### Booked — real invitation status

![Booked — real invitation status](screenshots/sales-workspace/e2e-05-booked.png)

### Booked demo on Appointments

![Booked demo on Appointments](screenshots/sales-workspace/e2e-06-appointments.png)

### Pipeline after booking

![Pipeline after booking](screenshots/sales-workspace/e2e-07-pipeline.png)

### Won → existing Sold / Onboarding hand-off

![Won → existing Sold / Onboarding hand-off](screenshots/sales-workspace/e2e-08-won-handoff.png)

### Completed — every lead worked today

![Completed — every lead worked today](screenshots/sales-workspace/e2e-10-follow-ups-completed.png)

## Before (current main) for comparison

### Before — My Day

![Before: My Day](screenshots/sales-workspace/before-my_day.png)

### Before — Prospects list

![Before: Prospects list](screenshots/sales-workspace/before-prospects.png)

### Before — Opportunity record

![Before: Opportunity record](screenshots/sales-workspace/before-opportunity.png)

### Before — Nine-stage pipeline

![Before: Nine-stage pipeline](screenshots/sales-workspace/before-pipeline.png)

### Before — Team Calendar

![Before: Team Calendar](screenshots/sales-workspace/before-calendar.png)
