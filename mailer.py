import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

# Only .html templates are escaped, the plain-text parts must stay verbatim
mail_templates = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates" / "email"),
    autoescape=select_autoescape(["html"]),
)

SMTP_TIMEOUT_SECONDS = 15


def _smtp_settings() -> dict | None:
    """Read SMTP settings from the environment. None if incomplete."""
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM")

    if not host or not sender:
        return None

    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER"),
        "password": os.environ.get("SMTP_PASSWORD"),
        "sender": sender,
    }


def send_mail(to: str, subject: str, text_body: str, html_body: str) -> None:
    """Send a mail with a plain-text and an HTML part. Errors are logged, never raised.

    Callers run this as a background task and must not reveal to the client
    whether sending worked, so failures only end up in the log.
    """
    settings = _smtp_settings()

    if settings is None:
        logger.error("Mail not sent: SMTP_HOST and SMTP_FROM are not configured.")
        return

    message = EmailMessage()
    message["From"] = settings["sender"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        # Port 465 uses implicit TLS, everything else upgrades via STARTTLS
        if settings["port"] == 465:
            server = smtplib.SMTP_SSL(
                settings["host"], settings["port"], timeout=SMTP_TIMEOUT_SECONDS
            )
        else:
            server = smtplib.SMTP(
                settings["host"], settings["port"], timeout=SMTP_TIMEOUT_SECONDS
            )

        with server:
            if settings["port"] != 465:
                server.starttls()
            if settings["user"]:
                server.login(settings["user"], settings["password"] or "")
            server.send_message(message)

    except (smtplib.SMTPException, OSError):
        logger.exception("Mail to a registration address could not be sent.")


def _base_url() -> str:
    return os.environ.get("BASE_URL", "").rstrip("/")


def _render_mail(name: str, **context) -> tuple[str, str]:
    """Render the plain-text and HTML version of a mail template."""
    text_body = mail_templates.get_template(f"{name}.txt").render(**context)
    html_body = mail_templates.get_template(f"{name}.html").render(**context)
    return text_body, html_body


def send_confirmation_mail(to: str, token: str) -> None:
    link = f"{_base_url()}/confirm?token={quote(token)}"
    text_body, html_body = _render_mail("confirm", link=link)
    send_mail(
        to,
        "Legiceptum: Bitte bestätige deine E-Mail-Adresse",
        text_body,
        html_body
    )


def send_already_registered_mail(to: str) -> None:
    text_body, html_body = _render_mail(
        "already_registered", login_url=f"{_base_url()}/login"
    )
    send_mail(
        to,
        "Legiceptum: Du hast bereits ein Konto",
        text_body,
        html_body
    )
