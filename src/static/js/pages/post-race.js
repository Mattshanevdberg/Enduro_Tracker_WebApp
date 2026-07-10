/*
Post-race page behaviour.

This file owns the live post-race workflow in templates/post_race.html. Shared
Leaflet map primitives and category form auto-submit behaviour live in
components/maps.js and components/forms.js. The track overlays, timing polling,
map size preferences, finish confirmation, and manual timing modal stay here
because they currently belong only to the post-race page.
*/

(function initialisePostRacePage() {
  window.EnduroForms?.attachAutoSubmitSelects();

  const maps = window.EnduroMaps;
  const mapNode = document.getElementById('map');
  const statusNode = document.getElementById('map-status');
  const mapHeightInput = document.getElementById('map-height');
  const mapHeightValue = document.getElementById('map-height-value');
  const mapWidthInput = document.getElementById('map-width');
  const mapWidthValue = document.getElementById('map-width-value');
  const layoutNode = document.querySelector('.layout');
  const ridersTable = document.querySelector('.riders-table');
  const keyListNode = document.getElementById('track-key-list');
  const raceId = mapNode?.dataset?.raceId || '';
  const categoryLabel = mapNode?.dataset?.category || '';
  const categoryText = categoryLabel ? `category "${categoryLabel}"` : 'selected category';
  const mapConfigNode = document.getElementById('post-race-map-config');
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  const BASE_ROUTE_COLOR = '#1f78b4';
  const TRACK_PALETTE = [
    '#e31a1c', '#33a02c', '#ff7f00', '#6a3d9a', '#b15928', '#1b9e77',
    '#d95f02', '#7570b3', '#e7298a', '#66a61e', '#e6ab02', '#a6761d'
  ];
  const MAP_HEIGHT_STORAGE_KEY = 'postRaceMapHeightPx';
  const MAP_WIDTH_STORAGE_KEY = 'postRaceMapWidthPx';
  const LIVE_TRACK_REFRESH_MS = 5000;
  const LIVE_TIMING_REFRESH_MS = 5000;

  let map = null;
  let routeLayer = null;
  let layoutCheckHandle = null;
  let liveTrackRefreshHandle = null;
  let liveTimingRefreshHandle = null;
  let timingRefreshInFlight = false;
  const trackLayers = new Map();
  const trackDataCache = new Map();
  const trackMeta = new Map();
  const timingRows = new Map();
  const inFlightTrackRefresh = new Set();
  let publicMapConfig = null;
  let basemapAttached = false;

  // Build headers for browser-originated JSON POST requests that use the
  // logged-in session cookie. Flask-WTF accepts X-CSRFToken for JSON/fetch
  // requests where a hidden form field is not available.
  function csrfJsonHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    return headers;
  }

  // Read the server-rendered public Esri configuration from JSON rather than
  // embedding Jinja expressions in this static page script. Invalid or absent
  // configuration remains safe because the shared helper will use OSM instead.
  if (mapConfigNode?.textContent) {
    try {
      publicMapConfig = JSON.parse(mapConfigNode.textContent);
    } catch (error) {
      console.error('Failed to parse post-race map configuration:', error);
    }
  }

  // Update the status line without requiring every page state to include one.
  function updateStatus(message) {
    if (statusNode) statusNode.textContent = message;
  }

  // Create an empty Leaflet map only when a post-race interaction needs it. The
  // selected route or track sets the view before a basemap can request tiles.
  function ensureMap() {
    if (map) return map;
    if (!maps || !mapNode) return null;
    map = maps.createMap(mapNode, { basemap: 'none' });
    return map;
  }

  // Attach satellite imagery only after valid bounds are visible. A later
  // server-backed usage guard will change satelliteAllowed to false, causing
  // this same helper to select the retained OpenStreetMap fallback instead.
  function attachPostRaceBasemap() {
    if (!map || !maps || basemapAttached) return;
    const result = maps.attachConfiguredBasemap(map, publicMapConfig);
    basemapAttached = !!result?.layer;
  }

  // Build cache-busted endpoints for live rider tracks and timing refreshes.
  function getTrackRequestUrl(raceRiderId, preferCache) {
    const cacheMode = preferCache ? 'prefer_cache=1&' : '';
    return `/races/${encodeURIComponent(raceId)}/race-rider/${encodeURIComponent(raceRiderId)}/track?${cacheMode}_ts=${Date.now()}`;
  }

  function getTimingRequestUrl() {
    return `/races/${encodeURIComponent(raceId)}/race-rider-timings?category=${encodeURIComponent(categoryLabel)}&_ts=${Date.now()}`;
  }

  // Keep the multiple-RFID explanation visible only while a row needs review.
  function updateRfidReviewNote(hasFlaggedTiming) {
    const note = document.getElementById('rfid-review-note');
    if (!note) return;
    note.classList.toggle('is-hidden', !hasFlaggedTiming);
    note.style.display = hasFlaggedTiming ? '' : 'none';
  }

  function renderFinishTimingText(finishTime, multipleRfidFlag) {
    const display = finishTime || 'waiting...';
    return multipleRfidFlag && finishTime ? `${display}*` : display;
  }

  // Apply live timing data to the matching table row and its action controls.
  function applyTimingUpdate(timing) {
    const raceRiderId = String(timing.race_rider_id || '');
    const row = timingRows.get(raceRiderId);
    if (!row) return;

    const startCell = row.querySelector('.timing-start');
    const finishCell = row.querySelector('.timing-finish');
    const manualButton = row.querySelector('.manual-edit-btn');
    const confirmButton = row.querySelector('.confirm-timing-btn');
    if (startCell) startCell.textContent = timing.start_time_rfid || 'waiting...';
    if (finishCell) {
      finishCell.textContent = renderFinishTimingText(timing.finish_time_rfid, timing.multiple_rfid_flag);
      finishCell.classList.toggle('rfid-warning', !!timing.multiple_rfid_flag);
    }
    if (manualButton) {
      manualButton.dataset.start = timing.start_time_input || '';
      manualButton.dataset.end = timing.finish_time_input || '';
    }
    if (confirmButton) {
      const isConfirmed = !!timing.finish_time_rfid_confirmed;
      confirmButton.textContent = isConfirmed ? 'Confirmed' : 'Confirm';
      confirmButton.disabled = isConfirmed || !timing.finish_time_rfid;
    }
  }

  // Refresh RFID timing cells while the page is visible, avoiding overlapping calls.
  function refreshRiderTimings() {
    if (document.hidden || timingRefreshInFlight || !raceId) return;
    timingRefreshInFlight = true;
    fetch(getTimingRequestUrl(), { cache: 'no-store' })
      .then(response => {
        if (!response.ok) throw new Error('Timing refresh failed.');
        return response.json();
      })
      .then(data => {
        let hasFlaggedTiming = false;
        (data.riders || []).forEach(timing => {
          hasFlaggedTiming ||= !!timing.multiple_rfid_flag;
          applyTimingUpdate(timing);
        });
        updateRfidReviewNote(hasFlaggedTiming);
        scheduleLayoutCheck();
      })
      .catch(() => {
        // Preserve the current display when a transient timing request fails.
      })
      .finally(() => {
        timingRefreshInFlight = false;
      });
  }

  function startLiveTimingPolling() {
    if (liveTimingRefreshHandle) window.clearInterval(liveTimingRefreshHandle);
    liveTimingRefreshHandle = window.setInterval(refreshRiderTimings, LIVE_TIMING_REFRESH_MS);
  }

  // Confirm the displayed RFID finish time and re-read timing after success.
  function confirmFinishTiming(raceRiderId, button) {
    if (!raceRiderId || button.disabled) return;
    if (!window.confirm('Confirm this RFID finish time and ignore further RFID finish reads for this rider?')) return;

    button.disabled = true;
    fetch(`/races/${encodeURIComponent(raceId)}/race-rider/${encodeURIComponent(raceRiderId)}/confirm-finish`, {
      method: 'POST',
      headers: csrfJsonHeaders(),
    })
      .then(response => {
        if (!response.ok) throw new Error('Finish confirmation failed.');
        return response.json();
      })
      .then(data => {
        if (data.timing) applyTimingUpdate(data.timing);
        refreshRiderTimings();
      })
      .catch(() => {
        button.disabled = false;
        window.alert('Failed to confirm timing. Please try again.');
      });
  }

  // Extract coordinates for lightweight change detection during live track polling.
  function extractLineCoords(geojson) {
    const features = geojson?.features;
    if (!Array.isArray(features)) return [];
    for (const feature of features) {
      const geometry = feature?.geometry;
      if (geometry?.type === 'LineString' && Array.isArray(geometry.coordinates)) {
        return geometry.coordinates;
      }
      if (geometry?.type === 'MultiLineString' && Array.isArray(geometry.coordinates)) {
        return geometry.coordinates.flat();
      }
    }
    return [];
  }

  function trackGeometryChanged(previousGeojson, nextGeojson) {
    if (!previousGeojson) return true;
    const previousCoords = extractLineCoords(previousGeojson);
    const nextCoords = extractLineCoords(nextGeojson);
    if (previousCoords.length !== nextCoords.length) return true;
    if (!nextCoords.length) return false;
    const previousLast = previousCoords.at(-1) || [];
    const nextLast = nextCoords.at(-1) || [];
    return previousLast[0] !== nextLast[0] || previousLast[1] !== nextLast[1];
  }

  // Re-stack the layout when map and table widths no longer fit side by side.
  function evaluateLayoutStacking() {
    if (!layoutNode || !mapNode || !ridersTable) return;
    ridersTable.classList.add('is-measuring');
    const shouldStack = (mapNode.getBoundingClientRect().width + ridersTable.scrollWidth + 20) > layoutNode.clientWidth;
    ridersTable.classList.remove('is-measuring');
    layoutNode.classList.toggle('is-stacked', shouldStack);
    ridersTable.classList.toggle('is-stacked', shouldStack);
  }

  function scheduleLayoutCheck() {
    if (layoutCheckHandle) return;
    layoutCheckHandle = window.requestAnimationFrame(() => {
      layoutCheckHandle = null;
      evaluateLayoutStacking();
    });
  }

  // Persist organiser map size preferences and inform Leaflet after each change.
  function applyMapSize(axis, pixels, shouldPersist) {
    if (!mapNode) return;
    const isHeight = axis === 'height';
    const storageKey = isHeight ? MAP_HEIGHT_STORAGE_KEY : MAP_WIDTH_STORAGE_KEY;
    const valueNode = isHeight ? mapHeightValue : mapWidthValue;
    mapNode.style[axis] = `${pixels}px`;
    if (valueNode) valueNode.textContent = `${pixels}px`;
    if (shouldPersist) localStorage.setItem(storageKey, String(pixels));
    if (map) map.invalidateSize();
    scheduleLayoutCheck();
  }

  function initialiseMapSizeControl(input, axis, storageKey, fallbackValue) {
    if (!input) return;
    const storedValue = parseInt(localStorage.getItem(storageKey) || '', 10);
    const initialValue = Number.isFinite(storedValue) ? storedValue : fallbackValue;
    input.value = String(initialValue);
    applyMapSize(axis, initialValue, false);
    input.addEventListener('input', () => {
      const nextValue = parseInt(input.value, 10);
      if (Number.isFinite(nextValue)) applyMapSize(axis, nextValue, true);
    });
  }

  function assignTrackColor(index) {
    if (index < TRACK_PALETTE.length) return TRACK_PALETTE[index];
    return `hsl(${(index * 137.508) % 360}, 70%, 45%)`;
  }

  // Derive track metadata from table rows so the key can be rendered separately.
  function createTrackMetaFromRow(row, index) {
    const raceRiderId = (row.dataset.raceRiderId || '').trim();
    if (!raceRiderId) return;
    const riderName = row.dataset.name || 'Unknown rider';
    const teamName = row.dataset.team || 'No team';
    const deviceId = (row.dataset.device || '').trim();
    const labelParts = [riderName, `ID ${raceRiderId}`];
    if (teamName && teamName !== 'No team') labelParts.splice(1, 0, teamName);
    trackMeta.set(raceRiderId, {
      raceRiderId,
      riderName,
      deviceId,
      color: assignTrackColor(index),
      label: labelParts.join(' | '),
      toggle: null,
      keyRow: null,
    });
  }

  function updateTrackKeyActiveStates() {
    trackMeta.forEach(meta => {
      if (!meta.keyRow) return;
      const isActive = trackLayers.has(meta.raceRiderId);
      meta.keyRow.dataset.active = isActive ? 'true' : 'false';
      if (meta.toggle) meta.toggle.checked = isActive;
    });
  }

  function buildKeyItem(label, color, isActive, includeToggle) {
    const row = document.createElement('div');
    row.className = 'track-key-item';
    row.dataset.active = isActive ? 'true' : 'false';
    if (includeToggle) {
      const toggle = document.createElement('input');
      toggle.type = 'checkbox';
      toggle.className = 'track-toggle device-track-toggle';
      toggle.style.setProperty('--track-color', color);
      row.appendChild(toggle);
    } else {
      const swatch = document.createElement('span');
      swatch.className = 'track-key-swatch';
      swatch.style.setProperty('--swatch-color', color);
      row.appendChild(swatch);
    }
    const text = document.createElement('span');
    text.textContent = label;
    row.appendChild(text);
    return row;
  }

  function buildTrackKey() {
    if (!keyListNode) return;
    keyListNode.innerHTML = '';
    keyListNode.appendChild(buildKeyItem(categoryLabel ? `Route (${categoryLabel})` : 'Route', BASE_ROUTE_COLOR, true, false));
    trackMeta.forEach(meta => {
      meta.keyRow = buildKeyItem(meta.label, meta.color, false, true);
      keyListNode.appendChild(meta.keyRow);
    });
    updateTrackKeyActiveStates();
  }

  // Draw the selected route using shared GeoJSON helpers, keeping it behind tracks.
  function drawRoute(geojson) {
    const currentMap = ensureMap();
    if (!currentMap || !maps) return;
    if (routeLayer) currentMap.removeLayer(routeLayer);
    routeLayer = maps.addGeojsonLayer(currentMap, geojson, { style: { color: BASE_ROUTE_COLOR, weight: 3 } });
    routeLayer?.bringToBack();
    if (maps.fitMapToLayer(currentMap, routeLayer)) {
      maps.setMapBoundsLimit(currentMap, routeLayer, 0.25);
      attachPostRaceBasemap();
    }
  }

  function addTrackLayer(raceRiderId, geojson, shouldRefocus) {
    const currentMap = ensureMap();
    const meta = trackMeta.get(raceRiderId);
    if (!currentMap || !maps || !meta) return;
    const existingLayer = trackLayers.get(raceRiderId);
    if (existingLayer) currentMap.removeLayer(existingLayer);
    const layer = maps.addGeojsonLayer(currentMap, geojson, { style: { color: meta.color, weight: 3 } });
    if (!layer) return;
    trackLayers.set(raceRiderId, layer);
    layer.bringToFront();
    routeLayer?.bringToBack();
    if (shouldRefocus && maps.fitMapToLayer(currentMap, layer)) attachPostRaceBasemap();
    updateTrackKeyActiveStates();
  }

  function removeTrackLayer(raceRiderId) {
    const layer = trackLayers.get(raceRiderId);
    if (layer && map) map.removeLayer(layer);
    trackLayers.delete(raceRiderId);
    updateTrackKeyActiveStates();
  }

  // Fetch and render a selected rider track, retaining selection on transient errors.
  function fetchAndShowTrack(meta, options) {
    const settings = { shouldRefocus: true, silent: false, preferCache: false, keepSelectionOnError: true, ...options };
    if (!settings.silent) updateStatus(`Loading stored track for ${meta.riderName} (race rider ${meta.raceRiderId}${meta.deviceId ? `, device ${meta.deviceId}` : ''})...`);
    return fetch(getTrackRequestUrl(meta.raceRiderId, settings.preferCache), { cache: 'no-store' })
      .then(response => {
        if (!response.ok) throw new Error('Track request failed.');
        return response.json();
      })
      .then(geojson => {
        const previousGeojson = trackDataCache.get(meta.raceRiderId);
        trackDataCache.set(meta.raceRiderId, geojson);
        if (meta.toggle && !meta.toggle.checked) return;
        if (trackGeometryChanged(previousGeojson, geojson) || !trackLayers.has(meta.raceRiderId)) {
          addTrackLayer(meta.raceRiderId, geojson, settings.shouldRefocus);
        }
        if (!settings.silent) updateStatus(`Showing stored track for ${meta.riderName} (race rider ${meta.raceRiderId}${meta.deviceId ? `, device ${meta.deviceId}` : ''}).`);
      })
      .catch(() => {
        if (!settings.keepSelectionOnError) {
          if (meta.toggle) meta.toggle.checked = false;
          removeTrackLayer(meta.raceRiderId);
        }
        if (!settings.silent) updateStatus('No stored track available for this race rider.');
      });
  }

  function refreshSelectedTracks() {
    if (document.hidden) return;
    trackMeta.forEach(meta => {
      if (!meta.toggle?.checked || inFlightTrackRefresh.has(meta.raceRiderId)) return;
      inFlightTrackRefresh.add(meta.raceRiderId);
      fetchAndShowTrack(meta, { shouldRefocus: false, silent: true, preferCache: true })
        .finally(() => inFlightTrackRefresh.delete(meta.raceRiderId));
    });
  }

  function startLiveTrackPolling() {
    if (liveTrackRefreshHandle) window.clearInterval(liveTrackRefreshHandle);
    liveTrackRefreshHandle = window.setInterval(refreshSelectedTracks, LIVE_TRACK_REFRESH_MS);
  }

  // Initialise table metadata, key toggles, map controls, and live refreshes.
  document.querySelectorAll('.rider-row').forEach((row, index) => {
    const raceRiderId = (row.dataset.raceRiderId || '').trim();
    if (raceRiderId) timingRows.set(raceRiderId, row);
    createTrackMetaFromRow(row, index);
  });

  document.querySelectorAll('.confirm-timing-btn').forEach(button => {
    button.addEventListener('click', () => confirmFinishTiming((button.dataset.raceRiderId || '').trim(), button));
  });

  buildTrackKey();
  trackMeta.forEach(meta => {
    const toggle = meta.keyRow?.querySelector('.track-toggle');
    if (!toggle) return;
    meta.toggle = toggle;
    toggle.addEventListener('change', () => {
      if (!meta.raceRiderId) {
        toggle.checked = false;
        updateStatus('No race rider id linked for this rider.');
      } else if (toggle.checked) {
        const cached = trackDataCache.get(meta.raceRiderId);
        if (cached) addTrackLayer(meta.raceRiderId, cached, true);
        fetchAndShowTrack(meta, { shouldRefocus: !cached, silent: !!cached, preferCache: true });
      } else {
        removeTrackLayer(meta.raceRiderId);
        updateStatus(`Hid track for ${meta.riderName} (race rider ${meta.raceRiderId}).`);
      }
    });
  });

  initialiseMapSizeControl(mapHeightInput, 'height', MAP_HEIGHT_STORAGE_KEY, 480);
  initialiseMapSizeControl(mapWidthInput, 'width', MAP_WIDTH_STORAGE_KEY, Math.round(mapNode?.getBoundingClientRect().width || 560));
  if (!mapNode || !raceId || !maps) {
    updateStatus('Map cannot load (missing map metadata or shared map helpers).');
  } else {
    maps.fetchRouteGeojson(raceId, categoryLabel)
      .then(geojson => {
        drawRoute(geojson);
        updateStatus(`Showing route for ${categoryText}.`);
      })
      .catch(() => updateStatus('Failed to load route data.'));
  }

  startLiveTrackPolling();
  startLiveTimingPolling();
  refreshRiderTimings();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshSelectedTracks();
      refreshRiderTimings();
    }
  });
  updateTrackKeyActiveStates();
  scheduleLayoutCheck();
  window.addEventListener('resize', scheduleLayoutCheck);

  // Manual timing modal ------------------------------------------------------
  // Keep manual timing and TXT upload logic page-specific because it is coupled
  // to post-race endpoints and its rider timing table.
  const modal = document.getElementById('manual-modal');
  if (!modal) return;
  const startInput = document.getElementById('manual-start');
  const endInput = document.getElementById('manual-end');
  const fileInput = document.getElementById('manual-log');
  const statusLine = document.getElementById('manual-status');
  const title = document.getElementById('manual-title');
  const cancelButton = document.getElementById('manual-cancel');
  const uploadButton = document.getElementById('manual-upload-text');
  const saveButton = document.getElementById('manual-save');
  let currentRaceRiderId = null;
  let currentDeviceId = '';
  let currentRiderName = '';

  function setManualStatus(message) {
    if (statusLine) statusLine.textContent = message || '';
  }

  function closeModal() {
    modal.style.display = 'none';
    currentRaceRiderId = null;
    currentDeviceId = '';
    currentRiderName = '';
    if (fileInput) fileInput.value = '';
    setManualStatus('');
  }

  function openModal(raceRiderId, riderName, deviceId, startValue, endValue) {
    currentRaceRiderId = raceRiderId;
    currentDeviceId = deviceId || '';
    currentRiderName = riderName || '';
    title.textContent = `Manual timing for ${riderName}${deviceId ? ` (device ${deviceId})` : ''}`;
    startInput.value = startValue || '';
    endInput.value = endValue || '';
    if (fileInput) fileInput.value = '';
    setManualStatus('');
    modal.style.display = 'flex';
  }

  function buildManualTimesPayload() {
    return { start_time: startInput.value, end_time: endInput.value };
  }

  cancelButton?.addEventListener('click', closeModal);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeModal();
  });

  saveButton?.addEventListener('click', () => {
    if (!currentRaceRiderId) return;
    if (!window.confirm('Are you sure? This is a permanent change. Original times will be lost.')) return;
    fetch(`/races/${encodeURIComponent(raceId)}/race-rider/${encodeURIComponent(currentRaceRiderId)}/manual-times`, {
      method: 'POST', headers: csrfJsonHeaders(), body: JSON.stringify(buildManualTimesPayload()),
    })
      .then(response => {
        if (!response.ok) throw new Error('Manual update failed.');
        return response.json();
      })
      .then(() => {
        refreshRiderTimings();
        closeModal();
      })
      .catch(() => window.alert('Failed to update times. Please check format and try again.'));
  });

  uploadButton?.addEventListener('click', async () => {
    if (!currentRaceRiderId) return;
    if (!currentDeviceId) {
      window.alert('This rider has no device id, so a log cannot be ingested.');
      return;
    }
    const file = fileInput?.files?.[0];
    if (!file) {
      window.alert('Select a .txt log file before uploading.');
      return;
    }
    if (!window.confirm('Upload this text log and rebuild track history using the times above?')) return;

    try {
      uploadButton.disabled = true;
      setManualStatus(`Uploading TXT log for ${currentRiderName || 'rider'}...`);
      updateStatus(`Uploading TXT log for ${currentRiderName || 'rider'}...`);
      const rawText = await file.text();
      if (!rawText.trim()) throw new Error('Selected file was empty.');

      const uploadResponse = await fetch('/api/v1/upload-text', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pid: currentDeviceId, log: rawText }),
      });
      if (!uploadResponse.ok) throw new Error('Upload failed.');

      const timingResponse = await fetch(`/races/${encodeURIComponent(raceId)}/race-rider/${encodeURIComponent(currentRaceRiderId)}/manual-times`, {
        method: 'POST', headers: csrfJsonHeaders(), body: JSON.stringify(buildManualTimesPayload()),
      });
      if (!timingResponse.ok) throw new Error('Timing update failed.');

      setManualStatus('Upload complete. Refreshing view...');
      window.location.reload();
    } catch (error) {
      setManualStatus('');
      window.alert(error?.message || 'TXT upload failed. Please try again.');
    } finally {
      uploadButton.disabled = false;
    }
  });

  document.querySelectorAll('.manual-edit-btn').forEach(button => {
    button.addEventListener('click', () => {
      const raceRiderId = (button.dataset.raceRiderId || '').trim();
      if (!raceRiderId) return;
      openModal(
        raceRiderId,
        button.dataset.name || 'Unknown rider',
        (button.dataset.device || '').trim(),
        (button.dataset.start || '').replace(' ', 'T'),
        (button.dataset.end || '').replace(' ', 'T'),
      );
    });
  });
})();
