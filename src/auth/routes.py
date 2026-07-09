"""
Browser routes for authentication flows.

All authentication pages live in this module so signup, login, logout, and
password-reset behaviour remains grouped under src.auth rather than spread
through the general web blueprints.
"""

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_user, logout_user
from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.auth.login import clear_auth_version, remember_auth_version
from src.auth.mail import send_password_reset_email
from src.auth.passwords import check_password, hash_password, validate_password
from src.auth.rate_limits import limiter
from src.auth.tokens import (
    PASSWORD_RESET_PURPOSE,
    create_auth_token,
    find_valid_token,
    mark_token_used,
)
from src.db.models import Rider, SessionLocal, User
from src.utils.time import utc_now


bp_auth = Blueprint("auth", __name__)
GENERIC_LOGIN_ERROR = "Username/email or password is incorrect."
FORGOT_PASSWORD_RESPONSE = (
    "If an account exists for that email address, a password reset link has been sent."
)
RESET_LINK_INVALID_MESSAGE = "This password reset link is invalid, expired, or has already been used."
PASSWORD_RESET_MINUTES = 30


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


def _login_form_data() -> dict:
    """
    Read login form values into a template-friendly dictionary.

    Input Args:
      None. Reads from Flask's request.form.

    Output:
      Dictionary containing the submitted identifier. The password is excluded
      so it is never rendered back into the template.
    """
    return {
        "identifier": (request.form.get("identifier") or "").strip(),
    }


def _find_login_user(session, identifier: str):
    """
    Find a user by username or email for login.

    Input Args:
      session: active SQLAlchemy session.
      identifier: submitted username or email address.

    Output:
      Matching User row when found; otherwise None.

    Notes:
      Callers must use a generic credential error when this returns None so the
      login page does not reveal whether an email or username exists.
    """
    identifier_normalized = _normalize_auth_value(identifier)
    if not identifier_normalized:
        return None

    return (
        session.query(User)
        .filter(
            (User.username_normalized == identifier_normalized)
            | (User.email_normalized == identifier_normalized)
        )
        .first()
    )


def _find_active_user_by_email(session, email: str):
    """
    Find an active user by email for password reset.

    Input Args:
      session: active SQLAlchemy session.
      email: submitted recovery email address.

    Output:
      Active User row when found; otherwise None.

    Notes:
      Callers must always render the same forgot-password response whether this
      returns a user or None.
    """
    email_normalized = _normalize_auth_value(email)
    if not email_normalized:
        return None

    return (
        session.query(User)
        .filter(
            User.email_normalized == email_normalized,
            User.is_active.is_(True),
        )
        .first()
    )


def _forgot_password_form_data() -> dict:
    """
    Read forgot-password form values into a template-friendly dictionary.

    Input Args:
      None. Reads from Flask's request.form.

    Output:
      Dictionary containing the submitted recovery email.
    """
    return {
        "email": (request.form.get("email") or "").strip(),
    }


def _reset_password_form_data() -> dict:
    """
    Read reset-password form values.

    Input Args:
      None. Reads from Flask's request.form.

    Output:
      Dictionary containing the new password and confirmation.
    """
    return {
        "password": request.form.get("password") or "",
        "password_confirm": request.form.get("password_confirm") or "",
    }


def _render_forgot_password_response(form: dict):
    """
    Render the standard forgot-password success response.

    Input Args:
      form: template form dictionary.

    Output:
      Rendered forgot_password.html response.

    Notes:
      This response is deliberately reused for existing and non-existing email
      addresses so the route does not reveal whether an account exists.
    """
    return render_template(
        "forgot_password.html",
        form=form,
        message=FORGOT_PASSWORD_RESPONSE,
        success=True,
    )


def _post_login_redirect(user):
    """
    Redirect a logged-in user to the correct dashboard for their role.

    Input Args:
      user: authenticated User row.

    Output:
      Flask redirect response.
    """
    if getattr(user, "role", "") == "admin":
        return redirect(url_for("home.dashboard_admin"))
    return redirect(url_for("home.dashboard"))


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


@bp_auth.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    """
    Log a rider or admin into the browser session.

    Input Args:
      None. GET renders the login form. POST reads username/email and password.

    Output:
      GET: rendered login.html.
      POST validation error: rendered login.html with generic message and 400
      status.
      POST inactive account: rendered login.html with inactive-account message
      and 403 status.
      POST success: redirect to the role-appropriate dashboard.

    Notes:
      Wrong username/email and wrong password both use the same generic message
      so the route does not reveal whether a submitted account exists.
    """
    form = {"identifier": ""}
    if request.method == "GET":
        return render_template("login.html", form=form, message=None, success=None)

    form = _login_form_data()
    password = request.form.get("password") or ""

    if not form["identifier"] or not password:
        return render_template(
            "login.html",
            form=form,
            message=GENERIC_LOGIN_ERROR,
            success=False,
        ), 400

    session = SessionLocal()
    try:
        user = _find_login_user(session, form["identifier"])
        if not user or not check_password(user.password_hash, password):
            return render_template(
                "login.html",
                form=form,
                message=GENERIC_LOGIN_ERROR,
                success=False,
            ), 400

        if not getattr(user, "is_active", False):
            return render_template(
                "login.html",
                form=form,
                message="Your account is inactive. Please contact an administrator.",
                success=False,
            ), 403

        user.last_login_at = utc_now()
        session.commit()

        login_user(user)
        remember_auth_version(user)
        return _post_login_redirect(user)
    except SQLAlchemyError as exc:
        session.rollback()
        return render_template(
            "login.html",
            form=form,
            message=f"DB error: {exc}",
            success=False,
        ), 500
    finally:
        session.close()


