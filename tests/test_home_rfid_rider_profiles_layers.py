"""
Focused regression tests for home, crawler guidance, RFID, and rider-profile
route layering.

The tests cover dashboard race preparation, the public robots.txt response,
RFID filter/query behavior, the existing HTTP response contracts, and the
intentionally web-only rider profile placeholder. Isolated SQLite tables
prevent changes to configured databases.
"""

import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from flask import Blueprint, Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import IngestRfid, Race
from src.services.home import load_race_display_data
from src.services.rfid import list_filtered_rfid_records
from src.utils.rfid import (
    DEFAULT_RFID_LIMIT,
    MAX_RFID_LIMIT,
    datetime_filter_to_epoch,
    normalize_rfid_filters,
    parse_optional_int,
    parse_rfid_limit,
)
from src.web.home import bp_home, dashboard, dashboard_admin, home_page
from src.web.rfid import bp_rfid, rfid_index
from src.web.rider_profiles import bp_rider_profiles


def isolated_session_factory(*tables):
    """
    Create an in-memory SQLAlchemy session factory for selected model tables.

    Input Args:
      tables: SQLAlchemy Table objects required by one focused test.

    Output:
      Tuple containing the engine and configured session factory.
    """
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in tables:
        table.create(bind=engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
    )
    return engine, session_factory


