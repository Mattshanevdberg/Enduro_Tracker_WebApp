"""
PostgreSQL-only integration tests for automatic race-entry concurrency.

These tests are deliberately gated by RUN_POSTGRES_INTEGRATION=1. They mutate
and clean up the configured database, so ordinary discovery skips them and the
documented workflow runs them only against a disposable migrated PostgreSQL
stack. They verify behavior SQLite cannot provide: FOR UPDATE SKIP LOCKED and
PostgreSQL enforcement of the composite/per-race constraints.
"""

import os
import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.db.models import Category, Device, Race, RaceRider, Rider, Route
from src.services.race_entry import assign_device_and_create_entry


RUN_POSTGRES_INTEGRATION = os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"


@unittest.skipUnless(
    RUN_POSTGRES_INTEGRATION,
    "Set RUN_POSTGRES_INTEGRATION=1 only for a disposable PostgreSQL database.",
)
class PostgreSQLRaceEntryIntegrationTestCase(unittest.TestCase):
    """Verify PostgreSQL row locking and race-scope constraints."""

    @classmethod
    def setUpClass(cls):
        """Bind sessions to the explicitly supplied disposable PostgreSQL URL."""
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url.startswith("postgresql"):
            raise unittest.SkipTest("A PostgreSQL DATABASE_URL is required.")
        cls.engine = create_engine(database_url, future=True)
        cls.session_factory = sessionmaker(
            bind=cls.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

    @classmethod
    def tearDownClass(cls):
        """Dispose the PostgreSQL connection pool after integration coverage."""
        cls.engine.dispose()

    def setUp(self):
        """Commit uniquely named fixture rows visible to concurrent sessions."""
        suffix = uuid4().hex[:12]
        session = self.session_factory()
        try:
            self.race = Race(name=f"PG Entry Race {suffix}", status="upcoming")
            self.other_race = Race(name=f"PG Other Race {suffix}", status="upcoming")
            self.rider_one = Rider(name=f"PG Rider One {suffix}")
            self.rider_two = Rider(name=f"PG Rider Two {suffix}")
            self.device_one = Device(
                id=f"pg-lock-{suffix}",
                returned=True,
                active=True,
            )
            self.device_two = Device(
                id=f"pg-other-{suffix}",
                returned=True,
                active=False,
            )
            session.add_all(
                [
                    self.race,
                    self.other_race,
                    self.rider_one,
                    self.rider_two,
                    self.device_one,
                    self.device_two,
                ]
            )
            session.flush()
            self.route = Route(race_id=self.race.id, name="PG Main Course")
            self.other_route = Route(
                race_id=self.other_race.id,
                name="PG Other Course",
            )
            session.add_all([self.route, self.other_route])
            session.flush()
            self.category = Category(
                race_id=self.race.id,
                route_id=self.route.id,
                name="PG Main Category",
                name_normalized="pg main category",
                display_order=1,
                archived=False,
            )
            self.other_category = Category(
                race_id=self.other_race.id,
                route_id=self.other_route.id,
                name="PG Other Category",
                name_normalized="pg other category",
                display_order=1,
                archived=False,
            )
            session.add_all([self.category, self.other_category])
            session.commit()
            for row in (
                self.race,
                self.other_race,
                self.rider_one,
                self.rider_two,
                self.device_one,
                self.device_two,
                self.route,
                self.other_route,
                self.category,
                self.other_category,
            ):
                session.refresh(row)
            self.race_id = self.race.id
            self.other_race_id = self.other_race.id
            self.rider_one_id = self.rider_one.id
            self.rider_two_id = self.rider_two.id
            self.device_one_id = self.device_one.id
            self.device_two_id = self.device_two.id
            self.route_id = self.route.id
            self.other_route_id = self.other_route.id
            self.category_id = self.category.id
            self.other_category_id = self.other_category.id
        finally:
            session.close()

    def tearDown(self):
        """Remove committed fixtures in foreign-key dependency order."""
        session = self.session_factory()
        try:
            session.query(RaceRider).filter(
                RaceRider.race_id.in_([self.race_id, self.other_race_id])
            ).delete(synchronize_session=False)
            session.query(Category).filter(
                Category.id.in_([self.category_id, self.other_category_id])
            ).delete(synchronize_session=False)
            session.query(Route).filter(
                Route.id.in_([self.route_id, self.other_route_id])
            ).delete(synchronize_session=False)
            session.query(Rider).filter(
                Rider.id.in_([self.rider_one_id, self.rider_two_id])
            ).delete(synchronize_session=False)
            session.query(Device).filter(
                Device.id.in_([self.device_one_id, self.device_two_id])
            ).delete(synchronize_session=False)
            session.query(Race).filter(
                Race.id.in_([self.race_id, self.other_race_id])
            ).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()

    def test_skip_locked_prevents_concurrent_duplicate_assignment(self):
        """Skip a candidate locked by another uncommitted assignment."""
        first_session = self.session_factory()
        second_session = self.session_factory()
        try:
            first = assign_device_and_create_entry(
                first_session,
                self.race_id,
                self.rider_one_id,
                self.category_id,
                has_device=False,
                confirms_previous_device=False,
            )
            second = assign_device_and_create_entry(
                second_session,
                self.race_id,
                self.rider_two_id,
                self.category_id,
                has_device=False,
                confirms_previous_device=False,
            )
            self.assertEqual(first.assigned_device_id, self.device_one_id)
            self.assertEqual(second.outcome, "none_available")
            self.assertIsNone(second.race_rider)
            self.assertTrue(
                second_session.get(Device, self.device_one_id).returned
            )
        finally:
            first_session.rollback()
            second_session.rollback()
            first_session.close()
            second_session.close()

    def test_postgresql_constraints_reject_duplicates_and_cross_race_category(self):
        """Enforce rider/device uniqueness and composite Category race scope."""
        session = self.session_factory()
        try:
            session.add(
                RaceRider(
                    race_id=self.race_id,
                    rider_id=self.rider_one_id,
                    device_id=self.device_one_id,
                    category_id=self.category_id,
                    active=True,
                    recording=True,
                )
            )
            session.commit()

            session.add(
                RaceRider(
                    race_id=self.race_id,
                    rider_id=self.rider_one_id,
                    device_id=self.device_two_id,
                    category_id=self.category_id,
                    active=True,
                    recording=True,
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            session.add(
                RaceRider(
                    race_id=self.race_id,
                    rider_id=self.rider_two_id,
                    device_id=self.device_one_id,
                    category_id=self.category_id,
                    active=True,
                    recording=True,
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            session.add(
                RaceRider(
                    race_id=self.race_id,
                    rider_id=self.rider_two_id,
                    device_id=self.device_two_id,
                    category_id=self.other_category_id,
                    active=True,
                    recording=True,
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
