/*
Shared Leaflet map helpers.

This file contains browser behaviour used by both the race form and post-race
pages. It intentionally exposes a small namespaced API instead of using
imports because the Flask application currently loads plain deferred scripts
without a JavaScript build step.

Depends on:
- Leaflet being loaded before this file.
*/

window.EnduroMaps = (function initialiseEnduroMaps() {
  // Create a standard OpenStreetMap-backed Leaflet map for a supplied DOM node.
  function createMap(mapElement) {
    if (!mapElement || typeof L === 'undefined') {
      return null;
    }

    const map = L.map(mapElement).setView([0, 0], 2);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OSM'
    }).addTo(map);
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

  return {
    createMap,
    fetchRouteGeojson,
    addGeojsonLayer,
    fitMapToLayer,
  };
})();
