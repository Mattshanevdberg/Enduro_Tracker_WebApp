"""
Pure device form normalization and validation helpers.

Functions
---------
normalize_device_form
    Trim submitted device values and represent blank optional values as None.
device_form_template_values
    Convert normalized values into strings that are safe to redisplay in forms.
validate_device_form
    Apply database-independent device id and RFID EPC length rules.

This module deliberately has no Flask, SQLAlchemy, or template dependencies so
the same input rules can be reused by browser routes, future API endpoints, and
focused unit tests.
"""

MAX_DEVICE_ID_LENGTH = 64
MAX_DEVICE_EPC_LENGTH = 128


def normalize_device_form(
    device_id: str | None,
    device_info: str | None,
    epc_id: str | None,
) -> dict:
    """
    Normalize raw device form values.

    Input Args:
      device_id: raw tracker device identifier.
      device_info: raw optional descriptive text.
      epc_id: raw optional RFID EPC value.

    Output:
      Dictionary with trimmed values and None for blank optional fields.
    """
    return {
        "id": (device_id or "").strip(),
        "device_info": (device_info or "").strip() or None,
        "epc_id": (epc_id or "").strip() or None,
    }


def device_form_template_values(form: dict) -> dict:
    """
    Build string values for the device creation template.

    Input Args:
      form: normalized dictionary returned by normalize_device_form.

    Output:
      Dictionary whose optional values use empty strings instead of None.
    """
    return {
        "id": form.get("id") or "",
        "device_info": form.get("device_info") or "",
        "epc_id": form.get("epc_id") or "",
    }


def validate_device_form(form: dict, require_device_id: bool = True) -> list[str]:
    """
    Validate database-independent device form rules.

    Input Args:
      form: normalized dictionary returned by normalize_device_form.
      require_device_id: when True, require and length-check the device id.

    Output:
      List of validation messages. An empty list means the values are valid.

    Notes:
      Database uniqueness is intentionally checked by src.services.devices,
      because pure utilities must not query durable state.
    """
    errors = []
    device_id = form.get("id") or ""
    epc_id = form.get("epc_id")

    if require_device_id and not device_id:
        errors.append("Device ID is required.")
    if require_device_id and len(device_id) > MAX_DEVICE_ID_LENGTH:
        errors.append(f"Device ID must be <= {MAX_DEVICE_ID_LENGTH} characters.")
    if epc_id and len(epc_id) > MAX_DEVICE_EPC_LENGTH:
        errors.append(f"RFID EPC must be <= {MAX_DEVICE_EPC_LENGTH} characters.")

    return errors
