# Agent Instructions

These rules apply to all Codex work in this repository.

1. Keep comments extensive and in the format currently used.
2. Always update function descriptions and keep them in the format currently used.
3. Update `README.md` whenever any changes are made, maintaining the current README format.
4. Reference `Web Application System Design.pdf` when answering questions and performing updates.
5. Overall python file descriptions should include all the routes that are in the file when relevant, using the current format.
6. Always test the resulting behaviour and provide manual tests for the developer to perform to ensure that factionality is working.
7. Whenever routes, hostnames, access controls, or indexing requirements change, review and update the applicable crawler controls (`robots.txt`, sitemap entries, canonical URLs, and `noindex` behavior). Keep intended public viewer pages crawlable, avoid unnecessary crawling of authenticated or operational routes, and never treat crawler directives as access control.

## Python Layering Rule

When creating or modifying Python web functionality, use the following structure:

- `src/utils/*`: pure or low-level reusable helpers. These modules must not render templates or contain Flask route concerns.
- `src/services/*`: business/application logic that coordinates models, database state, payload construction, and domain rules.
- `src/web/*`: Flask controllers only. Routes should parse requests, enforce decorators, call utilities/services, manage transaction boundaries, and return templates, redirects, JSON, or HTTP errors.

Before creating a function:

1. Check whether an existing utility or service can be reused.
2. Put pure parsing, normalization, validation, conversion, and formatting in `utils`.
3. Put database queries, model mutations, durable-state coordination, and domain decisions in `services`.
4. Keep route functions thin and limited to HTTP concerns.
5. Split services by cohesive responsibility when one service file would become difficult to understand.
6. Do not create utility or service modules for static placeholder routes that have no reusable or business logic.
7. Preserve existing URLs, endpoint names, decorators, templates, response messages, and status codes unless a change is explicitly requested.
8. Add focused tests for utilities, services, and controller behavior.
9. Update function/module descriptions and `README.md` after changes.

The following files are intentional structural exceptions and should not be restructured unless explicitly requested:

- `src/api/ingest.py`
- `src/auth/routes.py`
- `src/main.py`

## Frontend Layering and Template Separation Rule

When creating or modifying frontend functionality, preserve these ownership boundaries:

- `templates/*`: semantic HTML/Jinja structure, server-rendered content, URLs, form fields, and declarative `class`/`data-*` hooks only.
- `src/static/css/*`: all presentation, layout, responsive behavior, states, animation, and visual tokens.
- `src/static/js/components/*`: reusable browser behavior shared by at least two pages.
- `src/static/js/pages/*`: page-specific selectors, initialization, interactions, endpoint construction, and workflow behavior.

Apply these rules:

1. Do not add `<style>` blocks, `style` attributes, executable inline scripts, or inline event handlers such as `onclick`, `onchange`, or `onsubmit` to templates. Use external CSS and `addEventListener`.
2. Templates may contain non-executable `<script type="application/json">` data blocks. Otherwise, expose server-rendered values to external JavaScript through safe `data-*` attributes or JSON; never put Jinja syntax inside external JavaScript.
3. Put theme tokens, typography, page shells, and baseline controls in `base.css`/`base_new.css`; reusable forms in `forms.css`/`forms_new.css`; reusable tables and compact lists in `tables.css`/`tables_new.css`; reusable maps in `maps.css`; and page-only presentation in the appropriate page stylesheet.
4. Load shared CSS first and page-specific overrides last. Load only the stylesheets required by the page.
5. Load JavaScript with `defer`, shared components first, and the page script last. Do not create a shared component until a second page needs the same stable behavior.
6. During the redesign, migrate one reviewed page at a time. Do not overwrite or delete legacy CSS/JavaScript, and do not load a legacy asset together with its `_new` counterpart on the same page.
7. Keep primary navigation, links, forms, submissions, and server-rendered content functional without JavaScript. JavaScript may enhance behavior but must not be the only way to access essential functionality.
8. Before adding a new rule or helper, check whether an existing shared or page-specific asset owns that concern. Avoid duplicated styles, duplicated event wiring, and unrelated cross-page changes.
9. After migrating a page, test desktop, mobile, keyboard/accessibility, no-JavaScript fallbacks, and affected workflows. Update `README.md` with the page’s asset ownership, load order, and migration status.

External library `<link>` and `<script>` declarations may remain in templates when required. Any other exception requires explicit user approval and documentation in `README.md`.

## Change Approval Workflow

For every request that would modify code, configuration, templates, assets, documentation, data, or external state, use this staged process:

1. **Clarify:** Inspect the existing implementation using read-only actions and ask the user any questions required to define the change. Do not modify files yet.
2. **Receive feedback:** Wait for the user’s answers and incorporate their decisions.
3. **Explain:** Provide a concise implementation outline covering the files and behavior affected, structural approach, compatibility concerns, and planned testing. Do not implement yet.
4. **Final review:** Give the user an opportunity to provide final notes or revisions.
5. **Approval:** Wait for an explicit go-ahead. The original request, answers to questions, or approval of the general idea do not count as implementation approval.
6. **Implement:** Only after explicit approval, make the agreed changes, test the resulting behavior, update required documentation, and provide manual verification steps.

If the user changes the requirements before approval, revise the questions or implementation outline and wait for approval again. Do not skip a stage unless the user explicitly instructs the agent to do so.