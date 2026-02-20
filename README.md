# Enduro Tracker Web Documentation

This document maps the Flask routes, background workers, and utility helpers to
their responsibilities, data sources, and UI triggers. It also includes a
per-table summary of the database schema so you can trace how data flows from
ingest to display.

## Agent Instructions

- Keep comments extensive and in the format currently used.
- Always update function descriptions and keep them in the format currently used.
- Update `README.md` whenever any changes are made, maintaining the current README format.

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

## src/web/devices.py

### _list_devices
- Purpose: Helper to fetch all `Device` rows ordered by id.
- Reads: `Device`.
- Writes: None.
- Called from: `devices_index` only (internal helper).

### devices_index (GET/POST `/devices/`)
- Purpose: List devices (GET) and create a new device (POST).
- Reads: `Device` (list view).
- Writes: `Device` (new row on POST).
- Renders: `templates/devices.html`.
- Called from:
  - `templates/home.html`: "Manage Devices" button (GET).
  - `templates/devices.html`: "Save" button in "Add a new device" form (POST).
  - `templates/device_edit.html`: "Back to Devices" link (GET).

### device_edit (GET/POST `/devices/<device_id>/edit`)
- Purpose: Edit `device_info` for a specific device.
- Reads: `Device` (by id).
- Writes: `Device.device_info` (on POST).
- Renders: `templates/device_edit.html`.
- Called from:
  - `templates/devices.html`: "Edit" link in devices table (GET).
  - `templates/device_edit.html`: "Save" button (POST).

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
- UI: map includes multi-select rider track overlays controlled from the compact legend beside race info (toggle state synced to active overlays, reselects replace prior overlays), persisted map height/width sliders, auto-stacking of the riders table under the map when widths clash, 5-second live refresh polling for selected riders (cache-first), and a manual timing modal that can optionally upload a TXT log to `/api/v1/upload-text` before reapplying the chosen start/end window.
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

### manual_times (POST `/races/<race_id>/race-rider/<race_rider_id>/manual-times`)
- Purpose: Overwrite start/finish times and rebuild a trimmed track snapshot.
- Reads: `RaceRider`, latest `TrackHist` (for raw text).
- Writes: `RaceRider.start_time_rfid_epoch`, `RaceRider.finish_time_rfid_epoch`, new `TrackHist` row with `updated_at_epoch`.
- Returns: JSON status.
- Timezone: inputs must be timezone-naive; values are assumed to be in the configured local timezone (`config.yaml` → `global.timezone`) and converted to UTC before saving.
- Called from:
  - `templates/post_race.html`: "Manual Edit" button opens modal, modal "Save" and "Upload TXT" trigger JS `fetch`.

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
- Writes: `Point` inserts (including `received_at_epoch`) using ON CONFLICT DO NOTHING; updates `IngestRaw.processed_at_epoch` and `parse_error` (only set if all fixes are invalid).
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

## Database Tables (src/db/models.py)

- `ingest_raw`: raw device uploads (payload JSON, received/processed timestamps, parse error). Columns: `id`, `device_id`, `payload_json`, `received_at`, `received_at_epoch`, `processed_at`, `processed_at_epoch`, `parse_error`.
- `devices`: registered hardware devices; referenced by race_riders. Columns: `id`, `device_info`.
- `points`: parsed GNSS fixes per device (t_epoch, lat/lon, optional metrics). Columns: `id`, `device_id`, `t_epoch`, `lat`, `lon`, `ele`, `sog`, `cog`, `fx`, `hdop`, `nsat`, `received_at`, `received_at_epoch`.
- `riders`: core athlete details (name, team, bike, bio). Columns: `id`, `name`, `bike`, `bio`, `team`, `category`.
- `races`: event metadata (name, description, website, starts/ends, active flag). Columns: `id`, `name`, `description`, `website`, `starts_at`, `starts_at_epoch`, `ends_at`, `ends_at_epoch`, `active`.
- `route`: per-race route geometry storage (gpx/geojson). Columns: `id`, `race_id`, `geojson`, `gpx`.
- `categories`: category labels tied to a route; unique per route. Columns: `id`, `route_id`, `name`.
- `race_riders`: joins rider, device, and category for a race; stores timing and status flags. Columns: `id`, `rider_id`, `device_id`, `category_id`, `comm_setting`, `active`, `recording`, `start_time_rfid`, `start_time_rfid_epoch`, `finish_time_rfid`, `finish_time_rfid_epoch`, `start_time_pi`, `start_time_pi_epoch`, `finish_time_pi`, `finish_time_pi_epoch`.
- `leaderboard_cache`: live leaderboard snapshot per category. Columns: `category_id`, `payload_json`, `etag`, `updated_at`, `updated_at_epoch`.
- `track_cache`: live track geojson per race_rider. Columns: `race_rider_id`, `geojson`, `etag`, `updated_at`, `updated_at_epoch`.
- `leaderboard_hist`: archived leaderboard snapshots per category. Columns: `id`, `category_id`, `payload_json`, `official_pdf`, `updated_at`, `updated_at_epoch`.
- `track_hist`: archived track snapshots per race_rider (geojson/gpx/raw text). Columns: `id`, `race_rider_id`, `geojson`, `gpx`, `raw_txt`, `updated_at`, `updated_at_epoch`.

