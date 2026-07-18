# NEET / MBBS Counselling Website Monitor — Setup Guide

This guide will walk you through setting up a free, automatic system that
checks 4 government counselling websites every 15 minutes and sends a
WhatsApp message and an email to you and two other people the moment
anything changes on those pages.

You do **not** need to know how to code. Just follow the steps below in
order. Take your time — there's no rush, and nothing here costs any money.

**What you're building, in plain words:** A robot that runs on GitHub's
free computers (not yours), checks the websites every 15 minutes, remembers
what it saw last time, and texts/emails 3 people if anything is different.

---

## Before you start — what you'll need

- A free Gmail account (a new one, dedicated just for sending these alerts,
  is best — don't use your everyday personal Gmail)
- 3 WhatsApp phone numbers (the 3 people who should get alerts)
- 3 email addresses (can be the same 3 people, or different people)
- About 30-45 minutes, done once

---

## Step 1: Create a free GitHub account and repository

1. Go to **github.com** and click **Sign up**. Follow the prompts to create
   a free account (an email address and a password is all you need).
2. Once logged in, click the **+** icon in the top-right corner, then click
   **New repository**.
3. Name it something like `neet-counselling-monitor`.
4. Set it to **Private** (recommended, so no one else sees your setup) or
   Public — either works, but Private keeps things tidier.
5. Click **Create repository**.

*(Technical note: "repository" just means "a project folder" on GitHub.)*

---

## Step 2: Generate a Gmail App Password

You should **not** use your normal Gmail password in this system. Instead,
Google lets you create a special "App Password" that only works for this
one purpose and can be turned off any time without affecting your main
password.

1. Go to **myaccount.google.com** and sign in to the Gmail account you want
   to send alerts FROM (ideally a dedicated Gmail account you created just
   for this).
2. In the left menu, click **Security**.
3. Under "How you sign in to Google," make sure **2-Step Verification** is
   turned ON. (App Passwords only work if this is enabled. If it's off,
   click it and follow the prompts to turn it on — you'll need your phone.)
4. Once 2-Step Verification is on, go back to the **Security** page and
   search the page (Ctrl+F / Cmd+F) for **"App passwords"**, or go directly
   to **myaccount.google.com/apppasswords**.
5. You may be asked to sign in again.
6. Under "App name," type something like `NEET Monitor` and click
   **Create**.
7. Google will show you a **16-character password** (like `abcd efgh ijkl
   mnop`). **Copy this down somewhere safe right now** — Google will not
   show it to you again.
8. This 16-character code (remove the spaces) is your `GMAIL_APP_PASSWORD`.
   You'll paste it into GitHub in Step 4.

---

## Step 3: Set up CallMeBot for WhatsApp (each of the 3 people does this individually)

We use a free service called **CallMeBot** to send WhatsApp messages
without any paid API. Each person keeps their own private key — nobody
else (not even you, the organiser) needs to see anyone else's phone number
if you'd rather not.

**Each of the 3 recipients must do the following on their own phone:**

1. Save this contact on your phone: **+34 644 59 71 07** (name it
   "CallMeBot" or anything you like).
2. Open WhatsApp and send this exact message to that number:
   ```
   I allow callmebot to send me messages
   ```
3. Wait for a reply from CallMeBot. Within a minute or two, you'll receive
   a message back containing your personal **API key** — a string of
   numbers, like `1234567`.
4. **Write down two things**:
   - Your WhatsApp phone number **with country code and no spaces or
     symbols**, e.g. `919876543210` for an Indian number starting `98765
     43210` (91 is India's country code, no `+` or leading `0`).
   - Your personal API key from CallMeBot's reply.
5. Send these two pieces of information (phone number + API key) securely
   to whoever is setting up the GitHub secrets (Step 4) — for example, the
   organiser. This is the only information that needs to be shared, and it
   only allows sending messages to that one person's own WhatsApp — it
   can't be used to see their messages or contacts.

Repeat this for all 3 people. You will end up with 3 pairs of
(phone number, API key).

> **Note on reliability:** CallMeBot is a free community-run service. It is
> normally reliable but occasionally has short outages or rate limits
> (roughly one message every few seconds per number, which is not a
> problem for us since we only message on real changes). If a person stops
> receiving WhatsApp messages, they may need to resend the "I allow
> callmebot..." message to re-activate their key. The email alerts will
> still work as a reliable backup even if WhatsApp has a hiccup.

---

## Step 4: Add all your secrets to GitHub Actions

"Secrets" are where GitHub securely stores your passwords and keys so they
never appear in your code or are visible to anyone browsing the
repository.

1. Open your repository on GitHub (the one you made in Step 1).
2. Click **Settings** (top menu of the repository).
3. In the left sidebar, click **Secrets and variables**, then **Actions**.
4. Click the green **New repository secret** button.
5. Add each of the following **one at a time** — type the exact Name shown,
   paste the matching Value, then click **Add secret**. Repeat for all 13:

| Secret Name | Value to paste in |
|---|---|
| `GMAIL_ADDRESS` | The Gmail address you made in Step 2, e.g. `yourname@gmail.com` |
| `GMAIL_APP_PASSWORD` | The 16-character App Password from Step 2 (no spaces) |
| `RECIPIENT_EMAIL_1` | Email address of person 1 |
| `RECIPIENT_EMAIL_2` | Email address of person 2 |
| `RECIPIENT_EMAIL_3` | Email address of person 3 |
| `WHATSAPP_PHONE_1` | Person 1's WhatsApp number with country code (e.g. `919876543210`) |
| `WHATSAPP_APIKEY_1` | Person 1's CallMeBot API key |
| `WHATSAPP_PHONE_2` | Person 2's WhatsApp number with country code |
| `WHATSAPP_APIKEY_2` | Person 2's CallMeBot API key |
| `WHATSAPP_PHONE_3` | Person 3's WhatsApp number with country code |
| `WHATSAPP_APIKEY_3` | Person 3's CallMeBot API key |

That's it — no phone numbers, emails, or passwords ever appear in the
code itself. They live only in this secure Secrets area.

---

## Step 5: Upload all the files to your repository

You have 4 files/folders to upload:
- `monitor.py`
- `requirements.txt`
- `.github/workflows/monitor.yml`
- `data/.gitkeep` (an empty placeholder file)

**Easiest way (no coding tools needed):**

1. On your repository's main page, click **Add file** → **Upload files**.
2. Drag and drop `monitor.py` and `requirements.txt` into the box, then
   scroll down and click **Commit changes**.
3. For the workflow file, you need to recreate the folder structure. Click
   **Add file** → **Create new file**.
4. In the "Name your file" box, type exactly:
   `.github/workflows/monitor.yml`
   (Typing the slashes will automatically create the folders for you.)
5. Paste in the full contents of `monitor.yml` provided to you, then click
   **Commit changes**.
6. Repeat step 3-5 for `data/.gitkeep`, typing the filename `data/.gitkeep`
   and pasting its content.

**If you're comfortable with git on a computer instead**, you can simply
copy all the files into your local clone of the repository and run:
```
git add .
git commit -m "Add NEET counselling monitor"
git push
```

---

## Step 6: Trigger a manual test run and verify

1. On your repository page, click the **Actions** tab.
2. On the left, click **NEET Counselling Website Monitor**.
3. Click the **Run workflow** dropdown button on the right, make sure
   "Send a test WhatsApp/email message right now" is set to `true`, and
   click the green **Run workflow** button.
4. Wait about 30-60 seconds, then refresh the page. You'll see a new run
   appear. Click on it to watch its progress.
5. If it finishes with a green checkmark ✅, everything worked!
   - All 3 people should receive a WhatsApp message and an email saying
     "Test Message (Setup Successful)."
   - Because this is also literally the **first ever run**, this test
     message doubles as your "first deployment" confirmation — you don't
     need to do anything extra for that.
6. If it finishes with a red ❌, click into the run and read the log —
   see the Troubleshooting section below for common fixes.

---

## Step 7: Confirm the automatic schedule is active

That's it — you don't need to do anything else! Once the files are in your
repository, GitHub automatically runs the check every 15 minutes, forever,
for free, using GitHub's own computers. Your computer or phone can be off.

To double check it's running on schedule:
- Go to the **Actions** tab any time and you'll see a new run appear
  roughly every 15 minutes.
- Every day around 8:00 AM IST, if nothing changed on any of the 4 sites,
  all 3 people will get one "No Changes Found — system is active" message,
  so you always know it's still working.
- The moment any of the 4 pages changes — new text, a new link, a new PDF,
  a new announcement — everyone gets an alert immediately, any time of day
  or night.

---

## What the alert messages look like

**WhatsApp / Email alert when something changes:**
```
Subject: NEET Counselling Update Detected - MCC UG Medical Counselling

A change was detected on: MCC UG Medical Counselling
Time: 18 Jul 2026, 09:32 AM IST
Change type: 1 new PDF/document link(s) added.
Visit: https://mcc.nic.in/ug-medical-counselling/

New PDF/document link(s):
- https://mcc.nic.in/documents/notice-round-2.pdf
```

**Daily "no changes" status message (once a day, ~8:00 AM IST):**
```
Subject: NEET Counselling Monitor - Daily Status: No Changes Found

Good morning! As of 18 Jul 2026, 08:00 AM IST, the automated monitor
checked all 4 counselling websites and found no changes since the last
alert. This is just a daily confirmation that the system is up and
running.
```

**Test message (first run, and any time you manually request one):**
```
Subject: NEET Counselling Monitor - Test Message (Setup Successful)

This is a test message confirming your NEET/MBBS counselling website
monitor is set up correctly and running.
```

---

## Troubleshooting

**"Username and Password not accepted" / Gmail authentication error**
- Make sure `GMAIL_APP_PASSWORD` is the 16-character App Password from
  Step 2, NOT your normal Gmail login password.
- Make sure 2-Step Verification is turned on for that Gmail account —
  App Passwords don't work without it.
- Re-check `GMAIL_ADDRESS` is spelled correctly with no typos.

**CallMeBot WhatsApp message never arrives**
- Double check the phone number was saved with country code and no `+`,
  spaces, or leading `0` (e.g. `919876543210`).
- The person may need to re-send `I allow callmebot to send me messages`
  to `+34 644 59 71 07` on WhatsApp — CallMeBot keys occasionally expire
  after long periods of inactivity.
- Check that `WHATSAPP_APIKEY_1/2/3` was copied exactly, with no extra
  spaces.

**The Actions run shows a red ❌**
- Click into the failed run, then click the "Run the website monitor"
  step to read the error log.
- Most failures are a missing or misspelled secret name — go back to
  Step 4 and check every secret name matches exactly (they are
  case-sensitive).

**A website seems to be down and no alert was sent**
- This is expected and safe behaviour — if a site is temporarily
  unreachable, the monitor simply logs it and tries again in 15 minutes.
  It will never send a false "changed" alert just because a site was
  briefly down.

**I want to test again after the first run**
- Go to Actions → NEET Counselling Website Monitor → Run workflow, and
  leave "Send a test message" set to `true`. This works any time, not just
  on the very first run.

**I want to change how often it checks, or add keyword filtering later**
- Open `.github/workflows/monitor.yml` and edit the line
  `- cron: "*/15 * * * *"` to change frequency.
- Keyword filtering (e.g. only alert on the word "MBBS" or "Round 2") can
  be added later inside `monitor.py` in the `diff_snapshots` function —
  just ask for help when you're ready for that.

---

You're all set! The system will now quietly watch these 4 pages around the
clock and let you and your 2 teammates know the moment anything important
happens — completely free.
