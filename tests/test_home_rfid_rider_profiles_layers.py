"""
Focused regression tests for home, crawler guidance, RFID, and rider-profile
route layering.

The tests cover categorized dashboard composition, crawler responses, RFID
filter/query behavior, existing HTTP contracts, and public rider profile
navigation. Isolated SQLite tables prevent changes to configured databases.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree

from flask import Blueprint, Flask
from flask_login import LoginManager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import IngestRfid, Race, Rider
from src.services.home import load_dashboard_display_data, load_race_display_data
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
        """Create isolated Race/Rider tables covering each lifecycle state."""
        self.engine, self.session_factory = isolated_session_factory(
            Race.__table__,
            Rider.__table__,
        )
        session = self.session_factory()
        try:
            session.add_all(
                [
                    Race(name="Upcoming", starts_at_epoch=200, status="upcoming"),
                    Race(name="Draft", starts_at_epoch=100, status="draft"),
                    Race(name="Live", starts_at_epoch=150, status="live"),
                    Race(name="Completed", starts_at_epoch=50, status="completed"),
                    Rider(name="Dashboard Rider", team="Test Team"),
                ]
            )
            session.commit()
        finally:
            session.close()

    def tearDown(self):
        """Dispose the isolated Race database."""
        self.engine.dispose()

    def test_dashboard_service_filters_orders_and_prepares_display_values(self):
        """Filter lifecycle values and compose public race/rider collections."""
        session = self.session_factory()
        try:
            public_races = load_race_display_data(
                session,
                statuses=("upcoming", "live", "completed"),
            )
            self.assertEqual(
                [race.name for race in public_races],
                ["Completed", "Live", "Upcoming"],
            )
            self.assertTrue(all(race.starts_at is not None for race in public_races))

            all_races = load_race_display_data(session)
            self.assertEqual(len(all_races), 4)
            dashboard_data = load_dashboard_display_data(session)
            self.assertEqual(dashboard_data["race_sections"]["upcoming"][0].name, "Upcoming")
            self.assertEqual(dashboard_data["race_sections"]["live"][0].name, "Live")
            self.assertEqual(dashboard_data["race_sections"]["past"][0].name, "Completed")
            self.assertEqual(dashboard_data["riders"][0].name, "Dashboard Rider")
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
            self.assertEqual(len(public_context["race_sections"]["upcoming"]), 1)
            self.assertEqual(public_context["selected_tab"], "upcoming")

            admin_template, admin_context = dashboard_admin.__wrapped__()
            self.assertEqual(admin_template, "dashboard_admin.html")
            self.assertEqual(len(admin_context["races"]), 4)

    def test_public_dashboard_template_renders_rider_tab_and_real_links(self):
        """Render the full dashboard template with its required URL endpoints."""
        repository_root = Path(__file__).resolve().parents[1]
        app = Flask(
            __name__,
            template_folder=str(repository_root / "templates"),
            static_folder=str(repository_root / "src" / "static"),
        )
        app.config.update(TESTING=True, SECRET_KEY="dashboard-template-test")
        app.jinja_env.globals["csrf_token"] = lambda: "test-csrf-token"
        login_manager = LoginManager()

        @login_manager.user_loader
        def load_test_user(_user_id):
            """Keep this isolated dashboard request anonymous."""
            return None

        login_manager.init_app(app)

        auth_blueprint = Blueprint("auth", __name__)
        auth_blueprint.add_url_rule("/login", endpoint="login", view_func=lambda: "Login")
        auth_blueprint.add_url_rule("/signup", endpoint="signup", view_func=lambda: "Signup")
        auth_blueprint.add_url_rule(
            "/logout",
            endpoint="logout",
            view_func=lambda: "Logout",
            methods=["POST"],
        )
        races_blueprint = Blueprint("races", __name__)
        for endpoint, route in (
            ("post_race", "/races/<int:race_id>/post"),
            ("enter_race", "/races/<int:race_id>/enter"),
            ("race_results", "/races/<int:race_id>/results"),
        ):
            races_blueprint.add_url_rule(
                route,
                endpoint=endpoint,
                view_func=lambda race_id: str(race_id),
            )
        profiles_blueprint = Blueprint("rider_profiles", __name__)
        profiles_blueprint.add_url_rule(
            "/rider/<int:rider_id>",
            endpoint="rider_profile",
            view_func=lambda rider_id: str(rider_id),
        )
        profiles_blueprint.add_url_rule(
            "/rider/<int:rider_id>/profile-image",
            endpoint="rider_profile_image",
            view_func=lambda rider_id: str(rider_id),
        )
        app.register_blueprint(auth_blueprint)
        app.register_blueprint(races_blueprint)
        app.register_blueprint(profiles_blueprint)
        app.register_blueprint(bp_home)

        with patch("src.web.home.SessionLocal", new=self.session_factory):
            response = app.test_client().get("/dashboard?tab=riders")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-selected-tab="riders"', response.data)
        self.assertIn(b"Dashboard Rider", response.data)
        self.assertIn(b"/rider/1", response.data)
        self.assertIn(b"dashboard-panel-upcoming", response.data)

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

    def test_sitemap_lists_current_canonical_indexable_pages(self):
        """List the landing page, dashboard, and durable rider profiles."""
        app = Flask(__name__)
        app.register_blueprint(bp_home)

        with patch("src.web.home.SessionLocal", new=self.session_factory):
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
                "https://kooksnylive.co.za/rider/1",
            ],
        )


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
    """Verify public rider-index redirection and profile-detail rendering."""

    def test_public_rider_profile_routes(self):
        """Redirect the index to its tab and render a canonical rider detail."""
        repository_root = Path(__file__).resolve().parents[1]
        engine, session_factory = isolated_session_factory(Rider.__table__)
        session = session_factory()
        try:
            rider = Rider(
                name="Profile Rider",
                team="Profile Team",
                bike="Profile Bike",
                bio="Profile biography.",
            )
            session.add(rider)
            session.commit()
            rider_id = rider.id
        finally:
            session.close()

        app = Flask(
            __name__,
            template_folder=str(repository_root / "templates"),
            static_folder=str(repository_root / "src" / "static"),
        )
        upload_directory = TemporaryDirectory()
        app.config.update(
            TESTING=True,
            SECRET_KEY="rider-profile-test",
            PROFILE_IMAGE_UPLOAD_DIR=upload_directory.name,
        )
        home_blueprint = Blueprint("home", __name__)
        home_blueprint.add_url_rule(
            "/dashboard",
            endpoint="dashboard",
            view_func=lambda: "Dashboard",
        )
        riders_blueprint = Blueprint("riders", __name__)
        riders_blueprint.add_url_rule(
            "/riders/<int:rider_id>/edit",
            endpoint="rider_form",
            view_func=lambda rider_id: f"Edit {rider_id}",
        )
        app.register_blueprint(home_blueprint)
        app.register_blueprint(riders_blueprint)
        app.register_blueprint(bp_rider_profiles)

        try:
            with (
                patch("src.web.rider_profiles.SessionLocal", new=session_factory),
                patch(
                    "src.web.rider_profiles.current_user",
                    new=SimpleNamespace(
                        is_authenticated=False,
                        is_active=False,
                        role=None,
                        rider_id=None,
                    ),
                ),
            ):
                index_response = app.test_client().get("/rider")
                self.assertEqual(index_response.status_code, 302)
                self.assertIn(b"/dashboard?tab=riders", index_response.data)

                profile_response = app.test_client().get(f"/rider/{rider_id}")
                self.assertEqual(profile_response.status_code, 200)
                self.assertIn(b"Profile Rider", profile_response.data)
                self.assertIn(b"Profile biography.", profile_response.data)
                self.assertIn(
                    f"https://kooksnylive.co.za/rider/{rider_id}".encode(),
                    profile_response.data,
                )
                missing_response = app.test_client().get("/rider/9999")
                self.assertEqual(missing_response.status_code, 404)

                stored_key = f"rider-{rider_id}-{'a' * 32}.webp"
                image_path = Path(upload_directory.name) / stored_key
                image_path.write_bytes(b"normalized-webp-test")
                image_session = session_factory()
                try:
                    image_rider = image_session.get(Rider, rider_id)
                    image_rider.profile_image_filename = stored_key
                    image_session.commit()
                finally:
                    image_session.close()

                image_response = app.test_client().get(
                    f"/rider/{rider_id}/profile-image"
                )
                self.assertEqual(image_response.status_code, 200)
                self.assertEqual(image_response.mimetype, "image/webp")
                self.assertEqual(
                    image_response.headers["X-Content-Type-Options"],
                    "nosniff",
                )
                image_response.close()
        finally:
            upload_directory.cleanup()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
