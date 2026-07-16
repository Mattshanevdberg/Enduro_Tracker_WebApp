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
