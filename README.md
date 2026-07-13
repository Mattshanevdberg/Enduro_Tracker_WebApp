# Enduro Tracker Web Documentation

This document maps the Flask routes, background workers, and utility helpers to
their responsibilities, data sources, and UI triggers. It also includes a
per-table summary of the database schema so you can trace how data flows from
ingest to display.

## Agent Instructions

- Keep comments extensive and in the format currently used.
- Always update function descriptions and keep them in the format currently used.
- Update `README.md` whenever any changes are made, maintaining the current README format.
- Reference `Web Application System Design.pdf` when answering questions and performing updates.

## compose.yaml

### server
- Purpose: Existing application service built from the repository Dockerfile.
- Reads/Writes: receives `DATABASE_URL` and `FLASK_SECRET_KEY` from Compose so the runtime DB connection and Flask secret handling become environment-driven.
- Depends on: `db` with health condition so PostgreSQL is started before the app container, and `redis` with health condition so authentication rate-limit storage is available before the web app starts.
- Exposes: host port `${APP_HOST_PORT}` to the Gunicorn web process listening on container port `8000`.
- Notes: the SQLAlchemy engine already prefers `DATABASE_URL`, so Compose now controls whether the app runs against PostgreSQL and the SQLite runtime path is no longer used. The Dockerfile starts Gunicorn with `src.main:app`, which matches the Flask WSGI entry point used on the remote server. `FLASK_SECRET_KEY` must be passed explicitly into the container because Compose variable substitution alone does not make an env value available to `os.environ` inside the running Flask process.

### upload-text sanitizing
- Purpose: `POST /api/v1/upload-text` now strips embedded NUL (`0x00`) bytes from the uploaded raw text before parsing it and before storing it in `track_hist.raw_txt`.
- Why: PostgreSQL text columns reject embedded NUL bytes, so this keeps the text-upload path safe even if a device log contains restart-related corruption.
- Scope: only the unsupported NUL bytes are removed; the rest of the raw uploaded text is preserved.

### parse-worker
- Purpose: Background parser service that polls `ingest_raw`, converts fixes into `points`, and marks raw rows as processed.
- Reads/Writes: receives the same `DATABASE_URL` as the web app so it operates on the shared PostgreSQL database.
- Depends on: `db` with health condition so parsing only starts after PostgreSQL is ready.
- Command: `python -m src.workers.parse_worker`.
- Notes: runs as a standalone long-lived Compose service so parsing can be verified independently from the web process during the PostgreSQL stack test.

### gpx-worker
- Purpose: Background GeoJSON cache service that polls `points`, resolves the linked `race_rider`, and refreshes `track_cache`.
- Reads/Writes: receives the same `DATABASE_URL` as the web app so it operates on the shared PostgreSQL database.
- Depends on: `db` with health condition so cache generation only starts after PostgreSQL is ready.
- Command: `python -m src.workers.gpx_worker`.
- Notes: runs as a standalone long-lived Compose service so the live track cache path can be validated against empty PostgreSQL before data migration.

### rfid-worker
- Purpose: Background RFID timing service that polls `ingest_rfid`, resolves EPC tags to devices, and updates `race_riders` start/finish timing fields.
- Reads/Writes: receives the same `DATABASE_URL` as the web app so it operates on the shared PostgreSQL database.
- Depends on: `db` with health condition so RFID processing starts after PostgreSQL is ready.
- Command: `python -m src.workers.rfid_worker`.
- Notes: polls every 30 seconds, marks each RFID ingest row as processed, stores skip/error reasons on false reads, sets `race_riders.multiple_rfid_flag` when a read cannot be grouped into start/finish windows, and ignores further finish reads after `finish_time_rfid_confirmed` is set.

### db
- Purpose: PostgreSQL service introduced for the staged SQLite-to-PostgreSQL migration before remote deployment.
- Image: `postgres:18`.
- Persists: named volume `postgres-data` mounted at `/var/lib/postgresql`, with the real Docker volume name driven by `POSTGRES_VOLUME_NAME`.
- Auth: reads `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` from the active environment file for the selected stack.
- Exposes: container port `5432` to other Compose services.
- Healthcheck: uses `pg_isready` with the same env-driven database name and user so Compose can tell when the database is actually ready to accept connections.
- Notes: this follows the system design direction of SQLite for local development/prototyping and PostgreSQL for the remote/server-style deployment. With `postgres:18`, the working mount target is `/var/lib/postgresql`. Using separate `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_VOLUME_NAME` values for dev and prod keeps the two environments fully isolated, which matches the environment separation expected by `Web Application System Design V4 - 20260224.pdf`.

### redis
- Purpose: In-memory Redis service used by Flask-Limiter to store short-lived authentication rate-limit counters.
- Image: `redis:8-alpine`.
- Exposes: container port `6379` to other Compose services only.
- Healthcheck: uses `redis-cli ping` so Compose can tell when Redis is ready before starting the web app.
- Notes: no named persistence volume is configured because these counters are temporary. Losing Redis state restarts the rate-limit windows, but does not lose application data.

### cloudflared
- Purpose: run a containerised, remotely-managed Cloudflare Tunnel connector inside the same Compose network as the app stack.
- Image: `cloudflare/cloudflared:latest`.
- Command: `tunnel --no-autoupdate run --token ${TUNNEL_TOKEN}`.
- Depends on: `server` so the connector only starts after the web service container is created.
- Exposes: no host ports; the connector makes outbound connections to Cloudflare only.
- Notes: the public hostname mapping is managed in the Cloudflare dashboard, while the container only needs the environment-specific tunnel token. Because `cloudflared` lives in the same Docker network, the tunnel target remains `http://server:8000` even when dev and prod publish different host ports on the same machine.

## compose.debug.yaml

### Debug workflow override
- Purpose: override the base Compose stack so the web service runs with Flask debug mode while still using the same PostgreSQL-backed services as the normal runtime stack.
- Web service behavior: bind-mounts the repository into `/app`, replaces Gunicorn with `flask --app src.main:app run --debug --host=0.0.0.0 --port=8000`, and keeps the published port at `8000`.
- Worker behavior: bind-mounts the repository into `/app` for `parse-worker`, `gpx-worker`, and `rfid-worker` so a worker restart picks up local code changes without rebuilding the image.
- One-command debug start:
```bash
docker compose -f compose.yaml -f compose.debug.yaml up --build
```
- Notes: this keeps Docker Compose as the source of truth for PostgreSQL, environment variables, and service wiring while making the web process easier to debug during development.

## Public Domain Workflow

### Current public environment exposure
- Purpose: expose the Dockerised application through Cloudflare without publishing the laptop or remote server directly to the internet.
- Stack shape: public browser request -> Cloudflare edge -> remotely-managed Cloudflare Tunnel -> `cloudflared` container -> `server:8000` on the Docker network.
- Notes: this fits the remote/public access direction in `Web Application System Design V4 - 20260224.pdf`, while keeping the origin host behind Cloudflare and avoiding direct inbound exposure.

### Why this setup is used
- Hardware / host machine: the physical laptop or remote server running the Docker stack.
- Public access needs DNS: the purchased domain must point to Cloudflare so Cloudflare can proxy the public hostname.
- CGNAT note: the local internet connection uses CGNAT, so the host cannot simply publish its own public IP directly to the internet.
- Cloudflare role: Cloudflare acts as the public-facing proxy and forwards requests through a secure outbound tunnel started by the `cloudflared` container.
- Registrar vs DNS note: the registrar manages domain ownership, while Cloudflare becomes the DNS authority after the nameserver change.

### Remotely-managed tunnel layout
- Dev hostname: `dev.kooksnylive.co.za`.
- Prod hostname: `app.kooksnylive.co.za`.
- Dev and prod each use a separate Cloudflare Tunnel token.
- The tunnel ingress for both environments targets `http://server:8000` because `cloudflared` runs in the same Compose network as the Flask service.
- If dev and prod run on the same host, the two stacks must publish different host ports even though the internal tunnel target stays `server:8000`.

### Environment-specific Compose stacks
- Bring up dev:
```bash
docker compose -p enduro-dev --env-file .env.dev up -d
```
- Bring up prod:
```bash
docker compose -p enduro-prod --env-file .env.prod up -d
```
- Why project names matter: without `-p`, Compose treats repeated `up` commands from the same repository as the same stack, so the second run replaces the first stack's containers, tunnel token, port mapping, and database volume wiring.

### Same-machine dev/prod note
- If dev and prod run on the same Docker host, use different values for `APP_HOST_PORT`, `APP_HOSTNAME`, `TUNNEL_TOKEN`, database credentials, and `POSTGRES_VOLUME_NAME`.
- The host ports may differ, for example `8000` for dev and `8001` for prod, but the Cloudflare public hostname configuration should still point to `http://server:8000`.

### Public hostname troubleshooting note
- Cloudflare error `1033` usually indicates a public-hostname-to-tunnel routing problem rather than an app crash.
- When the tunnel connector is healthy but the site still shows `1033`, check that the correct public hostname is attached to the correct tunnel and that the DNS record points at the intended `cfargotunnel.com` target.

### References used for this setup
- Cloudflare add-site / nameserver handoff: `https://developers.cloudflare.com/fundamentals/manage-domains/add-site/`
- Cloudflare Tunnel setup: `https://developers.cloudflare.com/tunnel/setup/`
- Cloudflare remotely-managed tunnel creation: `https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/`
- Cloudflare error `1033`: `https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/`

## .env Files

### .env.dev / .env.prod runtime variables
- Purpose: environment-specific source files for Compose interpolation and runtime container settings.
- Reads/Writes: read by Docker Compose at startup time; should remain ignored by git because they contain secrets and tokens.
- Core PostgreSQL values: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_VOLUME_NAME`.
- Public access values: `TUNNEL_TOKEN`, `APP_HOSTNAME`, and `APP_HOST_PORT`.
- Flask secret values: `FLASK_SECRET_KEY` must be passed into the `server` container explicitly through the Compose `environment` section so Flask can read it through `os.environ`.
- Map values: `MAP_PROVIDER`, `MAP_STYLE`, `ARCGIS_API_KEY`, and the map-limit variables must also be passed explicitly into the `server` container. Flask uses the provider/style/key only to render the public post-race map configuration; later usage controls will read the limits server-side. The referrer-restricted Esri browser API key is intentionally exposed only on the post-race page and must never be committed.
- Auth email and security values: `RESEND_API_KEY`, `MAIL_FROM`, `APP_PUBLIC_BASE_URL`, `AUTH_TOKEN_PEPPER`, `AUTH_PASSWORD_MIN_LENGTH`, `AUTH_RATE_LIMIT_STORAGE_URL`, `SESSION_COOKIE_SECURE`, and `SESSION_COOKIE_SAMESITE` are passed into the `server` container for the authentication workstream. Resend is used only for forgot-password reset links in the current plan; signup email verification is intentionally not enabled.
- Notes: changing PostgreSQL bootstrap variables on an already-initialised volume does not reconfigure an existing database cluster. Clean separation requires a fresh volume name per environment or an explicit manual database/user migration.

## Python Requirements

### Authentication and security packages
- Purpose: Add the package baseline for the viewer/rider/admin authentication workstream described by `Web Application System Design V4 - 20260224.pdf`.
- Packages: `Flask-Login` for browser login sessions, `Flask-WTF` for form handling and CSRF protection, `Flask-Limiter` for rate limiting sensitive auth routes, `redis` for shared rate-limit storage, `email-validator` for signup/reset email validation, and `resend` for forgot-password email delivery.
- Notes: Resend is only used for password-reset emails in the current plan. Signup email verification is intentionally not enabled.

## src/main.py

### create_app
- Purpose: Flask application factory that creates the app instance, loads the Flask secret key, configures browser security helpers, and attaches all API and web blueprints.
- Reads: `FLASK_SECRET_KEY`, the `MAP_*` map configuration values, `ARCGIS_API_KEY`, and the auth email/security configuration values from the container runtime environment; `config.yaml` for host and port globals; `src.auth.login.login_manager` for browser session setup; `src.auth.rate_limits` for Redis-backed rate limiting; `src.auth.csrf` helpers for CSRF setup; `src.auth.routes.bp_auth` for signup and future auth pages; `src.web.rider_profiles.bp_rider_profiles` for the future rider profile page.
- Writes: `app.config["SECRET_KEY"]` plus secure session-cookie settings, the map provider, style, browser API key, map-limit configuration values, and `AUTH_RATE_LIMIT_STORAGE_URL`; initialises Flask-Login, Flask-Limiter, and Flask-WTF CSRF protection on the app.
- Registers: ingest API routes, auth browser routes, home/dashboard routes, public rider profile routes, rider management, devices, races, and RFID record viewer blueprints.
- Called from: module import path `src.main:app` for Gunicorn, and the direct-run block at the bottom of the file.
- Notes: the app now expects `FLASK_SECRET_KEY` to exist in the container environment. If Compose does not pass that value into the `server` service, Gunicorn fails during import with `KeyError: 'FLASK_SECRET_KEY'`. Browser form blueprints are CSRF-protected. The tracker ingest blueprint remains CSRF-exempt because it is used by device/API clients rather than browser-session forms. Runtime table creation is intentionally disabled; run Alembic migrations before starting the server or workers.

### CORS policy
- Purpose: Keep Cross-Origin Resource Sharing disabled by default for the server-rendered web app.
- Behavior: the application no longer calls global `CORS(app)` and no longer depends on `flask-cors`.
- Why: current browser requests use same-origin relative URLs, while tracker/device uploads are direct HTTP client calls and do not depend on browser CORS. Removing broad CORS avoids unnecessary cross-origin browser access once cookie-based login is introduced.
- When to reintroduce: add a narrow CORS helper only if a separate browser frontend on another origin must call selected API endpoints, for example `https://dashboard.kooksnylive.co.za` calling `https://api.kooksnylive.co.za/api/v1/races`. In that case, restrict allowed origins and keep credentials disabled unless there is a deliberate cookie-authenticated cross-origin design.

