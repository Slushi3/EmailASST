"""
AI-Powered Email Assistant
---------------------------
Flask app that:
  1. Connects to Gmail via IMAP and fetches unread emails
  2. Loads business context from knowledgebase.txt
  3. Uses OpenAI (gpt-4o-mini) to draft personalized replies
  4. Shows everything on a dashboard (index.html)
  5. Sends the (optionally edited) reply via SMTP when you click "Send"

Configuration is read from environment variables (see .env.example).
NEVER hardcode your email password or API key in this file.
"""

import os
import email
import imaplib
import smtplib
import ssl
from email.header import decode_header
from email.message import EmailMessage
from email.mime.text import MIMEText

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()  # reads variables from a local .env file if present

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")  # Gmail App Password, NOT your normal password

# --- AI provider (OpenRouter, using the OpenAI-compatible API) ---
OPENAI_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# OpenRouter model names are prefixed with the provider, e.g. "openai/gpt-4o-mini"
# See https://openrouter.ai/models for the full list.
OPENAI_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

# Optional headers OpenRouter uses for its public leaderboard / rankings.
# Safe to leave blank.
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
OPENROUTER_SITE_NAME = os.getenv("OPENROUTER_SITE_NAME", "AI Email Assistant")

KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "knowledgebase.txt")
MAX_EMAILS_TO_FETCH = int(os.getenv("MAX_EMAILS_TO_FETCH", "15"))

FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
    print("[WARN] EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set. IMAP/SMTP calls will fail until configured in .env")
if not OPENAI_API_KEY:
    print("[WARN] OPENROUTER_API_KEY not set. AI draft generation will fail until configured in .env")

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# Extra headers OpenRouter reads for its public rankings (both optional).
_openrouter_headers = {}
if OPENROUTER_SITE_URL:
    _openrouter_headers["HTTP-Referer"] = OPENROUTER_SITE_URL
if OPENROUTER_SITE_NAME:
    _openrouter_headers["X-Title"] = OPENROUTER_SITE_NAME

openai_client = (
    OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        default_headers=_openrouter_headers or None,
    )
    if OPENAI_API_KEY
    else None
)

# In-memory cache of the last fetched emails + generated drafts.
# Keyed by a stable id (IMAP UID) so the dashboard can reference them.
EMAIL_CACHE = {}


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------
def load_knowledge_base(path: str = KNOWLEDGE_BASE_PATH) -> str:
    """Read the business knowledge base file used as AI context."""
    if not os.path.exists(path):
        return "No knowledge base file found. Reply generically and politely."
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# IMAP: fetching unread emails
# ---------------------------------------------------------------------------
def _decode_mime_header(raw_header: str) -> str:
    """Decode a MIME-encoded email header (subject, sender name, etc.)."""
    if not raw_header:
        return ""
    parts = decode_header(raw_header)
    decoded = ""
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded += text.decode(charset or "utf-8", errors="replace")
        else:
            decoded += text
    return decoded


