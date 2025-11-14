""" 

Simple SMTP alert helper for the SOMA pipeline. 

 

Reads connection settings from environment variables so you 

dont hard-code anything in the image: 

 

SMTP_SERVER (default: mail2.jcpenney.com) 

SMTP_PORT (default: 25) 

SMTP_SENDER (default: jadebay2@jcp.com) 

ALERT_EMAILS (comma-sep list; required) 

 

Usage (sync): 

 

from backend.email_helper import send_alert 

send_alert("Stage failed", "Details go here") 

 

Usage (async): 

 

await send_alert_async("Subject", "Body text") 

 

""" 

 

import os 

import smtplib 

import asyncio 

from email.mime.multipart import MIMEMultipart 

from email.mime.text import MIMEText 

from typing import List 

 

SMTP_SERVER = os.getenv("SMTP_SERVER", "mail2.jcpenney.com") 
SMTP_PORT = int(os.getenv("SMTP_PORT", "25")) 
SMTP_SENDER = os.getenv("SMTP_SENDER", "jadebay2@jcp.com") 

 

RAW_RECIPS = os.getenv("ALERT_EMAILS", "") 
ALERT_EMAILS: List[str] = [e.strip() for e in RAW_RECIPS.split(",") if e.strip()] 

 

if not ALERT_EMAILS: 

    print("[email_helper] WARN: ALERT_EMAILS env-var is empty — no alerts will be sent.") 

 

 

def _build_msg(subject: str, body: str) -> MIMEMultipart: 

    msg = MIMEMultipart() 

    msg["From"] = SMTP_SENDER 

    msg["To"] = ", ".join(ALERT_EMAILS) 

    msg["Subject"] = subject 

    msg.attach(MIMEText(body, "plain")) 

    return msg 

 

 

def send_alert(subject: str, body: str) -> None: 

    """Blocking send — safe inside the existing monitor scripts.""" 

    if not ALERT_EMAILS: 

        return 

    try: 

        with smtplib.SMTP(host=SMTP_SERVER, port=SMTP_PORT) as server: 

            server.ehlo() 

            server.sendmail(SMTP_SENDER, ALERT_EMAILS, _build_msg(subject, body).as_string()) 

        print(f"[email_helper] Alert sent: {subject}") 

    except Exception as exc: 

        print(f"[email_helper] Failed to send alert: {exc}") 

 

 

# ---------- asyncio-friendly wrapper ---------- 

async def send_alert_async(subject: str, body: str) -> None: 

    """Run the blocking send() in a thread so we don’t block the event loop.""" 

    loop = asyncio.get_event_loop() 

    await loop.run_in_executor(None, send_alert, subject, body) 

 

 