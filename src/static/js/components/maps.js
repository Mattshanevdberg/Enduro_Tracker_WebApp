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
      attribution: '&copy; OSM'
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
    fetchRouteGeojson,
    addGeojsonLayer,
    fitMapToLayer,
    setMapBoundsLimit,
  };
})();
