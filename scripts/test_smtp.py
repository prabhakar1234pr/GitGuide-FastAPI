#!/usr/bin/env python3
"""Test SMTP configuration by sending a sample email."""

import os
import sys

# Add project root before importing app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main():
    try:
        from app.config import settings
        from app.services.smtp_service import SMTPError, send_access_invite_email
    except ImportError as e:
        print(f"[FAIL] Import error: {e}")
        return 1

    to_email = "prabhakarelavala1@gmail.com"
    print("SMTP Test")
    print("-" * 40)
    print(f"Host: {settings.smtp_host or '(not set)'}")
    print(f"Port: {settings.smtp_port}")
    print(f"User: {settings.smtp_user or '(not set)'}")
    print(f"From: {settings.smtp_from_email or settings.smtp_user or 'noreply@crysivo.com'}")
    print("-" * 40)

    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        print("[FAIL] SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD in .env")
        return 1

    print(f"Sending test email to {to_email}...")

    # Use the existing invite email function with a sample project/link
    access_link = "https://www.crysivo.com/dashboard"
    project_name = "SMTP Test Project"

    try:
        ok = send_access_invite_email(to_email, access_link, project_name)
        if ok:
            print(f"[OK] Email sent successfully to {to_email}")
            return 0
        print("[FAIL] send_access_invite_email returned False")
        return 1
    except SMTPError as e:
        print(f"[FAIL] SMTPError: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
