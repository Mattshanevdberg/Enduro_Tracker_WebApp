"""
Focused regression tests for the layered race management feature.

The suite covers pure race parsing, lifecycle/page services, route/GPX storage,
entry management, RFID timing and snapshots, history/cache retrieval, and key
Flask controller responses. All persistence uses isolated in-memory tables.
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Blueprint, Flask
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import (
    Category,
    Device,
    Race,
    RaceRider,
    Rider,
    Route,
    TrackCache,
    TrackHist,
)
from src.services.race_riders import (
    create_race_rider,
    get_scoped_race_rider,
    load_race_rider_management_data,
    update_race_rider,
)
from src.services.race_routes import (
    RaceRouteNotFoundError,
    RaceRouteValidationError,
    clear_route_gpx,
    create_race_category,
    create_race_category_with_route,
    create_race_route,
    get_route_geojson,
    list_race_categories,
    list_race_routes,
    store_route_gpx,
)
from src.services.race_timing import (
    RaceRiderFinishMissingError,
    build_post_race_riders,
    confirm_race_rider_finish,
    list_race_rider_timings,
    race_rider_timing_payload,
    update_manual_race_rider_times,
)
from src.services.race_tracks import get_race_rider_track_geojson
from src.services.races import (
    RaceNotFoundError,
    RaceValidationError,
    load_post_race_data,
    load_race_edit_data,
    save_race,
)
from src.utils.races import (
    DEFAULT_RACE_CATEGORIES,
    normalize_race_form,
    parse_manual_time_epoch,
    select_category,
)
from src.web.races import (
    add_race_category,
    add_race_route,
    bp_races,
    confirm_finish_time,
    edit_race,
    manual_times,
    new_race,
    save_race as save_race_route,
)


VALID_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="RaceLayerTest" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Test</name><trkseg>
    <trkpt lat="-33.900000" lon="18.400000"></trkpt>
    <trkpt lat="-33.910000" lon="18.410000"></trkpt>
  </trkseg></trk>
</gpx>
"""


