"""
Devices management: create and edit Device rows.

- GET/POST /devices/           -> list devices + create form / create new device (custom primary key 'id')
- GET/POST /devices/<id>/edit  -> edit page for device_info / save edits

Notes
-----
* We keep 'id' immutable after creation to avoid breaking references.
* Minimal validation: require non-empty id, length <= 64, and uniqueness.
"""

from flask import Blueprint, request, render_template, redirect, url_for
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from src.db.models import SessionLocal, Device

bp_devices = Blueprint("devices", __name__, url_prefix="/devices")

MAX_ID_LEN = 64

def _list_devices(session):
    return session.query(Device).order_by(Device.id.asc()).all()

@bp_devices.route("/", methods=["GET", "POST"])
def devices_index():
    """
    GET: show create form + devices table
    POST: create device with custom primary key 'id' and optional 'device_info'
    """
    session = SessionLocal()
    message = None
    is_ok = None

    try:
        if request.method == "POST":
            device_id = (request.form.get("id") or "").strip()
            device_info = (request.form.get("device_info") or "").strip() or None

            # Validation
            errs = []
            if not device_id:
                errs.append("Device ID is required.")
            if len(device_id) > MAX_ID_LEN:
                errs.append(f"Device ID must be <= {MAX_ID_LEN} characters.")

            if errs:
                devices = _list_devices(session)
                return render_template(
                    "devices.html",
                    devices=devices,
                    message=" ".join(errs),
                    success=False,
                    form={"id": device_id, "device_info": device_info},
                ), 400

            # Create and commit
            d = Device(id=device_id, device_info=device_info)
            session.add(d)
            session.commit()
            message = f"✅ Device '{device_id}' created."
            is_ok = True

        # GET or after successful POST: show list
        devices = _list_devices(session)
        return render_template(
            "devices.html",
            devices=devices,
            message=message,
            success=is_ok,
            form={"id": "", "device_info": ""},
        )
    except IntegrityError:
        session.rollback()
        devices = _list_devices(session)
        return render_template(
            "devices.html",
            devices=devices,
            message="A device with that ID already exists.",
            success=False,
            form={"id": "", "device_info": ""},
        ), 400
    except SQLAlchemyError as e:
        session.rollback()
        devices = _list_devices(session)
        return render_template(
            "devices.html",
            devices=devices,
            message=f"DB error: {e}",
            success=False,
            form={"id": "", "device_info": ""},
        ), 500
    finally:
        session.close()

@bp_devices.route("/<device_id>/edit", methods=["GET", "POST"])
def device_edit(device_id: str):
    """
    Edit a device's device_info. Primary key 'id' is read-only here.
    """
    session = SessionLocal()
    try:
        dev = session.query(Device).get(device_id)
        if not dev:
            return render_template(
                "device_edit.html",
                device=None,
                message=f"Device '{device_id}' not found.",
                success=False,
            ), 404

        if request.method == "POST":
            device_info = (request.form.get("device_info") or "").strip() or None
            dev.device_info = device_info
            session.commit()
            return render_template(
                "device_edit.html",
                device=dev,
                message="✅ Saved.",
                success=True,
            )

        # GET edit form
        return render_template(
            "device_edit.html",
            device=dev,
            message=None,
            success=None,
        )
    except SQLAlchemyError as e:
        session.rollback()
        return render_template(
            "device_edit.html",
            device=None,
            message=f"DB error: {e}",
            success=False,
        ), 500
    finally:
        session.close()
