/*
Kooksnylive next-generation shared table helpers.

This file has no legacy counterpart, so it remains fully opt-in. It supports the
scroll affordances in tables_new.css and can convert reviewed tables marked with
data-responsive-table into labelled mobile rows. An explicit
data-responsive-table="compact" value selects the flatter dashboard-derived
variant. Tables remain ordinary, server-rendered HTML when JavaScript is
unavailable.

Depends on:
- components/base_new.js only for its optional DOM-ready helper.
*/

window.EnduroTables = (function initialiseEnduroTablesNew() {
  // Copy visible column headings into each cell's data-label. A table is marked
  // responsive-ready only when every body row matches the final heading row,
  // avoiding a misleading mobile layout for complex colspan/rowspan tables.
  function attachResponsiveLabels(root) {
    const scope = root || document;
    scope.querySelectorAll('table[data-responsive-table]').forEach(table => {
      if (table.dataset.responsiveBound === 'true') return;

      const headingCells = Array.from(table.querySelectorAll('thead tr:last-child th'));
      const bodyRows = Array.from(table.querySelectorAll('tbody tr'));
      if (!headingCells.length || !bodyRows.length) return;

      const headings = headingCells.map(cell => cell.textContent.trim());
      const rowsMatch = bodyRows.every(row => row.children.length === headings.length);
      if (!rowsMatch) return;

      bodyRows.forEach(row => {
        Array.from(row.children).forEach((cell, index) => {
          if (!cell.hasAttribute('data-label')) {
            cell.dataset.label = headings[index];
          }
        });
      });

      table.dataset.responsiveBound = 'true';
      table.classList.add('is-responsive-ready');
      if (table.dataset.responsiveTable === 'compact') {
        table.classList.add('is-compact-table');
        table.closest('.table-card')?.classList.add('has-compact-table');
      }
    });
  }

  // Reflect horizontal scroll position as classes consumed by tables_new.css.
  // This gives wide tables subtle left/right edge shadows without changing how
  // keyboard, touch, or pointer users scroll the native container.
  function attachScrollStates(root) {
    const scope = root || document;
    scope.querySelectorAll('.table-card').forEach(container => {
      if (container.dataset.scrollStateBound === 'true') return;

      const updateState = () => {
        const maximumScroll = Math.max(0, container.scrollWidth - container.clientWidth);
        container.classList.toggle('is-scrollable', maximumScroll > 1);
        container.classList.toggle('is-scrolled', container.scrollLeft > 1);
        container.classList.toggle(
          'is-scrolled-end',
          maximumScroll <= 1 || container.scrollLeft >= maximumScroll - 1,
        );
      };

      container.dataset.scrollStateBound = 'true';
      container.addEventListener('scroll', updateState, { passive: true });
      if ('ResizeObserver' in window) {
        const observer = new ResizeObserver(updateState);
        observer.observe(container);
        const table = container.querySelector('table');
        if (table) observer.observe(table);
      } else {
        window.addEventListener('resize', updateState, { passive: true });
      }
      updateState();
    });
  }

  function init(root) {
    attachResponsiveLabels(root);
    attachScrollStates(root);
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
    attachResponsiveLabels,
    attachScrollStates,
    init,
  };
})();