## Templates (templates/*.html)

### home.html
- General: Home/landing page and navigation hub.
- Displays: Races table (name, start, website, active).
- UI actions: "Input Rider Details", "Manage Devices", "Add New Race", "Edit", "Post Race".
- Linked pages (buttons):
  - "Input Rider Details" → `/riders/new` (riders form page).
  - "Manage Devices" → `/devices/` (devices list page).
  - "Add New Race" → `/races/new` (new race form).
  - "Edit" → `/races/<id>/edit` (race edit page).
  - "Post Race" → `/races/<id>/post` (post-race page).
- Pulls: `races`, `default_category`.
- Pushes: none (links only).
- Routes called: `/`, `/riders/new`, `/devices/`, `/races/new`, `/races/<id>/edit`, `/races/<id>/post`.
- Embedded scripts: none.

### devices.html
- General: Device list and create form.
- Displays: Device ID, Device Info.
- UI actions: "Save" (create), "Edit", "Back to Home".
- Linked pages (buttons):
  - "Back to Home" → `/` (home page).
  - "Edit" → `/devices/<id>/edit` (device edit page).
- Pulls: `devices`, `message`, `success`, `form`.
- Pushes: POST create device.
- Routes called: `/devices/` (GET/POST), `/devices/<id>/edit`, `/`.
- Embedded scripts: none.

### device_edit.html
- General: Edit a single device's info.
- Displays: Device ID (read-only), Device Info.
- UI actions: "Save", "Back to Devices", "Home".
- Linked pages (buttons):
  - "Back to Devices" → `/devices/` (devices list page).
  - "Home" → `/` (home page).
- Pulls: `device`, `message`, `success`.
- Pushes: POST update device info.
- Routes called: `/devices/<id>/edit`, `/devices/`, `/`.
- Embedded scripts: none.

### riders_form.html
- General: Create/edit rider form with riders list.
- Displays: Rider fields and riders table.
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
- UI actions: "Save Changes", category dropdown (reload), "Upload GPX", "Remove GPX", "Save" (add rider), "Edit" (update rider entry), "Remove" (delete entry), "Back".
- Linked pages (buttons/links):
  - "Back" → `/` (home page).
  - "Open Website" → external race website URL (if set).
- Pulls: `race`, `categories`, `selected_category`, `route`, `geojson`, `riders`, `devices`, `race_riders`, `last_device_by_rider`.
- Pushes: POST save race, upload/remove GPX, add/edit/remove riders.
- Routes called: `/races/save`, `/races/<id>/edit?category=...`, `/races/<id>/route/upload`, `/races/<id>/route/remove`, `/races/<id>/route/geojson`, `/races/<id>/riders/add`, `/races/<id>/riders/<entry_id>/edit`, `/races/<id>/riders/<entry_id>/remove`.
- Embedded scripts:
  - Map preview: fetches route GeoJSON and renders via Leaflet.
  - Rider add helper: auto-fills device based on `last_device_by_rider` mapping.

### post_race.html
- General: Post-race review with route map and rider tracks.
- Displays: Race metadata, category route map, riders list with timing, manual timing modal with optional TXT log upload.
- UI actions: Category dropdown (reload), "Show Track", "Manual Edit", modal "Save/Cancel/Upload TXT".
- Linked pages (buttons/links):
  - "Back to Home" → `/` (home page).
- Pulls: `race`, `categories`, `selected_category`, `geojson`, `riders`.
- Pushes: Fetch route GeoJSON, fetch stored rider track (cache-first for live polling), POST manual timing edits, POST TXT log ingest.
- Routes called: `/races/<id>/post?category=...`, `/races/<id>/route/geojson?category=...`, `/races/<id>/race-rider/<id>/track`, `/races/<id>/race-rider/<id>/track?prefer_cache=1`, `/races/<id>/race-rider/<id>/manual-times`, `/api/v1/upload-text`.
- Embedded scripts:
  - Route map load/render (Leaflet).
  - "Show Track" overlay fetch + render.
  - 5-second polling refresh for selected rider tracks (preserves selected toggles and layer state).
  - Manual timing modal + POST update + TXT log upload.