@bp_auth.route("/logout", methods=["POST"])
def logout():
    """
    Clear the current browser login session.

    Input Args:
      None. The route reads the current Flask-Login session.

    Output:
      Redirect to the login page.
    """
    clear_auth_version()
    logout_user()
    return redirect(url_for("auth.login"))


@bp_auth.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    """
    Start the password-reset flow by accepting a recovery email address.

    Input Args:
      None. GET renders the form. POST reads the submitted recovery email.

    Output:
      GET: rendered forgot_password.html.
      POST: rendered forgot_password.html with the same success message whether
      or not the email belongs to an active account.

    Notes:
      If an active account exists, a one-use 30-minute password-reset token is
      created and emailed. The raw token is never logged or stored directly.
    """
    form = {"email": ""}
    if request.method == "GET":
        return render_template("forgot_password.html", form=form, message=None, success=None)

    form = _forgot_password_form_data()
    session = SessionLocal()
    try:
        user = _find_active_user_by_email(session, form["email"])
        if user:
            raw_token = create_auth_token(
                session,
                user,
                purpose=PASSWORD_RESET_PURPOSE,
                expires_in_minutes=PASSWORD_RESET_MINUTES,
            )
            session.commit()

            try:
                send_password_reset_email(user, raw_token)
            except Exception:
                # Do not expose mail delivery/configuration failures to the
                # browser because that would reveal whether the account exists.
                pass

        return _render_forgot_password_response(form)
    except SQLAlchemyError:
        session.rollback()
        return _render_forgot_password_response(form)
    finally:
        session.close()


@bp_auth.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token: str):
    """
    Complete a one-use password reset from an emailed reset link.

    Input Args:
      token: raw password-reset token from the URL.

    Output:
      GET valid token: rendered reset_password.html.
      GET invalid token: rendered reset_password.html with an invalid-link
      message and 400 status.
      POST invalid form/token: rendered reset_password.html with errors.
      POST success: redirect to /login.

    Notes:
      Successful reset changes the password hash, consumes the token, and
      increments user.auth_version so existing sessions stop working.
    """
    form = {"password": "", "password_confirm": ""}
    session = SessionLocal()
    try:
        auth_token = find_valid_token(session, token, PASSWORD_RESET_PURPOSE)
        if not auth_token:
            return render_template(
                "reset_password.html",
                token=token,
                form=form,
                message=RESET_LINK_INVALID_MESSAGE,
                success=False,
            ), 400

        if request.method == "GET":
            return render_template(
                "reset_password.html",
                token=token,
                form=form,
                message=None,
                success=None,
            )

        form = _reset_password_form_data()
        errors = validate_password(form["password"], form["password_confirm"])
        if errors:
            return render_template(
                "reset_password.html",
                token=token,
                form=form,
                message=" ".join(errors),
                success=False,
            ), 400

        user = session.get(User, auth_token.user_id)
        if not user or not getattr(user, "is_active", False):
            return render_template(
                "reset_password.html",
                token=token,
                form=form,
                message=RESET_LINK_INVALID_MESSAGE,
                success=False,
            ), 400

        user.password_hash = hash_password(form["password"])
        user.auth_version = int(getattr(user, "auth_version", 0) or 0) + 1
        user.updated_at = utc_now()
        mark_token_used(auth_token)
        session.commit()

        return redirect(url_for("auth.login"))
    except SQLAlchemyError as exc:
        session.rollback()
        return render_template(
            "reset_password.html",
            token=token,
            form=form,
            message=f"DB error: {exc}",
            success=False,
        ), 500
    finally:
        session.close()


@bp_auth.route("/admin/users", methods=["GET"])
def user_management():
    """
    Render the future user-management placeholder.

    Input Args:
      None.

    Output:
      Placeholder page for admin user management.

    Notes:
      Later this route will let admins view users, update roles, activate or
      deactivate accounts, and reset relevant account flags.
    """
    return render_template(
        "placeholder.html",
        title="User Management",
        description="Future admin user-management page.",
        route="/admin/users",
        access="admin",
        back_url=url_for("home.dashboard_admin"),
        back_label="Back to Admin Dashboard",
    )
