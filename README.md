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
- Auth email and security values: `RESEND_API_KEY`, `MAIL_FROM`, `APP_PUBLIC_BASE_URL`, `AUTH_TOKEN_PEPPER`, `AUTH_PASSWORD_MIN_LENGTH`, and `AUTH_RATE_LIMIT_STORAGE_URL` are passed into the `server` container for the authentication workstream. Resend is used only for forgot-password reset links in the current plan; signup email verification is intentionally not enabled.
- Notes: changing PostgreSQL bootstrap variables on an already-initialised volume does not reconfigure an existing database cluster. Clean separation requires a fresh volume name per environment or an explicit manual database/user migration.

## Python Requirements

### Authentication and security packages
- Purpose: Add the package baseline for the viewer/rider/admin authentication workstream described by `Web Application System Design V4 - 20260224.pdf`.
- Packages: `Flask-Login` for browser login sessions, `Flask-WTF` for form handling and CSRF protection, `Flask-Limiter` for rate limiting sensitive auth routes, `redis` for shared rate-limit storage, `email-validator` for signup/reset email validation, and `resend` for forgot-password email delivery.
- Notes: Resend is only used for password-reset emails in the current plan. Signup email verification is intentionally not enabled.

## src/main.py

### create_app
- Purpose: Flask application factory that creates the app instance, loads the Flask secret key, registers CORS, and attaches all API and web blueprints.
- Reads: `FLASK_SECRET_KEY`, the `MAP_*` map configuration values, `ARCGIS_API_KEY`, and the auth email configuration values from the container runtime environment; `config.yaml` for host and port globals; `src.auth.login.login_manager` for browser session setup.
- Writes: `app.config["SECRET_KEY"]` plus the map provider, style, browser API key, and map-limit configuration values used by the post-race map workflow; initialises Flask-Login on the app.
- Registers: ingest API routes, home, riders, devices, races, and RFID record viewer blueprints.
- Called from: module import path `src.main:app` for Gunicorn, and the direct-run block at the bottom of the file.
- Notes: the app now expects `FLASK_SECRET_KEY` to exist in the container environment. If Compose does not pass that value into the `server` service, Gunicorn fails during import with `KeyError: 'FLASK_SECRET_KEY'`.

## src/auth/login.py

### login_manager
- Purpose: Shared Flask-Login manager that configures browser login session handling for the application.
- Reads: none at definition time.
- Writes: Flask-Login settings including the future login endpoint `auth.login`, the login-required message, and the login message category.
- Called from:
  - `src.main:create_app`, where `login_manager.init_app(app)` attaches the manager to the Flask app.
- Notes: the actual `/login` route is added in a later auth step. No existing routes are protected by this change yet.

### load_user
- Purpose: Load the current browser user from the Flask session id.
- Reads: future `User` model from `src.db.models` when it exists, and `SessionLocal` for a short-lived database lookup.
- Writes: None.
- Returns: active User row for a valid session id, otherwise `None`.
- Called from:
  - Flask-Login during request handling when a browser session contains a user id.
- Notes: this loader deliberately returns `None` until the User model is introduced in Step 3, so the application can start safely during the staged implementation.

### Direct-run debug block
- Purpose: support direct local execution through `python src/main.py`.
- Reads: `API_HOST` and `API_PORT` from `config.yaml`.
- Writes: none.
- Called from: only when `src/main.py` is executed directly.
- Notes: the containerised runtime uses Gunicorn from the Dockerfile rather than this `app.run(...)` path, so the production deployment does not depend on Flask's built-in debug server.

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

