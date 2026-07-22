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

  ready(() => enhance(document));

  return {
    announce,
    enhance,
    ready,
    setBusy,
  };
})();
