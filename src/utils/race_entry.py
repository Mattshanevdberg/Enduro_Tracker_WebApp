"""
Pure race-entry form parsing helpers.

Functions
---------
normalize_race_entry_form
    Parse a category identifier and the rider's device answers.

The helper deliberately has no Flask, SQLAlchemy, or template dependencies.
"""


def normalize_race_entry_form(values) -> tuple[dict, list[str]]:
    """
    Parse and validate one automatic race-entry submission.

    Input Args:
      values: mapping-like submitted form values.

    Output:
      Tuple containing normalized values and ordered validation messages.
    """
    errors = []
    normalized = {
        "category_id": None,
        "has_device": None,
        "confirms_previous_device": False,
    }

    try:
        category_id = int(values.get("category_id"))
        if category_id < 1:
            raise ValueError
        normalized["category_id"] = category_id
    except (TypeError, ValueError):
        errors.append("Category selection is required.")

    has_device = (values.get("has_device") or "").strip().lower()
    if has_device not in {"yes", "no"}:
        errors.append("Select whether the rider currently has a device.")
    else:
        normalized["has_device"] = has_device == "yes"

    confirmation = (
        values.get("confirms_previous_device") or ""
    ).strip().lower()
    if normalized["has_device"]:
        if confirmation not in {"yes", "no"}:
            errors.append("Confirm whether the suggested device is correct.")
        else:
            normalized["confirms_previous_device"] = confirmation == "yes"

    return normalized, errors
