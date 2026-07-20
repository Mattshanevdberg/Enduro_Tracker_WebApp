"""
Focused regression tests for the layered device registry feature.

The tests cover pure normalization/validation, service-level database rules,
and the existing Flask route behavior against an isolated in-memory database.
They never connect to or modify the configured development/production database.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Blueprint, Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Device
from src.services.devices import (
    DeviceValidationError,
    create_device,
    get_device,
    list_devices,
    update_device,
)
from src.utils.devices import (
    MAX_DEVICE_EPC_LENGTH,
    MAX_DEVICE_ID_LENGTH,
    device_form_template_values,
    normalize_device_form,
    validate_device_form,
)
from src.web.devices import bp_devices, device_edit, devices_index


class DeviceLayerTestCase(unittest.TestCase):
    """Exercise device utilities and services with isolated SQLAlchemy state."""

    def setUp(self):
        """Create an isolated in-memory devices table and session factory."""
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Device.__table__.create(bind=self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

    def tearDown(self):
        """Dispose the isolated database after each test."""
        self.engine.dispose()

    def test_normalization_template_values_and_field_validation(self):
        """Normalize whitespace and enforce pure id/EPC field constraints."""
        form = normalize_device_form("  pi001  ", "  Test tracker  ", "  EPC-1  ")
        self.assertEqual(
            form,
            {
                "id": "pi001",
                "device_info": "Test tracker",
                "epc_id": "EPC-1",
                "returned": True,
                "active": True,
            },
        )
        self.assertEqual(device_form_template_values(form), form)

        blank_form = normalize_device_form(" ", " ", " ")
        self.assertEqual(
            device_form_template_values(blank_form),
            {
                "id": "",
                "device_info": "",
                "epc_id": "",
                "returned": True,
                "active": True,
            },
        )
        self.assertEqual(validate_device_form(blank_form), ["Device ID is required."])

        invalid_form = normalize_device_form(
            "x" * (MAX_DEVICE_ID_LENGTH + 1),
            None,
            "e" * (MAX_DEVICE_EPC_LENGTH + 1),
        )
        self.assertEqual(len(validate_device_form(invalid_form)), 2)

    def test_create_list_update_and_uniqueness_rules(self):
        """Create and update devices while enforcing id and EPC uniqueness."""
        session = self.session_factory()
        try:
            first_form = normalize_device_form("pi002", "Second", "EPC-2")
            second_form = normalize_device_form("pi001", "First", "EPC-1")
            create_device(session, first_form)
            create_device(session, second_form)
            session.commit()

            self.assertEqual([device.id for device in list_devices(session)], ["pi001", "pi002"])

            with self.assertRaisesRegex(DeviceValidationError, "already exists"):
                create_device(session, normalize_device_form("pi001", None, None))

            with self.assertRaisesRegex(DeviceValidationError, "already linked"):
                create_device(session, normalize_device_form("pi003", None, "EPC-2"))

            first_device = get_device(session, "pi001")
            update_device(
                session,
                first_device,
                normalize_device_form("ignored-new-id", "Updated", "EPC-1"),
            )
            session.commit()
            self.assertEqual(first_device.id, "pi001")
            self.assertEqual(first_device.device_info, "Updated")
            self.assertTrue(first_device.returned)
            self.assertTrue(first_device.active)

            with self.assertRaisesRegex(DeviceValidationError, "already linked"):
                update_device(
                    session,
                    first_device,
                    normalize_device_form("pi001", "Rejected", "EPC-2"),
                )
        finally:
            session.close()


class DeviceRouteTestCase(unittest.TestCase):
    """Verify that the existing device URLs still provide their HTTP behavior."""

    def setUp(self):
        """Register unwrapped route handlers against an isolated database."""
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Device.__table__.create(bind=self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

        repository_root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(repository_root / "templates"))
        self.app.config.update(TESTING=True, SECRET_KEY="device-layer-test")
        self.app.jinja_env.globals["csrf_token"] = lambda: "test-csrf-token"

        # Device templates link back to this endpoint. Register a minimal home
        # blueprint so the isolated controller test mirrors that application
        # dependency without importing unrelated dashboard/database behavior.
        home_blueprint = Blueprint("home", __name__)
        home_blueprint.add_url_rule(
            "/dashboard-admin",
            endpoint="dashboard_admin",
            view_func=lambda: "Admin dashboard",
        )
        self.app.register_blueprint(home_blueprint)
        self.app.register_blueprint(bp_devices)

        # The authorization decorator is tested with the wider auth feature. These
        # focused controller tests unwrap it so they can exercise request parsing,
        # service calls, status codes, and templates without creating auth tables.
        self.app.view_functions["devices.devices_index"] = devices_index.__wrapped__
        self.app.view_functions["devices.device_edit"] = device_edit.__wrapped__
        self.client = self.app.test_client()
        self.session_patch = patch(
            "src.web.devices.SessionLocal",
            new=self.session_factory,
        )
        self.session_patch.start()

    def tearDown(self):
        """Stop controller patching and dispose the isolated database."""
        self.session_patch.stop()
        self.engine.dispose()

    def test_device_create_edit_list_and_not_found_routes(self):
        """Keep device GET/POST routes, responses, and persistence working."""
        self.assertEqual(self.client.get("/devices/").status_code, 200)

        create_response = self.client.post(
            "/devices/",
            data={
                "id": "pi010",
                "device_info": "Route test",
                "epc_id": "EPC-10",
                "returned": "on",
                "active": "on",
            },
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertIn(b"Device &#39;pi010&#39; created", create_response.data)

        duplicate_response = self.client.post(
            "/devices/",
            data={"id": "pi010", "device_info": "Duplicate", "epc_id": ""},
        )
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertIn(b"already exists", duplicate_response.data)

        self.assertEqual(self.client.get("/devices/pi010/edit").status_code, 200)
        edit_response = self.client.post(
            "/devices/pi010/edit",
            data={
                "device_info": "Updated route test",
                "epc_id": "EPC-11",
                "returned": "on",
            },
        )
        self.assertEqual(edit_response.status_code, 200)

        verification_session = self.session_factory()
        try:
            device = verification_session.get(Device, "pi010")
            self.assertEqual(device.device_info, "Updated route test")
            self.assertEqual(device.epc_id, "EPC-11")
            self.assertTrue(device.returned)
            self.assertFalse(device.active)
        finally:
            verification_session.close()

        self.assertEqual(self.client.get("/devices/missing/edit").status_code, 404)


if __name__ == "__main__":
    unittest.main()
