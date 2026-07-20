/*
Race-entry form behaviour.

Current responsibilities:
- Attach shared auto-submit behavior to the administrator rider selector.
- Require previous-device confirmation only when the rider says they have one.
*/

(function initialiseRaceEntryPage() {
  window.EnduroForms?.attachAutoSubmitSelects();

  const hasDeviceInputs = document.querySelectorAll('input[name="has_device"]');
  const confirmationFieldset = document.getElementById('previous-device-confirmation');
  const confirmationInputs = document.querySelectorAll(
    'input[name="confirms_previous_device"]',
  );

  const syncConfirmationState = () => {
    const selected = document.querySelector('input[name="has_device"]:checked');
    const hasDevice = selected?.value === 'yes';
    if (confirmationFieldset) {
      confirmationFieldset.hidden = !hasDevice;
    }
    confirmationInputs.forEach(input => {
      input.required = hasDevice;
      if (!hasDevice) {
        input.checked = false;
      }
    });
  };

  hasDeviceInputs.forEach(input => {
    input.addEventListener('change', syncConfirmationState);
  });
  syncConfirmationState();
})();
