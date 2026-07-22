/*
Public dashboard interaction controller.

Responsibilities:
- enhance server-rendered tab links without removing no-JavaScript navigation;
- synchronize hero copy/art, ARIA state, URL state, and visible tab panel;
- compact the hero as the independent list pane scrolls;
- load public rider pages into an accessible dialog while preserving each link
  as a complete standalone-page fallback.
*/

(() => {
  'use strict';

  const app = document.querySelector('[data-dashboard-app]');
  if (!app) return;

  const content = app.querySelector('[data-dashboard-content]');
  const heroImage = app.querySelector('[data-dashboard-hero-image]');
  const eyebrow = app.querySelector('[data-dashboard-eyebrow]');
  const title = app.querySelector('[data-dashboard-title]');
  const message = app.querySelector('[data-dashboard-message]');
  const tabs = [...app.querySelectorAll('[data-dashboard-tab]')];
  const panels = [...app.querySelectorAll('[data-dashboard-panel]')];
  const compactThreshold = 56;

  /**
   * Select one server-rendered dashboard panel and synchronize its hero.
   *
   * @param {string} tabKey supported dashboard tab key.
   * @param {boolean} updateHistory whether to add the selection to browser history.
   */
  function selectTab(tabKey, updateHistory = true) {
    const selectedTab = tabs.find((tab) => tab.dataset.dashboardTab === tabKey) || tabs[0];
    if (!selectedTab) return;

    tabs.forEach((tab) => {
      const selected = tab === selectedTab;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });

    panels.forEach((panel) => {
      const selected = panel.dataset.dashboardPanel === selectedTab.dataset.dashboardTab;
      panel.hidden = !selected;
      panel.classList.remove('is-entering');
      if (selected) {
        // Restart only the short transform animation; content never relies on it.
        void panel.offsetWidth;
        panel.classList.add('is-entering');
      }
    });

    eyebrow.textContent = selectedTab.dataset.eyebrow || '';
    title.textContent = selectedTab.dataset.title || '';
    message.textContent = selectedTab.dataset.message || '';
    heroImage.src = selectedTab.dataset.heroImage;
    app.dataset.selectedTab = selectedTab.dataset.dashboardTab;
    app.classList.remove('is-compact');
    content.scrollTo({ top: 0, behavior: 'auto' });

    if (updateHistory) {
      window.history.pushState(
        { dashboardTab: selectedTab.dataset.dashboardTab },
        '',
        selectedTab.href,
      );
    }
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', (event) => {
      event.preventDefault();
      selectTab(tab.dataset.dashboardTab);
    });

    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let nextIndex = index;
      if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = tabs.length - 1;
      tabs[nextIndex].focus();
      selectTab(tabs[nextIndex].dataset.dashboardTab);
    });
  });

  content.addEventListener('scroll', () => {
    if (content.scrollTop > compactThreshold) app.classList.add('is-compact');
    if (content.scrollTop < 12) app.classList.remove('is-compact');
  }, { passive: true });

  window.addEventListener('popstate', () => {
    const tabKey = new URL(window.location.href).searchParams.get('tab') || 'upcoming';
    selectTab(tabKey, false);
  });

  const dialog = document.querySelector('[data-rider-profile-dialog]');
  const dialogContent = document.querySelector('[data-rider-profile-dialog-content]');
  const closeButton = document.querySelector('[data-rider-profile-close]');

  if (!dialog || typeof dialog.showModal !== 'function') return;

  /** Load a public rider page and copy its marked profile card into the dialog. */
  async function openRiderProfile(url) {
    dialogContent.innerHTML = '<p>Loading rider profile…</p>';
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch(url, { headers: { 'X-Requested-With': 'dashboard-profile-dialog' } });
      if (!response.ok) throw new Error(`Profile request failed with ${response.status}`);
      const profileDocument = new DOMParser().parseFromString(await response.text(), 'text/html');
      const profile = profileDocument.querySelector('[data-rider-profile-content]');
      if (!profile) throw new Error('Profile content was missing from the response');
      dialogContent.replaceChildren(profile);
      closeButton.focus();
    } catch (error) {
      window.location.assign(url);
    }
  }

  document.addEventListener('click', (event) => {
    const profileLink = event.target.closest('[data-rider-profile-link]');
    if (!profileLink || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    openRiderProfile(profileLink.href);
  });

  closeButton.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
