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

import resend


def _required_env(name: str) -> str:
    """
    Read a required environment variable and fail with a safe message when absent.

    Input Args:
      name: environment variable name to read.

    Output:
      Stripped environment variable value.
    """
    value = (os.environ.get(name) or "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> None:
    """
    Send a single password-reset-style test email through Resend.

    Input Args:
      None. Configuration is read from environment variables.

    Output:
      Prints the Resend response id/status without printing the API key.
    """
    resend.api_key = _required_env("RESEND_API_KEY")
    test_email_to = _required_env("TEST_EMAIL_TO")
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