## src/auth/__init__.py

- Purpose: Mark `src.auth` as the authentication helper package for browser users.
- Reads: None.
- Writes: None.
- Functions: None.
- Notes: auth route modules and helper modules live under this package so the viewer/rider/admin authentication workstream remains grouped together, matching the role split described in `Web Application System Design V4 - 20260224.pdf`.
- Checks: importing `src.auth` succeeds and has no side effects.

## src/auth/login.py

### AUTH_VERSION_SESSION_KEY
- Purpose: Single Flask session key used to store the authenticated user's `auth_version`.
- Reads: None.
- Writes: None.
- Called from:
  - `src.auth.login:remember_auth_version`.
  - `src.auth.login:clear_auth_version`.
  - `src.auth.login:load_user`.
- Notes: keeping the key centralised avoids mismatches between login, logout, and session-validation code.

### login_manager
- Purpose: Shared Flask-Login manager that configures browser login session handling for the application.
- Reads: none at definition time.
- Writes: Flask-Login settings including the future login endpoint `auth.login`, the login-required message, and the login message category.
- Called from:
  - `src.main:create_app`, where `login_manager.init_app(app)` attaches the manager to the Flask app.
- Notes: the actual `/login` route is added in a later auth step. No existing routes are protected by this change yet.

### remember_auth_version
- Purpose: Store the user's current database `auth_version` in the signed Flask session after successful login.
- Reads: `user.auth_version`.
- Writes: `auth_version` into the Flask session cookie.
- Returns: None.
- Called from:
  - Future login route immediately after Flask-Login's `login_user(user)`.
- Notes: this enables old browser sessions to be rejected after password resets, forced logouts, or sensitive account changes.

### clear_auth_version
- Purpose: Remove the stored `auth_version` from the Flask session.
- Reads: Flask session.
- Writes: removes the `auth_version` session value when present.
- Returns: None.
- Called from:
  - Future logout and forced-session-clear flows.

### load_user
- Purpose: Load the current browser user from the Flask session id.
- Reads: `User`, `SessionLocal`, and the signed Flask session `auth_version`.
- Writes: None.
- Returns: active User row when the id and session `auth_version` are valid, otherwise `None`.
- Called from:
  - Flask-Login during request handling when a browser session contains a user id.
- Notes: returning `None` makes Flask-Login treat the request as anonymous. That cleanly redirects anonymous, deleted, inactive, or stale-session users to the configured login route when they access protected pages. The actual login route will separately deny inactive-account login attempts with a clear message.

### Checks
- `load_user()` returns a user for a valid active user with matching `auth_version`.
- Invalid ids, missing users, inactive users, missing session `auth_version`, and mismatched `auth_version` return None.
- Anonymous, invalid, inactive, and stale sessions redirect to the configured login route once a protected route is accessed.

## src/auth/csrf.py

### csrf
- Purpose: Shared Flask-WTF `CSRFProtect` instance for protecting browser forms and JSON POST requests from Cross-Site Request Forgery.
- Reads: Flask app configuration after `init_csrf(app)` is called.
- Writes: CSRF request hooks and template helpers onto the Flask application.
- Called from:
  - `src.auth.csrf:init_csrf`.

### init_csrf
- Purpose: Attach Flask-WTF CSRF protection to the Flask application.
- Reads: Flask application instance.
- Writes: CSRF middleware/hooks onto the Flask app.
- Returns: None.
- Called from:
  - `src.main:create_app`.

### exempt_blueprints
- Purpose: Exempt non-browser-session blueprints from CSRF enforcement.
- Reads: Flask Blueprint objects passed by `src.main:create_app`.
- Writes: CSRF exemption registrations for those blueprints.
- Returns: None.
- Called from:
  - `src.main:create_app`.
- Notes: browser form blueprints should normally stay protected. The current exemption is for tracker/device ingest, which should use device/API authentication instead of CSRF.

### Checks
- Flask app starts with CSRF protection initialised.
- Browser POST forms include hidden `csrf_token` values.
- Browser JavaScript POST requests to protected race routes send `X-CSRFToken`.
- Tracker ingest remains operational while CSRF-exempt.
- CSRF-less submissions to protected browser routes fail.

## src/auth/passwords.py

### validate_password
- Purpose: Validate password strength and optional confirmation matching for signup and reset-password forms.
- Reads: raw submitted password and optional confirmation value.
- Writes: None.
- Returns: list of validation error strings; an empty list means the password passed.
- Policy: minimum six characters and at least one number or special character; confirmation must match when supplied.
- Notes: six characters is intentionally the current MVP policy but should be increased before public release.

### hash_password
- Purpose: Convert a raw password into a Werkzeug password hash before database storage.
- Reads: raw submitted password.
- Writes: None.
- Returns: hash string suitable for `users.password_hash`.
- Notes: plaintext passwords must never be stored in the database.

### check_password
- Purpose: Verify a submitted password against the stored password hash during login.
- Reads: stored `users.password_hash` value and raw submitted password.
- Writes: None.
- Returns: True when the password matches; False otherwise.

### Checks
- Valid password passes validation.
- Too-short password fails.
- Password without a number or special character fails.
- Confirmation mismatch fails.
- Stored hash is not equal to plaintext.
- Correct password verifies against the stored hash.
- Wrong password fails verification.

## src/auth/tokens.py

### generate_raw_token
- Purpose: Generate a random URL-safe token for password-reset links.
- Reads: None.
- Writes: None.
- Returns: raw token string to email to the user.
- Notes: raw tokens must never be stored directly in the database.

### hash_token
- Purpose: Convert a raw token into a peppered SHA-256 hash for database storage.
- Reads: raw token and `AUTH_TOKEN_PEPPER` from the environment.
- Writes: None.
- Returns: hex digest stored in `auth_tokens.token_hash`.

### create_auth_token
- Purpose: Create a new one-time auth token row for a user and return the raw token for emailing.
- Reads: active SQLAlchemy session, target user, token purpose, and expiry duration.
- Writes: invalidates older unused tokens for the same user/purpose, then inserts a new `AuthToken` row containing only the token hash.
- Returns: raw token string for the reset link.
- Notes: currently used for `password_reset`, but the `purpose` field keeps the helper reusable.

### find_valid_token
- Purpose: Validate a submitted raw token from a reset link.
- Reads: active SQLAlchemy session, submitted raw token, expected purpose, `auth_tokens`, and `AUTH_TOKEN_PEPPER`.
- Writes: None.
- Returns: matching `AuthToken` row when the token exists, has the expected purpose, has not been used, and has not expired; otherwise None.

### mark_token_used
- Purpose: Consume a token after successful use.
- Reads: `AuthToken` row.
- Writes: sets `used_at` to the current UTC time.
- Returns: None.

### invalidate_existing_tokens
- Purpose: Cancel older unused tokens for the same user and purpose before issuing a new one.
- Reads: active SQLAlchemy session, `user_id`, purpose, and `auth_tokens`.
- Writes: sets `used_at` on matching unused tokens.
- Returns: number of token rows invalidated.

### Checks
- Raw token is never stored.
- Token hash is stored.
- Expired token fails validation.
- Used token fails validation.
- Wrong-purpose token fails validation.
- Creating a new token invalidates earlier unused tokens for the same user and purpose.

## src/auth/mail.py

### build_password_reset_url
- Purpose: Build the public password-reset URL from `APP_PUBLIC_BASE_URL` and the raw reset token.
- Reads: `APP_PUBLIC_BASE_URL` and raw reset token.
- Writes: None.
- Returns: fully-qualified reset URL such as `https://dev.kooksnylive.co.za/reset-password/<token>`.
- Notes: the URL is built from trusted environment configuration through `src.utils.env:required_env`, not the inbound request host.

### send_email
- Purpose: Send one transactional email through Resend.
- Reads: `RESEND_API_KEY`, `MAIL_FROM`, recipient email address, subject, and HTML body.
- Writes: one email through Resend.
- Returns: Resend API response.
- Notes: the API key is never printed.

### send_password_reset_email
- Purpose: Send a password-reset link to `user.email`.
- Reads: User row email address and raw reset token.
- Writes: one password-reset email through `send_email`.
- Returns: Resend API response.
- Notes: the current password is never emailed, and callers should not log reset links because they contain the raw token.

### Checks
- Password-reset URL uses `APP_PUBLIC_BASE_URL` rather than the inbound request host.
- Missing email configuration fails with a clear `RuntimeError`.
- Resend API key is not printed.
- Manual Resend smoke test sends successfully when `RESEND_API_KEY`, `MAIL_FROM`, and `TEST_EMAIL_TO` are configured.

## src/auth/routes.py

Module header documents:
- `GET/POST /signup`
- `GET/POST /login`
- `POST /logout`
- `GET/POST /forgot-password`
- `GET/POST /reset-password/<token>`
- `GET /admin/users`

Module notes:
- Viewers remain anonymous and do not have `User` rows.
- Public signup always creates `role='rider'`.
- Forgot-password and reset-password flows do not reveal whether an email exists and never email/store the existing password.
- `/admin/users` is currently an admin-only placeholder route protected with `admin_required`.

### bp_auth
- Purpose: Flask Blueprint for browser authentication routes.
- Reads: None at definition time.
- Writes: route registrations for signup and future login/logout/password-reset pages.
- Called from:
  - `src.main:create_app`, where the blueprint is registered on the Flask app.
- Notes: all auth routes live in this module rather than `src/web` so authentication remains grouped in one place.

### _normalize_auth_value
- Purpose: Normalise usernames and email addresses for case-insensitive uniqueness checks.
- Reads: raw submitted username or email value.
- Writes: None.
- Returns: lowercase, trimmed string.

### _signup_form_data
- Purpose: Read signup form fields into a template-friendly dictionary.
- Reads: `request.form` values for name, surname, username, and email.
- Writes: None.
- Returns: dictionary of non-password signup fields.
- Notes: password values are intentionally not returned to the template after validation errors.

### _validate_signup_form
- Purpose: Validate required signup fields and password policy.
- Reads: signup form dictionary, raw password, password confirmation, and `email-validator` format checks.
- Writes: None.
- Returns: list of validation error messages.
- Calls:
  - `src.auth.passwords:validate_password`.

### _signup_identity_exists
- Purpose: Check whether the submitted username or email is already registered.
- Reads: active SQLAlchemy session, `users.username_normalized`, and `users.email_normalized`.
- Writes: None.
- Returns: True when either identity value already exists.

### _login_form_data
- Purpose: Read login form values into a template-friendly dictionary.
- Reads: `request.form` value for username/email identifier.
- Writes: None.
- Returns: dictionary containing the non-password login identifier.
- Notes: password values are intentionally not returned to the template after validation errors.

### _find_login_user
- Purpose: Find a login account by username or email.
- Reads: active SQLAlchemy session, `users.username_normalized`, and `users.email_normalized`.
- Writes: None.
- Returns: matching User row when found; otherwise None.
- Notes: callers must use the same generic error for missing users and wrong passwords so login does not reveal whether an email or username exists.

