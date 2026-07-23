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
- Whenever routes, hostnames, access controls, or indexing requirements change, review and update the applicable crawler controls (`robots.txt`, sitemap entries, canonical URLs, and `noindex` behavior). Keep intended public viewer pages crawlable, avoid unnecessary crawling of authenticated or operational routes, and never treat crawler directives as access control.

## compose.yaml

### server
- Purpose: Existing application service built from the repository Dockerfile.
- Reads/Writes: receives `DATABASE_URL`, `FLASK_SECRET_KEY`, and profile-image settings from Compose; writes normalized Rider images to the `profile-images` volume mounted at `/var/lib/enduro-tracker/profile-images`.
- Depends on: `db` with health condition so PostgreSQL is started before the app container, and `redis` with health condition so authentication rate-limit storage is available before the web app starts.
- Exposes: host port `${APP_HOST_PORT}` to the Gunicorn web process listening on container port `8000`.
- Notes: the SQLAlchemy engine already prefers `DATABASE_URL`, so Compose now controls whether the app runs against PostgreSQL and the SQLite runtime path is no longer used. The Dockerfile starts Gunicorn with `src.main:app`, which matches the Flask WSGI entry point used on the remote server. `FLASK_SECRET_KEY` must be passed explicitly into the container because Compose variable substitution alone does not make an env value available to `os.environ` inside the running Flask process. The image seeds the media mount point with `appuser` ownership so a newly created volume remains writable by the non-root process.

### profile-images volume
- Purpose: durable storage for Rider-uploaded profile pictures outside the immutable application image and Git checkout.
- Mount: named Compose volume `profile-images` at `/var/lib/enduro-tracker/profile-images` in the `server` container only.
- Environment isolation: the real volume name comes from `PROFILE_IMAGES_VOLUME_NAME`; `.env.dev` uses `enduro_tracker_dev_profile_images` and `.env.prod` uses `enduro_tracker_prod_profile_images`.
- Lifecycle: ordinary `docker compose down`, `pull`, `up`, and container recreation preserve the volume. `docker compose down -v` deletes named data volumes and must not be used unless both PostgreSQL and profile media are deliberately being destroyed or restored.
- Backup: production database backups do not include this volume. Back up the production profile-image volume separately and keep its backup paired with the corresponding PostgreSQL backup.

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
- If dev and prod run on the same Docker host, use different values for `APP_HOST_PORT`, `APP_HOSTNAME`, `TUNNEL_TOKEN`, database credentials, `POSTGRES_VOLUME_NAME`, and `PROFILE_IMAGES_VOLUME_NAME`.
- The host ports may differ, for example `8000` for dev and `8001` for prod, but the Cloudflare public hostname configuration should still point to `http://server:8000`.

### Profile-image production workflow addition
- This update adds `Pillow` to `requirements.txt`. Because `compose.debug.yaml` bind-mounts source code but still uses dependencies from `APP_IMAGE_TAG`, build the new development image and update `APP_IMAGE_TAG` before starting this update's debug stack. Subsequent source-only edits can use the normal live bind-mount workflow.
- Before the first deployment of an image containing profile uploads, add `PROFILE_IMAGES_VOLUME_NAME=enduro_tracker_prod_profile_images` and `PROFILE_IMAGE_MAX_BYTES=5242880` to the production host's ignored `.env.prod`; keep the corresponding development name `enduro_tracker_dev_profile_images` in `.env.dev`.
- No manual `docker volume create` is required. The normal `docker compose ... up -d` creates the environment-specific named volume and mounts it into the new server container.
- The existing build, push, pull, Alembic, and `up -d` order remains valid. The media volume is independent of Alembic; the database migration creates only the nullable string key.
- Continue using `docker compose down` without `-v`. Never add `-v` to routine dev or production shutdowns because it removes named PostgreSQL and profile-image data.
- After first production startup, verify the attachment with `docker volume inspect enduro_tracker_prod_profile_images` and perform one rider upload/reload test before considering the deployment complete.
- Add `enduro_tracker_prod_profile_images` to production backups. A PostgreSQL dump alone cannot restore Rider images, even though it restores their stored keys.

### Public hostname troubleshooting note
- Cloudflare error `1033` usually indicates a public-hostname-to-tunnel routing problem rather than an app crash.
- When the tunnel connector is healthy but the site still shows `1033`, check that the correct public hostname is attached to the correct tunnel and that the DNS record points at the intended `cfargotunnel.com` target.

### Google Search discovery setup
- Step 4 - landing-page information: `templates/landing.html` uses the descriptive title `Kooksnylive | Live Enduro and Motocross Race Tracking`, a concise search-result description, and the canonical URL `https://kooksnylive.co.za/` so crawlers can identify the preferred root-domain version of the landing page.
- Step 5 - crawler guidance: public `GET /robots.txt` allows the intended anonymous viewer pages while asking cooperative crawlers not to visit `/admin/`, `/api/v1/`, `/dashboard-admin`, `/devices`, `/rfid`, `/riders/`, or authenticated race-management paths, including rider/admin entry, named-route/category mutation, and GPX upload/removal. It advertises `https://kooksnylive.co.za/sitemap.xml` as the canonical production sitemap location.
- Step 6 - XML sitemap: public `GET /sitemap.xml` returns `application/xml` and lists `https://kooksnylive.co.za/`, `https://kooksnylive.co.za/dashboard`, and each current canonical `https://kooksnylive.co.za/rider/<id>` profile.
- Sitemap scope: redirect-only `/rider` is omitted, while distinct rider details are discovered dynamically from stable Rider ids. Add public race pages/results only when their canonical/indexing policy is finalized; a route does not need to be listed merely because anonymous users can access it.
- Draft race indexing: direct draft race pages remain addressable for preview and are not linked from the public dashboard; `templates/post_race.html` emits `noindex,nofollow` for draft rows. This crawler directive does not make the direct route private or restrict anonymous access when an ID is known.
- Host coverage: the host-independent Flask route returns the same protected-path exclusions for `kooksnylive.co.za`, `app.kooksnylive.co.za`, and `dev.kooksnylive.co.za`; automated tests explicitly verify the production root and development host responses. The sitemap remains rooted at the preferred canonical production domain.
- Public viewer scope: `/`, `/dashboard`, `/rider`, public race pages, public results, and the map/timing resources those pages require remain crawlable, matching the anonymous Viewer responsibilities in `Web Application System Design V4 - 20260224.pdf`.
- Security note: `robots.txt` is crawler guidance, not access control. Authentication and role decorators remain responsible for protecting private routes, consistent with the lightweight-auth and HTTPS requirements in `Web Application System Design V4 - 20260224.pdf`.
- Deployment check: after deploying the production image, inspect the landing page/dashboard/profile canonical links; confirm `robots.txt` returns the protected-path guidance; then confirm `sitemap.xml` contains the landing page, dashboard, and current public rider URLs.
- Search Console: submit `https://kooksnylive.co.za/sitemap.xml` under Sitemaps after the production deployment and monitor its processing status for fetch or URL errors.

### References used for this setup
- Cloudflare add-site / nameserver handoff: `https://developers.cloudflare.com/fundamentals/manage-domains/add-site/`
- Cloudflare Tunnel setup: `https://developers.cloudflare.com/tunnel/setup/`
- Cloudflare remotely-managed tunnel creation: `https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/`
- Cloudflare error `1033`: `https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/`
- Google sitemap creation and submission: `https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap`

## .env Files

