/*
Race form page behaviour.

This file owns JavaScript that is specific to templates/race_form.html. It
keeps template data in HTML data attributes and JSON data nodes so this static
file contains no Jinja syntax.

Current responsibilities:
- Show and require the inline route-name field only when that category option is selected.
- Use shared Leaflet helpers to render the selected category route preview.
- Keep the hidden rider and device form values in sync with their datalist inputs.

Future extraction guidance:
- Move reusable Leaflet helpers to components/maps.js only when another page
  needs the same stable behaviour.
- Move reusable input synchronisation helpers to components/forms.js only when
  another page needs them.
*/

(function initialiseRaceFormPage() {
  // Attach the shared category-select behaviour after the page DOM is available.
  window.EnduroForms?.attachAutoSubmitSelects();

  // Category route selection -------------------------------------------------
  // The category form supports either a durable existing route id or a new
  // descriptive route name. Keep the conditional field unambiguous and ensure
  // native browser validation matches the selected workflow.
  const routeChoice = document.getElementById('route_choice');
  const newRouteNameInput = document.getElementById('new_route_name');
  const syncNewRouteNameState = () => {
    if (!routeChoice || !newRouteNameInput) {
      return;
    }
    const createsNewRoute = routeChoice.value === 'new';
    newRouteNameInput.required = createsNewRoute;
    newRouteNameInput.disabled = !createsNewRoute;
  };
  routeChoice?.addEventListener('change', syncNewRouteNameState);
  syncNewRouteNameState();

  // GPX upload validation ----------------------------------------------------
  // The required attribute blocks an empty upload before it reaches the server,
  // where the plain-text validation response would otherwise replace the page.
  // Custom validity text keeps the browser's small validation popup specific to
  // the GPX workflow instead of using the generic required-field message.
  const gpxFileInput = document.getElementById('gpx-file');
  if (gpxFileInput) {
    gpxFileInput.addEventListener('invalid', () => {
      gpxFileInput.setCustomValidity('Please choose a GPX file before uploading.');
    });

    gpxFileInput.addEventListener('change', () => {
      gpxFileInput.setCustomValidity('');
    });
  }

  // Route preview setup -------------------------------------------------------
  // Read the server-rendered race metadata from the map container instead of
  // embedding Jinja expressions in this external JavaScript file.
  const mapElement = document.getElementById('map');
  const raceId = mapElement?.dataset?.raceId || '';
  const category = mapElement?.dataset?.category || '';

  if (mapElement && raceId && !window.EnduroMaps) {
    console.error('Shared map helpers are unavailable; skipping map preview setup.');
  } else if (mapElement && raceId) {
    const map = window.EnduroMaps.createMap(mapElement);
    if (!map) {
      console.error('Leaflet is unavailable; skipping map preview setup.');
    } else {
      window.EnduroMaps.fetchRouteGeojson(raceId, category)
      .then(geojson => {
        const layer = window.EnduroMaps.addGeojsonLayer(map, geojson);
        window.EnduroMaps.fitMapToLayer(map, layer);
      });
    }
  }

  // Rider/device form setup --------------------------------------------------
  // Read the server-rendered rider-to-last-device mapping from a JSON data node.
  // This keeps dynamic values out of the static JavaScript file.
  let lastDeviceByRider = {};
  const lastDeviceDataNode = document.getElementById('last-device-by-rider-data');
  if (lastDeviceDataNode?.textContent) {
    try {
      lastDeviceByRider = JSON.parse(lastDeviceDataNode.textContent);
    } catch (error) {
      console.error('Failed to parse last-device mapping:', error);
    }
  }

  const riderInput = document.getElementById('rider_id_input');
  const riderHidden = document.getElementById('rider_id_hidden');
  const deviceInput = document.getElementById('device_id_input');
  const deviceHidden = document.getElementById('device_id_hidden');

  // The controls are only available for an existing race. Guarding this block
  // keeps the page script safe if the race form layout changes in the future.
  if (!riderInput || !riderHidden || !deviceInput || !deviceHidden) {
    return;
  }

  // When the user selects a rider in the expected "id - name" format, submit
  // only the rider id and pre-fill the most recently associated device where known.
  riderInput.addEventListener('change', () => {
    const riderValue = riderInput.value.trim();
    const riderId = riderValue.split('-')[0].trim();
    riderHidden.value = riderId;

    if (lastDeviceByRider[riderId]) {
      deviceInput.value = lastDeviceByRider[riderId];
      deviceHidden.value = lastDeviceByRider[riderId];
      return;
    }

    // Clear any stale device selected for a previously chosen rider.
    deviceInput.value = '';
    deviceHidden.value = '';
  });

  // Keep the submitted device id in sync with the visible datalist input.
  deviceInput.addEventListener('change', () => {
    deviceHidden.value = deviceInput.value.trim();
  });
})();
