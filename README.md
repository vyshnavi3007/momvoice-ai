# MomVoice AI — Setup Guide

This runs the actual bot on your own laptop (not deployed to the cloud
yet — that's a later step). Read the "What this file does" note at the
top of each .py file if you want to understand it, not just run it.

## What each file does (plain language)

- **db.py** — Talks to your Cloud SQL database. Nothing else touches
  the database directly.
- **agent.py** — Defines your 3 AI agents (router, tracking, guidance)
  using Google's ADK. This is the "brain."
- **bot.py** — Connects to Telegram and runs everything. **This is the
  file you actually run.**
- **schema.sql** — The database table definitions. Run this once.

## One-time setup

### 1. Install Python dependencies
```
pip install -r requirements.txt
```

### 2. Set up your database tables
Connect to your Cloud SQL instance (Console > SQL > your instance >
"Connect using Cloud Shell", or use a tool like DBeaver/pgAdmin) and
run everything in `schema.sql`. This creates the `users`, `entries`,
and `guidance_queries` tables.

### 3. Authenticate your laptop with Google Cloud
```
gcloud auth application-default login
```
This lets your code securely act as you when talking to Vertex AI and
Cloud SQL. If you don't have `gcloud` installed, search "install
gcloud CLI" for your OS.

### 4. Fill in your .env file
Copy `.env.example` to a new file named `.env`, and fill in:
- `TELEGRAM_BOT_TOKEN` — from BotFather
- `GOOGLE_CLOUD_PROJECT` — your project ID (e.g. `momvoice-ai`)
- `INSTANCE_CONNECTION_NAME` — Console > SQL > your instance, copy the
  "Connection name" field
- `DB_PASS` — the password you generated when creating the instance

### 5. Run it
```
python bot.py
```
You should see "MomVoice AI bot is running..." — now open Telegram,
find your bot, and send it a message like:

> fed the baby 6oz at 3pm

or

> is this feeding schedule normal for a 6-month-old?

## If something breaks

- **"DefaultCredentialsError"** → run step 3 again (`gcloud auth
  application-default login`).
- **Bot doesn't reply on Telegram** → check the terminal for errors;
  most likely `.env` has a typo (double check `INSTANCE_CONNECTION_NAME`
  and `DB_PASS`).
- **"relation 'users' does not exist"** → you haven't run `schema.sql`
  against your database yet (step 2).

## Deploying to Cloud Run (so it runs 24/7, not just on your laptop)

Do this after your local testing (above) is working. This makes your
bot run permanently on Google Cloud, which is what Touchpoint 3 expects.

### 1. Make sure you're in the right project
```
gcloud config set project momvoice-ai
```

### 2. Enable two more APIs (one-time)
```
gcloud services enable run.googleapis.com cloudbuild.googleapis.com
```

### 3. Build and deploy in one command
From inside the `momvoice_app` folder:
```
gcloud run deploy momvoice-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT=momvoice-ai,GOOGLE_CLOUD_LOCATION=us-central1,GOOGLE_GENAI_USE_VERTEXAI=TRUE,MODEL_NAME=gemini-2.0-flash,INSTANCE_CONNECTION_NAME=momvoice-ai:us-central1:momvoice-db,DB_USER=postgres,DB_NAME=postgres \
  --set-secrets TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,DB_PASS=db-password:latest \
  --add-cloudsql-instances momvoice-ai:us-central1:momvoice-db
```

This takes a few minutes the first time. **Before running this**, you need
to create the two secrets it references (step 4) — do that first, then
come back and run this command.

### 4. Store your secrets properly (don't put passwords in plain env vars)
```
echo -n "YOUR_TELEGRAM_BOT_TOKEN" | gcloud secrets create telegram-bot-token --data-file=-
echo -n "YOUR_DB_PASSWORD" | gcloud secrets create db-password --data-file=-
```
Replace the placeholder text with your real token/password. If a secret
already exists and you need to update it:
```
echo -n "NEW_VALUE" | gcloud secrets versions add telegram-bot-token --data-file=-
```

### 5. Give your Cloud Run service permission to use these
```
gcloud secrets add-iam-policy-binding telegram-bot-token \
  --member="serviceAccount:$(gcloud projects describe momvoice-ai --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding db-password \
  --member="serviceAccount:$(gcloud projects describe momvoice-ai --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Also grant it the same two roles you gave yourself earlier — Cloud SQL
Client and Agent Platform User — but to the Cloud Run service account
this time (same command shape as above, with `roles/cloudsql.client`
and `roles/aiplatform.user`).

### 6. Set your webhook URL and redeploy
After step 3 finishes, it prints a **Service URL** (looks like
`https://momvoice-bot-xxxxx-uc.a.run.app`). Copy it, then run:
```
gcloud run services update momvoice-bot \
  --region us-central1 \
  --update-env-vars WEBHOOK_URL=https://momvoice-bot-xxxxx-uc.a.run.app
```
(replace with your real URL — no trailing slash)

This is quick (no rebuild needed) and finishes the setup. Once it's
done, message your bot on Telegram — it's now running permanently on
Google Cloud, no laptop required.

### If something doesn't respond
```
gcloud run services logs read momvoice-bot --region us-central1 --limit 50
```
This shows the last 50 log lines — the same kind of info you saw in
your terminal locally, just from the cloud.

## What's still not done (for later)

- Switching `MODEL_NAME` from Gemini to a self-hosted Gemma model on
  Vertex AI Model Garden, for lower cost
- BigQuery export + Looker Studio dashboards (Phase 2)

