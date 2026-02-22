"""
Optionale E-Mail-Benachrichtigung nach Buchung (Erfolg/Fehler).
Aktivierung über config.options.notify.enabled und SMTP-Angaben.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)


def send_notification(config: dict, success: bool, message: str) -> None:
    """Sendet eine E-Mail mit Betreff und Nachricht (Erfolg/Fehler)."""
    opts = config.get("options") or {}
    n = opts.get("notify") or {}
    if not n.get("enabled"):
        return
    to_email = n.get("to_email")
    from_email = n.get("from_email") or n.get("smtp_user")
    if not to_email or not from_email:
        log.warning("notify: to_email oder from_email fehlt")
        return
    subject = "[Trautermin] " + ("Erfolg" if success else "Fehler")
    body = message
    host = n.get("smtp_host")
    port = int(n.get("smtp_port") or 587)
    user = n.get("smtp_user")
    password = n.get("smtp_password")

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(from_email, [to_email], msg.as_string())
        log.info("Benachrichtigung gesendet an %s", to_email)
    except Exception as e:
        log.warning("E-Mail senden fehlgeschlagen: %s", e)
        raise
