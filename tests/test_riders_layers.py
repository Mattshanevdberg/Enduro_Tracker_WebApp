"""
Focused regression tests for the layered rider profile feature.

The tests cover pure form rules, service-level Rider/User coordination, shared
authorization behavior, and both existing Flask route patterns. All persistence
uses isolated in-memory SQLite tables rather than the configured application DB.
"""

import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from flask import Blueprint, Flask
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Rider, User
from src.services.riders import (
    RiderProfileLinkError,
    RiderValidationError,
    create_rider,
    get_rider,
    get_rider_for_update,
    list_riders,
    rider_account_has_profile,
    update_rider,
)
from src.services.profile_images import (
    delete_profile_image,
    is_profile_image_key,
    store_profile_image,
)
from src.utils.profile_images import ProfileImageValidationError
from src.utils.riders import (
    normalize_rider_form,
    rider_form_values,
    validate_rider_form,
)
from src.web.riders import bp_riders, rider_form


def authenticated_user(**overrides):
    """
    Build a lightweight authenticated user for service/controller tests.

    Input Args:
      overrides: user attributes that should replace the admin defaults.

    Output:
      SimpleNamespace compatible with the shared authorization helpers.
    """
    values = {
        "id": 999,
        "role": "admin",
        "rider_id": None,
        "is_authenticated": True,
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RiderDatabaseTestCase(unittest.TestCase):
    """Provide isolated Rider and User tables for rider feature tests."""

    def setUp(self):
        """Create an in-memory database using the application model tables."""
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Rider.__table__.create(bind=self.engine)
        User.__table__.create(bind=self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

    def tearDown(self):
        """Dispose the isolated database after each test."""
        self.engine.dispose()


class RiderLayerTestCase(RiderDatabaseTestCase):
    """Exercise rider utilities and services independently of Flask routes."""

    def test_normalization_form_values_and_validation(self):
        """Normalize rider values and enforce required profile rules."""
        form = normalize_rider_form(
            "  Alex Rider  ",
            "  Enduro Team ",
            " ",
            "  Test bio  ",
        )
        self.assertEqual(
            form,
            {
                "name": "Alex Rider",
                "team": "Enduro Team",
                "bike": None,
                "bio": "Test bio",
            },
        )
        self.assertEqual(rider_form_values(form)["bike"], "")
        self.assertEqual(validate_rider_form(form), [])

        invalid_form = normalize_rider_form(" ", None, None, None)
        self.assertEqual(
            validate_rider_form(invalid_form),
            ["Name is required."],
        )

    def test_profile_image_validation_storage_and_deletion(self):
        """Normalize safe images and reject content disguised by an extension."""
        source = BytesIO()
        Image.new("RGB", (1800, 900), color=(30, 90, 60)).save(source, "PNG")
        source.seek(0)

        with TemporaryDirectory() as upload_directory:
            key = store_profile_image(
                source,
                "portrait.png",
                upload_directory,
                5 * 1024 * 1024,
                rider_id=42,
            )
            self.assertTrue(is_profile_image_key(key, rider_id=42))
            stored_path = Path(upload_directory) / key
            self.assertTrue(stored_path.is_file())
            with Image.open(stored_path) as stored_image:
                self.assertEqual(stored_image.format, "WEBP")
                self.assertLessEqual(max(stored_image.size), 1200)
                self.assertNotIn("exif", stored_image.info)
            self.assertTrue(delete_profile_image(upload_directory, key, rider_id=42))
            self.assertFalse(stored_path.exists())

        with self.assertRaisesRegex(ProfileImageValidationError, "not a valid"):
            store_profile_image(
                BytesIO(b"not an image"),
                "portrait.png",
                "/tmp/unused-profile-images",
                5 * 1024 * 1024,
                rider_id=42,
            )
        with self.assertRaisesRegex(ProfileImageValidationError, "1 MB or smaller"):
            store_profile_image(
                BytesIO(b"x" * (1024 * 1024 + 1)),
                "oversized.png",
                "/tmp/unused-profile-images",
                1024 * 1024,
                rider_id=42,
            )

    def test_create_update_list_and_rider_account_link_rules(self):
        """Coordinate Rider writes and enforce one profile per rider account."""
        session = self.session_factory()
        try:
            user = User(
                first_name="Riley",
                last_name="Rider",
                username="riley",
                username_normalized="riley",
                email="riley@example.com",
                email_normalized="riley@example.com",
                password_hash="test-hash",
                role="rider",
                is_active=True,
                auth_version=1,
            )
            session.add(user)
            session.commit()

            rider = create_rider(
                session,
                normalize_rider_form(
                    "Riley Rider",
                    None,
                    "Bike",
                    None,
                ),
                user,
            )
            session.commit()
            self.assertEqual(user.rider_id, rider.id)
            self.assertIsNone(rider.profile_image_filename)
            self.assertTrue(rider_account_has_profile(user))

            admin_rider = create_rider(
                session,
                normalize_rider_form("Admin Entry", None, None, None),
                authenticated_user(),
            )
            session.commit()
            self.assertEqual(
                [row.name for row in list_riders(session)],
                ["Admin Entry", "Riley Rider"],
            )

            update_rider(
                admin_rider,
                normalize_rider_form("Updated Entry", "Team", None, None),
            )
            session.commit()
            self.assertEqual(get_rider(session, admin_rider.id).team, "Team")
            self.assertEqual(
                get_rider_for_update(session, admin_rider.id).id,
                admin_rider.id,
            )

            with self.assertRaises(RiderProfileLinkError):
                create_rider(
                    session,
                    normalize_rider_form("Second Profile", None, None, None),
                    user,
                )
            session.rollback()

            with self.assertRaisesRegex(RiderValidationError, "Name is required"):
                update_rider(
                    admin_rider,
                    normalize_rider_form("", None, None, None),
                )
        finally:
            session.close()


class RiderRouteTestCase(RiderDatabaseTestCase):
    """Verify the existing rider URLs and controller outcomes."""

    def setUp(self):
        """Register the rider controller against an isolated database."""
        super().setUp()
        repository_root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(repository_root / "templates"))
        self.upload_directory = TemporaryDirectory()
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="rider-layer-test",
            PROFILE_IMAGE_UPLOAD_DIR=self.upload_directory.name,
            PROFILE_IMAGE_MAX_BYTES=5 * 1024 * 1024,
        )
        self.app.jinja_env.globals["csrf_token"] = lambda: "test-csrf-token"

        # The rider template links to the admin dashboard, so the isolated app
        # provides only that endpoint rather than importing unrelated routes.
        home_blueprint = Blueprint("home", __name__)
        home_blueprint.add_url_rule(
            "/dashboard-admin",
            endpoint="dashboard_admin",
            view_func=lambda: "Admin dashboard",
        )
        home_blueprint.add_url_rule(
            "/dashboard",
            endpoint="dashboard",
            view_func=lambda: "Public dashboard",
        )
        profile_blueprint = Blueprint("rider_profiles", __name__)
        profile_blueprint.add_url_rule(
            "/rider/<int:rider_id>",
            endpoint="rider_profile",
            view_func=lambda rider_id: f"Rider {rider_id}",
        )
        profile_blueprint.add_url_rule(
            "/rider/<int:rider_id>/profile-image",
            endpoint="rider_profile_image",
            view_func=lambda rider_id: f"Rider image {rider_id}",
        )
        self.app.register_blueprint(home_blueprint)
        self.app.register_blueprint(profile_blueprint)
        self.app.register_blueprint(bp_riders)

        # Authorization decorators have separate shared behavior. Unwrapping the
        # route here lets these tests focus on request parsing and orchestration;
        # the patched current user is still checked by rider ownership helpers.
        self.app.view_functions["riders.rider_form"] = rider_form.__wrapped__
        self.client = self.app.test_client()
        self.session_patch = patch("src.web.riders.SessionLocal", new=self.session_factory)
        self.user_patch = patch(
            "src.web.riders.current_user",
            new=authenticated_user(),
        )
        self.session_patch.start()
        self.user_patch.start()

    def tearDown(self):
        """Stop controller patches and dispose the isolated database."""
        self.user_patch.stop()
        self.session_patch.stop()
        self.upload_directory.cleanup()
        super().tearDown()

    def test_create_edit_validation_and_missing_rider_routes(self):
        """Preserve rider GET/POST rendering, persistence, and response codes."""
        self.assertEqual(self.client.get("/riders/new").status_code, 200)

        create_response = self.client.post(
            "/riders/new",
            data={
                "name": "Route Rider",
                "team": "Route Team",
                "bike": "Route Bike",
                "bio": "Route Bio",
                "profile_image": (
                    self._profile_image_bytes((80, 120, 160)),
                    "route-rider.png",
                ),
            },
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertIn(b"Saved rider: Route Rider.", create_response.data)

        verification_session = self.session_factory()
        try:
            rider = verification_session.query(Rider).filter(Rider.name == "Route Rider").one()
            rider_id = rider.id
            original_profile_key = rider.profile_image_filename
            self.assertTrue(is_profile_image_key(original_profile_key, rider_id=rider_id))
            self.assertTrue(
                (Path(self.upload_directory.name) / original_profile_key).is_file()
            )
        finally:
            verification_session.close()

        self.assertEqual(self.client.get(f"/riders/{rider_id}/edit").status_code, 200)
        edit_response = self.client.post(
            f"/riders/{rider_id}/edit",
            data={
                "rider_id": str(rider_id),
                "name": "Updated Route Rider",
                "team": "Updated Team",
                "bike": "",
                "bio": "",
                "profile_image": (
                    self._profile_image_bytes((160, 80, 120)),
                    "replacement.webp",
                ),
            },
        )
        self.assertEqual(edit_response.status_code, 200)
        verification_session = self.session_factory()
        try:
            updated = verification_session.get(Rider, rider_id)
            replacement_key = updated.profile_image_filename
            self.assertNotEqual(replacement_key, original_profile_key)
            self.assertFalse(
                (Path(self.upload_directory.name) / original_profile_key).exists()
            )
            self.assertTrue((Path(self.upload_directory.name) / replacement_key).is_file())
        finally:
            verification_session.close()

        remove_response = self.client.post(
            f"/riders/{rider_id}/edit",
            data={
                "rider_id": str(rider_id),
                "name": "Updated Route Rider",
                "team": "Updated Team",
                "bike": "",
                "bio": "",
                "remove_profile_image": "1",
            },
        )
        self.assertEqual(remove_response.status_code, 200)
        verification_session = self.session_factory()
        try:
            self.assertIsNone(
                verification_session.get(Rider, rider_id).profile_image_filename
            )
            self.assertFalse((Path(self.upload_directory.name) / replacement_key).exists())
        finally:
            verification_session.close()

        invalid_image_response = self.client.post(
            f"/riders/{rider_id}/edit",
            data={
                "rider_id": str(rider_id),
                "name": "Updated Route Rider",
                "profile_image": (BytesIO(b"invalid"), "not-really.png"),
            },
        )
        self.assertEqual(invalid_image_response.status_code, 400)
        self.assertIn(b"not a valid JPEG, PNG, or WebP image", invalid_image_response.data)

        oversized_response = self.client.post(
            f"/riders/{rider_id}/edit",
            data={"rider_id": str(rider_id), "name": "Updated Route Rider"},
            environ_overrides={"CONTENT_LENGTH": str(6 * 1024 * 1024)},
        )
        self.assertEqual(oversized_response.status_code, 413)
        oversized_response.close()

        invalid_response = self.client.post(
            "/riders/new",
            data={"name": ""},
        )
        self.assertEqual(invalid_response.status_code, 400)
        self.assertIn(b"Name is required", invalid_response.data)
        self.assertEqual(self.client.get("/riders/9999/edit").status_code, 404)

    @staticmethod
    def _profile_image_bytes(colour) -> BytesIO:
        """
        Build one in-memory PNG upload for controller tests.

        Input Args:
          colour: Pillow-compatible RGB tuple.

        Output:
          Rewound BytesIO containing a small valid PNG image.
        """
        output = BytesIO()
        Image.new("RGB", (80, 60), color=colour).save(output, "PNG")
        output.seek(0)
        return output

    def test_rider_owner_redirect_and_access_rules(self):
        """Keep one-profile redirects and rider ownership enforcement intact."""
        session = self.session_factory()
        try:
            owned = Rider(name="Owned Rider")
            other = Rider(name="Other Rider")
            session.add_all([owned, other])
            session.commit()
            owned_id = owned.id
            other_id = other.id
        finally:
            session.close()

        rider_user = authenticated_user(role="rider", rider_id=owned_id)
        with patch("src.web.riders.current_user", new=rider_user):
            redirect_response = self.client.get("/riders/new")
            self.assertEqual(redirect_response.status_code, 302)
            self.assertTrue(redirect_response.headers["Location"].endswith(f"/riders/{owned_id}/edit"))
            self.assertEqual(self.client.get(f"/riders/{owned_id}/edit").status_code, 200)
            self.assertEqual(self.client.get(f"/riders/{other_id}/edit").status_code, 403)


if __name__ == "__main__":
    unittest.main()