### _find_active_user_by_email
- Purpose: Find an active user account by email for password reset.
- Reads: active SQLAlchemy session and `users.email_normalized`.
- Writes: None.
- Returns: active User row when found; otherwise None.
- Notes: callers always render the same forgot-password response whether this returns a user or None.

### _forgot_password_form_data
- Purpose: Read forgot-password form values into a template-friendly dictionary.
- Reads: `request.form` value for recovery email.
- Writes: None.
- Returns: dictionary containing the submitted recovery email.

### _reset_password_form_data
- Purpose: Read reset-password form values.
- Reads: `request.form` values for password and password confirmation.
- Writes: None.
- Returns: dictionary containing the new password and confirmation.

### _render_forgot_password_response
- Purpose: Render the standard forgot-password response.
- Reads: template form dictionary.
- Writes: None.
- Returns: rendered `forgot_password.html`.
- Notes: this response is deliberately reused for existing and non-existing email addresses so the route does not reveal whether an account exists.

### signup
- Purpose: Render the public rider signup form and create a rider login account on POST.
- Reads: signup form fields, `User`, `Rider`, password helpers, and the active database session.
- Writes: one linked `Rider` row and one linked `User` row with `role='rider'`; writes Flask-Login session data and the session `auth_version` after successful signup.
- Returns: rendered `signup.html` for GET or validation errors; redirects to the existing rider edit form on success.
- Notes: public signup ignores any submitted role/admin field and always creates a rider account. Later this success redirect should point to a dedicated rider/profile route that only allows the rider to edit their own linked profile.

### login
- Purpose: Render the login form and authenticate rider/admin users by username or email.
- Reads: username/email identifier, password, `User`, password hash checker, and the active database session.
- Writes: `last_login_at` on successful login; writes Flask-Login session data and session `auth_version`.
- Returns: rendered `login.html` for GET or failed login; redirects riders to `/dashboard` and admins to `/dashboard-admin` on success.
- Rate limit: POST requests are limited through Flask-Limiter.
- Notes: wrong username/email and wrong password use the same generic message: `Username/email or password is incorrect.` Inactive users are denied with a clear inactive-account message.

### logout
- Purpose: Clear the current browser login session.
- Reads: current Flask-Login session.
- Writes: removes stored session `auth_version` and logs the user out.
- Returns: redirect to `/login`.
- Notes: route accepts POST only and remains CSRF-protected.

### forgot_password
- Purpose: Start the password-reset flow by accepting a recovery email address.
- Reads: submitted email, active `User` row when present, auth-token helpers, and Resend mail helper.
- Writes: for active accounts only, invalidates old reset tokens, creates one new hashed 30-minute reset token, and sends a reset email.
- Returns: rendered `forgot_password.html` with the same success message whether or not the email belongs to an active account.
- Rate limit: POST requests are limited through Flask-Limiter.
- Notes: the current password is never emailed, and the raw reset token is never logged or stored directly.

### reset_password
- Purpose: Complete a one-use password reset from an emailed reset link.
- Reads: raw reset token from the URL, submitted new password, `AuthToken`, and linked `User`.
- Writes: new password hash, increments `user.auth_version`, updates `updated_at`, and marks the reset token used.
- Returns: rendered `reset_password.html` for invalid/expired/reused links or validation errors; redirects to `/login` after a successful reset.
- Rate limit: POST requests are limited through Flask-Limiter.
- Notes: incrementing `auth_version` invalidates existing browser sessions for that user.

### user_management
- Purpose: Placeholder for future admin user management.
- Reads: None.
- Writes: None.
- Returns: rendered `placeholder.html`.
- Route: `/admin/users`.
- Access: active admin account through `admin_required`.
- Notes: later this route will allow admins to view users, update roles, activate/deactivate accounts, and reset relevant account flags.

### Checks
- Public signup cannot create admin accounts.
- Successful signup creates a linked `Rider` row immediately.
- Successful signup creates a linked `User` row with `role='rider'`.
- Successful signup logs the new user in and stores the session `auth_version`.
- Successful signup redirects to `/riders/<rider_id>/edit` so the rider can complete their profile.
- Duplicate username/email submissions return a validation error.
- Invalid email format returns a validation error.
- Signup form is CSRF-protected and includes a hidden `csrf_token`.
- Login works with either username or email.
- Login sends riders to `/dashboard` and admins to `/dashboard-admin`.
- Login page links to `/forgot-password`.
- Logout clears the login session and session `auth_version`.
- Protected pages redirect to login after logout.
- Login does not reveal whether a username/email exists for wrong credentials.
- Forgot-password page always shows the same response for existing and non-existing emails.
- Forgot-password sends a reset link only for active accounts.
- Reset links are one-use and fail after expiry or reuse.
- Password reset increments `auth_version` so existing sessions stop working.
- Reset token is never logged or stored directly.

## src/web/rider_profiles.py

Module header documents:
- `GET /rider`

### bp_rider_profiles
- Purpose: Flask Blueprint for public rider profile pages.
- Reads: None at definition time.
- Writes: route registration for `/rider`.
- Called from:
  - `src.main:create_app`, where the blueprint is registered on the Flask app.

### rider_profiles
- Purpose: Placeholder for the future public rider profiles page.
- Reads: None.
- Writes: None.
- Returns: rendered `placeholder.html`.
- Route: `/rider`.
- Notes: later this page will list rider profiles and expose edit controls only to the linked rider or admins.

## src/auth/decorators.py

### user_has_role
- Purpose: Check whether a user object is authenticated, active, and assigned to an allowed role.
- Reads: user object attributes `is_authenticated`, `is_active`, and `role`.
- Writes: None.
- Returns: True when the user has one of the allowed roles; otherwise False.
- Notes: this helper keeps role comparison logic in one place for rider/admin route decorators.

### user_can_access_rider_resource
- Purpose: Check whether a user can access a resource owned by a Rider row.
- Reads: current user role, active/authenticated state, and linked `user.rider_id`.
- Writes: None.
- Returns: True for admins, or for riders whose linked Rider id matches the requested resource owner id.
- Called from:
  - `src.web.riders:_can_edit_rider`
  - `src.auth.decorators:require_rider_resource_access`
- Notes: this generic helper supports Rider profile ownership, RaceRider entry ownership, and future Rider-owned resources.

### require_rider_resource_access
- Purpose: Abort unless a user can access a Rider-owned resource.
- Reads: current user role and linked `user.rider_id`.
- Writes: None.
- Returns: None when allowed.
- Raises: 403 Forbidden when the user is not an admin and does not own the linked Rider resource.
- Called from:
  - `src.web.races:edit_race_rider`
  - `src.web.races:remove_race_rider`

### active_user_required
- Purpose: Protect routes that require any logged-in active account.
- Reads: Flask-Login `current_user`.
- Writes: None.
- Returns: wrapped Flask route function.
- Called from:
  - Future account/profile routes that require login but do not care whether the user is a rider or admin.
- Notes: anonymous, deleted, inactive, and auth-version-stale sessions are redirected by Flask-Login to the configured login route because `load_user()` returns None for those cases.

### rider_required
- Purpose: Protect rider-level routes.
- Reads: Flask-Login `current_user` and the user's `role`.
- Writes: None.
- Returns: wrapped Flask route function.
- Called from:
  - Future rider profile and rider race-entry routes.
- Notes: allowed roles are `rider` and `admin`, because admins have the highest permission level.

### admin_required
- Purpose: Protect admin-only routes.
- Reads: Flask-Login `current_user` and the user's `role`.
- Writes: None.
- Returns: wrapped Flask route function.
- Called from:
  - Future race, rider, device, RFID, manual timing, user-management, and quota-admin routes.
- Notes: allowed role is `admin` only. Riders receive 403.

### Checks
- Anonymous user is redirected to login for protected routes.
- Rider is blocked from admin-only route with 403.
- Admin can access admin route.
- Admin can access rider route.

## src/auth/rate_limits.py

### limiter
- Purpose: Shared Flask-Limiter instance used to decorate authentication and other abuse-sensitive routes.
- Reads: client identity through Flask-Limiter's `get_remote_address` key function.
- Writes: rate-limit headers on responses when route limits are active.
- Called from:
  - Future auth routes via decorators such as `@limiter.limit(...)`.
- Notes: no default route limit is applied in the setup step, so existing pages are not throttled until specific limits are added.

### init_limiter
- Purpose: Attach Flask-Limiter to the Flask application using Redis-backed counter storage.
- Reads: `AUTH_RATE_LIMIT_STORAGE_URL` from Flask app config.
- Writes: Flask-Limiter config keys `RATELIMIT_STORAGE_URI` and `RATELIMIT_HEADERS_ENABLED`, then initialises the Limiter extension.
- Returns: None.
- Raises: `RuntimeError` when `AUTH_RATE_LIMIT_STORAGE_URL` is missing so the app does not silently fall back to per-process Python memory counters.
- Called from:
  - `src.main:create_app`.
- Notes: the expected storage URL is `redis://redis:6379/0` in Compose. The same Redis service can later support temporary map/tile usage counters.

### Checks
- Flask-Limiter initialises with Redis-backed storage from `AUTH_RATE_LIMIT_STORAGE_URL`.
- Missing storage URL fails clearly rather than silently falling back to per-process memory.
- No global route throttling is applied until route-specific limits are added.
- Future auth routes can apply limits to `/login`, `/signup`, `/forgot-password`, and `/reset-password/<token>`.

### Direct-run debug block
- Purpose: support direct local execution through `python src/main.py`.
- Reads: `API_HOST` and `API_PORT` from `config.yaml`.
- Writes: none.
- Called from: only when `src/main.py` is executed directly.
- Notes: the containerised runtime uses Gunicorn from the Dockerfile rather than this `app.run(...)` path, so the production deployment does not depend on Flask's built-in debug server.

## src/utils/env.py

### env_bool
- Purpose: Parse environment variable strings into booleans for Flask and worker configuration values.
- Reads: named environment variable.
- Writes: None.
- Returns: `True`, `False`, or the provided default when the value is missing/unrecognised.
- Called from:
  - `src.main:create_app` for `SESSION_COOKIE_SECURE`.

### required_env
- Purpose: Read required environment variables and fail clearly when they are missing or blank.
- Reads: named environment variable.
- Writes: None.
- Returns: stripped environment value.
- Raises: `RuntimeError` when the value is missing or blank.
- Called from:
  - `src.auth.mail` for `APP_PUBLIC_BASE_URL`, `RESEND_API_KEY`, and `MAIL_FROM`.
- Notes: shared environment helpers should live here so auth, map, worker, and future admin modules do not each create their own local parsing functions.

## Alembic Migration Workflow

### Current baseline
- Purpose: the active Alembic baseline is [438e4bd69220_baseline_schema.py](/home/matthew/Desktop/Master_Dev/Enduro_Tracker_WebApp/migrations/versions/438e4bd69220_baseline_schema.py), which can build the current PostgreSQL schema from an empty database.
- Notes: legacy pre-baseline revisions are kept in [migrations/versions_legacy](/home/matthew/Desktop/Master_Dev/Enduro_Tracker_WebApp/migrations/versions_legacy) for reference only and are no longer part of the active migration chain.

### Standard change process
- Step 1: edit [models.py](/home/matthew/Desktop/Master_Dev/Enduro_Tracker_WebApp/src/db/models.py) first because the SQLAlchemy models remain the schema source of truth.
- Step 2: generate a migration through Compose so Alembic uses the same runtime setup as the application:
```bash
docker compose run --rm -v "$PWD:/app" --entrypoint alembic server -c alembic.ini revision --autogenerate -m "your change"
```
- Step 3: review the new file in [migrations/versions](/home/matthew/Desktop/Master_Dev/Enduro_Tracker_WebApp/migrations/versions) before applying it.
- Step 4: apply the migration through Compose:
```bash
docker compose run --rm -v "$PWD:/app" --entrypoint alembic server -c alembic.ini upgrade head
```
- Step 5: verify the applied revision:
```bash
docker compose run --rm -v "$PWD:/app" --entrypoint alembic server -c alembic.ini current
docker compose exec db psql -U enduro_tracker -d enduro_tracker -c 'SELECT * FROM alembic_version;'
```

### Review guidance
- Check that `down_revision` points to the current head revision.
- Check that the generated diff only contains the schema changes you intended.
- Check for unexpected destructive actions such as `drop_table`, `drop_column`, or a rename being represented as drop-plus-create.
- If the change needs data backfill or data reshaping, add that logic manually to the generated migration file before running `upgrade head`.

