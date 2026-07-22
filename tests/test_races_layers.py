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
    LeaderboardCache,
    LeaderboardHist,
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
from src.services.race_entry import (
    RaceEntryValidationError,
    assign_device_and_create_entry,
    get_rider_previous_device_id,
)
from src.services.race_routes import (
    RaceRouteNotFoundError,
    RaceRouteValidationError,
    assign_race_category_route,
    clear_route_gpx,
    create_race_category,
    create_race_category_with_route,
    create_race_route,
    delete_unused_race_category,
    delete_unused_race_route,
    get_route_geojson,
    list_race_category_records,
    list_race_routes,
    rename_race_category,
    rename_race_route,
    reorder_race_category,
    set_race_category_archived,
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
    normalize_race_form,
    parse_manual_time_epoch,
    parse_positive_id,
)
from src.utils.race_entry import normalize_race_entry_form
from src.web.races import (
    add_race_category,
    add_race_route,
    bp_races,
    confirm_finish_time,
    delete_race_category,
    delete_route,
    edit_race,
    edit_race_category,
    enter_race,
    enter_race_admin,
    manual_times,
    new_race,
    rename_route,
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
            LeaderboardCache.__table__,
            LeaderboardHist.__table__,
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
                status="upcoming",
            )
            rider_one = Rider(name="Alice Rider")
            rider_two = Rider(name="Bob Rider")
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
                name_normalized="professional",
                display_order=1,
                archived=False,
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
                "end_date": "2026-07-16",
                "end_time": "16:30",
                "description": " Test ",
                "location": " Test Valley ",
                "logo_image_filename": " test-race.webp ",
                "status": "upcoming",
            }
        )
        self.assertEqual(form["name"], "New Race")
        self.assertIsInstance(form["starts_at_epoch"], int)
        self.assertIsInstance(form["ends_at_epoch"], int)
        self.assertEqual(form["location"], "Test Valley")
        self.assertEqual(form["logo_image_filename"], "test-race.webp")
        self.assertEqual(parse_positive_id("42", required=True), 42)
        with self.assertRaises(ValueError):
            parse_positive_id("Professional", required=True)
        self.assertIsInstance(parse_manual_time_epoch("2026-07-16T10:30:00"), int)
        with self.assertRaises(ValueError):
            parse_manual_time_epoch("not-a-time")

        session = self.session_factory()
        try:
            created = save_race(session, form)
            session.commit()
            self.assertEqual(created.name, "New Race")
            self.assertEqual(created.status, "upcoming")
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
                self.category_id,
            )
            self.assertEqual(post_data["race"].name, "Layered Race")
            self.assertEqual(post_data["selected_category"].id, self.category_id)
            self.assertEqual(post_data["riders"][0]["name"], "Alice Rider")
            self.assertTrue(post_data["has_multiple_rfid_flag"])

            edit_data = load_race_edit_data(
                session,
                self.race_id,
                self.category_id,
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
                category.id,
                VALID_GPX,
            )
            session.commit()
            self.assertIn("FeatureCollection", get_route_geojson(session, self.race_id, category.id))
            self.assertEqual(
                get_route_geojson(session, self.race_id, shared_category.id),
                get_route_geojson(session, self.race_id, category.id),
            )
            self.assertEqual(
                [
                    row.name
                    for row in list_race_category_records(session, self.race_id)
                ],
                ["Professional", "Open", "Junior"],
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
                    999999,
                    VALID_GPX,
                )
            clear_route_gpx(session, self.race_id, category.id)
            session.commit()
            self.assertIsNone(route.geojson)
            self.assertIsNone(get_route_geojson(session, self.race_id, shared_category.id))
            with self.assertRaises(RaceRouteNotFoundError):
                clear_route_gpx(session, self.race_id, 999999)
        finally:
            session.close()

    def test_category_and_route_administration_operations(self):
        """Rename, order, archive, restore, and reassign durable race setup."""
        session = self.session_factory()
        try:
            route = create_race_route(session, self.race_id, "Junior Course")
            category = create_race_category(
                session,
                self.race_id,
                route.id,
                "Junior",
            )
            rename_race_route(
                session,
                self.race_id,
                route.id,
                "Youth Course",
            )
            rename_race_category(
                session,
                self.race_id,
                category.id,
                "Youth",
            )
            reorder_race_category(session, self.race_id, category.id, 1)
            main_route_id = session.get(Category, self.category_id).route_id
            assign_race_category_route(
                session,
                self.race_id,
                category.id,
                main_route_id,
            )
            set_race_category_archived(
                session,
                self.race_id,
                category.id,
                True,
            )
            session.commit()

            self.assertEqual(route.name, "Youth Course")
            self.assertEqual(category.name_normalized, "youth")
            self.assertEqual(category.display_order, 1)
            self.assertEqual(category.route_id, main_route_id)
            self.assertNotIn(
                "Youth",
                [
                    row.name
                    for row in list_race_category_records(session, self.race_id)
                ],
            )
            all_categories = list_race_category_records(
                session,
                self.race_id,
                include_archived=True,
            )
            self.assertTrue(
                next(row for row in all_categories if row.id == category.id).archived
            )

            set_race_category_archived(
                session,
                self.race_id,
                category.id,
                False,
            )
            session.commit()
            self.assertEqual(
                list_race_category_records(session, self.race_id)[0].name,
                "Youth",
            )

            # A setup-only category and its now-unreferenced former route may
            # be removed, while the category holding RaceRider history may not.
            delete_unused_race_category(session, self.race_id, category.id)
            session.flush()
            delete_unused_race_route(session, self.race_id, route.id)
            with self.assertRaisesRegex(
                RaceRouteValidationError,
                "Archive this category",
            ):
                delete_unused_race_category(
                    session,
                    self.race_id,
                    self.category_id,
                )
            session.rollback()
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