### home_page (GET `/`)
- Purpose: Render the home page with navigation and a quick races table.
- Reads: `Race` (ordered by `starts_at_epoch`), config categories from `config.yaml`.
- Writes: None.
- Renders: `templates/home.html`.
- Display: converts `starts_at_epoch` to a datetime for the template table.
- Called from:
  - `templates/home.html`: direct page load at `/`.
  - `templates/devices.html`: "Back to Home" link.
  - `templates/device_edit.html`: "Home" button.
  - `templates/riders_form.html`: "Back to Home" link.
  - `templates/race_form.html`: "Back" link.
  - `templates/post_race.html`: "Back to Home" link.
  - `templates/rfid_view.html`: "Back to Home" link.

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
- Purpose: Provide the lean shared static stylesheet for the Flask-rendered UI, currently applied to `templates/home.html`, `templates/riders_form.html`, `templates/devices.html`, `templates/device_edit.html`, `templates/rfid_view.html`, `templates/race_form.html`, and `templates/post_race.html`.
- Reads: CSS custom properties defined in `:root` for navy, white, forest green, neutral surfaces, borders, text, and shadows.
- Writes: Browser presentation only; no application data is changed.
- Styles: theme variables, page shell, page header, primary buttons, section titles, muted text, empty state, and mobile layout adjustments.
- Called from:
  - `templates/home.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/riders_form.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/devices.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/device_edit.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/rfid_view.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/race_form.html`: linked through `url_for('static', filename='css/base.css')`.
  - `templates/post_race.html`: linked through `url_for('static', filename='css/base.css')`.
- Notes: this CSS split follows the simple Flask web UI direction in `Web Application System Design V4 - 20260224.pdf`. Future work should move broad reusable rules out of `base.css` into component stylesheets and keep complex page-specific rules in their own page files.

### Shared component files
- Purpose: Provide reusable component stylesheets that are loaded after `base.css` by pages that need them.
- Current state: `forms.css`, `tables.css`, and `maps.css` exist under `src/static/css`; `templates/home.html`, `templates/riders_form.html`, `templates/devices.html`, `templates/device_edit.html`, `templates/rfid_view.html`, `templates/race_form.html`, and `templates/post_race.html` now load the relevant component files.
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
- Called from:
  - `templates/home.html`: "Manage Devices" button (GET).
  - `templates/devices.html`: "Save" button in "Add a new device" form (POST).
  - `templates/device_edit.html`: "Back to Devices" link (GET).

### device_edit (GET/POST `/devices/<device_id>/edit`)
- Purpose: Edit `device_info` and optional `epc_id` for a specific device.
- Reads: `Device` (by id).
- Writes: `Device.device_info` and `Device.epc_id` (on POST).
- Renders: `templates/device_edit.html`.
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
- Display: converts `time_stamp_epoch` and `received_at_epoch` to datetimes for the template table.
- Called from:
  - `templates/home.html`: "View RFID Records" button (GET).
  - `templates/rfid_view.html`: filter form and "Clear" link (GET).

## src/web/riders.py

### _validate_category
- Purpose: Helper to validate that a category is in the allowed list.
- Reads/Writes: None.
- Called from: `rider_form` only (internal helper).

### rider_form (GET/POST `/riders/new` and `/riders/<rider_id>/edit`)
- Purpose: Create a new rider or edit an existing rider.
- Reads: `Rider` (list and optional row for editing).
- Writes: `Rider` (insert or update).
- Renders: `templates/riders_form.html`.
- Called from:
  - `templates/home.html`: "Input Rider Details" button (GET `/riders/new`).
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
- Called from:
  - `templates/home.html`: "Add New Race" button.