def _extract_body(msg: email.message.Message) -> str:
    """Pull the plain-text body out of an email.message.Message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace").strip()
                except Exception:
                    continue
        # Fallback: no plain text part found, try html stripped down crudely
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace").strip()
                except Exception:
                    continue
        return ""
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace").strip()
        except Exception:
            return msg.get_payload() or ""


def fetch_unread_emails(limit: int = MAX_EMAILS_TO_FETCH):
    """
    Connect to Gmail via IMAP, fetch unread emails (without marking them read),
    and return a list of dicts: {uid, sender, sender_email, subject, body}
    """
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        raise RuntimeError("EMAIL_ADDRESS / EMAIL_APP_PASSWORD are not configured.")

    results = []
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        imap.select("INBOX", readonly=True)  # readonly=True so we don't auto-mark emails as read

        status, data = imap.uid("search", None, "UNSEEN")
        if status != "OK":
            return results

        uids = data[0].split()
        uids = uids[-limit:]  # most recent N unread
        uids.reverse()  # newest first

        for uid in uids:
            status, msg_data = imap.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = _decode_mime_header(msg.get("Subject", "(no subject)"))
            from_header = _decode_mime_header(msg.get("From", "unknown"))
            body = _extract_body(msg)

            # Try to split "Display Name <email@x.com>" into parts
            sender_name, sender_email = email.utils.parseaddr(from_header)
            if not sender_name:
                sender_name = sender_email

            results.append({
                "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                "sender": sender_name,
                "sender_email": sender_email,
                "subject": subject,
                "body": body[:1500],  # cap length to keep prompts reasonable
            })
    finally:
        try:
            imap.close()
        except Exception:
            pass
        imap.logout()

    return results


# ---------------------------------------------------------------------------
# AI: generating draft replies
# ---------------------------------------------------------------------------
def generate_draft_reply(sender: str, subject: str, body: str, knowledge_base: str) -> str:
    """Call OpenAI to draft a personalized reply using the knowledge base as context."""
    if not openai_client:
        return "[AI draft unavailable: OPENROUTER_API_KEY is not configured]"

    system_prompt = (
        "You are a helpful, professional job-search assistant replying to emails "
        "on behalf of a job seeker. Use ONLY the candidate information provided in the "
        "knowledge base below to answer questions about education, experience, skills, "
        "projects, certifications, qualifications, or career background. If the knowledge "
        "base doesn't cover something, politely say you'll get back with the exact details "
        "rather than guessing. Keep the tone warm, concise, and professional. Do not invent "
        "experience, qualifications, skills, certifications, projects, or job history that "
        "isn't in the knowledge base. Tailor replies to the specific job opportunity when "
        "relevant, highlighting the most suitable technical background. always Sign off as "
        "'Elton D’Mello' unless told otherwise.\n\n"
        f"--- CANDIDATE KNOWLEDGE BASE ---\n{knowledge_base}\n--- END KNOWLEDGE BASE ---"
    )

    user_prompt = (
        f"Draft a reply to this email.\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}\n\n"
        f"Write only the reply body text (no subject line, no headers)."
    )

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        return f"[AI draft generation failed: {exc}]"


# ---------------------------------------------------------------------------
# SMTP: sending the reply
# ---------------------------------------------------------------------------
def send_email_reply(to_address: str, subject: str, body: str):
    """Send a reply email via Gmail SMTP using an App Password."""
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        raise RuntimeError("EMAIL_ADDRESS / EMAIL_APP_PASSWORD are not configured.")

    msg = MIMEText(body)
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to_address

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, [to_address], msg.as_string())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    """Dashboard: fetch unread emails and show AI-drafted replies."""
    error = None
    emails = []

    try:
        knowledge_base = load_knowledge_base()
        fetched = fetch_unread_emails()

        for item in fetched:
            uid = item["uid"]
            # Reuse cached draft if we already generated one and the user
            # hasn't refreshed, so re-rendering the page doesn't re-call the API.
            if uid in EMAIL_CACHE and EMAIL_CACHE[uid].get("subject") == item["subject"]:
                item["draft"] = EMAIL_CACHE[uid]["draft"]
            else:
                item["draft"] = generate_draft_reply(
                    item["sender"], item["subject"], item["body"], knowledge_base
                )
                EMAIL_CACHE[uid] = item

            emails.append(item)

    except Exception as exc:
        error = str(exc)

    return render_template("index.html", emails=emails, error=error)


@app.route("/refresh", methods=["POST"])
def refresh():
    """Clear the cache so the next dashboard load re-fetches + re-drafts everything."""
    EMAIL_CACHE.clear()
    return redirect(url_for("index"))


@app.route("/send/<uid>", methods=["POST"])
def send(uid):
    """Send the (possibly edited) AI draft for a given email uid."""
    item = EMAIL_CACHE.get(uid)
    if not item:
        flash("Could not find that email in the cache — try refreshing the dashboard.", "error")
        return redirect(url_for("index"))

    edited_body = request.form.get("reply_body", item["draft"])

    try:
        send_email_reply(item["sender_email"], item["subject"], edited_body)
        flash(f"Reply sent to {item['sender_email']}", "success")
        # Remove from cache/dashboard once handled
        EMAIL_CACHE.pop(uid, None)
    except Exception as exc:
        flash(f"Failed to send reply: {exc}", "error")

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
