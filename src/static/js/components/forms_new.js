/*
Kooksnylive next-generation shared form helpers.

This file is the parallel replacement for components/forms.js. It preserves the
EnduroForms.attachAutoSubmitSelects API used by existing page scripts and adds
only opt-in, data-attribute behavior. No current template loads this file yet.

Depends on:
- components/base_new.js for shared busy-state behavior when available.
*/

window.EnduroForms = (function initialiseEnduroFormsNew() {
  // Attach change listeners only to explicitly marked selects. requestSubmit()
  // retains submit-button validation and submit event behavior, while submit()
  // remains a fallback for older browsers.
  function attachAutoSubmitSelects(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-auto-submit]').forEach(select => {
      if (select.dataset.autoSubmitBound === 'true') return;

      select.dataset.autoSubmitBound = 'true';
      select.addEventListener('change', () => {
        if (!select.form) return;
        if (typeof select.form.requestSubmit === 'function') {
          select.form.requestSubmit();
        } else {
          select.form.submit();
        }
      });
    });
  }

  // Optionally mirror the chosen local filename into a text target. The file
  // itself remains owned by the browser upload control and is never read here.
  function attachFileNameOutputs(root) {
    const scope = root || document;
    scope.querySelectorAll('input[type="file"][data-file-name-target]').forEach(input => {
      if (input.dataset.fileNameBound === 'true') return;

      const targetId = input.dataset.fileNameTarget;
      const target = targetId ? document.getElementById(targetId) : null;
      if (!target) return;

      input.dataset.fileNameBound = 'true';
      input.addEventListener('change', () => {
        const names = Array.from(input.files || []).map(file => file.name);
        target.textContent = names.join(', ') || input.dataset.emptyFileLabel || 'No file selected';
      });
    });
  }

  // Prevent accidental repeated submissions only on forms that explicitly opt
  // in. Existing pages with multiple intentional submits remain unchanged.
  function attachSubmitGuards(root) {
    const scope = root || document;
    scope.querySelectorAll('form[data-submit-once]').forEach(form => {
      if (form.dataset.submitGuardBound === 'true') return;

      form.dataset.submitGuardBound = 'true';
      form.addEventListener('submit', event => {
        if (event.defaultPrevented || form.dataset.submitting === 'true') {
          if (form.dataset.submitting === 'true') event.preventDefault();
          return;
        }

        form.dataset.submitting = 'true';
        if (window.EnduroUI?.setBusy) {
          window.EnduroUI.setBusy(form, true);
        } else {
          form.classList.add('is-busy');
          form.setAttribute('aria-busy', 'true');
        }
      });
    });
  }

  function init(root) {
    attachAutoSubmitSelects(root);
    attachFileNameOutputs(root);
    attachSubmitGuards(root);
  }

  const runInitialisation = () => init(document);
  if (window.EnduroUI?.ready) {
    window.EnduroUI.ready(runInitialisation);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runInitialisation, { once: true });
  } else {
    runInitialisation();
  }

  return {
    attachAutoSubmitSelects,
    attachFileNameOutputs,
    attachSubmitGuards,
    init,
  };
})();
