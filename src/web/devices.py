"""
Devices management: create and edit Device rows.

- GET/POST /devices/           -> list devices + create form / create new device (custom primary key 'id')
- GET/POST /devices/<id>/edit  -> edit page for device_info / epc_id / save edits

Notes
-----
* We keep 'id' immutable after creation to avoid breaking references.
* Minimal validation: require non-empty id, length <= 64, unique id, and unique optional RFID EPC.
"""

from flask import Blueprint, request, render_template
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from src.db.models import SessionLocal, Device

bp_devices = Blueprint("devices", __name__, url_prefix="/devices")

MAX_ID_LEN = 64
MAX_EPC_LEN = 128

def _list_devices(session):
    """
    Return all devices for the management table.

    Input Args:
      session: active SQLAlchemy session.

    Output:
      List of Device rows ordered by device id.
    """
    return session.query(Device).order_by(Device.id.asc()).all()


def _epc_in_use(session, epc_id: str, exclude_device_id: str | None = None) -> bool:
    """
    Check whether an RFID EPC is already assigned to another device.

    Input Args:
      session: active SQLAlchemy session.
      epc_id: submitted RFID EPC value.
      exclude_device_id: optional device id to ignore during edits.

    Output:
      True when another device already uses the EPC, otherwise False.
    """
    query = session.query(Device.id).filter(Device.epc_id == epc_id)
    if exclude_device_id is not None:
        query = query.filter(Device.id != exclude_device_id)
    return query.first() is not None

@bp_devices.route("/", methods=["GET", "POST"])
def devices_index():
    """
    GET: show create form + devices table
    POST: create device with custom primary key 'id', optional 'device_info', and optional 'epc_id'
    """
    session = SessionLocal()
    message = None
    is_ok = None

    try:
        if request.method == "POST":
            device_id = (request.form.get("id") or "").strip()
            device_info = (request.form.get("device_info") or "").strip() or None
            epc_id = (request.form.get("epc_id") or "").strip() or None

            # Validation
            errs = []
            if not device_id:
                errs.append("Device ID is required.")
            if len(device_id) > MAX_ID_LEN:
                errs.append(f"Device ID must be <= {MAX_ID_LEN} characters.")
            if epc_id and len(epc_id) > MAX_EPC_LEN:
                errs.append(f"RFID EPC must be <= {MAX_EPC_LEN} characters.")
            if device_id and session.get(Device, device_id) is not None:
                errs.append("A device with that ID already exists.")
            if epc_id and _epc_in_use(session, epc_id):
                errs.append("That RFID EPC is already linked to another device.")

            if errs:
                devices = _list_devices(session)
                return render_template(
                    "devices.html",
                    devices=devices,
                    message=" ".join(errs),
                    success=False,
                    form={"id": device_id, "device_info": device_info, "epc_id": epc_id or ""},
                ), 400

            # Create and commit
            d = Device(id=device_id, device_info=device_info, epc_id=epc_id)
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
            form={"id": "", "device_info": "", "epc_id": ""},
        )
    except IntegrityError:
        session.rollback()
        devices = _list_devices(session)
        return render_template(
            "devices.html",
            devices=devices,
            message="A device with that ID or RFID EPC already exists.",
            success=False,
            form={"id": "", "device_info": "", "epc_id": ""},
        ), 400
    except SQLAlchemyError as e:
        session.rollback()
        devices = _list_devices(session)
        return render_template(
            "devices.html",
            devices=devices,
            message=f"DB error: {e}",
            success=False,
            form={"id": "", "device_info": "", "epc_id": ""},
        ), 500
    finally:
        session.close()

@bp_devices.route("/<device_id>/edit", methods=["GET", "POST"])
def device_edit(device_id: str):
    """
    Edit a device's device_info and RFID EPC. Primary key 'id' is read-only here.
    """
    session = SessionLocal()
    try:
        dev = session.get(Device, device_id)
        if not dev:
            return render_template(
                "device_edit.html",
                device=None,
                message=f"Device '{device_id}' not found.",
                success=False,
            ), 404

        if request.method == "POST":
            device_info = (request.form.get("device_info") or "").strip() or None
            epc_id = (request.form.get("epc_id") or "").strip() or None

            errs = []
            if epc_id and len(epc_id) > MAX_EPC_LEN:
                errs.append(f"RFID EPC must be <= {MAX_EPC_LEN} characters.")
            if epc_id and _epc_in_use(session, epc_id, exclude_device_id=device_id):
                errs.append("That RFID EPC is already linked to another device.")

            if errs:
                dev.device_info = device_info
                dev.epc_id = epc_id
                return render_template(
                    "device_edit.html",
                    device=dev,
                    message=" ".join(errs),
                    success=False,
                ), 400

            dev.device_info = device_info
            dev.epc_id = epc_id
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
    except IntegrityError:
        session.rollback()
        return render_template(
            "device_edit.html",
            device=None,
            message="A device with that RFID EPC already exists.",
            success=False,
        ), 400
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
