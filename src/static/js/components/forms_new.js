/*
Kooksnylive next-generation shared form helpers.

This file is the parallel replacement for components/forms.js. It preserves the
EnduroForms.attachAutoSubmitSelects API used by existing page scripts and adds
only opt-in, data-attribute behavior, including local image preview updates for
future migrated rider forms. No current template loads this file yet.

Depends on:
- components/base_new.js for shared busy-state behavior when available.
*/

window.EnduroForms = (function initialiseEnduroFormsNew() {
  const previewObjectUrls = new WeakMap();

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

  /**
   * Preview a selected local image without uploading or reading its contents.
   *
   * Each opt-in file input supplies the target image id through
   * data-image-preview-target. Object URLs are revoked when replaced or reset,
   * and the server-rendered original src remains the no-selection fallback.
   *
   * @param {Document|Element} root document or migrated-form subtree.
   */
  function attachImagePreviews(root) {
    const scope = root || document;
    scope.querySelectorAll('input[type="file"][data-image-preview-target]').forEach(input => {
      if (input.dataset.imagePreviewBound === 'true') return;

      const targetId = input.dataset.imagePreviewTarget;
      const preview = targetId ? document.getElementById(targetId) : null;
      if (!preview || preview.tagName !== 'IMG') return;

      const originalSource = preview.currentSrc || preview.src;

      const restoreOriginal = () => {
        const previousUrl = previewObjectUrls.get(input);
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        previewObjectUrls.delete(input);
        preview.src = originalSource;
      };

      const updatePreview = () => {
        const file = input.files?.[0];
        if (!file || !file.type.startsWith('image/')) {
          restoreOriginal();
          return;
        }

        const previousUrl = previewObjectUrls.get(input);
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        const nextUrl = URL.createObjectURL(file);
        previewObjectUrls.set(input, nextUrl);
        preview.src = nextUrl;
      };

      input.dataset.imagePreviewBound = 'true';
      input.addEventListener('change', updatePreview);
      input.form?.addEventListener('reset', () => {
        window.requestAnimationFrame(restoreOriginal);
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
    attachImagePreviews(root);
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
    attachImagePreviews,
    attachSubmitGuards,
    init,
  };
})();
