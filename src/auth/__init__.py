"""
Authentication helpers for browser users.

This package holds the reusable authentication/security setup that sits below
the route blueprints. The database-backed User model is added in a later step,
so the first helper only wires Flask-Login into the app safely.
"""
