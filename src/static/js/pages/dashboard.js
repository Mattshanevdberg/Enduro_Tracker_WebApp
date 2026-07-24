/*
Public dashboard interaction controller.

Responsibilities:
- enhance server-rendered tab links without removing no-JavaScript navigation;
- synchronize hero copy/art, ARIA state, URL state, and visible tab panel;
- filter and rank each panel's server-rendered rows by primary/secondary fields;
- retain independent search state, original ordering, counts, and empty states;
- provide a consistent accessible clear control for every enhanced search;
- coordinate the native mobile navigation accordion and close it after selection;
- compact the hero from desktop scroll intent without short-list oscillation;
- preserve the existing position-based mobile hero compaction;
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
  const mobileMenu = app.querySelector('[data-dashboard-mobile-menu]');
  const mobileMenuToggle = app.querySelector('[data-dashboard-mobile-menu-toggle]');
  const mobileTitle = app.querySelector('[data-dashboard-mobile-title]');
  const mobileTabs = [...app.querySelectorAll('[data-dashboard-mobile-tab]')];
  const panels = [...app.querySelectorAll('[data-dashboard-panel]')];
  const searchForms = [...app.querySelectorAll('[data-dashboard-search]')];
  const compactThreshold = 56;
  const compactTransitionGuardMs = 360;
  const desktopLayoutQuery = window.matchMedia('(min-width: 721px)');
  let desktopCompactLocked = false;
  let desktopUpwardIntent = false;
  let desktopScrollbarDragActive = false;
  let compactTransitionGuardUntil = 0;
  let previousContentScrollTop = content.scrollTop;

  /**
   * Normalize user-entered and server-rendered values for forgiving matching.
   *
   * Input Args:
   *   value: Search query or row metadata value.
   *
   * Output:
   *   Lowercase, accent-insensitive text with collapsed whitespace.
   */
  function normalizeSearchValue(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLocaleLowerCase()
      .replace(/\s+/g, ' ')
      .trim();
  }

  /**
   * Assign a match tier so names always rank above secondary metadata.
   *
   * Input Args:
   *   row: Dashboard race/rider row containing explicit search metadata.
   *   query: Normalized non-empty search query.
   *
   * Output:
   *   One for a name-prefix match, two for a name-substring match, three for a
   *   secondary-field match, or null when the row does not match.
   */
  function searchRank(row, query) {
    const name = normalizeSearchValue(row.dataset.searchName);
    const secondary = normalizeSearchValue(row.dataset.searchSecondary);
    if (name.startsWith(query)) return 1;
    if (name.includes(query)) return 2;
    if (secondary.includes(query)) return 3;
    return null;
  }

  /**
   * Format the visible/total counter with the panel's singular/plural noun.
   *
   * Input Args:
   *   searchForm: Panel-owned search form containing unit labels.
   *   visibleCount: Number of rows currently matching.
   *   totalCount: Number of server-rendered rows in the panel.
   *   filtered: Whether a non-empty query is active.
   *
   * Output:
   *   Human-readable total or filtered counter text.
   */
  function formatSearchCount(searchForm, visibleCount, totalCount, filtered) {
    const noun = totalCount === 1
      ? searchForm.dataset.searchSingular
      : searchForm.dataset.searchPlural;
    return filtered
      ? `${visibleCount} of ${totalCount} ${noun}`
      : `${totalCount} ${noun}`;
  }

  /**
   * Activate one panel's independent, progressively enhanced search control.
   *
   * The original node order is captured once. Each query sorts matching rows
   * by name prefix, name substring, then secondary fields, while retaining the
   * original server order inside each tier. Clearing restores the exact list.
   *
   * Input Args:
   *   searchForm: Search form owned by one dashboard panel.
   *
   * Output:
   *   None. Event listeners update only the owning panel.
   */
  function initializeDashboardSearch(searchForm) {
    const panel = searchForm.closest('[data-dashboard-panel]');
    const input = searchForm.querySelector('[data-dashboard-search-input]');
    const clearButton = searchForm.querySelector('[data-dashboard-search-clear]');
    const count = panel?.querySelector('[data-dashboard-count]');
    const list = panel?.querySelector('[data-dashboard-list]');
    const empty = panel?.querySelector('[data-dashboard-search-empty]');
    const originalRows = list
      ? [...list.querySelectorAll('[data-dashboard-search-row]')]
      : [];

    if (!panel || !input || !clearButton || !count) return;

    /**
     * Apply the current query, reorder matches by tier, and synchronize states.
     *
     * Input Args:
     *   None. The query is read from the panel-owned search input.
     *
     * Output:
     *   None. Rows, count text, list visibility, and empty state are updated.
     */
    function applySearch() {
      const query = normalizeSearchValue(input.value);
      const rankedRows = originalRows
        .map((row, originalIndex) => ({
          row,
          originalIndex,
          rank: query ? searchRank(row, query) : 0,
        }));
      const matchingRows = rankedRows
        .filter(({ rank }) => rank !== null)
        .sort((left, right) => (
          left.rank - right.rank || left.originalIndex - right.originalIndex
        ));
      const matchingNodes = new Set(matchingRows.map(({ row }) => row));
      const nonMatchingRows = rankedRows.filter(({ row }) => !matchingNodes.has(row));

      [...matchingRows, ...nonMatchingRows].forEach(({ row }) => {
        row.hidden = !matchingNodes.has(row);
        list?.append(row);
      });

      const filtered = query.length > 0;
      const visibleCount = matchingRows.length;
      count.value = formatSearchCount(
        searchForm,
        visibleCount,
        originalRows.length,
        filtered,
      );
      count.textContent = count.value;
      if (list) list.hidden = filtered && visibleCount === 0;
      if (empty) empty.hidden = !filtered || visibleCount !== 0;
      clearButton.hidden = input.value.length === 0;
    }

    searchForm.addEventListener('submit', (event) => {
      event.preventDefault();
      applySearch();
    });
    input.addEventListener('input', applySearch);
    clearButton.addEventListener('click', () => {
      input.value = '';
      applySearch();
      input.focus();
    });
    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !input.value) return;
      event.preventDefault();
      input.value = '';
      applySearch();
    });

    if (originalRows.length === 0) {
      input.disabled = true;
      input.placeholder = `No ${searchForm.dataset.searchPlural} to search`;
    }
    searchForm.hidden = false;
  }

  /**
   * Reset all desktop compaction state and restore the expanded hero.
   *
   * Input Args:
   *   None.
   *
   * Output:
   *   None. The desktop intent lock and compact class are cleared together.
   */
  function resetHeroCompaction() {
    desktopCompactLocked = false;
    desktopUpwardIntent = false;
    desktopScrollbarDragActive = false;
    compactTransitionGuardUntil = 0;
    app.classList.remove('is-compact');
  }

  /**
   * Collapse and lock the desktop hero after a deliberate downward scroll.
   *
   * The guard covers the CSS grid transition. When a short list becomes fully
   * visible during that transition, the browser can clamp scrollTop to zero;
   * the lock prevents that layout-generated event from expanding the hero.
   *
   * Input Args:
   *   None.
   *
   * Output:
   *   None. Desktop compaction state and the compact class are activated.
   */
  function lockDesktopHeroCompact() {
    desktopCompactLocked = true;
    desktopUpwardIntent = false;
    compactTransitionGuardUntil = performance.now() + compactTransitionGuardMs;
    app.classList.add('is-compact');
  }

  /**
   * Expand a locked desktop hero when an upward action occurs at the list top.
   *
   * Input Args:
   *   None.
   *
   * Output:
   *   None. The hero remains compact unless the desktop lock is active and
   *   the independently scrolling content pane is at its top boundary.
   */
  function expandLockedHeroFromUpwardIntent() {
    if (
      desktopLayoutQuery.matches
      && desktopCompactLocked
      && content.scrollTop < 12
    ) {
      resetHeroCompaction();
    }
  }

  /**
   * Close the mobile navigation and synchronize its enhanced visual state.
   *
   * @param {boolean} restoreFocus whether focus should return to the menu summary.
   */
  function closeMobileMenu(restoreFocus = false) {
    if (!mobileMenu) return;
    mobileMenu.open = false;
    app.classList.remove('is-mobile-menu-open');
    if (restoreFocus) {
      window.requestAnimationFrame(() => mobileMenuToggle?.focus());
    }
  }

  /**
   * Select one server-rendered dashboard panel and synchronize its hero.
   *
   * @param {string} tabKey supported dashboard tab key.
   * @param {boolean} updateHistory whether to add the selection to browser history.
   * @param {boolean} restoreMenuFocus whether a mobile selection returns focus to its summary.
   */
  function selectTab(tabKey, updateHistory = true, restoreMenuFocus = false) {
    const selectedTab = tabs.find((tab) => tab.dataset.dashboardTab === tabKey) || tabs[0];
    if (!selectedTab) return;

    tabs.forEach((tab) => {
      const selected = tab === selectedTab;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });

    mobileTabs.forEach((tab) => {
      const selected = tab.dataset.dashboardMobileTab === selectedTab.dataset.dashboardTab;
      if (selected) tab.setAttribute('aria-current', 'page');
      else tab.setAttribute('aria-current', 'false');
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
    if (mobileTitle) mobileTitle.textContent = selectedTab.dataset.title || '';
    message.textContent = selectedTab.dataset.message || '';
    heroImage.src = selectedTab.dataset.heroImage;
    app.dataset.selectedTab = selectedTab.dataset.dashboardTab;
    resetHeroCompaction();
    content.scrollTo({ top: 0, behavior: 'auto' });
    previousContentScrollTop = 0;
    closeMobileMenu(restoreMenuFocus);

    if (updateHistory) {
      window.history.pushState(
        { dashboardTab: selectedTab.dataset.dashboardTab },
        '',
        selectedTab.href,
      );
    }
  }

  searchForms.forEach(initializeDashboardSearch);

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

  mobileTabs.forEach((tab) => {
    tab.addEventListener('click', (event) => {
      event.preventDefault();
      selectTab(tab.dataset.dashboardMobileTab, true, true);
    });
  });

  if (mobileMenu) {
    mobileMenu.addEventListener('toggle', () => {
      const open = mobileMenu.open;
      app.classList.toggle('is-mobile-menu-open', open);
    });

    mobileMenu.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !mobileMenu.open) return;
      event.preventDefault();
      closeMobileMenu(true);
    });
  }

  content.addEventListener('scroll', () => {
    const currentScrollTop = content.scrollTop;

    /*
    Mobile retains the original position-only behavior. Its compact hero has a
    different height/layout and is outside the desktop short-list bug scope.
    */
    if (!desktopLayoutQuery.matches) {
      if (currentScrollTop > compactThreshold) app.classList.add('is-compact');
      if (currentScrollTop < 12) app.classList.remove('is-compact');
      previousContentScrollTop = currentScrollTop;
      return;
    }

    const movedUp = currentScrollTop < previousContentScrollTop;
    if (!desktopCompactLocked && currentScrollTop > compactThreshold) {
      lockDesktopHeroCompact();
    } else if (
      desktopCompactLocked
      && currentScrollTop < 12
      && (
        desktopUpwardIntent
        || (
          desktopScrollbarDragActive && movedUp
          && performance.now() >= compactTransitionGuardUntil
        )
      )
    ) {
      resetHeroCompaction();
    }
    previousContentScrollTop = currentScrollTop;
  }, { passive: true });

  /*
  A short desktop list may have no remaining overflow after compaction, so an
  upward wheel/trackpad action will not emit another scroll event. Capture that
  explicit intent and expand directly when the pane is already at the top.
  */
  content.addEventListener('wheel', (event) => {
    if (!desktopLayoutQuery.matches || !desktopCompactLocked) return;
    if (event.deltaY < 0) {
      desktopUpwardIntent = true;
      expandLockedHeroFromUpwardIntent();
    } else if (event.deltaY > 0) {
      desktopUpwardIntent = false;
    }
  }, { passive: true });

  /*
  A scrollbar drag has no wheel/keyboard signal. Mark pointer activity inside
  the reserved scrollbar gutter so reaching the top still counts as deliberate
  upward intent, without treating unrelated layout-driven scroll changes as it.
  */
  content.addEventListener('pointerdown', (event) => {
    if (
      !desktopLayoutQuery.matches
      || !desktopCompactLocked
      || event.pointerType !== 'mouse'
    ) {
      return;
    }
    const contentBounds = content.getBoundingClientRect();
    const scrollbarWidth = content.offsetWidth - content.clientWidth;
    desktopScrollbarDragActive = (
      scrollbarWidth > 0
      && event.clientX >= contentBounds.right - scrollbarWidth
    );
  });

  window.addEventListener('pointerup', () => {
    desktopScrollbarDragActive = false;
  });

  /*
  Preserve an equivalent keyboard route while ignoring keys used inside form
  controls. Normal scroll events still cover scrollbar dragging on long lists.
  */
  content.addEventListener('keydown', (event) => {
    if (
      !desktopLayoutQuery.matches
      || !desktopCompactLocked
      || event.target.closest('input, textarea, select, [contenteditable="true"]')
    ) {
      return;
    }

    const upwardKey = (
      ['ArrowUp', 'PageUp', 'Home'].includes(event.key)
      || (event.key === ' ' && event.shiftKey)
    );
    const downwardKey = (
      ['ArrowDown', 'PageDown', 'End'].includes(event.key)
      || (event.key === ' ' && !event.shiftKey)
    );
    if (upwardKey) {
      desktopUpwardIntent = true;
      expandLockedHeroFromUpwardIntent();
    } else if (downwardKey) {
      desktopUpwardIntent = false;
    }
  });

  desktopLayoutQuery.addEventListener('change', () => {
    desktopCompactLocked = (
      desktopLayoutQuery.matches
      && app.classList.contains('is-compact')
    );
    desktopUpwardIntent = false;
    desktopScrollbarDragActive = false;
    compactTransitionGuardUntil = desktopCompactLocked
      ? performance.now() + compactTransitionGuardMs
      : 0;
    previousContentScrollTop = content.scrollTop;
  });

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
