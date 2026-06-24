/*
Shared form helpers.

This file contains reusable event wiring for server-rendered forms. Page files
call these helpers after their DOM is available so templates retain ownership of
field names, actions, and server-rendered values.
*/

window.EnduroForms = (function initialiseEnduroForms() {
  // Attach change listeners to selects marked for standard form auto-submission.
  // The marker keeps the behaviour explicit and avoids changing unrelated selects.
  function attachAutoSubmitSelects(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-auto-submit]').forEach(select => {
      if (select.dataset.autoSubmitBound === 'true') {
        return;
      }

      select.dataset.autoSubmitBound = 'true';
      select.addEventListener('change', () => {
        if (select.form) {
          select.form.submit();
        }
      });
    });
  }

  return {
    attachAutoSubmitSelects,
  };
})();