class AutomaticRaceEntryServiceTestCase(RaceDatabaseTestCase):
    """Exercise automatic assignment outcomes and inventory invariants."""

    def _create_previous_assignment(
        self,
        session,
        rider_id: int,
        device_id: str,
    ) -> RaceRider:
        """Create an older race assignment used as device history."""
        previous_race = Race(name="Previous Race", status="completed")
        session.add(previous_race)
        session.flush()
        previous_route = Route(
            race_id=previous_race.id,
            name="Previous Course",
        )
        session.add(previous_route)
        session.flush()
        previous_category = Category(
            route_id=previous_route.id,
            race_id=previous_race.id,
            name="Previous Category",
            name_normalized="previous category",
            display_order=1,
            archived=False,
        )
        session.add(previous_category)
        session.flush()
        previous_entry = RaceRider(
            race_id=previous_race.id,
            rider_id=rider_id,
            device_id=device_id,
            category_id=previous_category.id,
            active=False,
            recording=False,
        )
        session.add(previous_entry)
        session.flush()
        return previous_entry

    def test_entry_form_parsing_and_previous_available_reuse(self):
        """Parse answers and prefer an active returned previous device."""
        form, errors = normalize_race_entry_form(
            {
                "category_id": str(self.category_id),
                "has_device": "no",
                # Device ids are never accepted from the entry form. A forged
                # value must not override server-resolved device history.
                "device_id": "pi001",
            }
        )
        self.assertEqual(errors, [])
        self.assertFalse(form["has_device"])
        self.assertNotIn("rider_id", form)
        self.assertNotIn("device_id", form)

        session = self.session_factory()
        try:
            self._create_previous_assignment(
                session,
                self.rider_two_id,
                "pi002",
            )
            session.commit()
            self.assertEqual(
                get_rider_previous_device_id(session, self.rider_two_id),
                "pi002",
            )
            device = session.get(Device, "pi002")
            original_returned = device.returned
            result = assign_device_and_create_entry(
                session,
                self.race_id,
                self.rider_two_id,
                self.category_id,
                has_device=False,
                confirms_previous_device=False,
            )
            session.commit()
            self.assertEqual(result.outcome, "reused_previous")
            self.assertEqual(result.assigned_device_id, "pi002")
            self.assertEqual(result.assigned_category_id, self.category_id)
            self.assertEqual(result.assigned_category_name, "Professional")
            self.assertEqual(device.returned, original_returned)
        finally:
            session.close()

    def test_confirmed_held_device_ignores_returned_without_mutating_it(self):
        """Reuse a confirmed held device marked unreturned and preserve custody."""
        session = self.session_factory()
        try:
            self._create_previous_assignment(
                session,
                self.rider_two_id,
                "pi002",
            )
            device = session.get(Device, "pi002")
            device.returned = False
            session.commit()

            result = assign_device_and_create_entry(
                session,
                self.race_id,
                self.rider_two_id,
                self.category_id,
                has_device=True,
                confirms_previous_device=True,
            )
            session.commit()
            self.assertEqual(result.outcome, "reused_previous")
            self.assertFalse(result.inventory_discrepancy)
            self.assertFalse(device.returned)
        finally:
            session.close()

    def test_confirmed_returned_device_reports_inventory_discrepancy(self):
        """Assign a confirmed device marked in stock and report the mismatch."""
        session = self.session_factory()
        try:
            self._create_previous_assignment(
                session,
                self.rider_two_id,
                "pi002",
            )
            device = session.get(Device, "pi002")
            device.returned = True
            session.commit()

            result = assign_device_and_create_entry(
                session,
                self.race_id,
                self.rider_two_id,
                self.category_id,
                has_device=True,
                confirms_previous_device=True,
            )
            session.commit()
            self.assertTrue(result.inventory_discrepancy)
            self.assertIn("Inventory discrepancy", result.message)
            self.assertTrue(device.returned)
        finally:
            session.close()

    def test_inactive_confirmed_previous_requires_active_returned_replacement(self):
        """Never reuse an inactive held device regardless of returned state."""
        session = self.session_factory()
        try:
            self._create_previous_assignment(
                session,
                self.rider_two_id,
                "pi002",
            )
            previous = session.get(Device, "pi002")
            previous.active = False
            previous.returned = False
            session.add(
                Device(
                    id="pi003",
                    device_info="Replacement",
                    active=True,
                    returned=True,
                )
            )
            session.commit()

            result = assign_device_and_create_entry(
                session,
                self.race_id,
                self.rider_two_id,
                self.category_id,
                has_device=True,
                confirms_previous_device=True,
            )
            session.commit()
            self.assertEqual(result.outcome, "replacement_required")
            self.assertEqual(result.previous_device_id, "pi002")
            self.assertEqual(result.assigned_device_id, "pi003")
            self.assertFalse(previous.active)
            self.assertFalse(previous.returned)
        finally:
            session.close()

    def test_device_already_used_in_race_gets_fallback_or_none_available(self):
        """Replace a prior device already used in-race or create no entry."""
        session = self.session_factory()
        try:
            self._create_previous_assignment(
                session,
                self.rider_two_id,
                "pi001",
            )
            session.commit()
            replacement = assign_device_and_create_entry(
                session,
                self.race_id,
                self.rider_two_id,
                self.category_id,
                has_device=True,
                confirms_previous_device=True,
            )
            session.commit()
            self.assertEqual(replacement.outcome, "replacement_required")
            self.assertEqual(replacement.previous_device_id, "pi001")
            self.assertEqual(replacement.assigned_device_id, "pi002")

            session.delete(replacement.race_rider)
            session.get(Device, "pi002").active = False
            session.commit()
            none_available = assign_device_and_create_entry(
                session,
                self.race_id,
                self.rider_two_id,
                self.category_id,
                has_device=False,
                confirms_previous_device=False,
            )
            self.assertEqual(none_available.outcome, "none_available")
            self.assertIsNone(none_available.race_rider)
            self.assertEqual(
                session.query(RaceRider)
                .filter(
                    RaceRider.race_id == self.race_id,
                    RaceRider.rider_id == self.rider_two_id,
                )
                .count(),
                0,
            )
        finally:
            session.close()

    def test_available_pool_requires_both_active_and_returned(self):
        """Exercise every active/returned combination during assignment."""
        session = self.session_factory()
        try:
            candidate = session.get(Device, "pi002")
            for active, returned, should_assign in (
                (False, False, False),
                (False, True, False),
                (True, False, False),
                (True, True, True),
            ):
                with self.subTest(active=active, returned=returned):
                    candidate.active = active
                    candidate.returned = returned
                    session.commit()
                    result = assign_device_and_create_entry(
                        session,
                        self.race_id,
                        self.rider_two_id,
                        self.category_id,
                        has_device=False,
                        confirms_previous_device=False,
                    )
                    if should_assign:
                        self.assertEqual(result.assigned_device_id, "pi002")
                        self.assertEqual(result.outcome, "assigned_available")
                        self.assertEqual(candidate.returned, returned)
                        self.assertEqual(candidate.active, active)
                        session.delete(result.race_rider)
                        session.commit()
                    else:
                        self.assertEqual(result.outcome, "none_available")
                        self.assertIsNone(result.race_rider)
                        session.rollback()
        finally:
            session.close()

    def test_cross_race_category_tampering_is_rejected_by_service(self):
        """Reject a category id owned by another race before assignment."""
        session = self.session_factory()
        try:
            other_race = Race(name="Tampering Race", status="upcoming")
            session.add(other_race)
            session.flush()
            other_route = Route(race_id=other_race.id, name="Other Course")
            session.add(other_route)
            session.flush()
            other_category = Category(
                route_id=other_route.id,
                race_id=other_race.id,
                name="Other Category",
                name_normalized="other category",
                display_order=1,
                archived=False,
            )
            session.add(other_category)
            session.commit()

            with self.assertRaisesRegex(
                RaceEntryValidationError,
                "active category for this race",
            ):
                assign_device_and_create_entry(
                    session,
                    self.race_id,
                    self.rider_two_id,
                    other_category.id,
                    has_device=False,
                    confirms_previous_device=False,
                )
            self.assertIsNone(
                session.query(RaceRider)
                .filter_by(
                    race_id=self.race_id,
                    rider_id=self.rider_two_id,
                )
                .one_or_none()
            )
        finally:
            session.close()

    def test_duplicate_rider_and_archived_category_are_rejected(self):
        """Reject an existing race rider and categories closed to entry."""
        session = self.session_factory()
        try:
            with self.assertRaisesRegex(
                RaceEntryValidationError,
                "already entered",
            ):
                assign_device_and_create_entry(
                    session,
                    self.race_id,
                    self.rider_one_id,
                    self.category_id,
                    has_device=False,
                    confirms_previous_device=False,
                )
            category = session.get(Category, self.category_id)
            category.archived = True
            session.commit()
            with self.assertRaisesRegex(
                RaceEntryValidationError,
                "active category",
            ):
                assign_device_and_create_entry(
                    session,
                    self.race_id,
                    self.rider_two_id,
                    self.category_id,
                    has_device=False,
                    confirms_previous_device=False,
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
                name_normalized="open",
                display_order=2,
                archived=False,
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
                    name_normalized="professional",
                    display_order=3,
                    archived=False,
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            other_race = Race(name="Other Race", status="upcoming")
            session.add(other_race)
            session.flush()
            existing_category = session.get(Category, self.category_id)
            session.add(
                Category(
                    route_id=existing_category.route_id,
                    race_id=other_race.id,
                    name="Junior",
                    name_normalized="junior",
                    display_order=1,
                    archived=False,
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

            other_race = Race(name="Cross-scope Race", status="upcoming")
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
            "races.enter_race": enter_race,
            "races.enter_race_admin": enter_race_admin,
            "races.add_race_route": add_race_route,
            "races.add_race_category": add_race_category,
            "races.rename_route": rename_route,
            "races.edit_race_category": edit_race_category,
            "races.delete_race_category": delete_race_category,
            "races.delete_route": delete_route,
            "races.manual_times": manual_times,
            "races.confirm_finish_time": confirm_finish_time,
        }.items():
            self.app.view_functions[endpoint] = view.__wrapped__

        self.client = self.app.test_client()
        self.session_patch = patch("src.web.races.SessionLocal", new=self.session_factory)
        self.current_user = SimpleNamespace(
            id=1,
            role="admin",
            rider_id=None,
            is_authenticated=True,
            is_active=True,
        )
        self.user_patch = patch(
            "src.web.races.current_user",
            new=self.current_user,
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
            data={"name": "Controller Race", "status": "upcoming"},
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
        self.assertRegex(category_response.headers["Location"], r"category_id=\d+")
        shared_response = self.client.post(
            f"/races/{created_race_id}/categories/add",
            data={
                "category_name": "Junior Men",
                "route_choice": str(created_route_id),
            },
        )
        self.assertEqual(shared_response.status_code, 302)
        rename_response = self.client.post(
            f"/races/{created_race_id}/routes/{created_route_id}/rename",
            data={"route_name": "Renamed Course"},
        )
        self.assertEqual(rename_response.status_code, 302)
        session = self.session_factory()
        try:
            junior_men = session.query(Category).filter_by(
                race_id=created_race_id,
                name="Junior Men",
            ).one()
            junior_men_id = junior_men.id
        finally:
            session.close()
        edit_category_response = self.client.post(
            f"/races/{created_race_id}/categories/{junior_men_id}/edit",
            data={
                "category_name": "Youth Men",
                "display_order": "1",
                "route_id": str(created_route_id),
                "archived": "on",
            },
        )
        self.assertEqual(edit_category_response.status_code, 302)
        self.assertEqual(
            self.client.post(
                f"/races/{self.race_id}/categories/{self.category_id}/delete"
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                f"/races/{created_race_id}/routes/{created_route_id}/delete"
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                f"/races/{created_race_id}/categories/{junior_men_id}/delete"
            ).status_code,
            302,
        )

        edit_page = self.client.get(
            f"/races/{self.race_id}/edit?category_id={self.category_id}"
        )
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn(
            f'data-category-id="{self.category_id}"'.encode(),
            edit_page.data,
        )
        self.assertIn(b"maps.js?v=20260720-category-id-v1", edit_page.data)
        self.assertIn(b"race-form.js?v=20260720-category-id-v1", edit_page.data)
        self.assertEqual(
            self.client.get(
                f"/races/{self.race_id}/edit?category_id=999999"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                f"/races/{self.race_id}/post?category_id=999999"
            ).status_code,
            400,
        )
        entry_get = self.client.get(
            f"/races/{self.race_id}/entries/new?rider_id={self.rider_two_id}"
            f"&category_id={self.category_id}"
        )
        self.assertEqual(entry_get.status_code, 200)
        self.assertIn(b"Does the rider currently have a device", entry_get.data)
        entry_post = self.client.post(
            f"/races/{self.race_id}/entries/new",
            data={
                "rider_id": str(self.rider_two_id),
                "category_id": str(self.category_id),
                "has_device": "no",
            },
        )
        self.assertEqual(entry_post.status_code, 200)
        self.assertIn(b"Assigned device pi002", entry_post.data)
        self.assertIn(b"Category: <strong>Professional</strong>", entry_post.data)
        self.assertIn(b"Assigned device: <strong>pi002</strong>", entry_post.data)
        geojson_response = self.client.get(
            f"/races/{self.race_id}/route/geojson?category_id={self.category_id}"
        )
        self.assertEqual(geojson_response.status_code, 200)
        self.assertEqual(json.loads(geojson_response.data), {"history": "route"})

        timing_response = self.client.get(
            f"/races/{self.race_id}/race-rider-timings?category_id={self.category_id}"
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

    def test_self_entry_ignores_every_submitted_rider_id(self):
        """Derive self-entry identity only from the authenticated rider account."""
        self.current_user.role = "rider"
        self.current_user.rider_id = self.rider_one_id

        response = self.client.get(
            f"/races/{self.race_id}/enter?rider_id={self.rider_two_id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Alice Rider", response.data)
        self.assertIn(b"Professional", response.data)
        self.assertIn(b"pi001", response.data)

        malicious_post = self.client.post(
            f"/races/{self.race_id}/enter",
            data={
                "rider_id": str(self.rider_two_id),
                "category_id": str(self.category_id),
                "has_device": "no",
            },
        )
        self.assertEqual(malicious_post.status_code, 400)
        session = self.session_factory()
        try:
            self.assertIsNone(
                session.query(RaceRider)
                .filter_by(
                    race_id=self.race_id,
                    rider_id=self.rider_two_id,
                )
                .one_or_none()
            )
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
