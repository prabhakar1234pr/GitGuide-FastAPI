"""SMTP service for sending access-invite emails."""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


class SMTPError(Exception):
    """Raised when SMTP send fails."""

    pass


def send_access_invite_email(to_email: str, access_link: str, project_name: str) -> bool:
    """
    Send project access invite email via SMTP.

    Returns True if sent successfully. Raises SMTPError when SMTP is configured but fails.
    Returns False only when SMTP is not configured (no credentials).
    """
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        logger.warning(
            "SMTP not configured (missing SMTP_HOST, SMTP_USER, or SMTP_PASSWORD); skipping invite email"
        )
        return False

    subject = f"You've been invited to access {project_name} on Crysivo"
    html_body = f"""
    <p>You've been invited to access the project <strong>{project_name}</strong> on Crysivo.</p>
    <p>Click the link below to get started:</p>
    <p><a href="{access_link}" style="display:inline-block;padding:12px 24px;background:#18181b;color:#fff;text-decoration:none;border-radius:6px;font-weight:500;">Access Project</a></p>
    <p>Or copy this link: {access_link}</p>
    <p>If you didn't expect this invite, you can ignore this email.</p>
    <p>— Crysivo</p>
    """
    text_body = f"""
You've been invited to access the project {project_name} on Crysivo.

Click this link to get started: {access_link}

If you didn't expect this invite, you can ignore this email.

— Crysivo
    """.strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email or settings.smtp_user or "noreply@crysivo.com"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        if settings.smtp_use_tls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(msg["From"], to_email, msg.as_string())
        else:
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, context=context, timeout=30
            ) as server:
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(msg["From"], to_email, msg.as_string())
        logger.info(f"Access invite email sent to {to_email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP auth failed for {settings.smtp_user}: {e}")
        raise SMTPError(
            f"SMTP login failed. For Gmail, use an App Password (not account password). {e}"
        ) from e
    except smtplib.SMTPRecipientsRefused as e:
        logger.error(f"SMTP recipient refused: {e}")
        raise SMTPError(f"Invalid recipient email: {to_email}") from e
    except (smtplib.SMTPException, OSError) as e:
        logger.error(f"Failed to send invite email to {to_email}: {e}", exc_info=True)
        raise SMTPError(f"Failed to send email: {e}") from e
