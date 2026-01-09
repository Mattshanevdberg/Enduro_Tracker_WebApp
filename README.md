# Enduro Tracker Web Documentation

This document covers all routes and helper functions in `src/web`, including
which templates call each endpoint and the UI action that triggers it.

## src/web/home.py

### home_page (GET `/`)
- Purpose: Render the home page with navigation and a quick races table.
- Reads: `Race` (ordered by `starts_at`), config categories from `config.yaml`.
- Writes: None.
- Renders: `templates/home.html`.
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

### _parse_datetime
- Purpose: Helper to combine date/time strings into a UTC `datetime`.
- Reads/Writes: None.
- Called from: `save_race` only (internal helper).

### _find_or_create_route_for_category
- Purpose: Helper to ensure a `(Route, Category)` pair exists for a race/category.
- Reads: `Route`, `Category`.
- Writes: `Route`, `Category` (creates rows if missing).
- Called from: `edit_race`, `upload_gpx`, `add_race_rider` (internal helper).

### new_race (GET `/races/new`)
- Purpose: Render the "New Race" page.
- Reads: Config categories.
- Writes: None.
- Renders: `templates/race_form.html`.
- Called from:
  - `templates/home.html`: "Add New Race" button.

### post_race (GET `/races/<race_id>/post`)
- Purpose: Post-race view with route preview and rider list for a category.
- Reads: `Race`, `Route`, `Category`, `Rider`, `RaceRider`.
- Writes: None.
- Renders: `templates/post_race.html`.
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
- Purpose: Return stored GeoJSON track for a race rider, preferring `track_hist` and falling back to `track_cache`.
- Reads: `TrackHist`, `TrackCache`, `RaceRider`, `Category`, `Route`.
- Writes: None.
- Returns: GeoJSON payload (JSON).
- Called from:
  - `templates/post_race.html`: "Show Track" button (JS `fetch` on click).

### manual_times (POST `/races/<race_id>/race-rider/<race_rider_id>/manual-times`)
- Purpose: Overwrite start/finish times and rebuild a trimmed track snapshot.
- Reads: `RaceRider`, latest `TrackHist` (for raw text).
- Writes: `RaceRider.start_time_rfid`, `RaceRider.finish_time_rfid`, new `TrackHist` row.
- Returns: JSON status.
- Called from:
  - `templates/post_race.html`: "Manual Edit" button opens modal, modal "Save" triggers JS `fetch`.

### save_race (POST `/races/save`)
- Purpose: Create or update a `Race`.
- Reads: `Race` (when updating).
- Writes: `Race` (insert/update).
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
- Writes: `IngestRaw` (new row with `payload_json`).
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
- Reads: request JSON (`pid`, `log`); `RaceRider` for latest timing window.
- Writes: `TrackHist` (new row with `geojson`, `gpx`, `raw_txt`).
- Returns: empty 200 on success; JSON error on invalid input.
- Called from:
  - Text-log upload clients (no template references).

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
- Returns: list of cleaned fix dicts.
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

## src/workers/parse_worker.py

### _convert_fix
- Purpose: Convert a compact fix array into a `Point` ORM object.
- Reads: fix array values (scaled ints) and `device_id`.
- Writes: None (returns an object for later insert).
- Returns: `Point` instance or `None` if invalid.
- Called from:
  - `_process_batch_once` only (internal helper).

### _process_batch_once
- Purpose: Background batch step to move raw ingest data into `points`.
- Reads: `IngestRaw` rows where `processed_at IS NULL` (limit `BATCH_SIZE = 200`).
- Writes: `Point` inserts; updates `IngestRaw.processed_at` and `parse_error`.
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
- Reads: `RaceRider.start_time_rfid`, `RaceRider.finish_time_rfid`.
- Writes: None.
- Returns: `(race_rider_id, start_epoch, finish_epoch)` with `None` values when missing.
- Called from:
  - `main` only (internal helper).

### main
- Purpose: Live GeoJSON cache worker for the race_day display.
- Reads: `Point` (latest `t_epoch`), `RaceRider` (latest rider for device + timing window).
- Writes: `TrackCache.geojson` (upsert per `race_rider_id`), `TrackCache.updated_at`.
- Behavior: polls every `SLEEP_SEC = 5.0`, only rebuilds when new points arrive, and trims tracks to the rider’s start/end times when present.
- Called from:
  - CLI: `python -m src.workers.gpx_worker` (background process).