### PostgreSQL-specific note
- Do not convert generated operations to `batch_alter_table()` by default.
- That batch pattern was mainly a SQLite compatibility workaround and is no longer the normal path for this PostgreSQL-first setup.
- If Alembic generates direct operations such as `op.create_unique_constraint(...)`, keep them unless there is a specific PostgreSQL problem to solve.

### Verification guidance
- Verify schema changes in PostgreSQL, not in a local `.db` file.
- Use `psql` schema inspection for the affected table after the migration, for example:
```bash
docker compose exec db psql -U enduro_tracker -d enduro_tracker -c '\d points'
```
- For risky or destructive migrations, test against a disposable PostgreSQL database before applying them to the main runtime database.

## src/web/home.py

Module header documents:
- `GET /`
- `GET /dashboard`
- `GET /dashboard-admin`

### _race_display_data
- Purpose: Load race rows and add display-friendly datetime values for dashboard templates.
- Reads: `Race`, `starts_at_epoch`, `active`, and configured categories from `config.yaml`.
- Writes: temporary `starts_at` display attribute on race objects before rendering.
- Returns: tuple of races and default configured category.
- Notes: `active_only=True` is used for the public dashboard; `active_only=False` is used for the admin dashboard.

### home_page (GET `/`)
- Purpose: Render the public landing page.
- Reads: None.
- Writes: None.
- Renders: `templates/landing.html`.
- Buttons: View Races, Sign Up, Login.
- Called from:
  - Direct public page load at `/`.

### dashboard (GET `/dashboard`)
- Purpose: Render the public race dashboard with active races only and no management controls.
- Reads: active `Race` rows and default configured category.
- Writes: None.
- Renders: `templates/dashboard.html`.
- Buttons: Landing, Login, Sign Up, and View Race for each active race.
- Notes: this is the viewer/rider public dashboard. It intentionally excludes edit race, add race, device, RFID, and other admin controls.

### dashboard_admin (GET `/dashboard-admin`)
- Purpose: Render the admin operational dashboard with management controls.
- Reads: all `Race` rows and default configured category.
- Writes: None.
- Renders: `templates/dashboard_admin.html`.
- Access: protected with `admin_required`.
- Buttons: Public Dashboard, Input Rider Details, Manage Devices, Add New Race, View RFID Records, Edit race, and Post Race.
- Notes: this page contains the operational controls that previously lived on `/`.

### Checks
- Anonymous users can load `/` and `/dashboard`.
- Anonymous users see no management controls on `/dashboard`.
- Anonymous users are redirected to login for `/dashboard-admin`.
- Riders are blocked from `/dashboard-admin` with 403.
- Admins can load `/dashboard-admin`.

## CSS Structure

### Recommended combined structure
- Purpose: Use a combined CSS structure so shared theme and component styles stay reusable while complex pages can keep page-specific layout rules close to their own templates.
- Structure:
```text
src/static/css/
  base.css
  forms.css
  tables.css
  maps.css
  home.css
  race-form.css
  post-race.css
  rfid.css
```
- Positives: balances reuse and page safety, keeps the navy/white/forest-green theme consistent, reduces duplicated form/table/map styling, and lets behavior-heavy pages such as `templates/race_form.html` and `templates/post_race.html` have targeted layout fixes without destabilising simpler pages.
- Negatives: requires discipline about where each rule belongs, can create ambiguity for reusable-but-page-flavoured rules such as filter grids, and means some templates may load more than one stylesheet.
- Rule: put theme tokens, typography, page shell, and baseline buttons in `base.css`.
- Rule: put reusable form controls, form grids, labels, messages, and input states in `forms.css`.
- Rule: put reusable table wrappers, wide-table behavior, cells, headings, and table action styling in `tables.css`.
- Rule: put reusable Leaflet/map containers and map sizing helpers in `maps.css`.
- Rule: put only home-page refinements in `home.css`.
- Rule: put only race form layout and behavior-sensitive styling in `race-form.css`.
- Rule: put only post-race layout, modal, live timing, and live map refinements in `post-race.css`.
- Rule: put only RFID page refinements in `rfid.css` when they are not reusable enough for `forms.css` or `tables.css`.

### Page stylesheet example
- Purpose: Load shared styles first and page-specific overrides last so the base theme remains consistent while each page can safely refine its own layout.
- Example:
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/base.css') }}" />
<link rel="stylesheet" href="{{ url_for('static', filename='css/forms.css') }}" />
<link rel="stylesheet" href="{{ url_for('static', filename='css/tables.css') }}" />
<link rel="stylesheet" href="{{ url_for('static', filename='css/maps.css') }}" />
<link rel="stylesheet" href="{{ url_for('static', filename='css/race-form.css') }}" />
```
- Notes: stylesheet order matters. Shared files should define the default look, while page files should only add or override what that page genuinely needs.

### Current base.css usage
- Purpose: Provide the lean shared static stylesheet for the Flask-rendered UI, currently applied to `templates/landing.html`, `templates/dashboard.html`, `templates/dashboard_admin.html`, `templates/placeholder.html`, `templates/login.html`, `templates/signup.html`, `templates/forgot_password.html`, `templates/reset_password.html`, `templates/riders_form.html`, `templates/devices.html`, `templates/device_edit.html`, `templates/rfid_view.html`, `templates/race_form.html`, and `templates/post_race.html`.
- Reads: CSS custom properties defined in `:root` for navy, white, forest green, neutral surfaces, borders, text, and shadows.
- Writes: Browser presentation only; no application data is changed.
- Styles: theme variables, page shell, page header, primary buttons, section titles, muted text, empty state, and mobile layout adjustments.
- Called from:
  - `templates/landing.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/dashboard.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/dashboard_admin.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/placeholder.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/login.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/signup.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/forgot_password.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/reset_password.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/riders_form.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/devices.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/device_edit.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/rfid_view.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/race_form.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/post_race.html`: linked through `url_for('static', filename='css/base.css')`.
- Notes: this CSS split follows the simple Flask web UI direction in `Web Application System Design V4 - 20260224.pdf`. Future work should move broad reusable rules out of `base.css` into component stylesheets and keep complex page-specific rules in their own page files.

### Shared component files
- Purpose: Provide reusable component stylesheets that are loaded after `base.css` by pages that need them.
- Current state: `forms.css`, `tables.css`, and `maps.css` exist under `src/static/css`; `templates/landing.html`, `templates/dashboard.html`, `templates/dashboard_admin.html`, `templates/placeholder.html`, `templates/login.html`, `templates/signup.html`, `templates/forgot_password.html`, `templates/reset_password.html`, `templates/riders_form.html`, `templates/devices.html`, `templates/device_edit.html`, `templates/rfid_view.html`, `templates/race_form.html`, and `templates/post_race.html` now load the relevant component files.
- `forms.css`: contains reusable content panels, form grids, filter grids, field rows, inputs, checkboxes, file inputs, focus states, status messages, and form action layout.
- `tables.css`: contains reusable table cards, table cells, wide-table behavior, table heading styling, table action buttons, `pre-wrap`, and `code` wrapping helpers.
- `maps.css`: contains reusable compact map preview container styling plus shared Leaflet map wrapper/canvas styling for route and track maps.
- Notes: `maps.css` is currently loaded by `templates/race_form.html` for the route preview map and `templates/post_race.html` for the route/track review map; Home, Riders, Devices, and RFID pages do not need it.

## JS Structure

### Recommended shared component and page structure
- Purpose: Use shared JavaScript components for browser behaviour that is genuinely reused, while keeping each page's DOM wiring and workflow-specific behaviour in a page file.
- Structure:
```text
src/static/js/
  components/
    maps.js
    forms.js
    polling.js
  pages/
    race-form.js
    post-race.js
```
- How it works: A template loads the component files it needs first, then loads its page file last. The page file reads DOM elements and `data-*` attributes, calls shared helpers, and owns event listeners for that page. For example, `race-form.js` can initialise the GPX route preview and rider/device auto-fill, while `post-race.js` can initialise route/track maps, live timing polling, track toggles, and the manual timing modal.
- How it works: Server-rendered values must remain in the template as HTML `data-*` attributes or `<script type="application/json">` blocks. External `.js` files should read those values from the DOM instead of containing Jinja expressions.
- Positives: keeps complex pages manageable, prevents duplication when map, form, or polling behaviour is reused, keeps page-specific workflow logic isolated, and follows the existing Flask server-rendered UI direction described in `Web Application System Design V4 - 20260224.pdf`.
- Negatives: requires clear ownership boundaries, introduces script load-order decisions, and can create unnecessary abstraction if a helper is extracted before a second real use case exists.
- Rule: put reusable Leaflet setup, GeoJSON rendering helpers, and map resize helpers in `components/maps.js` only when at least two pages need the same behaviour.
- Rule: put reusable DOM form helpers, input synchronisation helpers, and confirmation helpers in `components/forms.js` only when they are shared by multiple pages.
- Rule: put reusable interval, visibility, in-flight request, and fetch-refresh helpers in `components/polling.js` only when the behaviour is shared.
- Rule: put selectors, page initialisation, page-specific endpoint construction, and page-only event listeners in `pages/<page-name>.js`.
- Rule: load component files before their dependent page file, and use `defer` for local script tags so the DOM is available before initialisation.
- Rule: do not put Jinja syntax such as `{{ race.id }}` directly in an external `.js` file; expose the value through a `data-*` attribute or JSON data block instead.
- Rule: replace inline event attributes such as `onchange` and `onsubmit` with `addEventListener` calls in the relevant page file as each page is migrated.
- Rule: retain external library loading, such as Leaflet, in the template unless a future dependency-management approach is introduced.
- Rule: load Esri Leaflet and Esri Leaflet Vector only in `templates/post_race.html`. The post-race page is the sole planned satellite-imagery consumer; the race form remains Leaflet/OpenStreetMap-only and must not load those dependencies.
- Rule: create a shared component only after a second page needs the same stable behaviour; otherwise keep the code in the owning page file.

### Page script example
- Purpose: Load shared helpers first and page-specific behaviour last, matching the same layering used by the CSS structure.
- Example:
```html
<script defer src="{{ url_for('static', filename='js/components/forms.js') }}"></script>
<script defer src="{{ url_for('static', filename='js/components/maps.js') }}"></script>
<script defer src="{{ url_for('static', filename='js/pages/race-form.js') }}"></script>
```
- Notes: Not every page needs every component file. A page should load only the shared scripts it uses, followed by its own page script. When a shared component is introduced, it must be loaded before the dependent page script.

### Current JS usage
- Purpose: Record the current incremental JavaScript migration state.
- Current state: `src/static/js/components/forms.js`, `src/static/js/components/maps.js`, `src/static/js/pages/race-form.js`, and `src/static/js/pages/post-race.js` exist. Templates load their required component files before their page file.
- `components/forms.js`: contains shared `data-auto-submit` select handling used by the category controls in `templates/race_form.html` and `templates/post_race.html`.
- `components/maps.js`: contains shared Leaflet map creation, selected-category route fetching, GeoJSON layer creation, and map-bounds fitting used by the race form and post-race pages.
- `components/maps.js`: retains the OpenStreetMap base-layer helper and adds Esri satellite attach/remove helpers. The race form uses the existing OSM default. The post-race page creates an empty map, fits its selected route or rider track first, then attaches the configured basemap so it requests only tiles near the visible course. After fitting the selected route it limits panning and minimum zoom to bounds padded by 25% on every side. It will use the retained OSM layer whenever the supplied configuration does not allow satellite imagery.
- `pages/race-form.js`: contains race-form-only GPX upload validation and rider/device auto-fill behaviour. It uses the shared form/map helpers for category auto-submit and route preview. The GPX input uses native required-field validation so an empty upload is blocked before navigation even when JavaScript is unavailable; the script supplies the GPX-specific text for the browser validation popup. The script reads the race id and category from `#map` data attributes and the rider/device mapping from the `#last-device-by-rider-data` JSON data node.
- `pages/post-race.js`: contains post-race-only live track/timing polling, track overlay controls, map size preferences, finish confirmation, and the manual timing/TXT upload modal. It reads its browser-safe map configuration from `#post-race-map-config`, fits the route before attaching the base layer, and uses the shared provider helper for the satellite/OSM decision.
- Notes: `components/polling.js` does not exist yet because polling is currently used only by the post-race page. Move polling code there only when another page needs the same stable behaviour.
- External map dependencies: `templates/post_race.html` loads Leaflet 1.9.4, Esri Leaflet 3.0.19, and Esri Leaflet Vector 4.3.2 in that order. The Esri libraries make `L.esri.Vector.vectorBasemapLayer(...)` available for the later satellite-basemap implementation; loading them alone does not make an Esri request or replace the current OpenStreetMap layer.

