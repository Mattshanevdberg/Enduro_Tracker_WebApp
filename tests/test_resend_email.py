"""
Send one Resend test email using the active runtime environment.

This script is intentionally a manual smoke test, not an automated pytest test.
It reads the same environment-style values as the Flask application so the
dev/prod Compose env files remain the source of truth.

Required environment variables:
  RESEND_API_KEY : Resend API key for the selected environment.
  TEST_EMAIL_TO  : destination address for the test email.

Optional environment variables:
  MAIL_FROM      : sender identity. Defaults to the Kooksnylive no-reply address.
"""

import os
import sys
from pathlib import Path

# Allow this file to be executed directly as:
#   python tests/test_resend_email.py
#
# Direct script execution places the tests directory on sys.path, not always the
# repository root. Adding the project root keeps the script aligned with the app
# import style while preserving the simple smoke-test command documented in the
# README.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import resend

from src.utils.env import required_env


def main() -> None:
    """
    Send a single password-reset-style test email through Resend.

    Input Args:
      None. Configuration is read from environment variables.

    Output:
      Prints the Resend response id/status without printing the API key.
    """
    try:
        resend.api_key = required_env("RESEND_API_KEY", "Resend smoke test")
        test_email_to = required_env("TEST_EMAIL_TO", "Resend smoke test")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    mail_from = (os.environ.get("MAIL_FROM") or "Kooksnylive <no-reply@kooksnylive.co.za>").strip()

    params: resend.Emails.SendParams = {
        "from": mail_from,
        "to": [test_email_to],
        "subject": "Kooksnylive Resend test",
        "html": (
            "<p>This is a Resend test email from the Kooksnylive dev environment.</p>"
            "<p>If you received this, the API key and sending domain are working.</p>"
        ),
    }

    response = resend.Emails.send(params)
    print(f"Resend test email sent. Response: {response}")


if __name__ == "__main__":
    main()