class RaceDatabaseTestCase(unittest.TestCase):
    """Provide isolated race-related model tables and representative records."""

    def setUp(self):
        """Create dependency-ordered tables and one race/category/entry fixture."""
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # SQLite disables foreign-key enforcement by default. Enable it in this
        # isolated connection so the focused tests exercise the same composite
        # race-scope guarantees that PostgreSQL enforces in production.
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        for table in (
            Race.__table__,
            Route.__table__,
            Category.__table__,
            Rider.__table__,
            Device.__table__,
            RaceRider.__table__,
            TrackCache.__table__,
            TrackHist.__table__,
        ):
            table.create(bind=self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

        session = self.session_factory()
        try:
            race = Race(
                name="Layered Race",
                starts_at_epoch=1_700_000_000,
                active=True,
            )
            rider_one = Rider(name="Alice Rider", category="Professional")
            rider_two = Rider(name="Bob Rider", category="Open")
            device_one = Device(id="pi001", device_info="Primary")
            device_two = Device(id="pi002", device_info="Backup")
            session.add_all([race, rider_one, rider_two, device_one, device_two])
            session.flush()
            route = Route(
                race_id=race.id,
                name="Main Course",
                gpx=VALID_GPX,
                geojson='{"history":"route"}',
            )
            session.add(route)
            session.flush()
            category = Category(
                route_id=route.id,
                race_id=race.id,
                name="Professional",
            )
            session.add(category)
            session.flush()
            race_rider = RaceRider(
                race_id=race.id,
                rider_id=rider_one.id,
                device_id=device_one.id,
                category_id=category.id,
                active=True,
                recording=True,
                start_time_rfid_epoch=100,
                finish_time_rfid_epoch=200,
                multiple_rfid_flag=True,
                finish_time_rfid_confirmed=False,
            )
            session.add(race_rider)
            session.flush()
            session.add(
                TrackCache(
                    race_rider_id=race_rider.id,
                    geojson='{"source":"cache"}',
                )
            )
            session.add(
                TrackHist(
                    race_rider_id=race_rider.id,
                    geojson='{"source":"history"}',
                    raw_txt=(
                        '{"utc":100,"lat":-33.9,"lon":18.4}\n'
                        '{"utc":150,"lat":-33.91,"lon":18.41}\n'
                        '{"utc":250,"lat":-33.92,"lon":18.42}\n'
                    ),
                    updated_at_epoch=200,
                )
            )
            session.commit()
            self.race_id = race.id
            self.category_id = category.id
            self.race_rider_id = race_rider.id
            self.rider_one_id = rider_one.id
            self.rider_two_id = rider_two.id
        finally:
            session.close()

    def tearDown(self):
        """Dispose the isolated race database."""
        self.engine.dispose()


class RaceLifecycleAndRouteServiceTestCase(RaceDatabaseTestCase):
    """Exercise race utilities, lifecycle composition, and route services."""

    def test_race_parsing_lifecycle_and_page_data(self):
        """Normalize/save races and build post/edit page contexts."""
        form = normalize_race_form(
            {
                "name": "  New Race  ",
                "website": " https://example.com ",
                "start_date": "2026-07-16",
                "start_time": "10:30",
                "description": " Test ",
                "active": "on",
            }
        )
        self.assertEqual(form["name"], "New Race")
        self.assertIsInstance(form["starts_at_epoch"], int)
        self.assertEqual(select_category("Bad", DEFAULT_RACE_CATEGORIES), "Professional")
        self.assertIsInstance(parse_manual_time_epoch("2026-07-16T10:30:00"), int)
        with self.assertRaises(ValueError):
            parse_manual_time_epoch("not-a-time")

        session = self.session_factory()
        try:
            created = save_race(session, form)
            session.commit()
            self.assertEqual(created.name, "New Race")
            category_count = session.query(Category).count()
            route_count = session.query(Route).count()
            empty_edit_data = load_race_edit_data(session, created.id, None)
            self.assertEqual(empty_edit_data["categories"], [])
            self.assertEqual(empty_edit_data["routes"], [])
            self.assertIsNone(empty_edit_data["selected_category"])
            self.assertEqual(session.query(Category).count(), category_count)
            self.assertEqual(session.query(Route).count(), route_count)
            with self.assertRaises(RaceValidationError):
                save_race(session, normalize_race_form({"name": ""}))
            with self.assertRaises(RaceNotFoundError):
                save_race(
                    session,
                    normalize_race_form({"race_id": "9999", "name": "Missing"}),
                )

            post_data = load_post_race_data(
                session,
                self.race_id,
                "Professional",
            )
            self.assertEqual(post_data["race"].name, "Layered Race")
            self.assertEqual(post_data["selected_category"], "Professional")
            self.assertEqual(post_data["riders"][0]["name"], "Alice Rider")
            self.assertTrue(post_data["has_multiple_rfid_flag"])

            edit_data = load_race_edit_data(
                session,
                self.race_id,
                "Professional",
            )
            self.assertEqual(edit_data["route"].race_id, self.race_id)
            self.assertEqual(edit_data["route"].name, "Main Course")
            self.assertEqual([route.name for route in edit_data["routes"]], ["Main Course"])
            self.assertEqual([r.name for r in edit_data["riders"]], ["Bob Rider"])
            self.assertEqual(edit_data["last_device_by_rider"][edit_data["race_riders"][0].rider_id], "pi001")
        finally:
            session.close()

    def test_route_gpx_storage_lookup_and_clear(self):
        """Create category routes, validate GPX, return GeoJSON, and clear it."""
        session = self.session_factory()
        try:
            route = create_race_route(
                session,
                self.race_id,
                "Open Course",
            )
            category = create_race_category(
                session,
                self.race_id,
                route.id,
                "Open",
            )
            self.assertEqual(category.name, "Open")
            _, shared_category = create_race_category_with_route(
                session,
                self.race_id,
                "Junior",
                route_id=route.id,
            )
            self.assertEqual(shared_category.route_id, category.route_id)
            self.assertEqual(
                [race_route.name for race_route in list_race_routes(session, self.race_id)],
                ["Main Course", "Open Course"],
            )
            store_route_gpx(
                session,
                self.race_id,
                "Open",
                VALID_GPX,
            )
            session.commit()
            self.assertIn("FeatureCollection", get_route_geojson(session, self.race_id, "Open"))
            self.assertEqual(
                get_route_geojson(session, self.race_id, "Junior"),
                get_route_geojson(session, self.race_id, "Open"),
            )
            self.assertEqual(
                list_race_categories(session, self.race_id),
                ["Junior", "Open", "Professional"],
            )

            with self.assertRaises(RaceRouteValidationError):
                create_race_route(session, self.race_id, "open course")
            with self.assertRaises(RaceRouteValidationError):
                create_race_category(
                    session,
                    self.race_id,
                    route.id,
                    "OPEN",
                )
            new_route, new_category = create_race_category_with_route(
                session,
                self.race_id,
                "Masters",
                new_route_name="Masters Course",
            )
            self.assertEqual(new_route.name, "Masters Course")
            self.assertEqual(new_category.route_id, new_route.id)

            with self.assertRaises(RaceRouteValidationError):
                store_route_gpx(
                    session,
                    self.race_id,
                    "Unsupported",
                    VALID_GPX,
                )
            clear_route_gpx(session, self.race_id, "Open")
            session.commit()
            self.assertIsNone(route.geojson)
            self.assertIsNone(get_route_geojson(session, self.race_id, "Junior"))
            with self.assertRaises(RaceRouteNotFoundError):
                clear_route_gpx(session, self.race_id, "Elite")
        finally:
            session.close()


class RaceEntryTimingTrackServiceTestCase(RaceDatabaseTestCase):
    """Exercise race assignments, timing state, snapshots, and track fallback."""

    def test_entry_management_and_timing_payloads(self):
        """Build selectors, create/update entries, and format/confirm timing."""
        session = self.session_factory()
        try:
            management = load_race_rider_management_data(session, self.category_id)
            self.assertEqual([r.name for r in management["riders"]], ["Bob Rider"])
            new_entry = create_race_rider(
                session,
                self.race_id,
                self.rider_two_id,
                "pi002",
                self.category_id,
            )
            session.commit()
            update_race_rider(new_entry, "pi002", False, False)
            session.commit()
            scoped = get_scoped_race_rider(session, self.race_id, new_entry.id)
            self.assertFalse(scoped.active)

            race_rider = get_scoped_race_rider(
                session,
                self.race_id,
                self.race_rider_id,
            )
            payload = race_rider_timing_payload(race_rider)
            self.assertTrue(payload["multiple_rfid_flag"])
            self.assertEqual(len(list_race_rider_timings(session, self.race_id)), 2)
            self.assertEqual(build_post_race_riders(session, self.category_id)[0]["name"], "Alice Rider")

            confirmed = confirm_race_rider_finish(
                session,
                self.race_id,
                self.race_rider_id,
            )
            self.assertTrue(confirmed.finish_time_rfid_confirmed)
            self.assertFalse(confirmed.multiple_rfid_flag)

            confirmed.finish_time_rfid_epoch = None
            with self.assertRaises(RaceRiderFinishMissingError):
                confirm_race_rider_finish(
                    session,
                    self.race_id,
                    self.race_rider_id,
                )
        finally:
            session.close()

    def test_category_scope_name_and_shared_route_constraints(self):
        """Enforce same-race routes and names while allowing route sharing."""
        session = self.session_factory()
        try:
            shared = Category(
                route_id=session.get(Category, self.category_id).route_id,
                race_id=self.race_id,
                name="Open",
            )
            session.add(shared)
            session.commit()
            self.assertEqual(shared.race_id, self.race_id)

            second_route = Route(race_id=self.race_id, name="Second Course")
            session.add(second_route)
            session.flush()
            session.add(
                Category(
                    route_id=second_route.id,
                    race_id=self.race_id,
                    name="professional",
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            other_race = Race(name="Other Race", active=True)
            session.add(other_race)
            session.flush()
            existing_category = session.get(Category, self.category_id)
            session.add(
                Category(
                    route_id=existing_category.route_id,
                    race_id=other_race.id,
                    name="Junior",
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
        finally:
            session.close()

    def test_race_rider_scope_and_per_race_uniqueness_constraints(self):
        """Reject cross-race categories and duplicate rider/device assignments."""
        session = self.session_factory()
        try:
            duplicate_rider = RaceRider(
                race_id=self.race_id,
                rider_id=self.rider_one_id,
                device_id="pi002",
                category_id=self.category_id,
                active=True,
                recording=True,
            )
            session.add(duplicate_rider)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            duplicate_device = RaceRider(
                race_id=self.race_id,
                rider_id=self.rider_two_id,
                device_id="pi001",
                category_id=self.category_id,
                active=True,
                recording=True,
            )
            session.add(duplicate_device)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            other_race = Race(name="Cross-scope Race", active=True)
            session.add(other_race)
            session.flush()
            session.add(
                RaceRider(
                    race_id=other_race.id,
                    rider_id=self.rider_two_id,
                    device_id="pi002",
                    category_id=self.category_id,
                    active=True,
                    recording=True,
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
        finally:
            session.close()

    def test_manual_snapshot_and_track_history_cache_fallback(self):
        """Create a trimmed snapshot and preserve history/cache preference."""
        session = self.session_factory()
        try:
            self.assertEqual(
                get_race_rider_track_geojson(
                    session,
                    self.race_id,
                    self.race_rider_id,
                ),
                '{"source":"history"}',
            )
            self.assertEqual(
                get_race_rider_track_geojson(
                    session,
                    self.race_id,
                    self.race_rider_id,
                    prefer_cache=True,
                ),
                '{"source":"cache"}',
            )

            before_count = session.query(TrackHist).count()
            update_manual_race_rider_times(
                session,
                self.race_id,
                self.race_rider_id,
                start_epoch=100,
                finish_epoch=200,
            )
            session.commit()
            self.assertEqual(session.query(TrackHist).count(), before_count + 1)
            latest = session.query(TrackHist).order_by(TrackHist.id.desc()).first()
            self.assertIn("1970-01-01T00:02:30Z", latest.gpx)
            self.assertNotIn("1970-01-01T00:04:10Z", latest.gpx)
        finally:
            session.close()


class RaceControllerTestCase(RaceDatabaseTestCase):
    """Smoke-test representative Flask race controller contracts."""

    def setUp(self):
        """Register race routes with required template endpoint dependencies."""
        super().setUp()
        repository_root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(repository_root / "templates"))
        self.app.config.update(TESTING=True, SECRET_KEY="race-layer-test")
        self.app.jinja_env.globals["csrf_token"] = lambda: "test-csrf-token"

        home = Blueprint("home", __name__)
        home.add_url_rule("/dashboard", endpoint="dashboard", view_func=lambda: "Dashboard")
        home.add_url_rule(
            "/dashboard-admin",
            endpoint="dashboard_admin",
            view_func=lambda: "Admin dashboard",
        )
        maps = Blueprint("map_tile_quota", __name__)
        maps.add_url_rule(
            "/api/map/config-status",
            endpoint="map_config_status",
            view_func=lambda: {},
        )
        maps.add_url_rule(
            "/api/map/tile-usage",
            endpoint="map_tile_usage",
            view_func=lambda: {},
            methods=["POST"],
        )
        self.app.register_blueprint(home)
        self.app.register_blueprint(maps)
        self.app.register_blueprint(bp_races)

        # Shared authorization is tested separately. Unwrap protected handlers so
        # this focused suite can verify their HTTP/service orchestration.
        for endpoint, view in {
            "races.new_race": new_race,
            "races.save_race": save_race_route,
            "races.edit_race": edit_race,
            "races.add_race_route": add_race_route,
            "races.add_race_category": add_race_category,
            "races.manual_times": manual_times,
            "races.confirm_finish_time": confirm_finish_time,
        }.items():
            self.app.view_functions[endpoint] = view.__wrapped__

        self.client = self.app.test_client()
        self.session_patch = patch("src.web.races.SessionLocal", new=self.session_factory)
        self.user_patch = patch(
            "src.web.races.current_user",
            new=SimpleNamespace(
                id=1,
                role="admin",
                rider_id=None,
                is_authenticated=True,
                is_active=True,
            ),
        )
        self.session_patch.start()
        self.user_patch.start()

    def tearDown(self):
        """Stop controller patches and dispose the isolated database."""
        self.user_patch.stop()
        self.session_patch.stop()
        super().tearDown()

    def test_race_form_page_json_and_timing_controller_contracts(self):
        """Preserve representative HTML, redirect, JSON, and validation responses."""
        self.assertEqual(self.client.get("/races/new").status_code, 200)
        self.assertEqual(self.client.post("/races/save", data={"name": ""}).status_code, 400)
        create_response = self.client.post(
            "/races/save",
            data={"name": "Controller Race", "active": "on"},
        )
        self.assertEqual(create_response.status_code, 302)
        self.assertRegex(
            create_response.headers["Location"],
            r"/races/\d+/edit$",
        )

        created_race_id = int(create_response.headers["Location"].split("/")[2])
        route_response = self.client.post(
            f"/races/{created_race_id}/routes/add",
            data={"route_name": "Main Course"},
        )
        self.assertEqual(route_response.status_code, 302)
        session = self.session_factory()
        try:
            created_route = session.query(Route).filter_by(
                race_id=created_race_id,
                name="Main Course",
            ).one()
            created_route_id = created_route.id
        finally:
            session.close()
        category_response = self.client.post(
            f"/races/{created_race_id}/categories/add",
            data={
                "category_name": "Junior Women",
                "route_choice": str(created_route_id),
            },
        )
        self.assertEqual(category_response.status_code, 302)
        self.assertIn("category=Junior+Women", category_response.headers["Location"])
        shared_response = self.client.post(
            f"/races/{created_race_id}/categories/add",
            data={
                "category_name": "Junior Men",
                "route_choice": str(created_route_id),
            },
        )
        self.assertEqual(shared_response.status_code, 302)

        self.assertEqual(self.client.get(f"/races/{self.race_id}/edit").status_code, 200)
        geojson_response = self.client.get(
            f"/races/{self.race_id}/route/geojson?category=Professional"
        )
        self.assertEqual(geojson_response.status_code, 200)
        self.assertEqual(json.loads(geojson_response.data), {"history": "route"})

        timing_response = self.client.get(
            f"/races/{self.race_id}/race-rider-timings?category=Professional"
        )
        self.assertEqual(timing_response.status_code, 200)
        self.assertEqual(timing_response.get_json()["riders"][0]["race_rider_id"], self.race_rider_id)

        invalid_manual = self.client.post(
            f"/races/{self.race_id}/race-rider/{self.race_rider_id}/manual-times",
            json={"start_time": "invalid", "end_time": ""},
        )
        self.assertEqual(invalid_manual.status_code, 400)
        confirm_response = self.client.post(
            f"/races/{self.race_id}/race-rider/{self.race_rider_id}/confirm-finish"
        )
        self.assertEqual(confirm_response.status_code, 200)
        self.assertTrue(confirm_response.get_json()["timing"]["finish_time_rfid_confirmed"])


if __name__ == "__main__":
    unittest.main()
