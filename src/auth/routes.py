"""
Browser routes for authentication flows.

All authentication pages live in this module so signup, login, logout, and
password-reset behaviour remains grouped under src.auth rather than spread
through the general web blueprints.
"""

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_user
from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.auth.login import remember_auth_version
from src.auth.passwords import hash_password, validate_password
from src.db.models import Rider, SessionLocal, User
from src.utils.time import utc_now


bp_auth = Blueprint("auth", __name__)


def _normalize_auth_value(value: str) -> str:
    """
    Normalise username/email values for case-insensitive uniqueness checks.

    Input Args:
      value: raw submitted username or email value.

    Output:
      Lowercase, trimmed string.
    """
    return (value or "").strip().lower()


def _signup_form_data() -> dict:
    """
    Read signup form values into a template-friendly dictionary.

    Input Args:
      None. Reads from Flask's request.form.

    Output:
      Dictionary containing submitted signup fields except the password values.

    Notes:
      Password values are intentionally not returned to the template after a
      validation failure.
    """
    return {
        "first_name": (request.form.get("first_name") or "").strip(),
        "last_name": (request.form.get("last_name") or "").strip(),
        "username": (request.form.get("username") or "").strip(),
        "email": (request.form.get("email") or "").strip(),
    }


def _validate_signup_form(form: dict, password: str, confirmation: str) -> list[str]:
    """
    Validate signup fields before creating a rider account.

    Input Args:
      form: dictionary returned by _signup_form_data.
      password: raw submitted password.
      confirmation: raw submitted password confirmation.

    Output:
      List of validation error messages. Empty list means the form can be saved.
    """
    errors = []
    if not form["first_name"]:
        errors.append("Name is required.")
    if not form["last_name"]:
        errors.append("Surname is required.")
    if not form["username"]:
        errors.append("Username is required.")
    if not form["email"]:
        errors.append("Email address is required.")
    else:
        try:
            validate_email(form["email"], check_deliverability=False)
        except EmailNotValidError:
            errors.append("Email address is not valid.")

    errors.extend(validate_password(password, confirmation))
    return errors


def _signup_identity_exists(session, username_normalized: str, email_normalized: str) -> bool:
    """
    Check whether a signup username or email is already registered.

    Input Args:
      session: active SQLAlchemy session.
      username_normalized: lowercase/trimmed username.
      email_normalized: lowercase/trimmed email address.

    Output:
      True when either value is already used by an existing User row.
    """
    return (
        session.query(User)
        .filter(
            (User.username_normalized == username_normalized)
            | (User.email_normalized == email_normalized)
        )
        .first()
        is not None
    )


@bp_auth.route("/signup", methods=["GET", "POST"])
def signup():
    """
    Create a public rider account and linked Rider profile.

    Input Args:
      None. GET renders the signup form. POST reads browser form fields.

    Output:
      GET: rendered signup.html.
      POST validation error: rendered signup.html with message and 400 status.
      POST success: redirect to the existing Rider edit form for profile
      completion.

    Notes:
      Public signup always creates role='rider'. Any submitted role/admin field
      is ignored so public signup cannot create admin accounts. Later this
      redirect should point to a rider/profile route that only allows the rider
      to edit their own linked profile.
    """
    form = {
        "first_name": "",
        "last_name": "",
        "username": "",
        "email": "",
    }

    if request.method == "GET":
        return render_template("signup.html", form=form, message=None, success=None)

    form = _signup_form_data()
    password = request.form.get("password") or ""
    confirmation = request.form.get("password_confirm") or ""
    errors = _validate_signup_form(form, password, confirmation)

    username_normalized = _normalize_auth_value(form["username"])
    email_normalized = _normalize_auth_value(form["email"])

    session = SessionLocal()
    try:
        if _signup_identity_exists(session, username_normalized, email_normalized):
            errors.append("Username or email address is already registered.")

        if errors:
            return render_template(
                "signup.html",
                form=form,
                message=" ".join(errors),
                success=False,
            ), 400

        rider = Rider(
            name=f"{form['first_name']} {form['last_name']}".strip(),
            category=None,
            team=None,
            bike=None,
            bio=None,
        )
        session.add(rider)
        session.flush()

        user = User(
            first_name=form["first_name"],
            last_name=form["last_name"],
            username=form["username"],
            username_normalized=username_normalized,
            email=form["email"],
            email_normalized=email_normalized,
            password_hash=hash_password(password),
            role="rider",
            rider_id=rider.id,
            is_active=True,
            auth_version=1,
            created_at=utc_now(),
            updated_at=utc_now(),
            last_login_at=utc_now(),
        )
        session.add(user)
        session.commit()

        login_user(user)
        remember_auth_version(user)

        return redirect(url_for("riders.rider_form", rider_id=rider.id))
    except IntegrityError:
        session.rollback()
        return render_template(
            "signup.html",
            form=form,
            message="Username or email address is already registered.",
            success=False,
        ), 400
    except SQLAlchemyError as exc:
        session.rollback()
        return render_template(
            "signup.html",
            form=form,
            message=f"DB error: {exc}",
            success=False,
        ), 500
    finally:
        session.close()