## src/web/devices.py

### _list_devices
- Purpose: Helper to fetch all `Device` rows ordered by id.
- Reads: `Device`.
- Writes: None.
- Called from: `devices_index` only (internal helper).

### _epc_in_use
- Purpose: Check whether an RFID EPC is already assigned to another device.
- Reads: `Device.epc_id`.
- Writes: None.
- Returns: True when another device already uses the EPC.
- Called from: `devices_index` and `device_edit` only (internal helper).

### devices_index (GET/POST `/devices/`)
- Purpose: List devices (GET) and create a new device (POST).
- Reads: `Device` (list view).
- Writes: `Device` (new row on POST, including optional `epc_id`).
- Renders: `templates/devices.html`.
- Access: active admin account through `admin_required`.
- Called from:
  - `templates/dashboard_admin.html`: "Manage Devices" button (GET).
  - `templates/devices.html`: "Save" button in "Add a new device" form (POST).
  - `templates/device_edit.html`: "Back to Devices" link (GET).

### device_edit (GET/POST `/devices/<device_id>/edit`)
- Purpose: Edit `device_info` and optional `epc_id` for a specific device.
- Reads: `Device` (by id).
- Writes: `Device.device_info` and `Device.epc_id` (on POST).
- Renders: `templates/device_edit.html`.
- Access: active admin account through `admin_required`.
- Called from:
  - `templates/devices.html`: "Edit" link in devices table (GET).
  - `templates/device_edit.html`: "Save" button (POST).

## src/web/rfid.py

### _parse_optional_int
- Purpose: Parse an optional integer filter value from the RFID records query string.
- Reads/Writes: None.
- Returns: integer value or None for an empty input.
- Called from: `rfid_index` only (internal helper).

### _parse_limit
- Purpose: Parse and clamp the RFID records row limit.
- Reads: raw limit string from `request.args`.
- Writes: None.
- Returns: integer limit between 1 and 1000.
- Called from: `rfid_index` only (internal helper).

### rfid_index (GET `/rfid/`)
- Purpose: List recent `IngestRfid` rows with server-side column filters.
- Reads: `IngestRfid` rows filtered by optional `id`, `epc`, `reader_id`, `ant`, reader datetime range, received datetime range, and `limit`.
- Writes: None.
- Renders: `templates/rfid_view.html`.
- Access: active admin account through `admin_required`.
- Display: converts `time_stamp_epoch` and `received_at_epoch` to datetimes for the template table.
- Called from:
  - `templates/dashboard_admin.html`: "View RFID Records" button (GET).
  - `templates/rfid_view.html`: filter form and "Clear" link (GET).

## src/web/riders.py

### _validate_category
- Purpose: Helper to validate that a category is in the allowed list.
- Reads/Writes: None.
- Called from: `rider_form` only (internal helper).

### _is_rider_user
- Purpose: Check whether the current account is a rider account.
- Reads: current user role.
- Writes: None.
- Returns: True for rider users; otherwise False.
- Called from: `_rider_already_exists`, `_can_edit_rider`, and `rider_form`.

### _rider_already_exists
- Purpose: Check whether a rider user already has a linked Rider profile.
- Reads: `current_user.rider_id`.
- Writes: None.
- Returns: True when a rider account already has a linked Rider row.
- Called from: `rider_form`.
- Notes: this prevents normal riders from using `/riders/new` to create multiple Rider rows for the same login account.

### _can_edit_rider
- Purpose: Check whether the current user can edit a requested Rider row.
- Reads: current user role and `current_user.rider_id` through `user_can_access_rider_resource`.
- Writes: None.
- Returns: True for admins, or for riders editing their own linked Rider row.
- Called from: `rider_form`.

### rider_form (GET/POST `/riders/new` and `/riders/<rider_id>/edit`)
- Purpose: Create a new rider or edit an existing rider.
- Reads: `Rider` (list and optional row for editing), current login user, and linked `User.rider_id` when a rider creates their first profile.
- Writes: `Rider` (insert or update). When a rider creates their first profile, also writes `User.rider_id` and `User.updated_at`.
- Renders: `templates/riders_form.html`.
- Access: active rider or admin account through `rider_required`.
- Notes: admins can create and edit any Rider row. Riders can create one linked Rider row only, and can edit only their own linked Rider row. A rider who already has a linked profile is redirected from `GET /riders/new` to their own edit page.
- Called from:
  - `templates/dashboard_admin.html`: "Input Rider Details" button (GET `/riders/new`).
  - `templates/riders_form.html`: "Edit" link in riders table (GET `/riders/<id>/edit`).
  - `templates/riders_form.html`: "Save" button in the rider form (POST create/update).

## src/web/races.py

### _find_or_create_route_for_category
- Purpose: Helper to ensure a `(Route, Category)` pair exists for a race/category.
- Reads: `Route`, `Category`.
- Writes: `Route`, `Category` (creates rows if missing).
- Called from: `edit_race`, `upload_gpx`, `add_race_rider` (internal helper).

### _read_track_hist_geojson
- Purpose: Helper to return the latest `track_hist.geojson` for a `race_rider_id` scoped to the requested race.
- Reads: `TrackHist`, `RaceRider`, `Category`, `Route`.
- Writes: None.
- Returns: `geojson` string or `None` when not found.
- Called from: `race_rider_track` only (internal helper).

### _read_track_cache_geojson
- Purpose: Helper to return `track_cache.geojson` for a `race_rider_id` scoped to the requested race.
- Reads: `TrackCache`, `RaceRider`, `Category`, `Route`.
- Writes: None.
- Returns: `geojson` string or `None` when not found.
- Called from: `race_rider_track` only (internal helper).

### _race_rider_timing_payload
- Purpose: Helper to format one `RaceRider` timing row for templates and JSON polling.
- Reads: `RaceRider.start_time_rfid_epoch`, `RaceRider.finish_time_rfid_epoch`, `RaceRider.multiple_rfid_flag`, `RaceRider.finish_time_rfid_confirmed`.
- Writes: None.
- Returns: timing display strings, datetime-local input values, RFID warning flag suppressed by confirmed finishes, and RFID finish confirmation flag.
- Called from: `post_race` and `race_rider_timings` only (internal helper).

### new_race (GET `/races/new`)
- Purpose: Render the "New Race" page.
- Reads: Config categories.
- Writes: None.
- Renders: `templates/race_form.html`.
- Access: active admin account through `admin_required`.
- Called from:
  - `templates/dashboard_admin.html`: "Add New Race" button.

### post_race (GET `/races/<race_id>/post`)
- Purpose: Post-race view with route preview and rider list for a category.
- Reads: `Race`, `Route`, `Category`, `Rider`, `RaceRider` (epoch timing columns for display).
- Writes: None.
- Renders: `templates/post_race.html`.
- Display: converts rider timing epochs to naive local datetimes for UI controls.
- UI: map includes multi-select rider track overlays controlled from the compact legend beside race info (toggle state synced to active overlays, reselects replace prior overlays), persisted map height/width sliders, auto-stacking of the riders table under the map when widths clash, 5-second live refresh polling for selected rider tracks (cache-first), 5-second live refresh polling for rider timing cells, and a manual timing modal that can optionally upload a TXT log to `/api/v1/upload-text` before reapplying the chosen start/end window.
- Called from:
  - `templates/dashboard.html`: "View Race" button in active races table.
  - `templates/dashboard_admin.html`: "Post Race" button in races table.
  - `templates/post_race.html`: category `<select>` `onchange` (GET with `?category=`).

### enter_race (GET `/races/<race_id>/enter`)
- Purpose: Placeholder for future rider/admin race entry.
- Reads: None.
- Writes: None.
- Renders: `templates/placeholder.html`.
- Access: active rider or admin account through `rider_required`.
- Called from:
  - `templates/dashboard.html`: "Enter Race" button.
  - `templates/dashboard_admin.html`: "Enter Race" button.
- Notes: later this will allow riders to enter races, choose category, see approval status, and use automatic device assignment.

### post_race_admin (GET `/races/<race_id>/post-admin`)
- Purpose: Placeholder for future admin post-race controls.
- Reads: None.
- Writes: None.
- Renders: `templates/placeholder.html`.
- Access: active admin account through `admin_required`.
- Called from:
  - `templates/dashboard_admin.html`: "Post Admin" button.
- Notes: later this should receive the admin timing controls that currently live on the public post-race page.

### race_results (GET `/races/<race_id>/results`)
- Purpose: Placeholder for future official public race results.
- Reads: None.
- Writes: None.
- Renders: `templates/placeholder.html`.
- Called from:
  - `templates/dashboard.html`: "Results" button.
  - `templates/dashboard_admin.html`: "Results" button.
- Notes: later this will show official released results and provide rider GPX/result downloads.

### device_geojson (GET `/races/<race_id>/device/<device_id>/geojson`)
- Purpose: Build GeoJSON on demand for a device track (no persistence).
- Reads: `Point` via `build_geojson_for_device`.
- Writes: None.
- Returns: GeoJSON payload (JSON).
- Called from:
  - No current template usage; available for external preview calls.

### race_rider_track (GET `/races/<race_id>/race-rider/<race_rider_id>/track`)
- Purpose: Return stored GeoJSON track for a race rider.
- Reads: `TrackHist`, `TrackCache`, `RaceRider`, `Category`, `Route`.
- Writes: None.
- Behavior:
  - Default: prefers latest `track_hist`, falls back to `track_cache`.
  - With `?prefer_cache=1`: prefers `track_cache` first (used by live post-race polling), then falls back to `track_hist`.
- Returns: GeoJSON payload (JSON).
- Called from:
  - `templates/post_race.html`: rider track checkbox toggles and 5-second live polling for selected riders.

### race_rider_timings (GET `/races/<race_id>/race-rider-timings`)
- Purpose: Return live `RaceRider` start/end timing values for the post-race riders table.
- Reads: `RaceRider`, `Category`, `Route`.
- Writes: None.
- Query params: `category` optionally scopes results to the selected post-race category.
- Returns: JSON payload with current timing display strings, datetime-local input strings, `multiple_rfid_flag`, and `finish_time_rfid_confirmed`.
- Called from:
  - `templates/post_race.html`: 5-second polling refresh for start/end timing cells, RFID warning state, and confirmation button state.

### manual_times (POST `/races/<race_id>/race-rider/<race_rider_id>/manual-times`)
- Purpose: Overwrite start/finish times and rebuild a trimmed track snapshot.
- Reads: `RaceRider`, latest `TrackHist` (for raw text).
- Writes: `RaceRider.start_time_rfid_epoch`, `RaceRider.finish_time_rfid_epoch`, `RaceRider.finish_time_rfid_confirmed`, `RaceRider.multiple_rfid_flag`, new `TrackHist` row with `updated_at_epoch`.
- Returns: JSON status.
- Access: active admin account through `admin_required`.
- Timezone: inputs must be timezone-naive; values are assumed to be in the configured local timezone (`config.yaml` → `global.timezone`) and converted to UTC before saving.
- Called from:
  - `templates/post_race.html`: "Manual Edit" button opens modal, modal "Save" and "Upload TXT" trigger JS `fetch`.

### confirm_finish_time (POST `/races/<race_id>/race-rider/<race_rider_id>/confirm-finish`)
- Purpose: Confirm the current RFID finish timing after organiser review.
- Reads: `RaceRider`, `Category`, `Route`.
- Writes: `RaceRider.finish_time_rfid_confirmed=True` and `RaceRider.multiple_rfid_flag=False`.
- Returns: JSON status with the refreshed timing payload.
- Access: active admin account through `admin_required`.
- Called from:
  - `templates/post_race.html`: "Confirm" timing button next to manual edit.

### save_race (POST `/races/save`)
- Purpose: Create or update a `Race`.
- Reads: `Race` (when updating).
- Writes: `Race` (insert/update).
- Behavior: parses date/time inputs and converts them to epoch seconds using the configured timezone for naive input.
- Access: active admin account through `admin_required`.
- Redirects: to edit page for the saved race.
- Called from:
  - `templates/race_form.html`: "Save Changes" button.

