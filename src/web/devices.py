"""
Admin-only HTTP controllers for device registry management.

Routes
------
GET/POST /devices/
    Render the device registry or create a device from submitted form values.
GET/POST /devices/<device_id>/edit
    Render one device or update its editable description and RFID EPC.

Notes
-----
* We keep 'id' immutable after creation to avoid breaking references.
* Pure normalization and field validation live in src.utils.devices.
* Device queries, uniqueness rules, and mutations live in src.services.devices.
* Device management is admin-only because devices control tracker assignment and
  RFID timing links.
"""

from flask import Blueprint, request, render_template
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from src.auth.decorators import admin_required
from src.db.models import SessionLocal
from src.services.devices import (
    DeviceValidationError,
    create_device,
    get_device,
    list_devices,
    update_device,
)
from src.utils.devices import device_form_template_values, normalize_device_form

bp_devices = Blueprint("devices", __name__, url_prefix="/devices")


@bp_devices.route("/", methods=["GET", "POST"])
@admin_required
def devices_index():
    """
    Render the registry on GET and create a device on POST.

    Input Args:
      None. POST values are read from Flask request.form.

    Output:
      Rendered devices.html response, with HTTP 400 for invalid submitted data
      and HTTP 500 for an unexpected database error.
    """
    session = SessionLocal()
    message = None
    is_ok = None

    try:
        if request.method == "POST":
            form = normalize_device_form(
                request.form.get("id"),
                request.form.get("device_info"),
                request.form.get("epc_id"),
            )
            try:
                device = create_device(session, form)
                session.commit()
            except DeviceValidationError as error:
                return render_template(
                    "devices.html",
                    devices=list_devices(session),
                    message=str(error),
                    success=False,
                    form=device_form_template_values(form),
                ), 400

            message = f"✅ Device '{device.id}' created."
            is_ok = True

        # GET and successful POST requests share the same current registry view.
        return render_template(
            "devices.html",
            devices=list_devices(session),
            message=message,
            success=is_ok,
            form={"id": "", "device_info": "", "epc_id": ""},
        )
    except IntegrityError:
        session.rollback()
        return render_template(
            "devices.html",
            devices=list_devices(session),
            message="A device with that ID or RFID EPC already exists.",
            success=False,
            form={"id": "", "device_info": "", "epc_id": ""},
        ), 400
    except SQLAlchemyError as error:
        session.rollback()
        return render_template(
            "devices.html",
            devices=list_devices(session),
            message=f"DB error: {error}",
            success=False,
            form={"id": "", "device_info": "", "epc_id": ""},
        ), 500
    finally:
        session.close()


@bp_devices.route("/<device_id>/edit", methods=["GET", "POST"])
@admin_required
def device_edit(device_id: str):
    """
    Render or update a device's editable description and RFID EPC.

    Input Args:
      device_id: immutable Device primary key from the URL path.

    Output:
      Rendered device_edit.html response, including 404 when the device does
      not exist, 400 for invalid submitted data, or 500 for a database error.
    """
    session = SessionLocal()
    try:
        device = get_device(session, device_id)
        if not device:
            return render_template(
                "device_edit.html",
                device=None,
                message=f"Device '{device_id}' not found.",
                success=False,
            ), 404

        if request.method == "POST":
            form = normalize_device_form(
                device_id,
                request.form.get("device_info"),
                request.form.get("epc_id"),
            )
            try:
                update_device(session, device, form)
                session.commit()
            except DeviceValidationError as error:
                return render_template(
                    "device_edit.html",
                    device=device,
                    message=str(error),
                    success=False,
                ), 400

            return render_template(
                "device_edit.html",
                device=device,
                message="✅ Saved.",
                success=True,
            )

        # GET edit form
        return render_template(
            "device_edit.html",
            device=device,
            message=None,
            success=None,
        )
    except IntegrityError:
        session.rollback()
        return render_template(
            "device_edit.html",
            device=None,
            message="A device with that RFID EPC already exists.",
            success=False,
        ), 400
    except SQLAlchemyError as error:
        session.rollback()
        return render_template(
            "device_edit.html",
            device=None,
            message=f"DB error: {error}",
            success=False,
        ), 500
    finally:
        session.close()