### post_race (GET `/races/<race_id>/post`)
- Purpose: Post-race view with route preview and rider list for a category.
- Reads: `Race`, `Route`, `Category`, `Rider`, `RaceRider` (epoch timing columns for display).
- Writes: None.
- Renders: `templates/post_race.html`.
- Display: converts rider timing epochs to naive local datetimes for UI controls.
- UI: map includes multi-select rider track overlays controlled from the compact legend beside race info (toggle state synced to active overlays, reselects replace prior overlays), persisted map height/width sliders, auto-stacking of the riders table under the map when widths clash, 5-second live refresh polling for selected rider tracks (cache-first), 5-second live refresh polling for rider timing cells, and a manual timing modal that can optionally upload a TXT log to `/api/v1/upload-text` before reapplying the chosen start/end window.
- Called from:
  - `templates/home.html`: "Post Race" button in races table.
  - `templates/post_race.html`: category `<select>` `onchange` (GET with `?category=`).

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
- Timezone: inputs must be timezone-naive; values are assumed to be in the configured local timezone (`config.yaml` → `global.timezone`) and converted to UTC before saving.
- Called from:
  - `templates/post_race.html`: "Manual Edit" button opens modal, modal "Save" and "Upload TXT" trigger JS `fetch`.

### confirm_finish_time (POST `/races/<race_id>/race-rider/<race_rider_id>/confirm-finish`)
- Purpose: Confirm the current RFID finish timing after organiser review.
- Reads: `RaceRider`, `Category`, `Route`.
- Writes: `RaceRider.finish_time_rfid_confirmed=True` and `RaceRider.multiple_rfid_flag=False`.
- Returns: JSON status with the refreshed timing payload.
- Called from:
  - `templates/post_race.html`: "Confirm" timing button next to manual edit.

### save_race (POST `/races/save`)
- Purpose: Create or update a `Race`.
- Reads: `Race` (when updating).
- Writes: `Race` (insert/update).
- Behavior: parses date/time inputs and converts them to epoch seconds using the configured timezone for naive input.
- Redirects: to edit page for the saved race.
- Called from:
  - `templates/race_form.html`: "Save Changes" button.

### edit_race (GET `/races/<race_id>/edit`)
- Purpose: Edit page for race data, route upload, and rider assignments.
- Reads: `Race`, `Route`, `Category`, `Rider`, `Device`, `RaceRider`.
- Writes: `Route`/`Category` if missing for the selected category.
- Renders: `templates/race_form.html`.
- Called from:
  - `templates/home.html`: "Edit" button in races table.
  - `templates/race_form.html`: category `<select>` `onchange` (GET with `?category=`).
  - Redirect from `save_race` after a successful save.

### upload_gpx (POST `/races/<race_id>/route/upload`)
- Purpose: Upload a GPX file and store both GPX and GeoJSON on `Route`.
- Reads: Uploaded file.
- Writes: `Route.gpx`, `Route.geojson`.
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Upload GPX" button (file upload form).

### remove_gpx (POST `/races/<race_id>/route/remove`)
- Purpose: Remove GPX/GeoJSON for the selected category.
- Reads: `Route`.
- Writes: `Route.gpx = None`, `Route.geojson = None`.
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
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Save" button in the "Add new rider" row.

### edit_race_rider (POST `/races/<race_id>/riders/<entry_id>/edit`)
- Purpose: Update device assignment and flags for a race rider entry.
- Reads: `RaceRider`.
- Writes: `RaceRider.device_id`, `RaceRider.active`, `RaceRider.recording`.
- Redirects: back to edit page.
- Called from:
  - `templates/race_form.html`: "Edit" button in the riders table.

### remove_race_rider (POST `/races/<race_id>/riders/<entry_id>/remove`)
- Purpose: Remove a rider from the race category.
- Reads: `RaceRider`.
- Writes: `RaceRider` (delete).
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

### _required_env
- Purpose: Read a required environment variable for the manual Resend smoke test and fail with a safe error message when it is missing.
- Reads: one named environment variable.
- Writes: a missing-variable message to stderr when validation fails.
- Returns: stripped environment value.
- Called from:
  - `tests/test_resend_email.py:main`.

### main
- Purpose: Send one manual test email through Resend using the same environment-variable pattern as the Flask runtime.
- Reads: `RESEND_API_KEY`, `TEST_EMAIL_TO`, and optional `MAIL_FROM`.
- Writes: one test email to the requested destination and prints the Resend response without printing the API key.
- Returns: process exit code (`0` success, `1` failure through `_required_env`).
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

