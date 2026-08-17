# AI-Powered Email Assistant

A Flask dashboard that reads your unread Gmail messages, drafts personalized
replies with GPT-4o-mini using your own business info as context, and lets
you send them with one click.

Interface.png

## File structure

```
email_assistant/
├── app.py                # Flask app: IMAP fetch, OpenAI drafting, SMTP send
├── templates/
│   └── index.html        # Dashboard UI
├── knowledgebase.txt      # Your business info (services, pricing, FAQ) — EDIT THIS
├── requirements.txt
├── .env.example           # Copy to .env and fill in secrets
└── README.md
```

## 1. Install dependencies

```bash
cd email_assistant
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Create a Gmail App Password (do NOT use your real password)

Gmail blocks plain-password IMAP/SMTP logins for security. You need a
16-character "App Password" instead:

1. Turn on 2-Step Verification on your Google account, if it isn't already:
   https://myaccount.google.com/security
2. Go to https://myaccount.google.com/apppasswords
3. Create a new app password (name it e.g. "email-assistant")
4. Google shows you a 16-character password like `abcd efgh ijkl mnop` —
   copy it. You won't be able to see it again.
5. Put it in your `.env` file as `EMAIL_APP_PASSWORD` (spaces are fine).

This app password only works for mail login — it cannot access your Google
account settings, so it's much safer than using your real password.

## 3. Get an OpenAI API key

1. Go to https://platform.openai.com/api-keys
2. Create a new secret key
3. Put it in `.env` as `OPENAI_API_KEY`

Note: API usage is billed separately from a ChatGPT subscription — check
https://platform.openai.com/usage to monitor costs. gpt-4o-mini is the
cheapest capable model and is set as the default here.

## 4. Configure your .env file

```bash
cp .env.example .env
```

Then edit `.env` and fill in:
- `EMAIL_ADDRESS` — your Gmail address
- `EMAIL_APP_PASSWORD` — the app password from step 2
- `OPENAI_API_KEY` — the key from step 3

**Never commit `.env` to git.** Add it to `.gitignore`:

```bash
echo ".env" >> .gitignore
```

## 5. Fill in your knowledge base

Open `knowledgebase.txt` and replace the placeholders with your real
services, pricing, policies, and FAQ answers. The AI only knows what's in
this file — it's instructed not to invent prices or policies, so the more
complete this file is, the better (and safer) the drafts will be.

## 6. Run it

```bash
python app.py
```

Visit **http://localhost:5000** — you'll see your unread emails with an
AI-drafted reply under each one. Edit any draft directly in the text box,
then click **Send Reply** to send it via SMTP. Click **Refresh Inbox** to
re-check for new unread mail (this re-generates drafts and re-calls the
OpenAI API, so it costs a small amount each time).

## Security notes

- The app never marks emails as read on its own (IMAP connection opens the
  inbox in read-only mode) only Gmail marking them read in the normal way
  will remove them from the "unread" list.
- Drafts are held in memory only (`EMAIL_CACHE`) and are cleared when you
  send a reply or refresh nothing is persisted to disk.
- Set `FLASK_SECRET_KEY` to a real random string before deploying anywhere
  beyond your own machine (`python -c "import secrets; print(secrets.token_hex(32))"`).
- If you ever deploy this publicly, put authentication in front of it —
  as written, anyone who can reach the Flask app can read your inbox
  contents and send email as you.

## Customizing

- **Model**: change `OPENAI_MODEL` in `.env` (e.g. to `gpt-4o` for higher
  quality at higher cost).
- **How many emails to fetch**: change `MAX_EMAILS_TO_FETCH`.
- **Tone/behavior of drafts**: edit the `system_prompt` in
  `generate_draft_reply()` inside `app.py`, or add a "TONE / STYLE NOTES"
  section to `knowledgebase.txt` (already included as a placeholder).