class HomeLayerTestCase(unittest.TestCase):
    """Verify dashboard service rules and controller delegation."""

    def setUp(self):
        """Create an isolated Race table with active and inactive rows."""
        self.engine, self.session_factory = isolated_session_factory(Race.__table__)
        session = self.session_factory()
        try:
            session.add_all(
                [
                    Race(name="Later Active", starts_at_epoch=200, active=True),
                    Race(name="Early Inactive", starts_at_epoch=100, active=False),
                    Race(name="Early Active", starts_at_epoch=150, active=True),
                ]
            )
            session.commit()
        finally:
            session.close()

    def tearDown(self):
        """Dispose the isolated Race database."""
        self.engine.dispose()

    def test_dashboard_service_filters_orders_and_prepares_display_values(self):
        """Load active/all races with ordered display datetimes."""
        session = self.session_factory()
        try:
            active_races = load_race_display_data(
                session,
                active_only=True,
            )
            self.assertEqual(
                [race.name for race in active_races],
                ["Early Active", "Later Active"],
            )
            self.assertTrue(all(race.starts_at is not None for race in active_races))

            all_races = load_race_display_data(
                session,
                active_only=False,
            )
            self.assertEqual(len(all_races), 3)
        finally:
            session.close()

    def test_home_controllers_select_the_expected_templates_and_scope(self):
        """Keep landing, public dashboard, and admin dashboard delegation intact."""
        app = Flask(__name__)
        with (
            app.test_request_context("/"),
            patch("src.web.home.SessionLocal", new=self.session_factory),
            patch("src.web.home.render_template", side_effect=lambda name, **ctx: (name, ctx)),
        ):
            self.assertEqual(home_page()[0], "landing.html")
            public_template, public_context = dashboard()
            self.assertEqual(public_template, "dashboard.html")
            self.assertEqual(len(public_context["races"]), 2)

            admin_template, admin_context = dashboard_admin.__wrapped__()
            self.assertEqual(admin_template, "dashboard_admin.html")
            self.assertEqual(len(admin_context["races"]), 3)

    def test_robots_txt_returns_same_protected_path_guidance_for_prod_and_dev(self):
        """Expose identical crawler exclusions through production and dev hosts."""
        app = Flask(__name__)
        app.register_blueprint(bp_home)

        expected_content = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /admin/\n"
            "Disallow: /api/v1/\n"
            "Disallow: /dashboard-admin\n"
            "Disallow: /devices\n"
            "Disallow: /rfid\n"
            "Disallow: /riders/\n"
            "Disallow: /races/new\n"
            "Disallow: /races/save\n"
            "Disallow: /races/*/edit\n"
            "Disallow: /races/*/enter\n"
            "Disallow: /races/*/entries/\n"
            "Disallow: /races/*/post-admin\n"
            "Disallow: /races/*/routes/\n"
            "Disallow: /races/*/categories/\n"
            "Disallow: /races/*/route/upload\n"
            "Disallow: /races/*/route/remove\n"
            "Disallow: /races/*/riders/\n"
            "Disallow: /races/*/race-rider/*/manual-times\n"
            "Disallow: /races/*/race-rider/*/confirm-finish\n"
            "Sitemap: https://kooksnylive.co.za/sitemap.xml\n"
        )

        for hostname in ("kooksnylive.co.za", "dev.kooksnylive.co.za"):
            with self.subTest(hostname=hostname):
                response = app.test_client().get(
                    "/robots.txt",
                    headers={"Host": hostname},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "text/plain")
                self.assertEqual(response.get_data(as_text=True), expected_content)

    def test_sitemap_lists_only_current_canonical_indexable_pages(self):
        """List the landing page and dashboard while rider remains a placeholder."""
        app = Flask(__name__)
        app.register_blueprint(bp_home)

        response = app.test_client().get(
            "/sitemap.xml",
            headers={"Host": "kooksnylive.co.za"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/xml")
        sitemap_root = ElementTree.fromstring(response.get_data(as_text=True))
        namespace = {"sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [
            location.text
            for location in sitemap_root.findall("sitemap:url/sitemap:loc", namespace)
        ]
        self.assertEqual(
            locations,
            [
                "https://kooksnylive.co.za/",
                "https://kooksnylive.co.za/dashboard",
            ],
        )
        self.assertNotIn("https://kooksnylive.co.za/rider", locations)


class RfidLayerTestCase(unittest.TestCase):
    """Verify RFID filter utilities, query service, and controller responses."""

    def setUp(self):
        """Create isolated RFID rows and an admin-viewer Flask application."""
        self.engine, self.session_factory = isolated_session_factory(IngestRfid.__table__)
        session = self.session_factory()
        try:
            session.add_all(
                [
                    IngestRfid(
                        epc="EPC-ONE",
                        ant="1",
                        reader_id="reader-a",
                        time_stamp_epoch=100,
                        received_at_epoch=110,
                    ),
                    IngestRfid(
                        epc="EPC-TWO",
                        ant="2",
                        reader_id="reader-b",
                        time_stamp_epoch=200,
                        received_at_epoch=210,
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()

        repository_root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(repository_root / "templates"))
        self.app.config.update(TESTING=True, SECRET_KEY="rfid-layer-test")
        home_blueprint = Blueprint("home", __name__)
        home_blueprint.add_url_rule(
            "/dashboard-admin",
            endpoint="dashboard_admin",
            view_func=lambda: "Admin dashboard",
        )
        self.app.register_blueprint(home_blueprint)
        self.app.register_blueprint(bp_rfid)
        self.app.view_functions["rfid.rfid_index"] = rfid_index.__wrapped__
        self.client = self.app.test_client()
        self.session_patch = patch("src.web.rfid.SessionLocal", new=self.session_factory)
        self.session_patch.start()

    def tearDown(self):
        """Stop controller patching and dispose the isolated RFID database."""
        self.session_patch.stop()
        self.engine.dispose()

    def test_rfid_filter_utilities_and_query_service(self):
        """Normalize, parse, clamp, filter, order, and format RFID viewer data."""
        filters = normalize_rfid_filters({"epc": "  two ", "limit": "1"})
        self.assertEqual(filters["epc"], "two")
        self.assertEqual(parse_optional_int(" 12 "), 12)
        self.assertIsNone(parse_optional_int(""))
        self.assertEqual(parse_rfid_limit("0"), 1)
        self.assertEqual(parse_rfid_limit(str(MAX_RFID_LIMIT + 1)), MAX_RFID_LIMIT)
        self.assertEqual(parse_rfid_limit(""), DEFAULT_RFID_LIMIT)
        self.assertIsInstance(datetime_filter_to_epoch("2026-07-16T10:00:00"), int)

        session = self.session_factory()
        try:
            rows = list_filtered_rfid_records(session, filters)
            self.assertEqual([row.epc for row in rows], ["EPC-TWO"])
            self.assertIsNotNone(rows[0].reader_time)
            self.assertIsNotNone(rows[0].received_at)
        finally:
            session.close()

    def test_rfid_route_success_and_invalid_filter_responses(self):
        """Keep the RFID viewer template and invalid-filter status behavior."""
        response = self.client.get("/rfid/?reader_id=reader&limit=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"EPC-TWO", response.data)
        self.assertNotIn(b"EPC-ONE", response.data)

        invalid_response = self.client.get("/rfid/?id=not-a-number")
        self.assertEqual(invalid_response.status_code, 400)
        self.assertIn(b"Numeric filters must be whole numbers", invalid_response.data)


class RiderProfilesRouteTestCase(unittest.TestCase):
    """Verify the intentionally web-only public rider-profile placeholder."""

    def test_public_rider_profiles_placeholder(self):
        """Keep GET /rider public and rendering the expected placeholder content."""
        repository_root = Path(__file__).resolve().parents[1]
        app = Flask(__name__, template_folder=str(repository_root / "templates"))
        app.config.update(TESTING=True, SECRET_KEY="rider-profile-test")
        home_blueprint = Blueprint("home", __name__)
        home_blueprint.add_url_rule(
            "/dashboard",
            endpoint="dashboard",
            view_func=lambda: "Dashboard",
        )
        app.register_blueprint(home_blueprint)
        app.register_blueprint(bp_rider_profiles)

        response = app.test_client().get("/rider")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Rider Profiles", response.data)
        self.assertIn(b"Placeholder only", response.data)


if __name__ == "__main__":
    unittest.main()