### edit_race (GET `/races/<race_id>/edit`)
- Purpose: Edit page for race data, route upload, and rider assignments.
- Reads: `Race`, `Route`, `Category`, `Rider`, `Device`, `RaceRider`.
- Writes: `Route`/`Category` if missing for the selected category.
- Renders: `templates/race_form.html`.
- Access: active admin account through `admin_required`.
- Called from:
  - `templates/dashboard_admin.html`: "Edit" button in races table.
  - `templates/race_form.html`: category `<select>` `onchange` (GET with `?category=`).
  - Redirect from `save_race` after a successful save.

### upload_gpx (POST `/races/<race_id>/route/upload`)
- Purpose: Upload a GPX file and store both GPX and GeoJSON on `Route`.
- Reads: Uploaded file.
- Writes: `Route.gpx`, `Route.geojson`.
- Access: active admin account through `admin_required`.
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Upload GPX" button (file upload form).

### remove_gpx (POST `/races/<race_id>/route/remove`)
- Purpose: Remove GPX/GeoJSON for the selected category.
- Reads: `Route`.
- Writes: `Route.gpx = None`, `Route.geojson = None`.
- Access: active admin account through `admin_required`.
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Remove GPX" button.

### route_geojson (GET `/races/<race_id>/route/geojson`)
- Purpose: Provide GeoJSON for the selected category route (map preview).
- Reads: `Route.geojson`.
- Writes: None.
- Returns: GeoJSON payload (JSON).
- Called from:
  - `templates/race_form.html`: map preview JS `fetch` on page load.
  - `templates/post_race.html`: map preview JS `fetch` on page load.

### add_race_rider (POST `/races/<race_id>/riders/add`)
- Purpose: Add a rider/device entry to a race category.
- Reads: `Category` (via helper lookup).
- Writes: `RaceRider` (new row).
- Access: active admin account through `admin_required`.
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Save" button in the "Add new rider" row.

### edit_race_rider (POST `/races/<race_id>/riders/<race_rider_id>/edit`)
- Purpose: Update device assignment and flags for a race rider entry.
- Reads: `RaceRider`, `Category`, `Route`, and current login user.
- Writes: `RaceRider.device_id`, `RaceRider.active`, `RaceRider.recording`.
- Access: active rider or admin account through `rider_required`. Admins can edit any race entry; riders can edit only entries linked to their own `current_user.rider_id`.
- Notes: the route verifies that the `race_rider_id` belongs to the requested `race_id` before applying the ownership check.
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Edit" button in the riders table.

### remove_race_rider (POST `/races/<race_id>/riders/<race_rider_id>/remove`)
- Purpose: Remove a rider from the race category.
- Reads: `RaceRider`, `Category`, `Route`, and current login user.
- Writes: `RaceRider` (delete).
- Access: active rider or admin account through `rider_required`. Admins can remove any race entry; riders can remove only entries linked to their own `current_user.rider_id`.
- Notes: the route verifies that the `race_rider_id` belongs to the requested `race_id` before applying the ownership check.
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Remove" button (with confirm dialog) in the riders table.

## src/api/ingest.py

### upload (POST `/api/v1/upload`)
- Purpose: Ingest compact GNSS JSON and store a durable raw copy for background parsing.
- Reads: request JSON (`pid` and `f` array).
- Writes: `IngestRaw` (new row with `payload_json`, `received_at_epoch`).
- Returns: empty 200 on success; 400/422 on bad input; 500 on DB error.
- Called from:
  - External device/ingest clients (no template references).

### upload_timing (POST `/api/v1/upload-timing`)
- Purpose: Validate a timing marker (epoch/device/phase/source) and acknowledge it.
- Reads: request JSON (`epoch`, `device_id`, `phase`, `source`).
- Writes: None (persistence deferred).
- Returns: JSON ack with `accepted: true` or validation error.
- Called from:
  - External timing feeds or devices (no template references).

### upload_rfid (POST `/api/v1/upload-rfid`)
- Purpose: Ingest an RFID reader tag event, normalize the timestamp/RSSI fields, and store the event for later worker processing.
- Reads: form values (`epc`, `rssi`, `ant`, `timestamp`, `readerId`, `average_rssi`) from the RFID reader's `application/x-www-form-urlencoded` POST body.
- Writes: `IngestRfid` (new row with `epc`, `rssi`, `ant`, `time_stamp_epoch`, `reader_id`, `avg_rssi`, `received_at_epoch`).
- Returns: JSON ack with `accepted: true`, inserted `id`, `epc`, `time_stamp_epoch`, and `received_at_epoch`; 422 on missing/invalid `epc` or `timestamp`; 500 on DB error.
- Called from:
  - External RFID reader software using a URL template such as `/api/v1/upload-rfid?mode=1&rfid={EPC}&rssi={avgRSSI}&datestamp={latSeenStr}&id={readerId}`.

### upload_text (POST `/api/v1/upload-text`)
- Purpose: Ingest a raw text log, parse fixes, trim to RFID window (if available), and persist to track history.
- Reads: request JSON (`pid`, `log`); all `RaceRider` rows for the device and their epoch timing windows.
- Writes: `TrackHist` (new row with `geojson`, `gpx`, `raw_txt`, `updated_at_epoch`) for:
  - the latest `race_rider_id` (always),
  - any earlier `race_rider_id` that does not yet have a `TrackHist`.
- Returns: empty 200 on success; JSON error on invalid input.
- Called from:
  - Text-log upload clients (no template references).
  - `templates/post_race.html`: manual timing modal "Upload TXT" button.

## src/utils/gpx.py

### _iso8601_utc
- Purpose: Convert epoch seconds to ISO8601 UTC string for GPX timestamps.
- Reads/Writes: None.
- Returns: formatted string.
- Called from:
  - `_build_gpx_string`, `build_gpx_for_device`, `build_geojson_for_device`.

### _parse_text_fixes
- Purpose: Parse line-delimited JSON fixes; drop malformed or missing utc/lat/lon.
- Reads: raw text log lines.
- Writes: None.
- Returns: list of cleaned fix dicts (drops rows with missing lat/lon or zeroed lat/lon pair).
- Called from:
  - `src/api/ingest.py:upload_text`
  - `src/web/races.py:manual_times`

### _build_gpx_string
- Purpose: Build a GPX 1.1 XML string from cleaned fixes.
- Reads: in-memory fixes list.
- Writes: None.
- Returns: GPX XML string.
- Called from:
  - `src/api/ingest.py:upload_text`
  - `src/web/races.py:manual_times`

### _build_geojson_string
- Purpose: Build a GeoJSON LineString FeatureCollection from cleaned fixes.
- Reads: in-memory fixes list.
- Writes: None.
- Returns: compact GeoJSON string.
- Called from:
  - `src/api/ingest.py:upload_text`
  - `src/web/races.py:manual_times`

### filter_fixes_by_window
- Purpose: Trim fixes to a start/finish epoch window (one-sided allowed).
- Reads: fix list with `utc` values.
- Writes: None.
- Returns: filtered fixes list.
- Called from:
  - `src/api/ingest.py:upload_text`
  - `src/web/races.py:manual_times`

### build_gpx_for_device
- Purpose: Query `Point` rows for a device and write a GPX file to disk.
- Reads: `Point` (ordered by `t_epoch`).
- Writes: GPX file to `out_dir` (default `logs/`).
- Returns: `(ok, path_or_error)` tuple.
- Called from:
  - Not currently referenced by code paths (kept for manual use or future worker).

### build_geojson_for_device
- Purpose: Query `Point` rows for a device and build a GeoJSON LineString.
- Reads: `Point` (ordered by `t_epoch`), optionally filtered by `start_epoch`/`finish_epoch`.
- Writes: optional GeoJSON file to `out_dir` when `save=True`.
- Returns: `(ok, path_or_json)` tuple.
- Called from:
  - `src/web/races.py:device_geojson`
  - `src/workers/gpx_worker.py:main`

### gpx_to_geojson
- Purpose: Convert raw GPX text to a GeoJSON LineString.
- Reads: GPX text input.
- Writes: None.
- Returns: `(ok, geojson_or_error)` tuple.
- Called from:
  - `src/web/races.py:upload_gpx`

## src/utils/time.py

### utc_now
- Purpose: Return a timezone-aware UTC datetime for timestamps such as token creation, token expiry, and token consumption.
- Reads: system clock.
- Writes: None.
- Returns: current UTC datetime.
- Called from:
  - `src/auth/tokens.py`

### as_aware_utc
- Purpose: Convert stored datetimes to timezone-aware UTC before comparing expiry values.
- Reads: datetime value read from the database.
- Writes: None.
- Returns: timezone-aware UTC datetime.
- Called from:
  - `src/auth/tokens.py`
- Notes: PostgreSQL preserves timezone-aware values in the target runtime, while lightweight SQLite checks can return naive datetimes. Naive values are treated as UTC.

### datetime_to_epoch
- Purpose: Convert a datetime to UTC epoch seconds, honoring the configured local timezone for naive datetimes.
- Reads: `configs/config.yaml` (`global.timezone`) when `tz_name` is not provided.
- Writes: None.
- Returns: epoch seconds (int).
- Called from:
  - `src/api/ingest.py:upload`
  - `src/api/ingest.py:upload_text`
  - `src/web/races.py:save_race`
  - `src/web/races.py:manual_times` (via `iso_to_epoch`)
  - `src/workers/parse_worker.py:_process_batch_once`
  - `src/workers/gpx_worker.py:main`

### epoch_to_datetime
- Purpose: Convert epoch seconds to a timezone-aware datetime in the configured local timezone.
- Reads: `configs/config.yaml` (`global.timezone`) when `tz_name` is not provided.
- Writes: None.
- Returns: timezone-aware `datetime`.
- Called from:
  - `src/web/home.py:home_page`
  - `src/web/races.py:post_race`
  - `src/web/races.py:edit_race`

### iso_to_epoch
- Purpose: Parse an ISO8601 datetime string and convert it to UTC epoch seconds.
- Reads: `configs/config.yaml` (`global.timezone`) when `tz_name` is not provided.
- Writes: None.
- Returns: epoch seconds (int) or None for empty input; rejects timezone-aware inputs when `allow_tz=False`.
- Called from:
  - `src/web/races.py:manual_times`

### rfid_timestamp_to_epoch
- Purpose: Parse RFID reader timestamp strings such as `20260526T163756` and convert them to UTC epoch seconds, with ISO8601 fallback support.
- Reads: `configs/config.yaml` (`global.timezone`) when `tz_name` is not provided.
- Writes: None.
- Returns: epoch seconds (int) or None for empty input.
- Called from:
  - `src/api/ingest.py:upload_rfid`

## src/workers/parse_worker.py

### _convert_fix
- Purpose: Convert a compact fix array into a `Point` ORM object.
- Reads: fix array values (scaled ints) and `device_id`.
- Writes: None (returns an object for later insert).
- Returns: `(Point | None, parse_error | None)`; drops missing/zeroed fixes and surfaces a reason when invalid.
- Called from:
  - `_process_batch_once` only (internal helper).

### _process_batch_once
- Purpose: Background batch step to move raw ingest data into `points`.
- Reads: `IngestRaw` rows where `processed_at_epoch IS NULL` (limit `BATCH_SIZE = 200`).
- Writes: `Point` inserts (including `received_at_epoch`) using PostgreSQL `ON CONFLICT DO NOTHING`; updates `IngestRaw.processed_at_epoch` and `parse_error` (only set if all fixes are invalid).
- Returns: number of `IngestRaw` rows processed.
- Called from:
  - `main` loop (internal helper).

### main
- Purpose: Run the background parser loop.
- Reads/Writes: same as `_process_batch_once`.
- Behavior: polls every `SLEEP_SEC = 1.0` when idle.
- Called from:
  - CLI: `python -m src.workers.parse_worker` (background process).

## src/workers/gpx_worker.py

### _distinct_devices
- Purpose: List distinct `device_id` values that have points.
- Reads: `Point.device_id`.
- Writes: None.
- Returns: list of device ids.
- Called from:
  - `main` only (internal helper).

### _latest_race_rider_window
- Purpose: Find the newest `race_rider.id` and its start/finish timing window for a device.
- Reads: `RaceRider.start_time_rfid_epoch`, `RaceRider.finish_time_rfid_epoch`.
- Writes: None.
- Returns: `(race_rider_id, start_epoch, finish_epoch)` with `None` values when missing.
- Called from:
  - `main` only (internal helper).

### main
- Purpose: Live GeoJSON cache worker for the race_day display.
- Reads: `Point` (latest `t_epoch`), `RaceRider` (latest rider for device + timing window).
- Writes: `TrackCache.geojson` (upsert per `race_rider_id`), `TrackCache.updated_at_epoch`.
- Behavior: polls every `SLEEP_SEC = 5.0`, only rebuilds when new points arrive, and trims tracks to the rider’s start/end times when present.
- Called from:
  - CLI: `python -m src.workers.gpx_worker` (background process).