- `ingest_raw`: raw device uploads (payload JSON, received/processed timestamps, parse error). Columns: `id`, `device_id`, `payload_json`, `received_at`, `received_at_epoch`, `processed_at`, `processed_at_epoch`, `parse_error`. Relationships: no enforced foreign-key relationship; records are associated to devices by `device_id` value only. Conditions: `device_id` and `payload_json` are required (`NOT NULL`).
- `ingest_rfid`: raw RFID reader tag events. Columns: `id`, `epc`, `rssi`, `ant`, `time_stamp_epoch`, `reader_id`, `avg_rssi`, `received_at_epoch`, `processed_at_epoch`, `process_error`. Relationships: view-only link to `devices` through `ingest_rfid.epc == devices.epc_id` so unknown/false RFID reads can still be stored. Conditions: `epc` is required (`NOT NULL`); `time_stamp_epoch`, `reader_id`, and `processed_at_epoch` are indexed for worker lookups.
- `devices`: registered hardware devices; referenced by race_riders. Columns: `id`, `device_info`, `epc_id`. Relationships: one device can map to many `race_riders` entries via `race_riders.device_id -> devices.id`, and can view many `ingest_rfid` rows through matching EPC values. Conditions: primary-key uniqueness on `id`; unique constraint `ux_devices_epc_id` enforces at most one device per non-null EPC tag.
- `points`: parsed GNSS fixes per device (t_epoch, lat/lon, optional metrics). Columns: `id`, `device_id`, `t_epoch`, `lat`, `lon`, `ele`, `sog`, `cog`, `fx`, `hdop`, `nsat`, `received_at`, `received_at_epoch`. Relationships: no enforced foreign key to `devices`; points are linked to `race_riders` through a view-only `device_id` join. Conditions: unique constraint `ux_points_device_time` enforces one row per (`device_id`, `t_epoch`).
- `riders`: core athlete details (name, team, bike, bio). Columns: `id`, `name`, `bike`, `bio`, `team`, `category`. Relationships: one rider can have many `race_riders` entries via `race_riders.rider_id -> riders.id`. Conditions: `name` is required (`NOT NULL`).
- `races`: event metadata (name, description, website, starts/ends, active flag). Columns: `id`, `name`, `description`, `website`, `starts_at`, `starts_at_epoch`, `ends_at`, `ends_at_epoch`, `active`. Relationships: one race can have many `route` rows via `route.race_id -> races.id`. Conditions: `name` and `active` are required (`NOT NULL`) and `active` defaults to `true`.
- `route`: per-race route geometry storage (gpx/geojson). Columns: `id`, `race_id`, `geojson`, `gpx`. Relationships: belongs to one `race` and can have many `categories` via `categories.route_id -> route.id`. Conditions: `race_id` is required (`NOT NULL`) and must reference an existing `races.id`.
- `categories`: category labels tied to a route; unique per route. Columns: `id`, `route_id`, `name`. Relationships: belongs to one `route` and is referenced by many `race_riders`, plus one-to-one cache/history links in `leaderboard_cache` and `leaderboard_hist`. Conditions: unique constraint `ux_route_category_name` enforces unique `name` per `route_id`.
- `race_riders`: joins rider, device, and category for a race; stores timing and status flags. Columns: `id`, `rider_id`, `device_id`, `category_id`, `comm_setting`, `active`, `recording`, `start_time_rfid`, `start_time_rfid_epoch`, `finish_time_rfid`, `finish_time_rfid_epoch`, `start_time_pi`, `start_time_pi_epoch`, `finish_time_pi`, `finish_time_pi_epoch`, `multiple_rfid_flag`, `finish_time_rfid_confirmed`. Relationships: each row belongs to one `rider`, one `device`, and one `category`, with one-to-one links to `track_cache` and `track_hist`. Conditions: `rider_id`, `device_id`, `category_id`, `active`, `recording`, `multiple_rfid_flag`, and `finish_time_rfid_confirmed` are required, with `active`/`recording` defaulting to `true` and RFID flags defaulting to `false`.
- `leaderboard_cache`: live leaderboard snapshot per category. Columns: `category_id`, `payload_json`, `etag`, `updated_at`, `updated_at_epoch`. Relationships: one-to-one with `categories` via `category_id` as both foreign key and primary key. Conditions: `payload_json` and `updated_at` are required (`NOT NULL`).
- `track_cache`: live track geojson per race_rider. Columns: `race_rider_id`, `geojson`, `etag`, `updated_at`, `updated_at_epoch`. Relationships: one-to-one with `race_riders` via `race_rider_id` as both foreign key and primary key. Conditions: `updated_at` is required (`NOT NULL`).
- `leaderboard_hist`: archived leaderboard snapshots per category. Columns: `id`, `category_id`, `payload_json`, `official_pdf`, `updated_at`, `updated_at_epoch`. Relationships: many history rows can belong to one `category` via `category_id -> categories.id`. Conditions: `category_id`, `payload_json`, and `updated_at` are required (`NOT NULL`).
- `track_hist`: archived track snapshots per race_rider (geojson/gpx/raw text). Columns: `id`, `race_rider_id`, `geojson`, `gpx`, `raw_txt`, `updated_at`, `updated_at_epoch`. Relationships: many history rows can belong to one `race_rider` via `race_rider_id -> race_riders.id`. Conditions: `race_rider_id` and `updated_at` are required (`NOT NULL`).