### .env.dev / .env.prod runtime variables
- Purpose: environment-specific source files for Compose interpolation and runtime container settings.
- Reads/Writes: read by Docker Compose at startup time; should remain ignored by git because they contain secrets and tokens.
- Core PostgreSQL values: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_VOLUME_NAME`.
- Public access values: `TUNNEL_TOKEN`, `APP_HOSTNAME`, and `APP_HOST_PORT`.
- Flask secret values: `FLASK_SECRET_KEY` must be passed into the `server` container explicitly through the Compose `environment` section so Flask can read it through `os.environ`.
- Map values: `MAP_PROVIDER`, `MAP_STYLE`, `ARCGIS_API_KEY`, and the map-limit variables must also be passed explicitly into the `server` container. Flask uses the provider/style/key in the map quota API only; the post-race HTML receives a safe bootstrap config and must not render the Esri key directly. The referrer-restricted Esri browser API key is returned only by `/api/map/config-status` when quota checks allow satellite imagery.
- Auth email and security values: `RESEND_API_KEY`, `MAIL_FROM`, `APP_PUBLIC_BASE_URL`, `AUTH_TOKEN_PEPPER`, `AUTH_PASSWORD_MIN_LENGTH`, `AUTH_RATE_LIMIT_STORAGE_URL`, `SESSION_COOKIE_SECURE`, and `SESSION_COOKIE_SAMESITE` are passed into the `server` container for the authentication workstream. Resend is used only for forgot-password reset links in the current plan; signup email verification is intentionally not enabled.
- Profile-image values: `PROFILE_IMAGES_VOLUME_NAME` selects an environment-specific durable Docker volume and `PROFILE_IMAGE_MAX_BYTES` sets the per-file upload limit passed to the web container. The committed/default limit is 5 MiB (`5242880` bytes).
- Notes: changing PostgreSQL bootstrap variables on an already-initialised volume does not reconfigure an existing database cluster. Clean separation requires a fresh volume name per environment or an explicit manual database/user migration.

## Python Requirements

### Authentication and security packages
- Purpose: Add the package baseline for the viewer/rider/admin authentication workstream described by `Web Application System Design V4 - 20260224.pdf`.
- Packages: `Flask-Login` for browser login sessions, `Flask-WTF` for form handling and CSRF protection, `Flask-Limiter` for rate limiting sensitive auth routes, `redis` for shared rate-limit storage, `email-validator` for signup/reset email validation, and `resend` for forgot-password email delivery.
- Notes: Resend is only used for password-reset emails in the current plan. Signup email verification is intentionally not enabled.

### Profile-image processing package
- Purpose: `Pillow` decodes submitted JPEG/PNG/WebP content, applies EXIF orientation, constrains dimensions, strips metadata through re-encoding, and writes one predictable WebP output format.
- Package: `Pillow==12.3.0`.
- Notes: file extensions and decoded image formats are both allowlisted; the submitted filename is never used as the stored key.

## src/main.py

### create_app
- Purpose: Flask application factory that creates the app instance, loads the Flask secret key, configures browser security helpers, and attaches all API and web blueprints.
- Reads: `FLASK_SECRET_KEY`, `PROFILE_IMAGE_UPLOAD_DIR`, `PROFILE_IMAGE_MAX_BYTES`, the `MAP_*` map configuration values, `ARCGIS_API_KEY`, and the auth email/security configuration values from the container runtime environment; `config.yaml` for host and port globals; `src.auth.login.login_manager` for browser session setup; `src.auth.rate_limits` for Redis-backed rate limiting; `src.auth.csrf` helpers for CSRF setup; `src.auth.routes.bp_auth` for auth pages; `src.web.rider_profiles.bp_rider_profiles` for the dashboard rider-tab redirect and public rider details/media; `src.web.map_tile_quota.bp_map_tile_quota` for map quota routes.
- Writes: `app.config["SECRET_KEY"]` plus secure session-cookie settings, profile-image path/limit settings, the map provider, style, browser API key, map-limit configuration values, and `AUTH_RATE_LIMIT_STORAGE_URL`; initialises Flask-Login, Flask-Limiter, and Flask-WTF CSRF protection on the app.
- Registers: ingest API routes, auth browser routes, home/dashboard routes, public rider profile routes, rider management, devices, races, RFID record viewer, and map tile quota blueprints.
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

## Rider profiles web layer

- Parent directory: `src/web`
- File: `rider_profiles.py`
- Layer decision: public profile lookup reuses `get_rider` from `src.services.riders`; redirects, canonical page rendering, edit-link visibility, and 404 responses remain in the web layer.

### rider_profiles
- Description: redirects public GET `/rider` to `/dashboard?tab=riders`, making the dashboard Riders tab the single rider index.
- Called from: legacy bookmarks and links that still target the former standalone rider index.
- Why this layer: redirect and URL construction are Flask response concerns.

### rider_profile
- Description: renders the canonical public read-only profile at GET `/rider/<rider_id>`; the marked profile card is also loaded into the dashboard dialog.
- Reads: one Rider through `src.services.riders.get_rider` and current-user ownership/admin access through `user_can_access_rider_resource`.
- Returns: `templates/rider_profile.html`, 404 for a missing rider, and an Edit Profile action only for the linked rider or an administrator.
- Progressive enhancement: dashboard links open this route in a modal when JavaScript is available and remain complete standalone navigation when it is unavailable.

### rider_profile_image
- Description: serves GET `/rider/<rider_id>/profile-image` from persistent profile-media storage as `image/webp` with conditional requests, long-lived caching, and `X-Content-Type-Options: nosniff`.
- Reads: the Rider's generated key through `get_rider` and the configured `PROFILE_IMAGE_UPLOAD_DIR`.
- Returns: the normalized image, or 404 for a missing Rider, invalid/mismatched key, or missing file.
- Indexing: this is a public image resource used by crawlable profile/dashboard pages; it is not added as a standalone sitemap URL and the existing robots rules do not block it.

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

### env_positive_int
- Purpose: Parse a strictly positive integer environment value while using an explicit safe fallback for missing, malformed, zero, or negative input.
- Reads: named environment variable.
- Writes: None.
- Returns: parsed positive integer or the supplied fallback.
- Called from:
  - `src.main:create_app` for `PROFILE_IMAGE_MAX_BYTES`.

### required_env
- Purpose: Read required environment variables and fail clearly when they are missing or blank.
- Reads: named environment variable.
- Writes: None.
- Returns: stripped environment value.
- Raises: `RuntimeError` when the value is missing or blank.
- Called from:
  - `src.auth.mail` for `APP_PUBLIC_BASE_URL`, `RESEND_API_KEY`, and `MAIL_FROM`.
- Notes: shared environment helpers should live here so auth, map, worker, and future admin modules do not each create their own local parsing functions.

## Python Layering Rule

### utils / services / web separation
- Purpose: keep helper logic organised by responsibility so route files stay thin and reusable logic does not become tied to Flask.
- `src/utils/*`: pure or low-level reusable helpers. These should avoid Flask route concerns and should not render templates. Good examples: Redis key construction, browser cookie id mechanics, time parsing, GPX conversion, and validation helpers.
- `src/services/*`: business/application logic that coordinates models, durable state, and domain rules. Good examples: billing-cycle calculation, current quota row creation, quota payload building, and block-reason decisions.
- `src/web/*`: Flask route/controller layer only. This layer should parse requests, call services/utils, enforce decorators, return `render_template()` or `jsonify()`, and keep route-specific HTTP glue.
- Notes: when adding functions, first check whether each function belongs in an existing utility or service module before adding another route-local helper. The device registry follows this rule through `src.utils.devices`, `src.services.devices`, and `src.web.devices`; the map quota feature follows it through the equivalent map modules.

## src/utils/devices.py

### Overall description
- Layer: utility.
- Purpose: normalize device form values and apply database-independent length/required-field rules.
- Why here: these functions are pure and reusable; they do not import Flask, SQLAlchemy, or templates.

### normalize_device_form
- Basic use: trim raw device id, description, and EPC values; convert blank optional values to `None`; normalize returned/active checkbox values.
- Called from: both routes in `src.web.devices` before a device service is called.

### normalize_device_boolean
- Basic use: convert browser checkbox strings, API-style truthy values, or booleans into returned/active state.
- Called from: `normalize_device_form`.

### device_form_template_values
- Basic use: convert normalized optional values back to empty strings while retaining returned/active booleans for safe creation-form redisplay.
- Called from: `devices_index` after user-correctable validation errors.

### validate_device_form
- Basic use: validate required device id, 64-character device-id limit, and 128-character EPC limit.
- Called from: `create_device` and `update_device` in `src.services.devices`.
- Notes: uniqueness is deliberately excluded because that requires database state and belongs in the service layer.

## Static media utility layer

- Parent directory: `src/utils`
- File: `media.py`

### normalize_static_image_filename / validate_static_image_filename
- Description: trim optional developer-managed image basenames, normalize the legacy/default literal `None` to an empty value, and reject directory paths, overlong names, or unsupported extensions.
- Called from: race form normalization/validation.
- Why this layer: Race artwork remains a developer-managed static asset, while Rider uploads use the separate profile-image pipeline below.

## Profile-image utility layer

- Parent directory: `src/utils`
- File: `profile_images.py`

### ProfileImageValidationError
- Description: carries a user-correctable profile-image extension, exact file-size, signature, format, pixel-count, or decode failure. The rider controller also rejects clearly oversized multipart requests before Werkzeug parses the uploaded file.
- Called from: raised by `prepare_profile_image`; handled by `rider_form` as HTTP 400.

### prepare_profile_image
- Description: bounds the untrusted stream read, allowlists JPEG/PNG/WebP names and decoded formats, rejects images above 20 megapixels, applies EXIF orientation, caps dimensions at 1,200 pixels, and emits metadata-free WebP bytes.
- Called from: `store_profile_image` in `src.services.profile_images`.
- Why this layer: image decoding and normalization are reusable low-level rules with no Flask, database, or filesystem-path concerns.

## Rider utility layer

- Parent directory: `src/utils`
- File: `riders.py`

### normalize_rider_form
- Description: trims rider form fields and converts blank optional team, bike, and bio values to `None`.
- Called from: `rider_form` in `web/riders.py` for create and edit submissions.
- Why this layer: normalization is pure reusable input handling with no Flask or database dependency.

### rider_form_values
- Description: builds template-safe strings from a Rider-like object, normalized dictionary, or empty form.
- Called from: `rider_form` in `web/riders.py` for initial, edit, error, and success form states.
- Why this layer: it is pure data shaping that can be reused by any interface presenting rider fields.

### validate_rider_form
- Description: requires a rider name; race categories are intentionally absent from rider profiles.
- Called from: `create_rider` and `update_rider` in `services/riders.py`.
- Why this layer: these rules depend only on supplied values rather than Flask or database state.

## RFID utility layer

- Parent directory: `src/utils`
- File: `rfid.py`

### normalize_rfid_filters
- Description: builds a complete dictionary of trimmed RFID viewer query values and supplies the default limit.
- Called from: `rfid_index` in `web/rfid.py`.
- Why this layer: it is pure reusable input normalization with no Flask or database dependency.

### parse_optional_int
- Description: converts an optional whole-number value to `int` or returns `None` for an empty value.
- Called from: `parse_rfid_limit` and `list_filtered_rfid_records`.
- Why this layer: numeric parsing is database-independent reusable validation.

### parse_rfid_limit
- Description: parses the requested row limit, defaults it to 200, and clamps it between 1 and 1000.
- Called from: `list_filtered_rfid_records` in `services/rfid.py`.
- Why this layer: limit parsing is a pure input rule rather than a route or persistence concern.

### datetime_filter_to_epoch
- Description: converts an optional datetime-local filter to epoch seconds by reusing `iso_to_epoch` from `utils/time.py`.
- Called from: `list_filtered_rfid_records` for reader and server datetime ranges.
- Why this layer: it is reusable low-level time parsing with no model or Flask dependency.

## Race utility layer

- Parent directory: `src/utils`
- Files: `races.py`, `race_entry.py`

### normalize_race_entry_form
- Description: parses the selected `category_id` plus the rider's current-device and previous-device confirmation answers; rider identity is deliberately excluded from submitted self-entry data.
- Called from: the rider and administrator entry workflows in `web/races.py`.
- Why this layer: it is pure form normalization and validation with no Flask, model, or database dependency.

### normalize_route_name / normalize_category_name
- Description: trim submitted descriptive route names and race category labels before validation or persistence.
- Called from: route/category creation services in `services/race_routes.py`.
- Why this layer: normalization is pure input handling with no Flask or database dependency.

### validate_route_name / validate_category_name
- Description: require non-empty route/category names within the model column length limits.
- Called from: route/category creation services in `services/race_routes.py`.
- Why this layer: reusable name validation is independent of HTTP and durable state.

### normalize_race_form
- Description: normalizes race fields and converts a local date/time pair to `starts_at_epoch`.
- Called from: `save_race` in `web/races.py`.
- Why this layer: it is pure form parsing that reuses `datetime_to_epoch` without Flask or model access.

### parse_positive_id
- Description: parses required or optional positive database identifiers used by category, rider, and route HTTP selections.
- Called from: race controllers before invoking race-scoped services.
- Why this layer: identifier parsing is pure validation and does not belong to Flask or persistence logic.

### parse_manual_time_epoch
- Description: parses an optional timezone-naive manual time into UTC epoch seconds.
- Called from: `manual_times` in `web/races.py`.
- Why this layer: it reuses `iso_to_epoch` and keeps time parsing independent of HTTP and persistence.

## src/utils/map_tile_quota.py

### Overall description
- Purpose: Provide shared helper logic for Esri/satellite map tile quota enforcement, browser identification, Redis short-term counters, Redis temporary blocks, monthly quota state, and database usage summaries.
- Contains:
  - `_timeout_seconds`: convert timeout minutes to seconds for Redis expiry.
  - `_normalise_tile_delta`: validate tile deltas before counting them.
  - `_minute_bucket`: round a datetime down to its minute bucket.
  - `_window_bucket_times`: list the minute buckets in the rolling usage window.
  - `_is_safe_browser_cookie_id`: check browser cookie ids before reusing them.
  - `generate_browser_cookie_id`: create a new anonymous browser identifier.
  - `get_or_create_browser_cookie_id`: read or create the anonymous browser cookie.
  - `browser_count_key`: build the Redis key for one browser/minute tile bucket.
  - `browser_block_key`: build the Redis key for a browser's temporary block flag.
  - `_redis_value_to_int`: safely parse Redis values into integers.
  - `get_browser_tile_count`: sum browser tile buckets inside the rolling window.
  - `increment_browser_tile_count`: add a tile delta to the current minute bucket.
  - `is_browser_over_tile_limit`: compare rolling browser usage against the tile limit.
  - `_browser_window_count_keys`: build current rolling-window Redis count keys.
  - `is_browser_blocked`: check whether a browser currently has a block flag.
  - `set_browser_block`: set a temporary browser block in Redis.
  - `reset_browser_block`: remove a browser block and optionally current window counters.
  - `_override_is_current`: check whether a monthly quota override is still active.
  - `is_monthly_blocked`: decide whether monthly/global quota state blocks satellite use.
  - `set_monthly_hard_stop`: set or clear the monthly hard-stop flag.
  - `reset_monthly_hard_stop`: clear the monthly hard-stop flag.
  - `record_tile_delta`: apply one tile delta to session and monthly DB rows.
- Notes: this matches the system design requirement to control tile-provider cost exposure while still allowing GPX/race viewing to continue with fallback map behavior.

### get_or_create_browser_cookie_id
- Purpose: Read an existing anonymous browser id cookie or create a new one.
- Reads: Flask request cookies.
- Writes: browser cookie on the response when a new id is needed.
- Returns: browser cookie id.
- Called from:
  - Future map config/status and tile usage routes.
- Notes: the cookie stores only an identifier; usage counts remain server-side in Redis/database stores.

### browser_count_key
- Purpose: Build the Redis key for one browser/minute tile count bucket.
- Reads: browser cookie id and optional bucket datetime.
- Writes: None.
- Returns: Redis key string.
- Called from: `get_browser_tile_count`, `increment_browser_tile_count`, and `reset_browser_block`.

### browser_block_key
- Purpose: Build the Redis key for a browser's temporary satellite block.
- Reads: browser cookie id.
- Writes: None.
- Returns: Redis key string.
- Called from: `is_browser_blocked`, `set_browser_block`, and `reset_browser_block`.

### get_browser_tile_count
- Purpose: Sum the Redis browser tile buckets inside the rolling usage window.
- Reads: Redis count keys for the current minute and previous minutes inside `MAP_USER_LIMIT_TIMEOUT_MIN`.
- Writes: None.
- Returns: integer count for the rolling window.
- Notes: this is minute-granularity rolling-window counting, not one counter that resets after inactivity.

### increment_browser_tile_count
- Purpose: Add a browser-reported tile delta to the current minute Redis bucket.
- Reads: current minute bucket key and the other bucket keys inside the rolling window.
- Writes: current minute bucket key and expiry.
- Returns: updated browser count inside the rolling window.
- Notes: each bucket expires after `MAP_USER_LIMIT_TIMEOUT_MIN` plus a small buffer. Enforcement is based on the sum of buckets inside the current window, so older usage naturally stops counting once it falls outside the window.

### is_browser_over_tile_limit
- Purpose: Compare rolling browser usage against `MAP_TILE_USER_LIMIT`.
- Reads: browser bucket keys inside the current rolling window.
- Writes: None.
- Returns: boolean.
- Notes: this is the preferred browser-limit check for automatic Esri fallback because it allows satellite usage again once the rolling-window total falls below the limit.

### is_browser_blocked
- Purpose: Check whether the browser currently has a Redis block key.
- Reads: Redis block key.
- Writes: None.
- Returns: boolean.

### set_browser_block
- Purpose: Set a temporary Redis block key for a browser.
- Reads: configured timeout minutes.
- Writes: Redis block key with expiry and reason value.
- Returns: None.

### reset_browser_block
- Purpose: Manually release a browser block.
- Reads: browser cookie id.
- Writes: deletes Redis block key and optionally deletes current rolling-window bucket keys.
- Returns: None.
- Called from:
  - Future admin map tile quota reset route.

### is_monthly_blocked
- Purpose: Decide whether monthly/global quota state should prevent Esri config release.
- Reads: `MapTileMonthlyQuota`-like row, current role, admin flag, and optional current time.
- Writes: None.
- Returns: boolean.
- Notes: missing quota state fails closed for non-admin users.

### set_monthly_hard_stop
- Purpose: Set or clear the monthly hard-stop flag.
- Reads: `MapTileMonthlyQuota`-like row and optional current time.
- Writes: `hard_stop_active`, `hard_stop_triggered_at`, and `updated_at`.
- Returns: None.

### reset_monthly_hard_stop
- Purpose: Clear the monthly hard-stop flag.
- Reads: `MapTileMonthlyQuota`-like row and optional current time.
- Writes: `hard_stop_active` and `updated_at`.
- Returns: None.

### record_tile_delta
- Purpose: Apply one accepted browser tile delta to both the usage-session row and monthly quota row.
- Reads: `MapTileUsageSession`-like row, `MapTileMonthlyQuota`-like row, tile delta, and optional current time.
- Writes: `usage_session.estimated_tiles_loaded`, `usage_session.session_last_seen_at`, `monthly_quota.estimated_tiles_used`, and `updated_at` fields.
- Returns: normalised tile delta.
- Notes: callers should pass deltas, not cumulative totals, to avoid double counting.

### Checks
- Browser count bucket keys expire using `MAP_USER_LIMIT_TIMEOUT_MIN` plus a small cleanup buffer.
- Browser block keys expire using `MAP_USER_LIMIT_TIMEOUT_MIN`.
- Browser tile counts are calculated from the current rolling window, not from one inactivity-refreshed counter.
- Tile deltas increment Redis count and database summary totals once.
- Reset removes the block key and, by default, current rolling-window count bucket keys.
- Monthly hard stop blocks satellite unless an active override is present.

## src/services/__init__.py

- Purpose: Mark `src.services` as the business-service package for application/domain logic.
- Reads: None.
- Writes: None.
- Functions: None.
- Notes: service modules sit between Flask routes and low-level utilities/database models. They should contain business rules but should not render templates or define routes.

## src/services/devices.py

### Overall description
- Layer: service.
- Purpose: coordinate device queries, uniqueness rules, and create/update mutations without depending on Flask.
- Why here: these operations use models and durable database state and can be reused by browser routes, future APIs, or commands.
- Transaction rule: functions stage changes; the calling controller owns commit/rollback.

### DeviceValidationError
- Basic use: carry one or more user-correctable field or uniqueness messages from the service to its caller.
- Called from: `create_device` and `update_device`; handled by both device routes.

### list_devices
- Basic use: return all `Device` rows ordered by immutable device id.
- Called from: `devices_index` for GET, successful POST, and error redisplay.

### get_device
- Basic use: load one `Device` by primary key or return `None`.
- Called from: `device_edit` and `create_device`'s duplicate-id check.

### device_epc_in_use
- Basic use: test EPC uniqueness, optionally excluding the device currently being edited.
- Called from: `create_device` and `update_device`.

### create_device
- Basic use: combine pure field validation with database uniqueness checks, then stage a new `Device` row including returned/active availability state.
- Called from: POST `/devices/`.

### update_device
- Basic use: validate and stage changes to `device_info`, `epc_id`, `returned`, and `active` while keeping `Device.id` immutable.
- Called from: POST `/devices/<device_id>/edit`.

## Rider service layer

- Parent directory: `src/services`
- File: `riders.py`
- Transaction rule: service functions stage changes; the calling controller owns commit and rollback.

### RiderValidationError
- Description: carries one or more user-correctable rider field messages.
- Called from: raised by `create_rider` and `update_rider`; handled by `rider_form` in `web/riders.py`.
- Why this layer: it represents a rider business-operation failure without depending on Flask responses.

### RiderProfileLinkError
- Description: reports that a rider account is missing or already linked and cannot create another profile.
- Called from: raised by `create_rider`; handled by `rider_form` as a forbidden request.
- Why this layer: one profile per rider account is a domain rule involving durable User state.

### list_riders
- Description: returns all Rider rows ordered by name.
- Called from: `rider_form` before rendering and after a successful save.
- Why this layer: it is reusable model querying that should not depend on templates or HTTP requests.

### get_rider
- Description: loads one Rider by primary key or returns `None`.
- Called from: `rider_form` when resolving GET edit requests and from public profile/media controllers.
- Why this layer: model lookup is durable-state access reusable outside Flask.

### get_rider_for_update
- Description: loads a Rider with `FOR UPDATE` so PostgreSQL serializes concurrent text/image changes to the same profile.
- Called from: POST `rider_form` before a generated image key can be replaced or removed.
- Why this layer: row locking is durable-state coordination that prevents simultaneous replacements from orphaning one request's generated file.

### rider_account_has_profile
- Description: checks whether an active rider account already has a linked `rider_id`.
- Called from: `rider_form` before allowing `/riders/new`.
- Why this layer: it applies the one-profile rule and reuses shared `user_has_role` authorization behavior.

### create_rider
- Description: validates and stages a Rider row; rider accounts are linked to it while admin-created profiles remain unlinked.
- Called from: `rider_form` for POST `/riders/new`.
- Why this layer: it coordinates validation, Rider/User models, account linking, and the reused `utc_now` helper.

### update_rider
- Description: validates and stages changes to a rider's name, team, bike, and bio while the controller/storage service separately coordinates generated profile-media keys. Category is selected only when entering a race.
- Called from: `rider_form` for POST `/riders/<rider_id>/edit`.
- Why this layer: it applies rider mutation rules independently of Flask requests and responses.

## Profile-image storage service layer

- Parent directory: `src/services`
- File: `profile_images.py`
- Storage rule: generated media lives in the configured persistent volume; PostgreSQL stores only the flat key on `Rider.profile_image_filename`.

### is_profile_image_key
- Description: accepts only `rider-<id>-<32 lowercase hexadecimal characters>.webp` keys and can require the embedded owner id to match a Rider.
- Called from: public media serving and obsolete-file cleanup.

### store_profile_image
- Description: calls `prepare_profile_image`, generates a UUID-based Rider key, writes with restrictive permissions to a same-directory temporary file, and atomically moves it into the persistent media directory.
- Called from: authenticated POST `/riders/new` and POST `/riders/<id>/edit` when a file is supplied.
- Why this layer: it coordinates durable filesystem state without taking ownership of Flask responses or database commits.

### delete_profile_image
- Description: removes only keys that match the generated flat-key format; missing and legacy values are left untouched safely.
- Called from: `rider_form` after a successful replacement/removal and when an uncommitted new file must be cleaned up.

## Home service layer

- Parent directory: `src/services`
- File: `home.py`

### load_race_display_data
- Description: queries all races or selected lifecycle statuses, orders by start epoch, and prepares start/end display datetimes.
- Called from: `load_dashboard_display_data` and `_render_admin_dashboard` in `web/home.py`.
- Why this layer: it coordinates Race model data and reusable dashboard preparation without depending on Flask rendering.
- Reuse: uses `epoch_to_datetime` from `utils/time.py`.

### load_dashboard_display_data
- Description: groups public races into upcoming, live, and newest-first completed collections and includes every Rider for the dashboard Riders tab; draft races remain private.
- Called from: `_render_public_dashboard` in `web/home.py`.
- Why this layer: it composes Race/Rider database state independently of Flask rendering.

### list_public_rider_ids
- Description: returns stable Rider ids for dynamic public sitemap entries.
- Called from: `sitemap` in `web/home.py`.

## RFID service layer

- Parent directory: `src/services`
- File: `rfid.py`

### list_filtered_rfid_records
- Description: parses normalized filters, applies the RFID database query, orders and limits results, and adds display datetimes.
- Called from: `rfid_index` in `web/rfid.py`.
- Why this layer: it coordinates IngestRfid durable state and viewer rules without handling Flask requests or templates.
- Reuse: uses RFID parsing utilities plus `epoch_to_datetime` from `utils/time.py`.

## Race lifecycle service layer

- Parent directory: `src/services`
- File: `races.py`

### RaceValidationError / RaceNotFoundError
- Description: distinguish user-correctable form failures from missing Race rows.
- Called from: raised by `save_race`/page services and mapped by `web/races.py`.
- Why this layer: they represent application outcomes without Flask response dependencies.

### RaceSaveResult
- Description: carries the staged Race plus optional rejected image-field text and validation feedback when the remaining valid race fields can still be saved.
- Called from: returned by `save_race_with_image_feedback`.
- Why this layer: it describes a partial application outcome without introducing Flask/template concerns into the service.

### get_race
- Description: loads one Race by primary key.
- Called from: race save and page-data services.
- Why this layer: it is reusable durable-state access.

### _prepare_race_display_times
- Description: populates `race.starts_at` and `race.ends_at` display attributes from their durable epoch columns.
- Called from: `load_post_race_data` and `load_race_edit_data`.
- Why this layer: it prepares model-backed application display data while reusing `epoch_to_datetime`.

### save_race
- Description: validates name, lifecycle, end-after-start ordering, and the static image basename, then stages race name/website/description/location/logo/start/end/status changes.
- Called from: race services and focused tests.
- Why this layer: it coordinates validation and model mutation without Flask responses.

### save_race_with_image_feedback
- Description: saves all valid race metadata while retaining the existing/default image when the optional submitted image basename is invalid, then returns field-specific feedback for redisplay.
- Called from: `save_race` in `web/races.py`.
- Why this layer: deciding that optional artwork must not discard otherwise valid race changes is an application rule rather than an HTTP concern.

### load_post_race_data
- Description: composes race, category, route GeoJSON, rider, and timing data for the post-race page.
- Called from: `post_race` in `web/races.py`.
- Why this layer: it coordinates several domain services into one page-level application operation.

### load_race_edit_data
- Description: composes all named race routes/categories, the selected shared Route/Category, and rider/device assignment management data without creating records on GET.
- Called from: `edit_race` in `web/races.py`.
- Why this layer: it builds reusable page data while leaving rendering to the controller.

## Race route service layer

- Parent directory: `src/services`
- File: `race_routes.py`

### RaceRouteValidationError / RaceRouteNotFoundError
- Description: distinguish invalid category/GPX input from a missing category route.
- Called from: route service operations and mapped by `upload_gpx`/`remove_gpx`.
- Why this layer: they expose route-operation outcomes without Flask dependencies.

### create_race_route
- Description: validates and stages a named Route independently of category creation, including case-insensitive per-race duplicate detection.
- Called from: `add_race_route` and inline new-route category creation.
- Why this layer: it owns race-route durable state and naming rules.

### create_race_category
- Description: validates and stages a freely named Category attached to an existing same-race Route.
- Called from: `create_race_category_with_route` and focused service tests.
- Why this layer: it owns category durable state and same-race selection rules.

### create_race_category_with_route
- Description: attaches a new Category to an existing race Route or creates a new named Route first.
- Called from: `add_race_category`.
- Why this layer: it coordinates the two supported category-creation workflows while keeping the controller thin.

### rename_race_route / rename_race_category
- Description: rename same-race routes/categories while maintaining normalized, case-insensitive identities.
- Called from: route rename and category edit POST controllers.
- Why this layer: durable naming and uniqueness rules belong at the service boundary.

### reorder_race_category
- Description: move a category to a positive display position and deterministically renumber the race's categories.
- Called from: `edit_race_category`.
- Why this layer: ordering is a multi-row durable-state operation.

### set_race_category_archived
- Description: archive or restore a category without deleting historical race entries.
- Called from: `edit_race_category`.
- Why this layer: archive visibility is a category domain rule.

### assign_race_category_route
- Description: reassign a category only to another Route owned by the same Race.
- Called from: `edit_race_category`.
- Why this layer: it enforces the same-race route rule before the composite database constraint.

### list_race_category_records
- Description: returns active or all category records in configured display order.
- Called from: race edit, post-race, and entry composition.
- Why this layer: it centralizes archive filtering and category ordering.

### list_race_routes
- Description: returns a race's named routes in case-insensitive name order.
- Called from: `load_race_edit_data`.
- Why this layer: it provides reusable race-scoped route selection data.

### get_category_for_race
- Description: loads one active Category by `category_id` with explicit race scope; archived access must be requested explicitly for administration.
- Called from: race edit, post-race, GPX, manual assignment, and deletion operations.
- Why this layer: it enforces race scoping at the durable-state boundary.

### get_route_for_category
- Description: loads one Route scoped to a race/category.
- Called from: GeoJSON retrieval and GPX removal operations.
- Why this layer: it centralizes the shared scoped query.

### get_route_geojson
- Description: returns stored route GeoJSON for a race-scoped `category_id`, or `None`.
- Called from: post-race data and `route_geojson`.
- Why this layer: it exposes reusable route data without HTTP response logic.

### category_is_unused / delete_unused_race_category
- Description: inspect every direct Category consumer (`RaceRider`, `LeaderboardCache`, and `LeaderboardHist`) and hard-delete only a category with no current or historical references; referenced categories must be archived.
- Called from: the guarded category deletion POST controller.
- Why this layer: historical-reference checks and durable deletion belong in one transactional domain operation.

### delete_unused_race_route
- Description: hard-delete a same-race Route only when no active or archived Category references it.
- Called from: the guarded route deletion POST controller.
- Why this layer: shared-route safety must be checked against durable category relationships rather than trusted form state.

### store_route_gpx
- Description: validates category/GPX input and stages GPX plus converted GeoJSON.
- Called from: `upload_gpx`.
- Why this layer: it coordinates GPX conversion with Route persistence.

### clear_route_gpx
- Description: clears GPX/GeoJSON without deleting the Route row.
- Called from: `remove_gpx`.
- Why this layer: it applies the route-removal domain behavior.

## Race-rider service layer

- Parent directory: `src/services`
- File: `race_riders.py`

### get_scoped_race_rider
- Description: loads a RaceRider only when its explicit, database-enforced `race_id` matches the requested race.
- Called from: entry edit/removal and timing services.
- Why this layer: it centralizes race scoping for authorization-safe operations.

### load_race_rider_management_data
- Description: builds available riders, devices, current entries, and last-device mappings.
- Called from: `load_race_edit_data`.
- Why this layer: it coordinates assignment data and reuses `list_riders` and `list_devices`.

### create_race_rider
- Description: stages a new active/recording rider-device-category assignment with the explicit race id required by the composite Category scope and per-race uniqueness constraints.
- Called from: `add_race_rider`.
- Why this layer: it owns RaceRider construction independent of Flask.

### update_race_rider
- Description: stages device, active, and recording changes.
- Called from: `edit_race_rider`.
- Why this layer: it applies assignment mutation rules.

### delete_race_rider
- Description: stages assignment deletion.
- Called from: `remove_race_rider`.
- Why this layer: it encapsulates RaceRider durable-state removal.

## Automatic race-entry service layer

- Parent directory: `src/services`
- File: `race_entry.py`

### get_rider_previous_device_id / load_race_entry_page_data
- Description: resolve a rider's most recent device assignment and compose the upcoming/live race-category entry page without changing inventory or assignment state; draft/completed races reject entry.
- Called from: `enter_race` and the automatic assignment service.
- Why this layer: these functions coordinate Race, Rider, Category, RaceRider, and Device history independently of HTTP rendering.

### assign_device_and_create_entry
- Description: creates one race-scoped RaceRider while locking the selected Device row with `FOR UPDATE SKIP LOCKED`; it prefers an eligible prior device, otherwise selects an active/returned device unused in the requested race.
- Outcomes: `reused_previous`, `assigned_available`, `replacement_required`, or `none_available`.
- Inventory discrepancy rule: `Device.returned` represents confirmed physical custody and is never changed by automatic assignment. If a rider confirms possession of their prior active, race-unused device while that device is marked `returned=True`, entry succeeds, the flag remains true, and the result reports an inventory discrepancy for administrator review.
- Replacement rule: an inactive prior device, a prior device already used in the race, or a rejected/unidentified suggestion requires an active, returned, race-unused replacement. If none exists, no RaceRider is created.
- Called from: `enter_race`.
- Why this layer: candidate selection, locking, durable assignment, and outcome rules are one transactional domain operation rather than controller logic.

## Race timing service layer

- Parent directory: `src/services`
- File: `race_timing.py`

### RaceRiderTimingNotFoundError / RaceRiderFinishMissingError
- Description: distinguish a missing scoped entry from an entry that has no finish time to confirm.
- Called from: timing mutation services and mapped by timing routes.
- Why this layer: they represent timing-domain outcomes independently of HTTP responses.

### race_rider_timing_payload
- Description: formats one RaceRider's timing values and warning/confirmation state.
- Called from: post-race rows, polling, and confirmation responses.
- Why this layer: it is reusable domain payload building.

### build_post_race_riders
- Description: combines rider identity/device data with timing payloads for one category.
- Called from: `load_post_race_data`.
- Why this layer: it coordinates Rider and RaceRider data for the application view.

### list_race_rider_timings
- Description: returns timing payloads scoped to a race and optional category.
- Called from: `race_rider_timings`.
- Why this layer: it owns scoped timing querying and payload construction.

### update_manual_race_rider_times
- Description: stages manual timing changes and an optional trimmed TrackHist snapshot.
- Called from: `manual_times`.
- Why this layer: it coordinates timing state, track history, GPX conversion, and timestamps.

### confirm_race_rider_finish
- Description: confirms an existing finish time and clears the multiple-read warning.
- Called from: `confirm_finish_time`.
- Why this layer: it applies the RFID finish-confirmation domain rule.

## Race track service layer

- Parent directory: `src/services`
- File: `race_tracks.py`

### read_track_history_geojson
- Description: returns the newest historical track GeoJSON scoped to a race entry.
- Called from: `get_race_rider_track_geojson`.
- Why this layer: it centralizes the history query and race scoping.

### read_track_cache_geojson
- Description: returns live cached GeoJSON scoped to a race entry.
- Called from: `get_race_rider_track_geojson`.
- Why this layer: it centralizes the cache query and race scoping.

### get_race_rider_track_geojson
- Description: applies history/cache preference and fallback behavior.
- Called from: `race_rider_track`.
- Why this layer: it owns reusable track-selection rules without returning Flask responses.

## src/services/map_tile_quota.py

### Overall description
- Purpose: Provide map tile quota business/service helpers for Esri billing-cycle, durable quota state, usage summaries, browser block history, and quota audit events.
- Contains:
  - `current_billing_month`: calculate the 25th-to-25th billing-cycle key.
  - `quota_defaults_from_config`: convert map-limit config values into quota defaults.
  - `get_or_create_current_quota`: load/create the current billing-cycle quota row.
  - `generate_usage_session_key`: create a public usage-session identifier.
  - `get_or_create_usage_session`: load/create a summarized browser/page usage row.
  - `update_quota_threshold_flags`: set warning/hard-stop timestamps and flags.
  - `apply_tile_usage_delta`: apply a tile delta and update threshold flags.
  - `record_browser_block`: record a browser block for admin visibility.
  - `release_browser_blocks`: mark active browser block rows released.
  - `set_viewers_only_blocked`: block/unblock anonymous viewer satellite access.
  - `set_global_hard_stop`: manually set/clear global hard-stop state.
  - `set_monthly_thresholds`: manually update active monthly quota thresholds.
  - `set_monthly_tile_estimate`: manually correct the current monthly tile estimate.
  - `set_monthly_override`: enable a temporary monthly hard-stop override.
  - `clear_monthly_override`: disable a monthly hard-stop override.
  - `record_quota_audit_event`: write admin/system quota actions to auth_audit_events.
  - `monthly_block_reason`: convert quota state into a frontend-safe block reason.
  - `quota_payload`: convert a quota row into JSON/admin display data.
- Notes: Redis rolling-window mechanics remain in `src.utils.map_tile_quota`; Flask request/response handling remains in `src.web.map_tile_quota`.

### current_billing_month
- Purpose: Calculate the current Esri billing-cycle month key.
- Reads: current UTC date, or supplied test datetime.
- Writes: None.
- Returns: `YYYY-MM` key for the cycle starting on the 25th.
- Notes: dates from the 1st to the 24th belong to the previous cycle-start month.

### quota_defaults_from_config
- Purpose: Convert map-limit configuration into defaults for a new monthly quota row.
- Reads: config dictionary values for `monthly_limit`, `warning_threshold`, and `hard_stop_threshold`.
- Writes: None.
- Returns: dictionary of normalised quota defaults.

### get_or_create_current_quota
- Purpose: Load or create the `MapTileMonthlyQuota` row for the active 25th-to-25th billing cycle.
- Reads: `map_tile_monthly_quota` through the provided SQLAlchemy session.
- Writes: a new `MapTileMonthlyQuota` row when one does not exist for the current cycle/provider.
- Returns: `MapTileMonthlyQuota` row.
- Notes: this keeps config-status fail-safe decisions tied to durable monthly quota state.

### generate_usage_session_key
- Purpose: Create a browser/page usage-session identifier.
- Reads: secure random source.
- Writes: None.
- Returns: URL-safe token.

### get_or_create_usage_session
- Purpose: Load an existing usage session by `session_key`, or create a summarized browser/page usage row.
- Reads: `map_tile_usage_sessions` through the provided SQLAlchemy session.
- Writes: a new `MapTileUsageSession` row when no valid session exists.
- Returns: `MapTileUsageSession` row.

### update_quota_threshold_flags
- Purpose: Set warning and hard-stop flags after monthly usage changes.
- Reads: `estimated_tiles_used`, `warning_threshold`, and `hard_stop_threshold`.
- Writes: `warning_triggered_at`, `hard_stop_active`, `hard_stop_triggered_at`, and `updated_at` when thresholds are crossed.
- Returns: None.

### apply_tile_usage_delta
- Purpose: Apply one tile delta to session/monthly totals and update threshold flags.
- Reads: `MapTileUsageSession`, `MapTileMonthlyQuota`, and tile delta.
- Writes: usage-session total, monthly estimated total, last-seen/update timestamps, warning state, and hard-stop state.
- Returns: normalised tile delta.

### record_browser_block
- Purpose: Record browser over-limit state for admin visibility.
- Reads: active `map_tile_browser_blocks` rows for the browser/reason.
- Writes: new or updated `MapTileBrowserBlock` row.
- Returns: `MapTileBrowserBlock` row.
- Notes: Redis rolling-window counting remains the enforcement source; this DB row gives admins something visible/resettable.

### release_browser_blocks
- Purpose: Mark active browser block rows as released.
- Reads: active `map_tile_browser_blocks` rows for the browser.
- Writes: `released_at`, `released_by_user_id`, `release_reason`, and `updated_at`.
- Returns: count of released rows.

### set_viewers_only_blocked
- Purpose: Block/unblock anonymous viewer satellite access.
- Reads: desired boolean state.
- Writes: `viewers_only_blocked` and `updated_at`.
- Returns: None.

### set_global_hard_stop
- Purpose: Manually set or clear global hard-stop state.
- Reads: desired boolean state.
- Writes: `hard_stop_active`, optionally `hard_stop_triggered_at`, and `updated_at`.
- Returns: None.

### set_monthly_thresholds
- Purpose: Manually update the active monthly quota row's monthly limit, warning threshold, and hard-stop threshold.
- Reads: submitted threshold values and current monthly estimate.
- Writes: `monthly_limit`, `warning_threshold`, `hard_stop_threshold`, recalculated warning/hard-stop flags, and `updated_at`.
- Returns: None.
- Notes: values are stored in the current DB row only; `.env` values continue to seed newly-created billing-cycle rows.

### set_monthly_tile_estimate
- Purpose: Manually correct the app-estimated monthly Esri tile usage.
- Reads: corrected estimate and current quota thresholds.
- Writes: `estimated_tiles_used`, recalculated warning/hard-stop flags, and `updated_at`.
- Returns: None.
- Notes: if the corrected estimate falls below thresholds, automatic warning/hard-stop state is cleared because the previous estimate was treated as inaccurate.

### set_monthly_override
- Purpose: Enable a temporary monthly hard-stop override.
- Reads: override duration and optional reason.
- Writes: `override_active`, `override_until`, `override_reason`, and `updated_at`.
- Returns: None.

### clear_monthly_override
- Purpose: Disable the active monthly override.
- Reads: current quota row.
- Writes: `override_active`, `override_until`, `override_reason`, and `updated_at`.
- Returns: None.

### record_quota_audit_event
- Purpose: Write map quota admin/system actions to `auth_audit_events`.
- Reads: actor user id, action name, and safe JSON metadata.
- Writes: `AuthAuditEvent` row.
- Returns: `AuthAuditEvent` row.

### monthly_block_reason
- Purpose: Convert monthly quota state into a frontend-safe fallback reason.
- Reads: current quota row, role, and admin flag.
- Writes: None.
- Returns: reason string such as `monthly_limit` or `viewers_disabled`, or None when not blocked.

### quota_payload
- Purpose: Convert a monthly quota row into JSON/admin display data.
- Reads: `MapTileMonthlyQuota` row fields.
- Writes: None.
- Returns: dictionary safe to expose to the browser.

### Checks
- Billing cycle uses the 25th-to-25th rule.
- Missing quota rows are created with environment-derived limits.
- Tile deltas update usage session and monthly quota totals once.
- Monthly hard-stop is activated when the hard-stop threshold is crossed.
- Browser block rows are visible/releasable for admins.
- Admin quota actions are captured in `auth_audit_events`.

## src/web/map_tile_quota.py

Module header documents:
- `GET /admin/map_tile_quota`
- `GET /api/map/config-status`
- `POST /api/map/tile-usage`
- `POST /admin/map_tile_quota/browser/<browser_cookie_id>/reset`
- `POST /admin/map_tile_quota/global-toggle`
- `POST /admin/map_tile_quota/runtime-limits`
- `POST /admin/map_tile_quota/runtime-limits/clear`
- `POST /admin/map_tile_quota/monthly-thresholds`
- `POST /admin/map_tile_quota/monthly-estimate`
- `POST /admin/map_tile_quota/monthly-override`
- `POST /admin/map_tile_quota/monthly-override/clear`

### bp_map_tile_quota
- Purpose: Flask Blueprint for map tile quota admin and browser map configuration routes.
- Reads: None at definition time.
- Writes: route registration for all map quota routes.
- Called from:
  - `src.main:create_app`, where the blueprint is registered on the Flask app.
- Notes: admin routes use `admin_required`; public config/usage routes remain anonymous so viewer maps can load and report usage.

### _config_int
- Purpose: Read integer map-limit values from Flask app configuration.
- Reads: Flask `current_app.config`.
- Writes: None.
- Returns: parsed integer or a safe default.

### _runtime_limit_overrides
- Purpose: Load process-only admin overrides for browser tile limit and rolling-window timeout.
- Reads: Flask `current_app.extensions`.
- Writes: creates `current_app.extensions["map_tile_quota_runtime_limit_overrides"]` if missing.
- Returns: mutable runtime override dictionary.
- Notes: these values do not edit `.env` and reset when the server process/container restarts.

### _runtime_config_int
- Purpose: Read effective browser limit values, preferring process-only overrides over `.env` config.
- Reads: runtime override dictionary and Flask `current_app.config`.
- Writes: None.
- Returns: parsed integer value.

### _runtime_limit_config
- Purpose: Build display state for the admin quota page showing current/default browser limits.
- Reads: runtime override dictionary and Flask `current_app.config`.
- Writes: None.
- Returns: dictionary containing current value, `.env` default, and overridden flag for each runtime limit.

### _map_quota_config
- Purpose: Gather map quota defaults from Flask configuration for the service layer.
- Reads: `MAP_TILE_MONTHLY_LIMIT`, `MAP_TILE_WARNING_THRESHOLD`, and `MAP_TILE_HARD_STOP_THRESHOLD` from Flask config.
- Writes: None.
- Returns: dictionary containing quota default values.

### _get_redis_client
- Purpose: Create/reuse the Redis client used for browser block checks.
- Reads: `AUTH_RATE_LIMIT_STORAGE_URL` from Flask app config.
- Writes: `current_app.extensions["map_tile_quota_redis"]` when the client is first created.
- Returns: Redis client.
- Raises: `RuntimeError` if Redis configuration is missing.
- Notes: this stays in the web layer because it uses Flask `current_app` extension storage.

### _current_browser_role
- Purpose: Convert Flask-Login state into a quota role snapshot.
- Reads: `current_user`.
- Writes: None.
- Returns: tuple of role string and admin boolean.

### _current_user_id
- Purpose: Return the current logged-in user id for usage analytics/admin audit events.
- Reads: `current_user`.
- Writes: None.
- Returns: user id or None.

### _safe_int
- Purpose: Parse optional integer request values.
- Reads: submitted request value.
- Writes: None.
- Returns: parsed integer or default.

### _hash_request_value
- Purpose: Hash request metadata before storing it in usage analytics.
- Reads: raw metadata such as user-agent or IP.
- Writes: None.
- Returns: SHA-256 hex digest or None.

### admin_map_tile_quota (GET `/admin/map_tile_quota`)
- Purpose: Render the admin map tile quota management page.
- Reads: current quota row through `src.services.map_tile_quota`, recent unreleased `MapTileBrowserBlock` rows, runtime browser limit override state, and Redis connectivity status.
- Writes: creates the current billing-cycle quota row if missing.
- Renders: `templates/map_tile_quota.html`.
- Access: protected with `admin_required`.

### map_config_status (GET `/api/map/config-status`)
- Purpose: Tell the frontend whether Esri satellite config may be released or whether it must fall back to OpenStreetMap.
- Reads: browser cookie, Redis browser block/rolling-window state, current quota row through `src.services.map_tile_quota`, current role, and map provider config.
- Writes: anonymous browser id cookie when missing; creates the current billing-cycle quota row if missing.
- Returns: JSON containing `satelliteAllowed`, provider/fallback info, reason, role, rolling browser count, quota status, and Esri config only when allowed.

### map_tile_usage (POST `/api/map/tile-usage`)
- Purpose: Record browser tile deltas and enforce browser/monthly quota state.
- Reads: JSON `tiles_delta`, optional `race_id`, optional `page_path`, optional `session_key`, browser cookie, Redis rolling-window state, current quota row, current role, and map limits.
- Rate limit: `120 per minute` through Flask-Limiter/Redis.
- Writes: browser id cookie when missing, Redis minute bucket count, `MapTileUsageSession`, `MapTileMonthlyQuota`, `MapTileBrowserBlock` when browser limit is exceeded, and threshold flags when crossed.
- Returns: JSON containing updated satellite/fallback state, usage session key, rolling browser count, and quota payload.
- Notes: this route expects browser JavaScript to send the CSRF token because it is a browser POST.

### reset_browser_quota (POST `/admin/map_tile_quota/browser/<browser_cookie_id>/reset`)
- Purpose: Admin reset for one browser's rolling-window quota state.
- Reads: browser cookie id path parameter and current admin user.
- Writes: clears Redis rolling-window bucket keys, releases active DB block rows, and records an audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.

### global_toggle (POST `/admin/map_tile_quota/global-toggle`)
- Purpose: Admin update for viewer-only block and global hard-stop flags.
- Reads: submitted form values and current quota row.
- Writes: quota flags and audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.

### runtime_limits (POST `/admin/map_tile_quota/runtime-limits`)
- Purpose: Admin process-only override for `MAP_TILE_USER_LIMIT` and `MAP_USER_LIMIT_TIMEOUT_MIN`.
- Reads: submitted browser limit/window values and current quota row.
- Writes: Flask process memory under `current_app.extensions`, plus audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.
- Notes: does not edit `.env`; restart or restore action returns behaviour to environment defaults.

### clear_runtime_limits (POST `/admin/map_tile_quota/runtime-limits/clear`)
- Purpose: Clear process-only browser limit overrides and restore `.env` defaults.
- Reads: current quota row.
- Writes: clears Flask process-memory overrides and records an audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.

### monthly_thresholds (POST `/admin/map_tile_quota/monthly-thresholds`)
- Purpose: Update the current billing-cycle monthly limit, warning threshold, and hard-stop threshold.
- Reads: submitted threshold values and current quota row.
- Writes: `map_tile_monthly_quota.monthly_limit`, `warning_threshold`, `hard_stop_threshold`, recalculated threshold flags, and audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.
- Notes: this persists in the database for the active billing cycle; it does not edit `.env`.

### monthly_estimate (POST `/admin/map_tile_quota/monthly-estimate`)
- Purpose: Correct the current billing-cycle Esri tile estimate when Esri platform totals differ from the app estimate.
- Reads: submitted corrected estimate and current quota row.
- Writes: `map_tile_monthly_quota.estimated_tiles_used`, recalculated warning/hard-stop flags, and audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.
- Notes: this persists in the database for the active billing cycle; it does not edit `.env` monthly thresholds.

### monthly_override (POST `/admin/map_tile_quota/monthly-override`)
- Purpose: Admin enables a temporary monthly hard-stop override.
- Reads: submitted duration/reason and current quota row.
- Writes: override fields and audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.

### clear_monthly_override_route (POST `/admin/map_tile_quota/monthly-override/clear`)
- Purpose: Admin clears the active monthly hard-stop override.
- Reads: current quota row.
- Writes: override fields and audit event.
- Returns: redirect to `/admin/map_tile_quota`.
- Access: protected with `admin_required`.

### Checks
- All ten map quota routes are registered.
- Admin routes are protected by `admin_required`.
- Config-status sets the anonymous browser id cookie when missing.
- Config-status does not release Esri config when Redis, quota, monthly, browser, or provider checks block satellite usage.
- Tile usage POST updates Redis rolling-window state and DB usage/quota state.
- Billing cycle uses the 25th-to-25th service-layer rule.
- Runtime browser limit overrides affect the current server process only and can be restored to `.env` defaults.
- Monthly estimate correction updates the current DB quota row and records an audit event.

## Alembic Migration Workflow

### Current baseline
- Purpose: the active Alembic baseline is [438e4bd69220_baseline_schema.py](/home/matthew/Desktop/Master_Dev/Enduro_Tracker_WebApp/migrations/versions/438e4bd69220_baseline_schema.py), which can build the current PostgreSQL schema from an empty database.
- Notes: legacy pre-baseline revisions are kept in [migrations/versions_legacy](/home/matthew/Desktop/Master_Dev/Enduro_Tracker_WebApp/migrations/versions_legacy) for reference only and are no longer part of the active migration chain.
- Current head: [e4f7a2c9d6b1_add_dashboard_profile_fields.py](/home/matthew/Desktop/Master_Dev/Enduro_Tracker_WebApp/migrations/versions/e4f7a2c9d6b1_add_dashboard_profile_fields.py) replaces `races.active` with the constrained `status` lifecycle, adds race location/static-logo fields, and adds the nullable Rider profile-media key. Existing active races migrate to `upcoming`; inactive races migrate to `draft` because their historical intent cannot be inferred safely. Rider image bytes are stored in the separate persistent volume, not PostgreSQL.

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

## Home web layer

- Parent directory: `src/web`
- File: `home.py`

### _selected_dashboard_tab
- Description: validates the optional `?tab=` selection against upcoming, live, past, and riders, defaulting invalid/missing values to upcoming.
- Called from: `_render_public_dashboard`.
- Why this layer: query-string parsing is an HTTP concern.

### _render_public_dashboard / _render_admin_dashboard
- Description: own the separate request-scoped session and rendering boundaries for the public composed dashboard and dense all-races administration dashboard.
- Called from: `dashboard` and `dashboard_admin` respectively.

### home_page (GET `/`)
- Description: renders the public `templates/landing.html` page.
- Called from: direct public page loads and the Landing link on the public dashboard.
- Why this layer: it is a Flask route whose only responsibility is template rendering.

### robots_txt (GET `/robots.txt`)
- Description: returns plain-text crawler guidance that allows public viewer pages, excludes authenticated administration, device, RFID, rider, race-management, and device-ingest paths, and points crawlers to the canonical production sitemap URL.
- Called from: search-engine and other cooperative web crawlers, plus direct production verification requests.
- Why this layer: it is a static Flask HTTP response with no reusable parsing, database coordination, or domain logic, so no utility or service module is needed.
- Security: the directives do not protect routes; login and role decorators remain the access-control boundary.
- Host behavior: the same response is served through the root production, application, and development hostnames because crawler path coverage is host-independent; the advertised sitemap stays on `https://kooksnylive.co.za`.

### sitemap (GET `/sitemap.xml`)
- Description: returns a UTF-8 XML sitemap containing the canonical production landing page, public dashboard, and current `/rider/<id>` URLs.
- Called from: the sitemap directive in `robots.txt`, Google Search Console submissions, and search-engine crawlers.
- Why this layer: XML/URL construction is an HTTP concern; the stable Rider id query is delegated to `list_public_rider_ids`.
- Scope: `/rider` redirects to the dashboard and is not listed; each distinct read-only `/rider/<id>` profile is listed. Public race details/results can be added later when their canonical/indexing policy is finalized.
- Host behavior: every application hostname can serve the route, but every `<loc>` uses the preferred `https://kooksnylive.co.za` canonical domain.

### dashboard (GET `/dashboard`)
- Description: validates `?tab=`, composes upcoming/live/completed races plus all riders, and renders the server-first tabbed `templates/dashboard.html`.
- Called from: landing/dashboard navigation and direct public requests.
- Why this layer: it selects the public HTTP view while the service owns race retrieval/preparation.

### dashboard_admin (GET `/dashboard-admin`)
- Description: calls `_render_admin_dashboard` for every race status and renders the dense, branded `templates/dashboard_admin.html`.
- Called from: admin login/navigation and direct admin requests.
- Why this layer: it selects the protected admin HTTP view and applies `admin_required`.

### Checks
- Anonymous users can load `/` and `/dashboard`.
- Anonymous users can load `/robots.txt` as `text/plain` and receive the expected allow, protected-path disallow, and sitemap directives through both production and development hostnames.
- Anonymous users can load `/sitemap.xml` as `application/xml`; it contains the canonical landing page/dashboard plus current public rider-detail URLs, while redirect-only `/rider` remains omitted.
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
  base_new.css
  dashboard.css
  dashboard-admin.css
  forms.css
  forms_new.css
  tables.css
  tables_new.css
  maps.css
  rider-profile.css
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
- Rule: keep the self-contained public split-screen dashboard in `dashboard.css`; it deliberately does not modify shared `base.css`.
- Rule: put only the dense admin-dashboard layout/brand refinements in `dashboard-admin.css`.
- Rule: put the reusable standalone/dialog rider profile card in `rider-profile.css`.
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

### Parallel `_new` visual migration foundation
- Purpose: allow the dashboard-derived visual language to replace the legacy shared interface one reviewed page at a time without changing unmigrated templates.
- New files: `base_new.css`, `forms_new.css`, and `tables_new.css`. Existing `base.css`, `forms.css`, and `tables.css` remain unchanged and continue serving every current template.
- Naming decision: the requested new table layer is named `tables_new.css`, rather than overwriting the existing `tables.css`, so the changeover remains genuinely parallel and cannot restyle old pages accidentally.
- Design direction: uses the dashboard's near-black ink, neutral page/paper surfaces, Kooksny green, large rounded cards, pill/artwork actions, strong compact headings, native mobile disclosure navigation, flat mobile result rows, soft shadows, and accessible focus/reduced-motion behavior.
- Compatibility: retains established classes including `.page-shell`, `.page-header`, `.btn`, `.content-panel`, `.form-grid`, `.filter-grid`, `.table-card`, `.wide-table`, and `.actions`. New opt-in patterns include `.art-action`, `.mobile-section-navigation`, `.image-upload-preview`, and `.compact-list`; `base_new.css` also supplies compatibility aliases for existing `--color-*` variables so reviewed page-specific styles can move gradually.
- Current usage: `templates/landing.html` loads `base_new.css` followed by its isolated `landing.css`; `templates/login.html` loads `base_new.css`, `forms_new.css`, and its isolated `login.css` in that order. All other templates retain their reviewed legacy or page-scoped styles until migrated individually.
- Per-page migration order: first review the page and its page-specific CSS/JS; then replace its legacy shared links with `base_new.css`, the needed `forms_new.css`/`tables_new.css`, and any reviewed page override; test desktop/mobile/accessibility; finally record that individual template as migrated.
- Load order example for a future form/table page:
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/base_new.css') }}" />
<link rel="stylesheet" href="{{ url_for('static', filename='css/forms_new.css') }}" />
<link rel="stylesheet" href="{{ url_for('static', filename='css/tables_new.css') }}" />
```
- Artwork actions: use `.art-action` with `.art-action-image` and `.art-action-label`; add `.compact-on-mobile` only after confirming the surrounding 42px action column remains usable and the text label may become visually hidden on mobile.
- Mobile section navigation: use a native `<details class="mobile-section-navigation" data-ui-disclosure>` with a `.mobile-section-navigation-toggle`, `.mobile-section-navigation-menu`, and links marked `data-ui-disclosure-option`. The server-rendered links remain functional without JavaScript; `base_new.js` adds close-after-selection and Escape behavior. An in-place selector may add `data-ui-disclosure-restore-focus`, while ordinary navigation links retain their default focus/scroll behavior.
- Image previews: a migrated image-upload form may pair `.image-upload-preview` with an input whose `data-image-preview-target` names the image id. `forms_new.js` updates only the local preview and revokes temporary object URLs; the server continues to validate and save the upload.
- Compact lists: non-tabular event/profile collections use `.compact-list` and `.compact-list-row`, with `.compact-list-media`, `.compact-list-content`, `.compact-list-primary-link`, metadata/summary, and an optional `.compact-list-action`. The primary link stretches across the row while the action retains the higher stacking level supplied by `.art-action`.
- Table responsiveness: horizontal scrolling remains the safe default. A reviewed simple table can opt into labelled mobile rows with `data-responsive-table` and `components/tables_new.js`; `data-responsive-table="compact"` selects the flatter separator-led variant. Complex colspan/rowspan tables should remain horizontally scrollable until deliberately redesigned.

### Current base.css usage
- Purpose: Provide the lean shared static stylesheet for Flask-rendered operational/simple pages. The public dashboard is intentionally self-contained in `dashboard.css`; the admin dashboard loads base/table styles and its scoped page stylesheet.
- Reads: CSS custom properties defined in `:root` for navy, white, forest green, neutral surfaces, borders, text, and shadows.
- Writes: Browser presentation only; no application data is changed.
- Styles: theme variables, page shell, page header, primary buttons, section titles, muted text, empty state, and mobile layout adjustments.
- Called from:
  - `templates/dashboard_admin.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/placeholder.html`: linked through `url_for('static', filename='css/base.css')`.
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

### Dashboard and shared component files
- Purpose: Provide reusable component stylesheets that are loaded after `base.css` by pages that need them.
- Current state: legacy `base.css`, `forms.css`, `tables.css`, and `maps.css` remain unchanged for unmigrated pages. The landing page now uses `base_new.css` plus `landing.css`, and Login uses `base_new.css`, `forms_new.css`, plus `login.css`; neither needs a `_new` JavaScript component. The live dashboard remains self-contained in `dashboard.css`.
- `landing.css`: owns the public landing page's plain-black viewport, upper-centred responsive logo/name lockup, visible two-line description, and three-column artwork navigation. The semantic links and text labels remain functional without JavaScript.
- `login.css`: owns the black login shell, dashboard-style linked brand, centred white form card, dark-on-white icon treatment, four-column desktop actions, and two-by-two mobile actions.
- `dashboard.css`: owns the two-pane viewport, shrinking hero, accessible desktop tabs/cards, mobile hero accordion, compact responsive rows, artwork actions, and rider dialog shell.
- `dashboard-admin.css`: gives the protected operations dashboard matching brand/card typography while retaining its compact tool grid and wide race table.
- `rider-profile.css`: styles the same read-only rider card on its canonical page and inside the dashboard dialog.
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
    base_new.js
    maps.js
    forms.js
    forms_new.js
    tables_new.js
    polling.js
  pages/
    dashboard.js
    race-form.js
    race-entry.js
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

### Parallel `_new` JavaScript migration foundation
- Purpose: mirror the CSS changeover so new shared behavior can be adopted per page without replacing scripts still used by legacy templates.
- New files: `components/base_new.js`, `components/forms_new.js`, and `components/tables_new.js`; no current template loads them.
- `base_new.js`: exposes `window.EnduroUI.ready`, `enhance`, `announce`, `setBusy`, and `attachDisclosures` for DOM readiness, progressive-enhancement markers, accessible live messages, consistent busy states, and native disclosure close/focus behavior.
- `forms_new.js`: preserves `window.EnduroForms.attachAutoSubmitSelects` for page-script compatibility and adds opt-in file-name output, local image preview, and submit-once helpers. It uses only explicit `data-*` markers, revokes replaced preview object URLs, and initializes idempotently.
- `tables_new.js`: exposes responsive label and horizontal-scroll-state helpers. It recognizes `data-responsive-table="compact"` for the flat mobile mode and refuses responsive enhancement when body/header cell counts do not match, leaving complex tables in their complete server-rendered form.
- Changeover rule: do not load a legacy component and its `_new` counterpart on the same page. When a behavior-heavy page is redesigned, create `pages/<page-name>_new.js`, preserve its old page script, and switch only the reviewed template to the new component/page chain.
- Future migrated load order:
```html
<script defer src="{{ url_for('static', filename='js/components/base_new.js') }}"></script>
<script defer src="{{ url_for('static', filename='js/components/forms_new.js') }}"></script>
<script defer src="{{ url_for('static', filename='js/components/tables_new.js') }}"></script>
<script defer src="{{ url_for('static', filename='js/pages/example-page_new.js') }}"></script>
```
- Progressive enhancement: all primary navigation, form fields, submissions, and table content must remain usable without these scripts, consistent with the server-rendered web interface in `Web Application System Design V4 - 20260224.pdf`.

### Current JS usage
- Purpose: Record the current incremental JavaScript migration state.
- Current state: current templates still use only `src/static/js/components/forms.js`, `src/static/js/components/maps.js`, `src/static/js/pages/dashboard.js`, `src/static/js/pages/race-form.js`, `src/static/js/pages/race-entry.js`, and `src/static/js/pages/post-race.js`. The three `_new` shared components exist as an inactive migration foundation.
- `pages/dashboard.js`: progressively enhances real server-rendered desktop tab/mobile disclosure/profile links, synchronizes hero/tab/URL state, closes the mobile accordion after selection, compacts the hero from the list pane's scroll position, implements keyboard navigation, and loads canonical rider pages into the native dialog. Navigation remains functional without JavaScript.
- `components/forms.js`: contains shared `data-auto-submit` select handling used by the category controls in `templates/race_form.html` and `templates/post_race.html`.
- `components/maps.js`: contains shared Leaflet map creation, selected-category route fetching, GeoJSON layer creation, map-bounds fitting, basemap switching, and Esri tile-usage reporting helpers used by the race form and post-race pages.
- `components/maps.js`: retains the OpenStreetMap base-layer helper and adds Esri satellite attach/remove helpers. The race form uses the existing OSM default. The post-race page creates an empty map, fits its selected route or rider track first, then attaches the backend-approved basemap so it requests only tiles near the visible course. After fitting the selected route it limits panning and minimum zoom to bounds padded by 25% on every side. It will use the retained OSM layer whenever `/api/map/config-status` does not allow satellite imagery. When Esri is attached, `createEsriTileUsageReporter` counts newly observed Esri tile resources and posts batched `tiles_delta` values to `/api/map/tile-usage`.
- `pages/race-form.js`: contains race-form-only GPX upload validation and rider/device auto-fill behaviour. It uses the shared form/map helpers for category auto-submit and route preview. The GPX input uses native required-field validation so an empty upload is blocked before navigation even when JavaScript is unavailable; the script supplies the GPX-specific text for the browser validation popup. The script reads the race id and category from `#map` data attributes and the rider/device mapping from the `#last-device-by-rider-data` JSON data node.
- `pages/race-entry.js`: contains entry-page-only administrator rider auto-submit and conditionally shows/requires the prior-device confirmation answer when the rider says they currently hold a device.
- `pages/post-race.js`: contains post-race-only live track/timing polling, track overlay controls, map size preferences, finish confirmation, Esri tile-usage reporter startup, and the manual timing/TXT upload modal. It reads safe bootstrap endpoint/page configuration from `#post-race-map-config`, fits the route before attaching the base layer, fetches `/api/map/config-status`, and only uses Esri satellite imagery when that response allows it. If the backend blocks satellite access, the tile-usage endpoint later returns `satelliteAllowed=false`, or the status request fails, it attaches OpenStreetMap and shows the configured unavailable message.
- Category-id asset cache control: templates append `v=20260720-category-id-v1` to the changed map and race page JavaScript URLs. This forces browsers and intermediate caches to fetch the `category_id` implementation instead of retaining the earlier category-name request code after deployment.
- Notes: `components/polling.js` does not exist yet because polling is currently used only by the post-race page. Move polling code there only when another page needs the same stable behaviour.
- External map dependencies: `templates/post_race.html` loads Leaflet 1.9.4, Esri Leaflet 3.0.19, and Esri Leaflet Vector 4.3.2 in that order. The Esri libraries make `L.esri.Vector.vectorBasemapLayer(...)` available for the later satellite-basemap implementation; loading them alone does not make an Esri request or replace the current OpenStreetMap layer.

## Dashboard and profile image structure

- Purpose: keep developer-managed brand/hero/race artwork in predictable Flask static directories while separating mutable Rider uploads from the application image.
- Structure:
```text
src/static/images/
  brand/logo.svg
  dashboard/heroes/upcoming.webp
  dashboard/heroes/live.webp
  dashboard/heroes/past.webp
  dashboard/heroes/riders.webp
  icons/login-rider.svg
  icons/signup-rider.svg
  icons/profile.svg
  icons/admin.svg
  icons/logout.svg
  races/default-race-logo.svg
  riders/default-profile.svg
```
- Hero/brand replacement: dashboard hero artwork uses WebP for efficient photographic imagery. Replace the four fixed `.webp` files above while retaining their filenames, or update `DASHBOARD_TAB_PRESENTATION` in `src/web/home.py` when a new filename is deliberate. The admin dashboard reuses `live.webp` as its background artwork.
- Race images: add a file beneath `src/static/images/races/` and enter only its basename in Race Logo/Image Filename on the race edit page.
- Rider default: `src/static/images/riders/default-profile.svg` remains the committed fallback when a Rider has no uploaded image.
- Rider uploads: authenticated Rider owners and administrators submit JPEG, PNG, or WebP through the rider form. The application creates a generated WebP key and writes the processed image beneath the `profile-images` volume mounted at `/var/lib/enduro-tracker/profile-images`.
- Validation: developer-managed Race basenames use `src.utils.media`; Rider uploads use bounded reads, decoded-format checks, pixel limits, metadata-stripping conversion, and generated filenames from `src.utils.profile_images` and `src.services.profile_images`.
- Storage boundary: mutable uploads must never be written into `src/static`, the Git checkout, or PostgreSQL binary columns. Dev and prod use different named volumes and require separate media backups.

## src/web/devices.py

### Overall description
- Layer: web/controller.
- Purpose: provide the two admin-only device registry routes while delegating validation and durable device rules to utilities/services.
- Why here: this module contains only Flask request parsing, access decoration, response/template selection, HTTP status mapping, and transaction boundaries.

### devices_index (GET/POST `/devices/`)
- Basic use: GET lists devices; POST normalizes submitted values and calls `create_device`.
- Layer reason: the function owns HTTP method/form handling, `admin_required`, commit/rollback, template rendering, and response codes.
- Renders: `templates/devices.html`.
- Access: active admin account through `admin_required`.
- Errors: returns 400 for validation/uniqueness problems and 500 for unexpected database errors.
- Called from:
  - `templates/dashboard_admin.html`: "Manage Devices" button (GET).
  - `templates/devices.html`: "Save" button in "Add a new device" form (POST).
  - `templates/device_edit.html`: "Back to Devices" link (GET).

### device_edit (GET/POST `/devices/<device_id>/edit`)
- Basic use: GET displays one device; POST normalizes editable values and calls `update_device`.
- Layer reason: the function maps the route path/form and service outcomes to the correct Flask template and HTTP response.
- Renders: `templates/device_edit.html`.
- Access: active admin account through `admin_required`.
- Errors: returns 404 for a missing device, 400 for validation/uniqueness problems, and 500 for unexpected database errors.
- Notes: the device id stays read-only and immutable.
- Called from:
  - `templates/devices.html`: "Edit" link in devices table (GET).
  - `templates/device_edit.html`: "Save" button (POST).

## RFID web layer

- Parent directory: `src/web`
- File: `rfid.py`

### rfid_index (GET `/rfid/`)
- Description: normalizes query parameters, delegates record retrieval to the RFID service, and renders `templates/rfid_view.html`.
- Called from:
  - `templates/dashboard_admin.html`: "View RFID Records" button (GET).
  - `templates/rfid_view.html`: filter form and "Clear" link (GET).
- Why this layer: it owns `request.args`, `admin_required`, template selection, session cleanup, and HTTP error mapping.
- Responses: 200 for a successful view, 400 for invalid filters, and 500 for unexpected database errors.

## Rider web layer

- Parent directory: `src/web`
- File: `riders.py`

### _profile_image_settings / _render_rider_form / _delete_obsolete_profile_image
- Description: read the configured media path/limit, supply consistent upload context to every form response, and best-effort remove a superseded generated file only after its Rider database update commits.
- Called from: `rider_form` for GET, validation/error responses, successful upload/replacement/removal, and database-failure cleanup.
- Why this layer: configuration access, Flask rendering, post-commit logging, and transaction-aware orchestration are controller concerns; image transformation/storage remain delegated.

### rider_form (GET/POST `/riders/new` and `/riders/<rider_id>/edit`)
- Description: renders the form/list on GET; on multipart POST it delegates Rider text changes, securely stores an optional generated WebP, updates only the server-owned media key, and removes the previous file after commit.
- Called from:
  - `templates/dashboard_admin.html`: "Input Rider Details" button (GET `/riders/new`).
  - `templates/riders_form.html`: "Edit" link in riders table (GET `/riders/<id>/edit`).
  - `templates/riders_form.html`: "Save" button in the rider form (POST create/update).
- Why this layer: it handles Flask form/path/file data, early multipart-size rejection, `rider_required`, the reused `user_can_access_rider_resource` ownership check, redirects, database/file transaction coordination, templates, and HTTP status codes.
- Access behavior: admins may create/edit any profile; riders may create one linked profile and edit only their own. GET `/riders/new` redirects an already-linked rider to their edit page.
- Responses: 200 for successful display/save, 302 for the profile redirect, 400 for text/image validation, 403 for forbidden access/linking, 404 for a missing rider, 413 for a clearly oversized multipart body, and 500 for unexpected database/storage errors.

## Race web layer

- Parent directory: `src/web`
- File: `races.py`
- Layer responsibility: all routes below parse Flask inputs, enforce access decorators, call the focused race services, own commit/rollback, and map outcomes to templates, redirects, JSON, or HTTP errors.

### _empty_race_form_page_data / _render_race_form
- Purpose: Build the blank race-management context and consistently render submitted values plus page/field feedback.
- Reads/Writes: No durable data.
- Called from: `new_race`, `save_race`, and `edit_race`.
- Notes: these remain web-layer helpers because they shape template response context rather than applying race domain rules.

### _post_race_map_bootstrap_config
- Purpose: Build safe browser bootstrap configuration for the post-race map.
- Reads: route names through `url_for`, current request path, and race id.
- Writes: None.
- Returns: endpoint URLs, race id, page path, and the satellite-unavailable message.
- Called from: `post_race` only.
- Notes: this helper deliberately does not include `ARCGIS_API_KEY`, provider, or style. The browser must call `/api/map/config-status` before Esri satellite imagery can be used.

### new_race (GET `/races/new`)
- Purpose: Render the "New Race" page.
- Reads: No durable categories; a new race starts with empty route/category state.
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
- Map config: renders only safe bootstrap values in `#post-race-map-config`; Esri provider/style/key must come from `/api/map/config-status` after quota checks.
- Called from:
  - `templates/dashboard.html`: full race-card title link in upcoming/live/past tabs.
  - `templates/dashboard_admin.html`: "Race Page" button in races table.
  - `templates/post_race.html`: category `<select>` auto-submit (GET with `?category_id=`).

### enter_race (GET/POST `/races/<race_id>/enter`)
- Purpose: Let an authenticated rider enter themselves through a staged category/device workflow and automatic locked device assignment.
- Reads: an `upcoming` or `live` Race, active/non-archived race-scoped `Category` rows and their shared `Route`, selected `Rider`, prior/current `RaceRider` assignments, and `Device` availability state.
- Writes: one `RaceRider` on a successful assignment; never changes `Device.returned` or `Device.active`.
- Renders: `templates/race_entry.html`.
- Access: active rider account through `rider_required`; administrators are redirected to the separate admin endpoint.
- Called from:
  - `templates/dashboard.html`: "Get Device" button on upcoming races.
- Notes: rider identity always comes from `current_user.rider_id`; submitted `rider_id` values are ignored. The confirmation shows both assigned category and device. Candidate rows are locked with PostgreSQL `FOR UPDATE SKIP LOCKED`.

### enter_race_admin (GET/POST `/races/<race_id>/entries/new`)
- Purpose: Provide the equivalent staged automatic-entry workflow when an administrator enters a selected rider on their behalf.
- Access: active admin account through `admin_required`.
- Identity boundary: submitted `rider_id` is accepted only on this explicitly administrator-authorized endpoint.
- Called from: `templates/dashboard_admin.html`: "Enter Rider" button.

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
  - Preserves the current history-first behavior: latest `track_hist`, then `track_cache` fallback.
  - History/cache selection and race scoping are delegated to `services/race_tracks.py`.
- Returns: GeoJSON payload (JSON).
- Called from:
  - `templates/post_race.html`: rider track checkbox toggles and 5-second live polling for selected riders.

### race_rider_timings (GET `/races/<race_id>/race-rider-timings`)
- Purpose: Return live `RaceRider` start/end timing values for the post-race riders table.
- Reads: `RaceRider`, `Category`, `Route`.
- Writes: None.
- Query params: `category_id` optionally scopes results to the selected post-race category.
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
- Behavior: parses date/time inputs and converts them to epoch seconds using the configured timezone for naive input. A blank or literal `None` image value selects the default artwork. A genuinely invalid optional image keeps the previous/default image, saves the other valid race fields, and redisplays the form with a success notice plus an inline image error; blocking validation failures redisplay submitted values with HTTP 400.
- Access: active admin account through `admin_required`.
- Redirects: to the edit page after a fully valid save; partial image-field saves render the populated edit page directly.
- Called from:
  - `templates/race_form.html`: "Save Changes" button.

### edit_race (GET `/races/<race_id>/edit`)
- Purpose: Edit page for race data, independent named-route/category setup, GPX upload, and rider assignments.
- Reads: `Race`, `Route`, `Category`, `Rider`, `Device`, `RaceRider`.
- Writes: None; route/category creation is performed only by explicit POST endpoints.
- Renders: `templates/race_form.html`.
- Access: active admin account through `admin_required`.
- Called from:
  - `templates/dashboard_admin.html`: "Edit" button in races table.
  - `templates/race_form.html`: category `<select>` auto-submit (GET with `?category_id=`).
  - Redirect from `save_race` after a successful save.

### add_race_route (POST `/races/<race_id>/routes/add`)
- Purpose: Create a named Route independently of categories so it can be reused.
- Reads: `Race` and existing same-race Route names.
- Writes: one `Route`.
- Access: active admin account through `admin_required`.
- Called from: `templates/race_form.html`: "Add Route" form.

### rename_route (POST `/races/<race_id>/routes/<route_id>/rename`)
- Purpose: Rename a same-race Route while preserving case-insensitive uniqueness.
- Writes: `Route.name`.
- Access: active admin account through `admin_required`.
- Called from: `templates/race_form.html`: "Rename Route" forms.

### delete_route (POST `/races/<race_id>/routes/<route_id>/delete`)
- Purpose: Hard-delete a Route only when no active or archived category uses it.
- Access: active admin account through `admin_required`.
- Conflict behavior: returns HTTP 409 when the route is referenced.

### add_race_category (POST `/races/<race_id>/categories/add`)
- Purpose: Create a freely named Category on an existing Route or create a new named Route inline.
- Reads: same-race `Route` and `Category` names.
- Writes: one `Category` and optionally one `Route`.
- Access: active admin account through `admin_required`.
- Called from: `templates/race_form.html`: "Add Category" form.

### edit_race_category (POST `/races/<race_id>/categories/<category_id>/edit`)
- Purpose: Rename, reorder, archive/restore, and reassign a race category.
- Writes: `Category.name`, `name_normalized`, `display_order`, `archived`, and `route_id`.
- Access: active admin account through `admin_required`.
- Called from: `templates/race_form.html`: "Save Category" forms.

### delete_race_category (POST `/races/<race_id>/categories/<category_id>/delete`)
- Purpose: Hard-delete only never-used categories; referenced categories retain their IDs and must be archived instead.
- Access: active admin account through `admin_required`.
- Conflict behavior: returns HTTP 409 when RaceRider or leaderboard data references the category.

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
  - `build_track_snapshot_from_raw_text`

### _build_gpx_string
- Purpose: Build a GPX 1.1 XML string from cleaned fixes.
- Reads: in-memory fixes list.
- Writes: None.
- Returns: GPX XML string.
- Called from:
  - `src/api/ingest.py:upload_text`
  - `build_track_snapshot_from_raw_text`

### _build_geojson_string
- Purpose: Build a GeoJSON LineString FeatureCollection from cleaned fixes.
- Reads: in-memory fixes list.
- Writes: None.
- Returns: compact GeoJSON string.
- Called from:
  - `src/api/ingest.py:upload_text`
  - `build_track_snapshot_from_raw_text`

### filter_fixes_by_window
- Purpose: Trim fixes to a start/finish epoch window (one-sided allowed).
- Reads: fix list with `utc` values.
- Writes: None.
- Returns: filtered fixes list.
- Called from:
  - `src/api/ingest.py:upload_text`
  - `build_track_snapshot_from_raw_text`

### build_track_snapshot_from_raw_text
- Purpose: Compose raw-text parsing, timing-window filtering, and GPX/GeoJSON serialization into one public helper.
- Reads: raw tracker text plus optional start/finish epochs.
- Writes: None.
- Returns: `(gpx_text, geojson_text)` or `None` when no fixes remain.
- Called from:
  - `services/race_timing.py:update_manual_race_rider_times`
- Notes: race services no longer import private GPX helper functions directly.

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
  - `services/race_routes.py:store_route_gpx`

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

## tests/test_devices_layers.py

### DeviceLayerTestCase
- Purpose: test pure normalization/validation plus returned/active state, service create/list/update, immutable-id, and uniqueness behavior.
- Database safety: uses only an isolated in-memory SQLite `devices` table.

### DeviceRouteTestCase
- Purpose: smoke-test the unchanged GET/POST device URLs, templates, status codes, create/edit persistence, duplicate rejection, and missing-device response.
- Isolation: unwraps only the separately tested admin decorator and replaces `SessionLocal` with the in-memory test session factory.

### DeviceAuthorizationTestCase
- Purpose: exercise the real `admin_required` wrapper, prove rider and inactive-admin POST requests cannot change `returned`/`active`, and verify an active admin can explicitly toggle handout, return, active, and inactive combinations.
- Database safety: uses an isolated in-memory SQLite `devices` table and patches only the authenticated user/session boundary.

### Run
```bash
.venv/bin/python -m unittest tests.test_devices_layers -v
```

## tests/test_riders_layers.py

### RiderLayerTestCase
- Purpose: test category-free rider normalization/validation, create/list/update operations, Rider/User linking, admin-created profiles, one-profile enforcement, secure raster normalization, generated key ownership, atomic storage, and deletion.
- Database safety: uses only isolated in-memory SQLite Rider and User tables.

### RiderRouteTestCase
- Purpose: smoke-test both rider URL patterns, multipart upload/replacement/removal, invalid-image responses, obsolete-file cleanup, text persistence, templates, redirects, ownership restrictions, and missing-rider responses.
- Isolation: unwraps only the shared route decorator and replaces `SessionLocal` with the in-memory test session factory.

### Run
```bash
.venv/bin/python -m unittest tests.test_riders_layers -v
```

## tests/test_home_rfid_rider_profiles_layers.py

### HomeLayerTestCase
- Purpose: test lifecycle filtering/grouping, start/end display conversion, all-rider composition, full dashboard template rendering, canonical sitemap entries, and controller delegation.

### RfidLayerTestCase
- Purpose: test filter normalization/parsing, query filtering/limits, display values, template rendering, and invalid-filter responses.

### RiderProfilesRouteTestCase
- Purpose: verify that public GET `/rider` redirects to the dashboard Riders tab, `/rider/<id>` renders a canonical public profile, and `/rider/<id>/profile-image` safely serves a generated-key WebP with nosniff behavior.

### Database safety
- Home and RFID tests use isolated in-memory SQLite model tables and do not access configured application databases.

### Run
```bash
.venv/bin/python -m unittest tests.test_home_rfid_rider_profiles_layers -v
```

## tests/test_races_layers.py

### RaceLifecycleAndRouteServiceTestCase
- Purpose: test race start/end/location/logo/status parsing and save behavior, `category_id` page composition, route/category create/rename/reorder/archive/reassignment, guarded unused deletion, shared routes, GPX storage/clearing, same-race references, and normalized case-insensitive uniqueness.

### RaceEntryTimingTrackServiceTestCase
- Purpose: test assignment management, per-race rider/device uniqueness, cross-race Category rejection, shared rider/device listing reuse, timing payloads, confirmation rules, manual trimmed snapshots, and history/cache fallback.

### AutomaticRaceEntryServiceTestCase
- Purpose: test form parsing, forged device-id exclusion, prior-device preference, every active/returned availability combination, inactive-held-device replacement, confirmed-held-device custody preservation, returned-inventory discrepancy reporting, already-used-device replacement, unavailable-device rollback, cross-race Category tampering, archived category rejection, and duplicate rider prevention.

### RaceControllerTestCase
- Purpose: smoke-test race form/save, route create/rename, category create/edit/archive, shared routes, `category_id` consumers, administrator entry, authenticated-rider identity isolation, assigned category/device display, route GeoJSON, timing polling, manual-time validation, and finish-confirmation HTTP contracts.

### Database safety
- Uses isolated in-memory SQLite race-related tables and does not access configured application databases.

### Run
```bash
.venv/bin/python -m unittest tests.test_races_layers -v
```

## tests/test_race_entry_postgresql_integration.py

### PostgreSQLRaceEntryIntegrationTestCase
- Purpose: verify `FOR UPDATE SKIP LOCKED` prevents two concurrent transactions from selecting one available Device, and verify PostgreSQL rejects duplicate rider/device assignments plus cross-race Category tampering through the composite foreign key.
- Safety gate: skipped unless `RUN_POSTGRES_INTEGRATION=1`; run it only against a disposable database because fixtures are committed for cross-session visibility and then cleaned up.
- Recommended workflow: start an isolated temporary PostgreSQL Compose project, apply `alembic upgrade head`, run the command below inside its application/migrator container, and tear the project down afterward.

### Run against a disposable migrated PostgreSQL database
```bash
RUN_POSTGRES_INTEGRATION=1 python -m unittest tests.test_race_entry_postgresql_integration -v
```

### Ordinary discovery behavior
- `python -m unittest discover -s tests -v` discovers these tests but reports them as skipped unless the explicit integration safety gate is enabled.

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
- `devices`: registered hardware devices; referenced by race_riders. Columns: `id`, `device_info`, `epc_id`, `returned`, `active`. Relationships: one device can map to many `race_riders` entries via `race_riders.device_id -> devices.id`, and can view many `ingest_rfid` rows through matching EPC values. Conditions: returned/active are required booleans defaulting true; primary-key uniqueness on `id`; `ux_devices_epc_id` enforces at most one device per non-null EPC tag.
- `points`: parsed GNSS fixes per device (t_epoch, lat/lon, optional metrics). Columns: `id`, `device_id`, `t_epoch`, `lat`, `lon`, `ele`, `sog`, `cog`, `fx`, `hdop`, `nsat`, `received_at`, `received_at_epoch`. Relationships: no enforced foreign key to `devices`; points are linked to `race_riders` through a view-only `device_id` join. Conditions: unique constraint `ux_points_device_time` enforces one row per (`device_id`, `t_epoch`).
- `riders`: race-independent athlete/public-profile details. Columns: `id`, `name`, `bike`, `bio`, `team`, `profile_image_filename`. Relationships: one rider can have many category-specific `race_riders` entries via `race_riders.rider_id -> riders.id`, and can optionally have one linked login account via `users.rider_id -> riders.id`. Conditions: `name` is required; no category is stored on the profile; the optional filename is an application-generated key for a normalized WebP in persistent profile-media storage. PostgreSQL does not contain the image bytes.
- `users`: browser login accounts for riders and admins. Columns: `id`, `first_name`, `last_name`, `username`, `username_normalized`, `email`, `email_normalized`, `password_hash`, `role`, `rider_id`, `is_active`, `auth_version`, `created_at`, `updated_at`, `last_login_at`. Relationships: optionally links one account to one `rider`, has many `auth_tokens`, and can be actor/target for `auth_audit_events`. Conditions: role is constrained to `rider` or `admin`; `username_normalized`, `email_normalized`, and non-null `rider_id` are unique; passwords are stored only as hashes. Notes: the model uses Flask-Login's `UserMixin` for standard login-session helpers such as `get_id()`.
- `auth_tokens`: one-time hashed authentication tokens, currently for password reset. Columns: `id`, `user_id`, `purpose`, `token_hash`, `expires_at`, `used_at`, `created_at`. Relationships: belongs to one `user`. Conditions: `user_id`, `purpose`, `token_hash`, `expires_at`, and `created_at` are required; raw tokens are never stored.
- `auth_audit_events`: security-relevant account event history. Columns: `id`, `actor_user_id`, `target_user_id`, `action`, `metadata_json`, `created_at`. Relationships: optional actor and target links to `users`. Conditions: `action` and `created_at` are required; metadata must not contain passwords, raw reset tokens, or other secrets.
- `map_tile_monthly_quota`: durable monthly Esri tile quota state for admin controls and hard-stop decisions. Columns: `id`, `billing_month`, `provider`, `estimated_tiles_used`, `monthly_limit`, `warning_threshold`, `hard_stop_threshold`, `warning_triggered_at`, `hard_stop_triggered_at`, `hard_stop_active`, `viewers_only_blocked`, `override_active`, `override_until`, `override_reason`, `last_usage_rollup_at`, `created_at`, `updated_at`. Relationships: none. Conditions: `billing_month` and `provider` are unique together via `ux_map_tile_monthly_quota_month_provider`; usage reports should increment `estimated_tiles_used` directly from tile deltas so hard-stop decisions happen immediately.
- `map_tile_usage_sessions`: summarized browser/page map tile usage sessions for analytics and quota evidence. Columns: `id`, `session_key`, `browser_cookie_id`, `user_id`, `role`, `race_id`, `billing_month`, `page_path`, `provider`, `session_started_at`, `session_last_seen_at`, `estimated_tiles_loaded`, `fallback_used`, `blocked_reason`, `user_agent_hash`, `ip_hash`, `created_at`, `updated_at`. Relationships: optionally links to `users` and `races`. Conditions: `session_key` is unique; `browser_cookie_id`, `role`, `billing_month`, `page_path`, provider, session timestamps, tile count, fallback flag, and timestamps are required. Notes: there is no `map_style` column and no `estimated_tiles_delta_unrolled` column because accepted usage-report deltas update both this table and `map_tile_monthly_quota.estimated_tiles_used` immediately.
- `map_tile_browser_blocks`: browser-level tile block history for admin visibility and early release. Columns: `id`, `browser_cookie_id`, `user_id`, `reason`, `tiles_at_block`, `blocked_at`, `blocked_until`, `released_at`, `released_by_user_id`, `release_reason`, `created_at`, `updated_at`. Relationships: optionally links to the affected `users` row and to the admin `users` row that released the block. Conditions: `browser_cookie_id`, `reason`, `blocked_at`, `blocked_until`, `created_at`, and `updated_at` are required. Notes: Redis remains the enforcement store for short-lived browser blocks; this table records block history and admin reset state. Quota admin actions should be recorded in `auth_audit_events` rather than a separate map-specific audit table.
- Migration: the map tile quota tables are created by `migrations/versions/4578a2e08ba3_add_esri_tile_quota_tables.py`. The migration is manually written because the dev database may already contain these tables from `Base.metadata.create_all()`; clean databases still receive normal `CREATE TABLE` operations.
- `races`: event/dashboard metadata. Columns: `id`, `name`, `description`, `website`, `location`, `logo_image_filename`, `starts_at`, `starts_at_epoch`, `ends_at`, `ends_at_epoch`, `status`. Relationships: one race can have many `route` rows via `route.race_id -> races.id`. Conditions: `name` and `status` are required; `ck_races_status` allows only `draft`, `upcoming`, `live`, and `completed`; optional image filenames refer to developer-managed files beneath `src/static/images/races/`. Entry is open only for upcoming/live races; draft races remain off the public dashboard and completed races appear under Past Races.
- `route`: named per-race route geometry storage. Columns: `id`, `race_id`, `name`, `geojson`, `gpx`. Relationships: belongs to one `race` and can have many shared `categories`. Conditions: `race_id` and `name` are required; `ck_route_name_trimmed_nonempty` rejects blank/padded names; `ux_route_race_name_ci` makes names case-insensitively unique per race; `ux_route_id_race_id` exposes the exact composite key required by category race-scope enforcement.
- `categories`: race-scoped category labels tied to a route. Columns: `id`, `route_id`, `race_id`, `name`, `name_normalized`, `display_order`, `archived`. Relationships: belongs to one same-race `route`, can share that route with other categories, and is referenced by race entries and leaderboard history. Conditions: the composite route key prevents cross-race assignment; normalized names are unique per race; names must be trimmed/non-empty; normalized identity must equal `lower(name)`; order must be positive. Archived rows retain history but are excluded from active selection.
- `race_riders`: joins a rider, device, race, and category while storing timing and status flags. Columns: `id`, `race_id`, `rider_id`, `device_id`, `category_id`, `comm_setting`, `active`, `recording`, `start_time_rfid`, `start_time_rfid_epoch`, `finish_time_rfid`, `finish_time_rfid_epoch`, `start_time_pi`, `start_time_pi_epoch`, `finish_time_pi`, `finish_time_pi_epoch`, `multiple_rfid_flag`, `finish_time_rfid_confirmed`. Relationships: each row belongs to one rider, device, and same-race category, with one-to-one links to track cache/history. Conditions: `(category_id, race_id) -> categories(id, race_id)` prevents cross-race category assignment; `ux_race_riders_race_rider` permits one entry per rider per race; `ux_race_riders_race_device` permits one assignment per device per race; required status/timing flags retain their existing defaults.
- `leaderboard_cache`: live leaderboard snapshot per category. Columns: `category_id`, `payload_json`, `etag`, `updated_at`, `updated_at_epoch`. Relationships: one-to-one with `categories` via `category_id` as both foreign key and primary key. Conditions: `payload_json` and `updated_at` are required (`NOT NULL`).
- `track_cache`: live track geojson per race_rider. Columns: `race_rider_id`, `geojson`, `etag`, `updated_at`, `updated_at_epoch`. Relationships: one-to-one with `race_riders` via `race_rider_id` as both foreign key and primary key. Conditions: `updated_at` is required (`NOT NULL`).
- `leaderboard_hist`: archived leaderboard snapshots per category. Columns: `id`, `category_id`, `payload_json`, `official_pdf`, `updated_at`, `updated_at_epoch`. Relationships: many history rows can belong to one `category` via `category_id -> categories.id`. Conditions: `category_id`, `payload_json`, and `updated_at` are required (`NOT NULL`).
- `track_hist`: archived track snapshots per race_rider (geojson/gpx/raw text). Columns: `id`, `race_rider_id`, `geojson`, `gpx`, `raw_txt`, `updated_at`, `updated_at_epoch`. Relationships: many history rows can belong to one `race_rider` via `race_rider_id -> race_riders.id`. Conditions: `race_rider_id` and `updated_at` are required (`NOT NULL`).

## Templates (templates/*.html)

### landing.html
- General: Public landing page.
- Displays: upper-centred Kooksnylive logo/name lockup with independently enlarged artwork offset 7.5 mm left on desktop and 5 mm left on mobile while the heading remains unchanged and centred, the two-line "Live Realtime Hard Enduro Rider Tracking" / "Bringing Enduro to the Fans" description, and high-level public actions on a plain-black viewport.
- Search metadata: uses a descriptive live enduro/motocross tracking title and description, and declares `https://kooksnylive.co.za/` as the canonical landing-page URL.
- Styles: Loads `src/static/css/base_new.css` first for the dashboard-derived next-generation shared theme, then `src/static/css/landing.css` for the isolated landing composition; no legacy shared CSS or JavaScript is loaded.
- UI actions: icon-led links with visible labels for "View Races", "Sign Up", and "Login", presented as one responsive horizontal row; View Races uses the white artwork variant for the black landing background.
- Linked pages (buttons):
  - "View Races" → `/dashboard` (public race dashboard).
  - "Sign Up" → `/signup` (rider signup).
  - "Login" → `/login` (rider/admin login).
- Pulls: none.
- Pushes: none.
- Routes called: `/`, `/dashboard`, `/signup`, `/login`.
- Embedded scripts: none.

### login.html
- General: Public rider/administrator login form.
- Displays: a dashboard-style linked Kooksnylive brand on a black page, a centred white card containing the Login heading/description, username/email identifier, password, authentication feedback, and icon-led account/public navigation.
- Styles: Loads `src/static/css/base_new.css`, `src/static/css/forms_new.css`, and the isolated `src/static/css/login.css` in that order; no legacy shared CSS or JavaScript is loaded.
- UI actions: icon-led "Login", "Forgot Password", "Sign Up", and "View Races" controls in one desktop row and a two-by-two mobile grid.
- Pulls: `form`, `message`, and `success`.
- Pushes: CSRF-protected username/email and password POST to `/login`.
- Routes called: `/login`, `/forgot-password`, `/signup`, and `/dashboard`.
- Embedded scripts: none; the form and navigation remain fully functional without JavaScript.
- Manual verification: confirm the black desktop/mobile shell, top-left home link, centred white card, one-row desktop actions, two-by-two mobile actions, visible keyboard focus, generic invalid-credential feedback, retained identifier after failure, successful rider/admin redirects, and working password-reset/signup/View Races links.

### dashboard.html
- General: Public, server-rendered race/rider dashboard based on the supplied hero-and-list mockup.
- Displays: separate upcoming, live, past/completed, and all-riders panels; race location/logo/date range/status; rider portrait/team/bike/short biography; enlarged responsive account artwork; and a compacting hero above the independently scrolling list.
- Search metadata: declares the base `/dashboard` canonical URL so `?tab=` variants do not compete in the index.
- Styles: Uses page-scoped `src/static/css/dashboard.css` plus the reusable profile card in `src/static/css/rider-profile.css`; desktop retains the full cards/tablist while widths up to 720px use a flat separated list and native hero accordion. Shared `base.css` and table styles are deliberately not applied.
- UI actions: desktop tab navigation, mobile accordion navigation, race-row links, artwork-led "Get Device" and "Results" controls, rider popup links, login/signup, role-aware profile/admin navigation, and CSRF-protected logout.
- Linked pages (buttons):
  - Brand → `/`.
  - "Login" → `/login`.
  - "Sign Up" → `/signup`.
  - "My Profile"/rider rows → `/rider/<id>` (loaded into the native dialog when JavaScript is available).
  - Race card → `/races/<id>/post` (public race page).
  - "Get Device" → `/races/<id>/enter`.
  - "Results" → `/races/<id>/results`.
- Pulls: `race_sections`, `riders`, `tab_presentation`, and `selected_tab`.
- Pushes: only the authenticated logout POST; tabs use GET `?tab=` URLs and JavaScript history enhancement.
- Routes called: `/dashboard?tab=...`, `/`, `/login`, `/signup`, `/logout`, `/rider/<id>`, `/races/<id>/post`, `/races/<id>/enter`, `/races/<id>/results`.
- Script: `src/static/js/pages/dashboard.js` enhances tabs, synchronizes and closes the mobile accordion, manages hero compaction/keyboard navigation/URL state, and loads rider dialogs while retaining functional real links.
- Notes: draft races are never rendered publicly; no admin mutation controls are shown here. Mobile suppresses the duplicate panel heading/description, keeps the count above the list, removes unused vertical hero space in normal/compact accordion states, and uses `get_devices.svg`/`results.svg` as compact independently labelled actions.

### dashboard_admin.html
- General: Dense protected admin operational dashboard in the public dashboard's brand language.
- Displays: compact administration tool cards and every race with start, location, external website, lifecycle status, and operational actions.
- Search metadata: declares `noindex,nofollow`; `robots.txt` also continues to exclude `/dashboard-admin` and `admin_required` remains the actual access control.
- Styles: Uses shared `base.css`/`tables.css` plus scoped `dashboard-admin.css`.
- UI actions: "Public Dashboard", "Riders", "Devices", "Add New Race", "RFID Records", "Users", "Map Usage", "Edit", "Race Page", "Post Admin", status-appropriate "Enter Rider", "Results", and CSRF-protected logout.
- Linked pages (buttons):
  - "Public Dashboard" → `/dashboard`.
  - "Input Rider Details" → `/riders/new` (riders form page).
  - "Manage Devices" → `/devices/` (devices list page).
  - "Add New Race" → `/races/new` (new race form).
  - "View RFID Records" → `/rfid/` (RFID records page).
  - "User Management" → `/admin/users`.
  - "Map Usage" → `/admin/map_tile_quota`.
  - "Edit" → `/races/<id>/edit` (race edit page).
  - "Post Race" → `/races/<id>/post` (current post-race page).
  - "Post Admin" → `/races/<id>/post-admin`.
  - "Enter Rider" → `/races/<id>/entries/new`.
  - "Results" → `/races/<id>/results`.
- Pulls: `races` with prepared start/end display values.
- Pushes: authenticated logout POST only; management remains in the existing linked pages.
- Routes called: `/dashboard-admin`, `/dashboard`, `/logout`, `/riders/new`, `/devices/`, `/races/new`, `/rfid/`, `/admin/users`, `/admin/map_tile_quota`, `/races/<id>/edit`, `/races/<id>/post`, `/races/<id>/post-admin`, `/races/<id>/entries/new`, `/races/<id>/results`.
- Embedded scripts: none.
- Notes: protected by admin access.

### Dashboard manual verification

1. Apply `alembic upgrade head`, sign in as an administrator, and confirm `/dashboard-admin` shows every race with a Draft, Upcoming, Live, or Completed badge.
2. Edit one race and save its location, start/end date-time, lifecycle status, and a basename that exists beneath `src/static/images/races/`; confirm the values return on the edit form.
3. Set representative races to each lifecycle value. Confirm `/dashboard` places Upcoming, Live, and Completed under the correct tabs and never displays Draft.
4. Confirm upcoming cards show `get_devices.svg` and open the existing authenticated entry flow; confirm live cards show a green LIVE marker, completed cards show `results.svg`, and every card title opens its public race page without intercepting either independent artwork action.
5. Scroll the lower list on desktop and confirm only that pane scrolls while the hero compacts to retain the logo/account controls, selected heading, and tabs; return to the top and confirm the full copy expands again.
6. At approximately 390px width, confirm the normal and compact-on-scroll heroes contain no large empty gap between the account controls and hero copy. Confirm the hamburger remains beside the current heading, the accordion exposes all four sections without overlap, and each option closes the menu and updates the hero/URL/list.
7. On mobile, confirm the duplicate panel heading/description is absent; the item count remains above flat separated rows; race rows show an 88px image, lifecycle pill, name, date range, and location; Get Device and Results retain small labels beneath their right-side artwork without increasing row height; rider rows show an 88px portrait, team pill, name, bike, and at most two biography lines; and no row introduces horizontal scrolling.
8. Open the Riders section and confirm every Rider appears. On mobile, selecting anywhere on a rider row should open the read-only dialog. On desktop, both the rider name and View Rider button should open it; opening the underlying link without JavaScript should still render `/rider/<id>` as a standalone page.
9. Sign in as a linked rider and confirm My Profile opens their dialog and exposes Edit Profile. Confirm another rider's public profile has no rider-owned edit access. An unlinked admin should see Admin Dashboard rather than a broken profile link.
10. As a linked rider, upload a JPEG/PNG/WebP profile picture from the edit form and confirm it appears in the form preview, dashboard list, modal, and standalone profile. Replace it and confirm the new image appears; remove it and confirm the committed default artwork returns. Repeat as an administrator for another Rider, and confirm a rider cannot edit another profile.
11. Verify `/rider` redirects to `/dashboard?tab=riders`, the dashboard/profile canonical links use `kooksnylive.co.za`, draft race pages emit `noindex,nofollow`, `/dashboard-admin` emits `noindex,nofollow`, and `/sitemap.xml` lists current public rider details.

### placeholder.html
- General: Shared placeholder page for planned UX routes.
- Displays: title, route, target access level, and placeholder note.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/forms.css` for the content panel.
- UI actions: one configurable back button.
- Linked pages (buttons):
  - Back button target is supplied by each route.
- Pulls: `title`, `description`, `route`, `access`, `back_url`, `back_label`.
- Pushes: none.
- Routes called: `/races/<id>/post-admin`, `/races/<id>/results`, `/admin/users`.
- Embedded scripts: none.

### rider_profile.html
- General: canonical public read-only rider detail and dashboard-dialog source.
- Displays: profile image, name, team, bike, biography, and conditional Edit Profile action.
- Styles: Uses `base.css` for the standalone shell and `rider-profile.css` for the reusable profile card.
- Access: public view; the edit action appears only for the linked rider or an administrator and still points at the existing protected edit route.
- Routes called: `/rider/<id>`, `/dashboard?tab=riders`, and authorized `/riders/<id>/edit`.

### devices.html
- General: Device list and create form.
- Displays: Device ID, RFID EPC, Device Info, Returned, and Active state.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the device form panel and messages, and `src/static/css/tables.css` for the device table.
- UI actions: "Save" (create), "Edit", "Back to Admin Dashboard".
- Linked pages (buttons):
  - "Back to Admin Dashboard" → `/dashboard-admin`.
  - "Edit" → `/devices/<id>/edit` (device edit page).
- Pulls: `devices`, `message`, `success`, `form`.
- Pushes: POST create device with optional RFID EPC and returned/active toggles.
- Routes called: `/devices/` (GET/POST), `/devices/<id>/edit`, `/dashboard-admin`.
- Embedded scripts: none.

### device_edit.html
- General: Edit a single device's info, RFID EPC, returned state, and active state.
- Displays: Device ID (read-only), Device Info, RFID EPC, Returned, and Active toggles.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/forms.css` for the edit form panel, inputs, status messages, and form action layout.
- UI actions: "Save", "Back to Devices", "Admin Dashboard".
- Linked pages (buttons):
  - "Back to Devices" → `/devices/` (devices list page).
  - "Admin Dashboard" → `/dashboard-admin`.
- Pulls: `device`, `message`, `success`.
- Pushes: POST update device info, optional RFID EPC, returned, and active state.
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
- Displays: race-independent rider name, team, bike, bio, current/default profile preview, bounded image upload, optional image removal, and the riders table.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the rider form panel and messages, and `src/static/css/tables.css` for the riders table.
- UI actions: "Save", profile-picture file selection/removal, "Edit", and role-aware back navigation.
- Linked pages (buttons/links):
  - "Back to Admin Dashboard" → `/dashboard-admin`.
  - "Edit" → `/riders/<id>/edit` (loads rider into form).
- Pulls: `riders`, `form`, `editing_rider`, `message`, `success`, and `profile_image_max_mb`.
- Pushes: multipart POST create/update Rider text plus an optional JPEG/PNG/WebP profile image or removal flag.
- Routes called: `/riders/new`, `/riders/<id>/edit`, `/rider/<id>`, `/rider/<id>/profile-image`, `/dashboard`, and `/dashboard-admin`.
- Embedded scripts: none.

### race_form.html
- General: Create/edit a race, independently create/rename/delete-unused named routes, create/rename/reorder/archive/reassign/delete-unused categories, upload shared-route GPX, and manage category riders using stable `category_id` values.
- Displays: Race name, website, location, static logo/image filename, start/end time, description, explicit lifecycle status, inline save/validation feedback, route/category administration, active category selector, selected route map preview, and rider/device tables.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for form controls and row actions, `src/static/css/tables.css` for the rider assignment table, `src/static/css/maps.css` for the Leaflet route preview container, and `src/static/css/race-form.css` for race-form-only layout.
- UI actions: "Save Changes", "Add Route", "Rename Route", guarded "Delete if unused", "Add Category", "Save Category", category dropdown, GPX upload/removal, and rider entry management.
- Linked pages (buttons/links):
  - "Back" → `/dashboard-admin`.
  - "Open Website" → external race website URL (if set).
- Pulls: `race`, optional submitted `race_form_values`, `message`, `success`, `image_error`, `routes`, `category_records`, active `categories`, `selected_category`, `route`, `geojson`, `riders`, `devices`, `race_riders`, and `last_device_by_rider`.
- Pushes: POST save race, create/rename routes, create/edit categories, upload/remove GPX, and add/edit/remove riders.
- Routes called: `/races/save`, `/races/<id>/edit?category_id=...`, `/races/<id>/routes/add`, `/races/<id>/routes/<route_id>/rename`, `/races/<id>/routes/<route_id>/delete`, `/races/<id>/categories/add`, `/races/<id>/categories/<category_id>/edit`, `/races/<id>/categories/<category_id>/delete`, plus GPX and rider management routes carrying `category_id`.
- Embedded scripts:
  - Category route selection: enables and requires the inline new-route name only when "Create a new route" is selected.
  - GPX upload validation: native required-field validation blocks an empty file submission with a browser popup; JavaScript supplies the GPX-specific popup text.
  - Shared form/map scripts: auto-submit the category selector and fetch/render the route GeoJSON through `components/forms.js` and `components/maps.js`.
  - Rider add helper: auto-fills device based on `last_device_by_rider` mapping.
- Manual verification: edit a race whose image is blank/default and confirm it saves without an extension error. Then submit an unsupported image extension and confirm all other valid race changes persist, the prior image remains unchanged, and the rejected filename appears beside an inline error for correction.

### race_entry.html
- General: Staged rider/admin race-entry page backed by automatic device assignment; self-entry and admin-on-behalf entry use separate authorization boundaries.
- Displays: selected rider, active category selection with route names, their most recent assigned device, current-device questions, assigned category and device, existing category/device entry, and any inventory discrepancy requiring admin review.
- Styles: Uses `src/static/css/base.css` and `src/static/css/forms.css`.
- UI actions: administrator rider selection, category selection, current-device answer, prior-device confirmation when applicable, and "Enter Race".
- Pulls: `race`, `riders`, `selected_rider`, `categories`, `previous_device_id`, `existing_entry`, `result`, `message`, and `success`.
- Pushes: POST rider/category and device answers; successful requests create one RaceRider, while unavailable replacement requests create none.
- Routes called: rider GET/POST `/races/<id>/enter`, admin GET/POST `/races/<id>/entries/new`, and the appropriate dashboard back link.
- Search metadata: `noindex,nofollow`; authenticated operational entry pages remain disallowed by `robots.txt` and are not included in the sitemap.
- Embedded scripts: shared form auto-submit plus `pages/race-entry.js` conditional prior-device confirmation behavior.

### post_race.html
- General: Post-race review with route map and rider tracks.
- Displays: Race metadata, category route map, riders list with timing, ambiguous RFID finish-time highlights with an asterisk review note, finish confirmation state, manual timing modal with optional TXT log upload.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for category/manual timing controls, `src/static/css/tables.css` for the riders timing table, `src/static/css/maps.css` for the Leaflet route/track map canvas, and `src/static/css/post-race.css` for post-race-only layout, track key, RFID warning, and modal styles.
- UI actions: Category dropdown (reload), "Show Track", "Manual Edit", "Confirm" timing, modal "Save/Cancel/Upload TXT".
- Linked pages (buttons/links):
  - "Back to Dashboard" → `/dashboard`.
- Pulls: `race`, `categories`, `selected_category`, `geojson`, `riders`, `has_multiple_rfid_flag`.
- Pushes: Fetch route GeoJSON, fetch map config status, POST Esri tile-usage deltas, fetch stored rider track (cache-first for live polling), fetch live rider timing values, POST manual timing edits, POST finish timing confirmation, POST TXT log ingest.
- Routes called: `/races/<id>/post?category_id=...`, `/races/<id>/route/geojson?category_id=...`, `/api/map/config-status`, `/api/map/tile-usage`, `/races/<id>/race-rider/<id>/track`, `/races/<id>/race-rider/<id>/track?prefer_cache=1`, `/races/<id>/race-rider-timings?category_id=...`, `/races/<id>/race-rider/<id>/manual-times`, `/races/<id>/race-rider/<id>/confirm-finish`, `/api/v1/upload-text`.
- Embedded scripts:
  - Shared form/map scripts: auto-submit the category selector and initialise the Leaflet route map through `components/forms.js` and `components/maps.js`.
  - Esri quota integration: fetch config-status after route bounds are fitted, count Esri tile resources, POST tile deltas to `/api/map/tile-usage`, and switch to OpenStreetMap when the backend blocks satellite access.
  - "Show Track" overlay fetch + render (page-specific).
  - 5-second polling refresh for selected rider tracks (preserves selected toggles and layer state).
  - 5-second polling refresh for start/end timing cells, multiple-RFID asterisk state, and confirmation button state.
  - Manual timing modal + POST update + TXT log upload.
