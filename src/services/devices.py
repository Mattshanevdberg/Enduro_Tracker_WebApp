"""
Device registry business and persistence operations.

Functions
---------
list_devices
    Return registered devices in stable device-id order.
get_device
    Load one registered device by its immutable id.
device_epc_in_use
    Check whether an RFID EPC belongs to another device.
create_device
    Validate uniqueness and stage a new Device row with availability state.
update_device
    Validate uniqueness and update a Device row's editable/availability fields.

The service coordinates device domain rules with SQLAlchemy state but does not
depend on Flask or render templates. Its caller owns commit and rollback so an
HTTP controller, command, or future API can choose its transaction boundary.
"""

from src.db.models import Device
from src.utils.devices import validate_device_form


class DeviceValidationError(ValueError):
    """Report one or more user-correctable device validation errors."""

    def __init__(self, errors: list[str]):
        """
        Store device validation messages as one service-layer exception.

        Input Args:
          errors: ordered list of validation messages for the caller to display.
        """
        self.errors = errors
        super().__init__(" ".join(errors))


def list_devices(session):
    """
    Return all registered devices in display order.

    Input Args:
      session: active SQLAlchemy session.

    Output:
      List of Device rows ordered by device id.
    """
    return session.query(Device).order_by(Device.id.asc()).all()


def get_device(session, device_id: str) -> Device | None:
    """
    Load a device by its immutable primary key.

    Input Args:
      session: active SQLAlchemy session.
      device_id: tracker device identifier from the route path or another caller.

    Output:
      Matching Device row, or None when it is not registered.
    """
    return session.get(Device, device_id)


def device_epc_in_use(
    session,
    epc_id: str,
    exclude_device_id: str | None = None,
) -> bool:
    """
    Check whether an RFID EPC is already assigned to another device.

    Input Args:
      session: active SQLAlchemy session.
      epc_id: normalized RFID EPC value.
      exclude_device_id: optional device id to ignore while editing that device.

    Output:
      True when another Device row already uses the EPC; otherwise False.
    """
    query = session.query(Device.id).filter(Device.epc_id == epc_id)
    if exclude_device_id is not None:
        query = query.filter(Device.id != exclude_device_id)
    return query.first() is not None


def create_device(session, form: dict) -> Device:
    """
    Validate and stage a new registered device.

    Input Args:
      session: active SQLAlchemy session.
      form: normalized device values from src.utils.devices.normalize_device_form.

    Output:
      Newly staged Device row. The caller must commit the transaction.

    Raises:
      DeviceValidationError when field rules or uniqueness rules fail.
    """
    errors = validate_device_form(form, require_device_id=True)
    device_id = form["id"]
    epc_id = form.get("epc_id")

    if device_id and get_device(session, device_id) is not None:
        errors.append("A device with that ID already exists.")
    if epc_id and device_epc_in_use(session, epc_id):
        errors.append("That RFID EPC is already linked to another device.")
    if errors:
        raise DeviceValidationError(errors)

    device = Device(
        id=device_id,
        device_info=form.get("device_info"),
        epc_id=epc_id,
        returned=bool(form.get("returned")),
        active=bool(form.get("active")),
    )
    session.add(device)
    return device


def update_device(session, device: Device, form: dict) -> Device:
    """
    Validate and stage changes to a device's editable fields.

    Input Args:
      session: active SQLAlchemy session.
      device: existing Device row being edited.
      form: normalized device values from src.utils.devices.normalize_device_form.

    Output:
      Updated Device row. The caller must commit the transaction.

    Raises:
      DeviceValidationError when the EPC length or uniqueness rule fails.

    Notes:
      Device.id is deliberately never changed. Submitted editable values are set
      before a validation exception so the web form can redisplay exactly what
      the administrator attempted to save, matching the previous route behavior.
    """
    errors = validate_device_form(form, require_device_id=False)
    epc_id = form.get("epc_id")
    if epc_id and device_epc_in_use(
        session,
        epc_id,
        exclude_device_id=device.id,
    ):
        errors.append("That RFID EPC is already linked to another device.")

    device.device_info = form.get("device_info")
    device.epc_id = epc_id
    device.returned = bool(form.get("returned"))
    device.active = bool(form.get("active"))

    if errors:
        raise DeviceValidationError(errors)
    return device
