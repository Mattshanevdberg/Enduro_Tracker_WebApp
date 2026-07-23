/*
Kooksnylive next-generation shared browser foundation.

This file is intentionally parallel to the existing JavaScript components and
is not loaded by any current template. A page should load it only when that page
migrates to base_new.css. The small namespaced API avoids global helper
functions and provides common progressive-enhancement behavior without a build
step or framework dependency.

Migration rule:
- Do not load a legacy component and its *_new counterpart on the same page.
- Load base_new.js before forms_new.js, tables_new.js, and page-specific scripts.
- Use data-ui-disclosure only on native details elements whose links and content
  remain complete without JavaScript.
*/

window.EnduroUI = (function initialiseEnduroUI() {
  let liveRegion = null;

  // Run a callback immediately when the DOM is ready, or defer it when this
  // script is moved out of the normal `defer` loading pattern in the future.
  function ready(callback) {
    if (typeof callback !== 'function') return;
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', callback, { once: true });
      return;
    }
    callback();
  }

  // Mark a document or subtree as enhanced. CSS remains server-first and does
  // not hide content based on this marker; page components may use it only for
  // optional behavior that has a complete non-JavaScript fallback.
  function enhance(root) {
    const scope = root || document;
    if (scope === document) {
      document.documentElement.classList.add('ui-new-ready');
    } else if (scope.classList) {
      scope.classList.add('ui-new-ready');
    }
  }

  // Create one reusable polite live region for asynchronous status messages.
  // It remains visually hidden through base_new.css while assistive technology
  // can announce updates triggered by migrated page scripts.
  function getLiveRegion() {
    if (liveRegion?.isConnected) return liveRegion;
    liveRegion = document.querySelector('[data-ui-live-region]');
    if (liveRegion) return liveRegion;

    liveRegion = document.createElement('div');
    liveRegion.className = 'visually-hidden';
    liveRegion.dataset.uiLiveRegion = 'true';
    liveRegion.setAttribute('role', 'status');
    liveRegion.setAttribute('aria-live', 'polite');
    liveRegion.setAttribute('aria-atomic', 'true');
    document.body.appendChild(liveRegion);
    return liveRegion;
  }

  // Replace the region text through an empty intermediate state so repeated
  // messages can still be announced by screen readers.
  function announce(message) {
    const region = getLiveRegion();
    region.textContent = '';
    window.requestAnimationFrame(() => {
      region.textContent = String(message || '');
    });
  }

  // Apply a consistent busy state to forms, panels, or buttons. The caller
  // remains responsible for clearing the state after its operation finishes.
  function setBusy(element, busy) {
    if (!element) return;
    const isBusy = busy === true;
    element.classList.toggle('is-busy', isBusy);
    element.setAttribute('aria-busy', isBusy ? 'true' : 'false');
  }

  /**
   * Enhance opt-in native disclosure navigation without replacing its fallback.
   *
   * Options marked data-ui-disclosure-option close their containing details
   * after selection. In-place selectors may additionally request summary focus
   * with data-ui-disclosure-restore-focus; ordinary links retain their normal
   * navigation/focus behavior. Escape always closes and restores summary focus.
   * Binding markers keep repeated page initialisation idempotent.
   *
   * @param {Document|Element} root document or migrated-page subtree.
   */
  function attachDisclosures(root) {
    const scope = root || document;
    scope.querySelectorAll('details[data-ui-disclosure]').forEach(disclosure => {
      if (disclosure.dataset.uiDisclosureBound === 'true') return;

      const summary = disclosure.querySelector(':scope > summary');
      if (!summary) return;

      const synchronizeState = () => {
        disclosure.classList.toggle('is-open', disclosure.open);
      };

      disclosure.dataset.uiDisclosureBound = 'true';
      disclosure.addEventListener('toggle', synchronizeState);
      disclosure.addEventListener('click', event => {
        const option = event.target.closest('[data-ui-disclosure-option]');
        if (!option) return;
        window.requestAnimationFrame(() => {
          disclosure.open = false;
          if (option.hasAttribute('data-ui-disclosure-restore-focus')) {
            summary.focus({ preventScroll: true });
          }
        });
      });
      disclosure.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !disclosure.open) return;
        event.preventDefault();
        disclosure.open = false;
        summary.focus();
      });
      synchronizeState();
    });
  }

  ready(() => {
    enhance(document);
    attachDisclosures(document);
  });

  return {
    announce,
    attachDisclosures,
    enhance,
    ready,
    setBusy,
  };
})();
