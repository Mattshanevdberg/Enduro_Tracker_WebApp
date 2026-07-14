/*
Shared Leaflet map helpers.

This file contains browser behaviour used by both the race form and post-race
pages. It intentionally exposes a small namespaced API instead of using
imports because the Flask application currently loads plain deferred scripts
without a JavaScript build step.

Depends on:
- Leaflet being loaded before this file.
- Esri Leaflet and Esri Leaflet Vector being loaded before this file only on
  pages that request the Esri satellite basemap.
*/

window.EnduroMaps = (function initialiseEnduroMaps() {
  const basemapLayers = new WeakMap();

  // Identify ArcGIS/Esri tile resources without counting the Esri JavaScript
  // libraries themselves. Vector and raster basemaps use slightly different
  // endpoint shapes, so this intentionally recognises the common tile endpoint
  // path fragments rather than one exact URL.
  function isEsriTileResource(resourceUrl) {
    if (!resourceUrl) return false;
    try {
      const url = new URL(resourceUrl, window.location.href);
      const host = url.hostname.toLowerCase();
      const path = url.pathname.toLowerCase();
      const isArcgisHost = host.includes('arcgis.com') || host.includes('esri.com');
      if (!isArcgisHost) return false;
      return path.includes('/tile/') || path.includes('vectortileserver') || path.includes('mapserver');
    } catch (error) {
      return false;
    }
  }

  // Use a stable client-only key for de-duplicating tile observations on this
  // page. The key is never sent to the backend; only numeric deltas are posted.
  // API-key-like query parameters are stripped before de-duplication so a tile
  // URL is not retained in memory with credentials attached.
  function normaliseTileResourceKey(resourceUrl) {
    try {
      const url = new URL(resourceUrl, window.location.href);
      ['apikey', 'apiKey', 'token', 'access_token', 'key'].forEach(param => url.searchParams.delete(param));
      return url.toString();
    } catch (error) {
      return String(resourceUrl || '');
    }
  }

  // Remove the currently attached base layer before a provider change. Route and
  // rider GeoJSON overlays are not stored here, so switching the imagery never
  // removes the application data shown on top of the map.
  function removeBasemap(map) {
    const currentBasemap = basemapLayers.get(map);
    if (currentBasemap && map?.hasLayer(currentBasemap)) {
      map.removeLayer(currentBasemap);
    }
    basemapLayers.delete(map);
  }

  // Attach the existing OSM base layer. This remains the race-form default and
  // the deliberate post-race fallback when satellite access is unavailable.
  function attachOpenStreetMapBasemap(map) {
    if (!map || typeof L === 'undefined') return null;
    removeBasemap(map);
    const layer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OSM',
      maxZoom: 22,
      maxNativeZoom: 19
    }).addTo(map);
    basemapLayers.set(map, layer);
    return layer;
  }

  // Attach Esri World Imagery through Esri Leaflet Vector. The key is a public,
  // referrer-restricted browser credential supplied by the post-race template.
  // Return null instead of throwing so callers can safely use the OSM fallback.
  function attachEsriSatelliteBasemap(map, publicMapConfig) {
    const style = publicMapConfig?.style || '';
    const apiKey = publicMapConfig?.apiKey || '';
    if (!map || typeof L === 'undefined' || !L.esri?.Vector || !apiKey || style !== 'arcgis/imagery') {
      return null;
    }

    try {
      removeBasemap(map);
      const layer = L.esri.Vector.vectorBasemapLayer(style, {
        apikey: apiKey,
        version: 2
      }).addTo(map);
      basemapLayers.set(map, layer);
      return layer;
    } catch (error) {
      return null;
    }
  }

  // Apply the configured provider after the page has fitted the map to valid
  // route or track bounds. This prevents the post-race map from first loading
  // tiles for a world-wide [0, 0], zoom 2 view that the organiser never needs.
  function attachConfiguredBasemap(map, publicMapConfig) {
    const satelliteAllowed = publicMapConfig?.satelliteAllowed === true;
    const isEsriSatellite = publicMapConfig?.provider === 'esri' && publicMapConfig?.style === 'arcgis/imagery';
    if (satelliteAllowed && isEsriSatellite) {
      const esriLayer = attachEsriSatelliteBasemap(map, publicMapConfig);
      if (esriLayer) return { layer: esriLayer, provider: 'esri' };
    }

    const osmLayer = attachOpenStreetMapBasemap(map);
    return { layer: osmLayer, provider: 'openstreetmap' };
  }

  // Create a batched Esri tile usage reporter. The reporter counts newly
  // observed Esri tile resource URLs and sends deltas, not cumulative totals, to
  // the Flask quota endpoint. It uses PerformanceObserver because Esri Leaflet
  // Vector does not expose the same tileload surface as a plain Leaflet tile
  // layer in every browser. If a Leaflet tileload event is available, callers
  // can attach it too; URL de-duplication prevents double counting.
  function createEsriTileUsageReporter(options) {
    const settings = {
      batchDelayMs: 1500,
      batchSize: 20,
      ...options
    };

    let pendingTiles = 0;
    let usageSessionKey = settings.sessionKey || '';
    let flushTimer = null;
    let performanceScanTimer = null;
    let observer = null;
    let isRunning = false;
    let isSending = false;
    let startedAt = 0;
    const countedTileKeys = new Set();
    const watchedLayers = new WeakMap();

    function buildHeaders() {
      const headers = { 'Content-Type': 'application/json' };
      if (settings.csrfToken) headers['X-CSRFToken'] = settings.csrfToken;
      return headers;
    }

    function scheduleFlush(delayMs) {
      if (!isRunning || flushTimer) return;
      flushTimer = window.setTimeout(() => {
        flushTimer = null;
        flush();
      }, typeof delayMs === 'number' ? delayMs : settings.batchDelayMs);
    }

    function handleQuotaResponse(data) {
      if (data?.usageSessionKey) usageSessionKey = data.usageSessionKey;
      if (data?.satelliteAllowed === false) {
        stop();
        if (typeof settings.onBlocked === 'function') settings.onBlocked(data);
      }
    }

    function flush() {
      if (!isRunning || isSending || pendingTiles <= 0 || !settings.tileUsageUrl) return Promise.resolve(null);

      const tilesDelta = pendingTiles;
      pendingTiles = 0;
      isSending = true;

      const payload = {
        tiles_delta: tilesDelta,
        race_id: settings.raceId || null,
        page_path: settings.pagePath || window.location.pathname,
      };
      if (usageSessionKey) payload.session_key = usageSessionKey;

      return fetch(settings.tileUsageUrl, {
        method: 'POST',
        headers: buildHeaders(),
        body: JSON.stringify(payload),
        cache: 'no-store',
        keepalive: true,
      })
        .then(response => {
          if (!response.ok) throw new Error('Tile usage report failed.');
          return response.json();
        })
        .then(data => {
          handleQuotaResponse(data);
          return data;
        })
        .catch(error => {
          if (typeof settings.onError === 'function') settings.onError(error);
          return null;
        })
        .finally(() => {
          isSending = false;
          if (pendingTiles > 0) scheduleFlush(settings.batchDelayMs);
        });
    }

    function recordTileUrl(resourceUrl) {
      if (!isRunning || !isEsriTileResource(resourceUrl)) return;
      const key = normaliseTileResourceKey(resourceUrl);
      if (!key || countedTileKeys.has(key)) return;
      countedTileKeys.add(key);
      pendingTiles += 1;
      if (pendingTiles >= settings.batchSize) {
        scheduleFlush(0);
      } else {
        scheduleFlush(settings.batchDelayMs);
      }
    }

    function processPerformanceEntry(entry) {
      if (!entry || entry.startTime < startedAt || entry.initiatorType === 'script') return;
      recordTileUrl(entry.name);
    }

    function scanPerformanceEntries() {
      if (!isRunning || !performance?.getEntriesByType) return;
      performance.getEntriesByType('resource').forEach(processPerformanceEntry);
    }

    function start() {
      if (isRunning) return;
      isRunning = true;
      startedAt = performance.now();

      if ('PerformanceObserver' in window) {
        observer = new PerformanceObserver(list => {
          list.getEntries().forEach(processPerformanceEntry);
        });
        try {
          observer.observe({ type: 'resource', buffered: true });
        } catch (error) {
          observer.observe({ entryTypes: ['resource'] });
        }
      }

      // Some browsers/extensions do not reliably fire PerformanceObserver
      // callbacks for cross-origin image resources even though the entries are
      // visible through getEntriesByType(). Poll briefly while the map is active
      // so Esri tile loads are still counted.
      scanPerformanceEntries();
      performanceScanTimer = window.setInterval(scanPerformanceEntries, 1000);
    }

    function watchLayer(layer) {
      if (!layer || watchedLayers.has(layer)) return;
      const handler = event => {
        const tileUrl = event?.tile?.currentSrc || event?.tile?.src;
        recordTileUrl(tileUrl);
      };
      if (typeof layer.on === 'function') {
        layer.on('tileload', handler);
        watchedLayers.set(layer, handler);
      }
    }

    function stop() {
      if (!isRunning) return;
      isRunning = false;
      if (flushTimer) {
        window.clearTimeout(flushTimer);
        flushTimer = null;
      }
      if (observer) {
        observer.disconnect();
        observer = null;
      }
      if (performanceScanTimer) {
        window.clearInterval(performanceScanTimer);
        performanceScanTimer = null;
      }
    }

    window.addEventListener('pagehide', () => {
      if (pendingTiles > 0) flush();
    }, { once: true });

    return {
      start,
      stop,
      flush,
      watchLayer,
      recordTileUrl,
      getUsageSessionKey: () => usageSessionKey,
    };
  }

  // Create a Leaflet map. Existing callers receive the original OSM behaviour;
  // post-race passes basemap: 'none' so it can fit its selected course before
  // requesting any satellite imagery.
  function createMap(mapElement, options) {
    if (!mapElement || typeof L === 'undefined') {
      return null;
    }

    const settings = options || {};
    const map = L.map(mapElement);
    if (settings.basemap !== 'none') {
      map.setView(settings.initialView || [0, 0], settings.initialZoom ?? 2);
      attachOpenStreetMapBasemap(map);
    }
    return map;
  }

  // Fetch the selected category route without embedding server template syntax
  // in a page script. The route endpoint is shared by both map pages.
  function fetchRouteGeojson(raceId, category) {
    const encodedRaceId = encodeURIComponent(raceId || '');
    const encodedCategory = encodeURIComponent(category || '');
    return fetch(`/races/${encodedRaceId}/route/geojson?category=${encodedCategory}`)
      .then(response => {
        if (!response.ok) {
          throw new Error('Route request failed.');
        }
        return response.json();
      });
  }

  // Add a GeoJSON layer to a Leaflet map and return it so the caller can own
  // page-specific ordering, removal, and refresh behaviour.
  function addGeojsonLayer(map, geojson, options) {
    if (!map || typeof L === 'undefined') {
      return null;
    }

    const layer = L.geoJSON(geojson, options || {});
    layer.addTo(map);
    return layer;
  }

  // Fit the visible map area to a Leaflet layer when that layer has valid bounds.
  // Empty GeoJSON is expected during normal route and track workflows, so failure
  // intentionally leaves the map at its existing view.
  function fitMapToLayer(map, layer, padding) {
    if (!map || !layer) {
      return false;
    }

    try {
      const bounds = layer.getBounds();
      if (!bounds.isValid()) {
        return false;
      }
      map.fitBounds(bounds, { padding: padding || [10, 10] });
      return true;
    } catch (error) {
      return false;
    }
  }

  // Restrict a map to a layer's padded bounds after the initial route fit. The
  // limit keeps post-race satellite users close to the race area, avoiding
  // accidental long-distance pans and the extra basemap tiles they would load.
  // A 25% padding keeps useful context around the route without permitting a
  // world-wide zoom-out; callers can supply a different fraction if required.
  function setMapBoundsLimit(map, layer, padding) {
    if (!map || !layer) {
      return false;
    }

    try {
      const bounds = layer.getBounds();
      if (!bounds.isValid()) {
        return false;
      }

      const paddedBounds = bounds.pad(typeof padding === 'number' ? padding : 0.25);
      const minimumZoom = map.getBoundsZoom(paddedBounds, false);
      map.setMaxBounds(paddedBounds);
      map.options.maxBoundsViscosity = 1.0;
      if (Number.isFinite(minimumZoom)) {
        map.setMinZoom(minimumZoom);
      }
      return true;
    } catch (error) {
      return false;
    }
  }

  return {
    createMap,
    removeBasemap,
    attachOpenStreetMapBasemap,
    attachEsriSatelliteBasemap,
    attachConfiguredBasemap,
    createEsriTileUsageReporter,
    fetchRouteGeojson,
    addGeojsonLayer,
    fitMapToLayer,
    setMapBoundsLimit,
  };
})();