## Templates (templates/*.html)

### home.html
- General: Home/landing page and navigation hub.
- Displays: Races table (name, start, website, active).
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/tables.css` for the race-list table.
- UI actions: "Input Rider Details", "Manage Devices", "Add New Race", "View RFID Records", "Edit", "Post Race".
- Linked pages (buttons):
  - "Input Rider Details" → `/riders/new` (riders form page).
  - "Manage Devices" → `/devices/` (devices list page).
  - "Add New Race" → `/races/new` (new race form).
  - "View RFID Records" → `/rfid/` (RFID records page).
  - "Edit" → `/races/<id>/edit` (race edit page).
  - "Post Race" → `/races/<id>/post` (post-race page).
- Pulls: `races`, `default_category`.
- Pushes: none (links only).
- Routes called: `/`, `/riders/new`, `/devices/`, `/races/new`, `/rfid/`, `/races/<id>/edit`, `/races/<id>/post`.
- Embedded scripts: none.

### devices.html
- General: Device list and create form.
- Displays: Device ID, RFID EPC, Device Info.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the device form panel and messages, and `src/static/css/tables.css` for the device table.
- UI actions: "Save" (create), "Edit", "Back to Home".
- Linked pages (buttons):
  - "Back to Home" → `/` (home page).
  - "Edit" → `/devices/<id>/edit` (device edit page).
- Pulls: `devices`, `message`, `success`, `form`.
- Pushes: POST create device with optional RFID EPC.
- Routes called: `/devices/` (GET/POST), `/devices/<id>/edit`, `/`.
- Embedded scripts: none.

### device_edit.html
- General: Edit a single device's info and RFID EPC.
- Displays: Device ID (read-only), Device Info, RFID EPC.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme and `src/static/css/forms.css` for the edit form panel, inputs, status messages, and form action layout.
- UI actions: "Save", "Back to Devices", "Home".
- Linked pages (buttons):
  - "Back to Devices" → `/devices/` (devices list page).
  - "Home" → `/` (home page).
- Pulls: `device`, `message`, `success`.
- Pushes: POST update device info and optional RFID EPC.
- Routes called: `/devices/<id>/edit`, `/devices/`, `/`.
- Embedded scripts: none.

### rfid_view.html
- General: RFID ingest records viewer with server-side filters.
- Displays: RFID row id, EPC, RSSI, average RSSI, antenna, reader id, reader time, and received time.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the filter panel, filter grid, messages, and filter actions, and `src/static/css/tables.css` for the wide RFID records table.
- UI actions: "Filter", "Clear", "Back to Home".
- Linked pages (buttons):
  - "Back to Home" → `/` (home page).
  - "Clear" → `/rfid/` (unfiltered RFID records page).
- Pulls: `rows`, `filters`, `message`, `success`, `max_limit`.
- Pushes: GET filter query string values only.
- Routes called: `/rfid/`, `/`.
- Embedded scripts: none.

### riders_form.html
- General: Create/edit rider form with riders list.
- Displays: Rider fields and riders table.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for the rider form panel and messages, and `src/static/css/tables.css` for the riders table.
- UI actions: "Save", "Edit", "Back to Home".
- Linked pages (buttons/links):
  - "Back to Home" → `/` (home page).
  - "Edit" → `/riders/<id>/edit` (loads rider into form).
- Pulls: `categories`, `riders`, `form`, `editing_rider`, `message`, `success`.
- Pushes: POST create/update rider.
- Routes called: `/riders/new`, `/riders/<id>/edit`, `/`.
- Embedded scripts: none.

### race_form.html
- General: Create/edit race, upload route GPX, manage category riders.
- Displays: Race fields, category selector, route map preview, rider/device tables.
- Styles: Uses `src/static/css/base.css` for the lean shared base theme, `src/static/css/forms.css` for form controls and row actions, `src/static/css/tables.css` for the rider assignment table, `src/static/css/maps.css` for the Leaflet route preview container, and `src/static/css/race-form.css` for race-form-only layout.
- UI actions: "Save Changes", category dropdown (reload), "Upload GPX", "Remove GPX", "Save" (add rider), "Edit" (update rider entry), "Remove" (delete entry), "Back".
- Linked pages (buttons/links):
  - "Back" → `/` (home page).
  - "Open Website" → external race website URL (if set).
- Pulls: `race`, `categories`, `selected_category`, `route`, `geojson`, `riders`, `devices`, `race_riders`, `last_device_by_rider`.
- Pushes: POST save race, upload/remove GPX, add/edit/remove riders.
- Routes called: `/races/save`, `/races/<id>/edit?category=...`, `/races/<id>/route/upload`, `/races/<id>/route/remove`, `/races/<id>/route/geojson`, `/races/<id>/riders/add`, `/races/<id>/riders/<entry_id>/edit`, `/races/<id>/riders/<entry_id>/remove`.
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
  - "Back to Home" → `/` (home page).
- Pulls: `race`, `categories`, `selected_category`, `geojson`, `riders`, `has_multiple_rfid_flag`.
- Pushes: Fetch route GeoJSON, fetch stored rider track (cache-first for live polling), fetch live rider timing values, POST manual timing edits, POST finish timing confirmation, POST TXT log ingest.
- Routes called: `/races/<id>/post?category=...`, `/races/<id>/route/geojson?category=...`, `/races/<id>/race-rider/<id>/track`, `/races/<id>/race-rider/<id>/track?prefer_cache=1`, `/races/<id>/race-rider-timings?category=...`, `/races/<id>/race-rider/<id>/manual-times`, `/races/<id>/race-rider/<id>/confirm-finish`, `/api/v1/upload-text`.
- Embedded scripts:
  - Shared form/map scripts: auto-submit the category selector and initialise the Leaflet route map through `components/forms.js` and `components/maps.js`.
  - "Show Track" overlay fetch + render (page-specific).
  - 5-second polling refresh for selected rider tracks (preserves selected toggles and layer state).
  - 5-second polling refresh for start/end timing cells, multiple-RFID asterisk state, and confirmation button state.
  - Manual timing modal + POST update + TXT log upload.