## src/workers/rfid_worker.py

### _get_unprocessed_rfid_rows
- Purpose: Fetch `IngestRfid` rows where `processed_at_epoch IS NULL`.
- Reads: `IngestRfid`.
- Writes: None.
- Returns: oldest unprocessed RFID rows up to `BATCH_SIZE`.
- Called from: `_process_batch_once` only (internal helper).

### _find_device_id_for_epc
- Purpose: Resolve an RFID EPC to a registered `Device.id`.
- Reads: `Device.epc_id`.
- Writes: None.
- Returns: device id or None when the EPC is unregistered.
- Called from: `_process_rfid_row` only (internal helper).

### _latest_race_rider_for_device
- Purpose: Find the newest `RaceRider` linked to a device and the related race start epoch.
- Reads: `RaceRider`, `Category`, `Route`, `Race`.
- Writes: None.
- Returns: `(RaceRider, starts_at_epoch)` or `(None, None)`.
- Called from: `_process_rfid_row` only (internal helper).

### _process_rfid_row
- Purpose: Apply one RFID ingest row to start/finish timing fields or flag it as ambiguous.
- Reads: `IngestRfid`, `Device`, `RaceRider`, `Race.starts_at_epoch`.
- Writes: `RaceRider.start_time_rfid_epoch`, `RaceRider.start_time_pi_epoch`, `RaceRider.finish_time_rfid_epoch`, `RaceRider.finish_time_pi_epoch`, `RaceRider.multiple_rfid_flag`, `IngestRfid.processed_at_epoch`, `IngestRfid.process_error`.
- Behavior: start reads must be within one hour of race start and repeated reads are averaged when within five seconds; first finish reads must be outside the start window, repeated finish reads are averaged when within five seconds, confirmed finish timings ignore later finish reads and clear stale multiple-RFID warnings, and unmatched extras set `multiple_rfid_flag`.
- Called from: `_process_batch_once` only (internal helper).

### _process_batch_once
- Purpose: Process one batch of unprocessed RFID ingest rows.
- Reads/Writes: same as `_process_rfid_row`.
- Returns: number of rows processed.
- Called from: `main` loop (internal helper).

### main
- Purpose: Run the background RFID timing worker.
- Reads/Writes: same as `_process_batch_once`.
- Behavior: polls every `SLEEP_SEC = 30.0` when idle.
- Called from:
  - CLI: `python -m src.workers.rfid_worker` (background process).

## tests/download_latest_track_hist_gpx.py

### download_latest_track_hist_gpx
- Purpose: Fetch the newest non-empty `track_hist.gpx` snapshot for a `race_rider_id` and write it to disk for manual export testing.
- Reads: `TrackHist` filtered by `race_rider_id`, ordered by `updated_at_epoch` (latest first, nulls last), then `updated_at`, then `id`.
- Writes: GPX file under project-level `downloads/` as `race_rider_<id>_track_hist_<track_hist_id>.gpx`.
- Returns: `(ok, path_or_error)` tuple.
- Called from:
  - `tests/download_latest_track_hist_gpx.py:main` (CLI wrapper).

### main
- Purpose: CLI wrapper that accepts `race_rider_id`, calls `download_latest_track_hist_gpx`, and prints result.
- Reads: CLI argument (`race_rider_id`).
- Writes: None directly (delegates file write to helper function).
- Returns: process exit code (`0` success, `1` failure).
- Called from:
  - Direct script execution: `python tests/download_latest_track_hist_gpx.py <race_rider_id>`.

## tests/test_resend_email.py

### main
- Purpose: Send one manual test email through Resend using the same environment-variable pattern as the Flask runtime.
- Reads: `RESEND_API_KEY`, `TEST_EMAIL_TO`, and optional `MAIL_FROM`.
- Writes: one test email to the requested destination and prints the Resend response without printing the API key.
- Returns: process exit code (`0` success, `1` failure through `src.utils.env:required_env` validation).
- Called from:
  - Docker Compose manual smoke test:
```bash
docker compose -p enduro-dev --env-file .env.dev run --rm -e TEST_EMAIL_TO=you@example.com server python tests/test_resend_email.py
```
- Notes: this script is a manual smoke test for the forgot-password email setup. It is not part of the automated test suite and should not be used to send signup verification emails.

## tests/migrate_sqlite_to_postgres.py

### migrate_sqlite_to_postgres
- Purpose: One-time helper to copy the application data from `enduro_tracker.db` into the PostgreSQL database referenced by `DATABASE_URL`.
- Reads: SQLite source file `enduro_tracker.db`.
- Writes: PostgreSQL tables in dependency order using batched inserts and per-table commits.
- Preserves: explicit primary keys, timestamp fields, epoch mirror fields, and other row values exactly as stored in SQLite, except embedded NUL bytes in text fields, which are stripped because PostgreSQL text columns cannot store them.
- Resets: PostgreSQL sequences for integer `id` primary key tables after each table copy so future inserts continue from the migrated max id.
- Skips: `alembic_version`, because PostgreSQL should already be stamped during the schema bootstrap step.
- Called from:
  - `tests/migrate_sqlite_to_postgres.py:main` (CLI wrapper).

### main
- Purpose: CLI wrapper that parses the optional source path, table subset, and batch size before running the SQLite-to-PostgreSQL migration.
- Reads: CLI args (`--source`, `--tables`, `--batch-size`) and `DATABASE_URL`.
- Writes: None directly (delegates DB copy work to `migrate_sqlite_to_postgres`).
- Returns: process exit code (`0` success, `1` failure).
- Called from:
  - Direct script execution: `DATABASE_URL=... python tests/migrate_sqlite_to_postgres.py`.

## Database Tables (src/db/models.py)

- Schema management: Alembic migrations are the source of truth for database schema. Runtime app, ingest, and worker startup must not call `Base.metadata.create_all()` because that can create tables outside migration history and confuse Alembic autogenerate. The legacy `init_db()` helper remains in `src/db/models.py` only for deliberate manual development use.
- `ingest_raw`: raw device uploads (payload JSON, received/processed timestamps, parse error). Columns: `id`, `device_id`, `payload_json`, `received_at`, `received_at_epoch`, `processed_at`, `processed_at_epoch`, `parse_error`. Relationships: no enforced foreign-key relationship; records are associated to devices by `device_id` value only. Conditions: `device_id` and `payload_json` are required (`NOT NULL`).
- `ingest_rfid`: raw RFID reader tag events. Columns: `id`, `epc`, `rssi`, `ant`, `time_stamp_epoch`, `reader_id`, `avg_rssi`, `received_at_epoch`, `processed_at_epoch`, `process_error`. Relationships: view-only link to `devices` through `ingest_rfid.epc == devices.epc_id` so unknown/false RFID reads can still be stored. Conditions: `epc` is required (`NOT NULL`); `time_stamp_epoch`, `reader_id`, and `processed_at_epoch` are indexed for worker lookups.
- `devices`: registered hardware devices; referenced by race_riders. Columns: `id`, `device_info`, `epc_id`. Relationships: one device can map to many `race_riders` entries via `race_riders.device_id -> devices.id`, and can view many `ingest_rfid` rows through matching EPC values. Conditions: primary-key uniqueness on `id`; unique constraint `ux_devices_epc_id` enforces at most one device per non-null EPC tag.
- `points`: parsed GNSS fixes per device (t_epoch, lat/lon, optional metrics). Columns: `id`, `device_id`, `t_epoch`, `lat`, `lon`, `ele`, `sog`, `cog`, `fx`, `hdop`, `nsat`, `received_at`, `received_at_epoch`. Relationships: no enforced foreign key to `devices`; points are linked to `race_riders` through a view-only `device_id` join. Conditions: unique constraint `ux_points_device_time` enforces one row per (`device_id`, `t_epoch`).
- `riders`: core athlete details (name, team, bike, bio). Columns: `id`, `name`, `bike`, `bio`, `team`, `category`. Relationships: one rider can have many `race_riders` entries via `race_riders.rider_id -> riders.id`, and can optionally have one linked login account via `users.rider_id -> riders.id`. Conditions: `name` is required (`NOT NULL`).
- `users`: browser login accounts for riders and admins. Columns: `id`, `first_name`, `last_name`, `username`, `username_normalized`, `email`, `email_normalized`, `password_hash`, `role`, `rider_id`, `is_active`, `auth_version`, `created_at`, `updated_at`, `last_login_at`. Relationships: optionally links one account to one `rider`, has many `auth_tokens`, and can be actor/target for `auth_audit_events`. Conditions: role is constrained to `rider` or `admin`; `username_normalized`, `email_normalized`, and non-null `rider_id` are unique; passwords are stored only as hashes. Notes: the model uses Flask-Login's `UserMixin` for standard login-session helpers such as `get_id()`.
- `auth_tokens`: one-time hashed authentication tokens, currently for password reset. Columns: `id`, `user_id`, `purpose`, `token_hash`, `expires_at`, `used_at`, `created_at`. Relationships: belongs to one `user`. Conditions: `user_id`, `purpose`, `token_hash`, `expires_at`, and `created_at` are required; raw tokens are never stored.
- `auth_audit_events`: security-relevant account event history. Columns: `id`, `actor_user_id`, `target_user_id`, `action`, `metadata_json`, `created_at`. Relationships: optional actor and target links to `users`. Conditions: `action` and `created_at` are required; metadata must not contain passwords, raw reset tokens, or other secrets.
- `map_tile_monthly_quota`: durable monthly Esri tile quota state for admin controls and hard-stop decisions. Columns: `id`, `billing_month`, `provider`, `estimated_tiles_used`, `monthly_limit`, `warning_threshold`, `hard_stop_threshold`, `warning_triggered_at`, `hard_stop_triggered_at`, `hard_stop_active`, `viewers_only_blocked`, `override_active`, `override_until`, `override_reason`, `last_usage_rollup_at`, `created_at`, `updated_at`. Relationships: none. Conditions: `billing_month` and `provider` are unique together via `ux_map_tile_monthly_quota_month_provider`; usage reports should increment `estimated_tiles_used` directly from tile deltas so hard-stop decisions happen immediately.
- `map_tile_usage_sessions`: summarized browser/page map tile usage sessions for analytics and quota evidence. Columns: `id`, `session_key`, `browser_cookie_id`, `user_id`, `role`, `race_id`, `billing_month`, `page_path`, `provider`, `session_started_at`, `session_last_seen_at`, `estimated_tiles_loaded`, `fallback_used`, `blocked_reason`, `user_agent_hash`, `ip_hash`, `created_at`, `updated_at`. Relationships: optionally links to `users` and `races`. Conditions: `session_key` is unique; `browser_cookie_id`, `role`, `billing_month`, `page_path`, provider, session timestamps, tile count, fallback flag, and timestamps are required. Notes: there is no `map_style` column and no `estimated_tiles_delta_unrolled` column because accepted usage-report deltas update both this table and `map_tile_monthly_quota.estimated_tiles_used` immediately.
- `map_tile_browser_blocks`: browser-level tile block history for admin visibility and early release. Columns: `id`, `browser_cookie_id`, `user_id`, `reason`, `tiles_at_block`, `blocked_at`, `blocked_until`, `released_at`, `released_by_user_id`, `release_reason`, `created_at`, `updated_at`. Relationships: optionally links to the affected `users` row and to the admin `users` row that released the block. Conditions: `browser_cookie_id`, `reason`, `blocked_at`, `blocked_until`, `created_at`, and `updated_at` are required. Notes: Redis remains the enforcement store for short-lived browser blocks; this table records block history and admin reset state. Quota admin actions should be recorded in `auth_audit_events` rather than a separate map-specific audit table.
- Migration: the map tile quota tables are created by `migrations/versions/4578a2e08ba3_add_esri_tile_quota_tables.py`. The migration is manually written because the dev database may already contain these tables from `Base.metadata.create_all()`; clean databases still receive normal `CREATE TABLE` operations.
- `races`: event metadata (name, description, website, starts/ends, active flag). Columns: `id`, `name`, `description`, `website`, `starts_at`, `starts_at_epoch`, `ends_at`, `ends_at_epoch`, `active`. Relationships: one race can have many `route` rows via `route.race_id -> races.id`. Conditions: `name` and `active` are required (`NOT NULL`) and `active` defaults to `true`.
- `route`: per-race route geometry storage (gpx/geojson). Columns: `id`, `race_id`, `geojson`, `gpx`. Relationships: belongs to one `race` and can have many `categories` via `categories.route_id -> route.id`. Conditions: `race_id` is required (`NOT NULL`) and must reference an existing `races.id`.
- `categories`: category labels tied to a route; unique per route. Columns: `id`, `route_id`, `name`. Relationships: belongs to one `route` and is referenced by many `race_riders`, plus one-to-one cache/history links in `leaderboard_cache` and `leaderboard_hist`. Conditions: unique constraint `ux_route_category_name` enforces unique `name` per `route_id`.
- `race_riders`: joins rider, device, and category for a race; stores timing and status flags. Columns: `id`, `rider_id`, `device_id`, `category_id`, `comm_setting`, `active`, `recording`, `start_time_rfid`, `start_time_rfid_epoch`, `finish_time_rfid`, `finish_time_rfid_epoch`, `start_time_pi`, `start_time_pi_epoch`, `finish_time_pi`, `finish_time_pi_epoch`, `multiple_rfid_flag`, `finish_time_rfid_confirmed`. Relationships: each row belongs to one `rider`, one `device`, and one `category`, with one-to-one links to `track_cache` and `track_hist`. Conditions: `rider_id`, `device_id`, `category_id`, `active`, `recording`, `multiple_rfid_flag`, and `finish_time_rfid_confirmed` are required, with `active`/`recording` defaulting to `true` and RFID flags defaulting to `false`.
- `leaderboard_cache`: live leaderboard snapshot per category. Columns: `category_id`, `payload_json`, `etag`, `updated_at`, `updated_at_epoch`. Relationships: one-to-one with `categories` via `category_id` as both foreign key and primary key. Conditions: `payload_json` and `updated_at` are required (`NOT NULL`).
- `track_cache`: live track geojson per race_rider. Columns: `race_rider_id`, `geojson`, `etag`, `updated_at`, `updated_at_epoch`. Relationships: one-to-one with `race_riders` via `race_rider_id` as both foreign key and primary key. Conditions: `updated_at` is required (`NOT NULL`).
- `leaderboard_hist`: archived leaderboard snapshots per category. Columns: `id`, `category_id`, `payload_json`, `official_pdf`, `updated_at`, `updated_at_epoch`. Relationships: many history rows can belong to one `category` via `category_id -> categories.id`. Conditions: `category_id`, `payload_json`, and `updated_at` are required (`NOT NULL`).
- `track_hist`: archived track snapshots per race_rider (geojson/gpx/raw text). Columns: `id`, `race_rider_id`, `geojson`, `gpx`, `raw_txt`, `updated_at`, `updated_at_epoch`. Relationships: many history rows can belong to one `race_rider` via `race_rider_id -> race_riders.id`. Conditions: `race_rider_id` and `updated_at` are required (`NOT NULL`).

## Templates (templates/*.html)

### landing.html
- General: Public landing page.
- Displays: Kooksnylive entry point and high-level public actions.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme.
- UI actions: "View Races", "Sign Up", "Login".
- Linked pages (buttons):
  - "View Races" → `/dashboard` (public race dashboard).
  - "Sign Up" → `/signup` (rider signup).
  - "Login" → `/login` (rider/admin login).
- Pulls: none.
- Pushes: none.
- Routes called: `/`, `/dashboard`, `/signup`, `/login`.
- Embedded scripts: none.

### dashboard.html
- General: Public race dashboard.
- Displays: Active races table (name, start, website).
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/tables.css` for the race-list table.
- UI actions: "Landing", "Login", "Sign Up", "Rider Profiles", "View Race", "Enter Race", "Results".
- Linked pages (buttons):
  - "Landing" → `/`.
  - "Login" → `/login`.
  - "Sign Up" → `/signup`.
  - "Rider Profiles" → `/rider`.
  - "View Race" → `/races/<id>/post` (public post-race page).
  - "Enter Race" → `/races/<id>/enter`.
  - "Results" → `/races/<id>/results`.
- Pulls: `races`, `default_category`.
- Pushes: none.
- Routes called: `/dashboard`, `/`, `/login`, `/signup`, `/rider`, `/races/<id>/post`, `/races/<id>/enter`, `/races/<id>/results`.
- Embedded scripts: none.
- Notes: no admin management controls are shown here.

### dashboard_admin.html
- General: Admin operational dashboard.
- Displays: All races table (name, start, website, active).
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/tables.css` for the race-list table.
- UI actions: "Public Dashboard", "Input Rider Details", "Manage Devices", "Add New Race", "View RFID Records", "Rider Profiles", "User Management", "Edit", "Post Race", "Post Admin", "Enter Race", "Results".
- Linked pages (buttons):
  - "Public Dashboard" → `/dashboard`.
  - "Input Rider Details" → `/riders/new` (riders form page).
  - "Manage Devices" → `/devices/` (devices list page).
  - "Add New Race" → `/races/new` (new race form).
  - "View RFID Records" → `/rfid/` (RFID records page).
  - "Rider Profiles" → `/rider`.
  - "User Management" → `/admin/users`.
  - "Edit" → `/races/<id>/edit` (race edit page).
  - "Post Race" → `/races/<id>/post` (current post-race page).
  - "Post Admin" → `/races/<id>/post-admin`.
  - "Enter Race" → `/races/<id>/enter`.
  - "Results" → `/races/<id>/results`.
- Pulls: `races`, `default_category`.
- Pushes: none (links only).
- Routes called: `/dashboard-admin`, `/dashboard`, `/riders/new`, `/devices/`, `/races/new`, `/rfid/`, `/rider`, `/admin/users`, `/races/<id>/edit`, `/races/<id>/post`, `/races/<id>/post-admin`, `/races/<id>/enter`, `/races/<id>/results`.
- Embedded scripts: none.
- Notes: protected by admin access.

### placeholder.html
- General: Shared placeholder page for planned UX routes.
- Displays: title, route, target access level, and placeholder note.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/forms.css` for the content panel.
- UI actions: one configurable back button.
- Linked pages (buttons):
  - Back button target is supplied by each route.
- Pulls: `title`, `description`, `route`, `access`, `back_url`, `back_label`.
- Pushes: none.
- Routes called: `/rider`, `/races/<id>/enter`, `/races/<id>/post-admin`, `/races/<id>/results`, `/admin/users`.
- Embedded scripts: none.

### devices.html
- General: Device list and create form.
- Displays: Device ID, RFID EPC, Device Info.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the device form panel and messages, and `src/static/css/tables.css` for the device table.
- UI actions: "Save" (create), "Edit", "Back to Admin Dashboard".
- Linked pages (buttons):
  - "Back to Admin Dashboard" → `/dashboard-admin`.
  - "Edit" → `/devices/<id>/edit` (device edit page).
- Pulls: `devices`, `message`, `success`, `form`.
- Pushes: POST create device with optional RFID EPC.
- Routes called: `/devices/` (GET/POST), `/devices/<id>/edit`, `/dashboard-admin`.
- Embedded scripts: none.

### device_edit.html
- General: Edit a single device's info and RFID EPC.
- Displays: Device ID (read-only), Device Info, RFID EPC.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/forms.css` for the edit form panel, inputs, status messages, and form action layout.
- UI actions: "Save", "Back to Devices", "Admin Dashboard".
- Linked pages (buttons):
  - "Back to Devices" → `/devices/` (devices list page).
  - "Admin Dashboard" → `/dashboard-admin`.
- Pulls: `device`, `message`, `success`.
- Pushes: POST update device info and optional RFID EPC.
- Routes called: `/devices/<id>/edit`, `/devices/`, `/dashboard-admin`.
- Embedded scripts: none.

### rfid_view.html
- General: RFID ingest records viewer with server-side filters.
- Displays: RFID row id, EPC, RSSI, average RSSI, antenna, reader id, reader time, and received time.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the filter panel, filter grid, messages, and filter actions, and `src/static/css/tables.css` for the wide RFID records table.
- UI actions: "Filter", "Clear", "Back to Admin Dashboard".
- Linked pages (buttons):
  - "Back to Admin Dashboard" → `/dashboard-admin`.
  - "Clear" → `/rfid/` (unfiltered RFID records page).
- Pulls: `rows`, `filters`, `message`, `success`, `max_limit`.
- Pushes: GET filter query string values only.
- Routes called: `/rfid/`, `/dashboard-admin`.
- Embedded scripts: none.

### riders_form.html
- General: Create/edit rider form with riders list.
- Displays: Rider fields and riders table.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the rider form panel and messages, and `src/static/css/tables.css` for the riders table.
- UI actions: "Save", "Edit", "Back to Admin Dashboard".
- Linked pages (buttons/links):
  - "Back to Admin Dashboard" → `/dashboard-admin`.
  - "Edit" → `/riders/<id>/edit` (loads rider into form).
- Pulls: `categories`, `riders`, `form`, `editing_rider`, `message`, `success`.
- Pushes: POST create/update rider.
- Routes called: `/riders/new`, `/riders/<id>/edit`, `/dashboard-admin`.
- Embedded scripts: none.

### race_form.html
- General: Create/edit race, upload route GPX, manage category riders.
- Displays: Race fields, category selector, route map preview, rider/device tables.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for form controls and row actions, `src/static/css/tables.css` for the rider assignment table, `src/static/css/maps.css` for the Leaflet route preview container, and `src/static/css/race-form.css` for race-form-only layout.
- UI actions: "Save Changes", category dropdown (reload), "Upload GPX", "Remove GPX", "Save" (add rider), "Edit" (update rider entry), "Remove" (delete entry), "Back".
- Linked pages (buttons/links):
  - "Back" → `/dashboard-admin`.
  - "Open Website" → external race website URL (if set).
- Pulls: `race`, `categories`, `selected_category`, `route`, `geojson`, `riders`, `devices`, `race_riders`, `last_device_by_rider`.
- Pushes: POST save race, upload/remove GPX, add/edit/remove riders.
- Routes called: `/races/save`, `/races/<id>/edit?category=...`, `/races/<id>/route/upload`, `/races/<id>/route/remove`, `/races/<id>/route/geojson`, `/races/<id>/riders/add`, `/races/<id>/riders/<race_rider_id>/edit`, `/races/<id>/riders/<race_rider_id>/remove`.
- Embedded scripts:
  - GPX upload validation: native required-field validation blocks an empty file submission with a browser popup; JavaScript supplies the GPX-specific popup text.
  - Shared form/map scripts: auto-submit the category selector and fetch/render the route GeoJSON through `components/forms.js` and `components/maps.js`.
  - Rider add helper: auto-fills device based on `last_device_by_rider` mapping.

### post_race.html
- General: Post-race review with route map and rider tracks.
- Displays: Race metadata, category route map, riders list with timing, ambiguous RFID finish-time highlights with an asterisk review note, finish confirmation state, manual timing modal with optional TXT log upload.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for category/manual timing controls, `src/static/css/tables.css` for the riders timing table, `src/static/css/maps.css` for the Leaflet route/track map canvas, and `src/static/css/post-race.css` for post-race-only layout, track key, RFID warning, and modal styles.
- UI actions: Category dropdown (reload), "Show Track", "Manual Edit", "Confirm" timing, modal "Save/Cancel/Upload TXT".
- Linked pages (buttons/links):
  - "Back to Dashboard" → `/dashboard`.
- Pulls: `race`, `categories`, `selected_category`, `geojson`, `riders`, `has_multiple_rfid_flag`.
- Pushes: Fetch route GeoJSON, fetch stored rider track (cache-first for live polling), fetch live rider timing values, POST manual timing edits, POST finish timing confirmation, POST TXT log ingest.
- Routes called: `/races/<id>/post?category=...`, `/races/<id>/route/geojson?category=...`, `/races/<id>/race-rider/<id>/track`, `/races/<id>/race-rider/<id>/track?prefer_cache=1`, `/races/<id>/race-rider-timings?category=...`, `/races/<id>/race-rider/<id>/manual-times`, `/races/<id>/race-rider/<id>/confirm-finish`, `/api/v1/upload-text`.
- Embedded scripts:
  - Shared form/map scripts: auto-submit the category selector and initialise the Leaflet route map through `components/forms.js` and `components/maps.js`.
  - "Show Track" overlay fetch + render (page-specific).
  - 5-second polling refresh for selected rider tracks (preserves selected toggles and layer state).
  - 5-second polling refresh for start/end timing cells, multiple-RFID asterisk state, and confirmation button state.
  - Manual timing modal + POST update + TXT log upload.
