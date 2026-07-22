"""
Automatic race-entry and device-assignment services.

Functions
---------
get_rider_previous_device_id
    Return the device from a rider's most recent RaceRider row.
list_active_race_categories
    Return active categories in configured display order.
load_race_entry_page_data
    Build race, rider, category, and previous-device entry form data.
assign_device_and_create_entry
    Lock a suitable device and create one RaceRider atomically.

The service treats Device.returned as physical inventory evidence. It may read
that flag but never mutates it: only explicit admin inventory actions can
confirm a physical return or handout.
"""

from dataclasses import dataclass

from sqlalchemy import case, exists

from src.db.models import Category, Device, Race, RaceRider, Rider
from src.services.race_riders import create_race_rider
from src.services.race_routes import list_race_category_records
from src.services.riders import list_riders


class RaceEntryValidationError(ValueError):
    """Report a user-correctable race-entry selection or state error."""


@dataclass
class DeviceAssignmentResult:
    """Describe the durable result of one automatic assignment attempt."""

    outcome: str
    race_rider: RaceRider | None
    assigned_device_id: str | None
    previous_device_id: str | None
    assigned_category_id: int | None = None
    assigned_category_name: str | None = None
    inventory_discrepancy: bool = False
    message: str = ""


def get_rider_previous_device_id(session, rider_id: int) -> str | None:
    """
    Return the device from a rider's most recent RaceRider row.

    Input Args:
      session: active SQLAlchemy session.
      rider_id: Rider primary key.

    Output:
      Device id from the highest RaceRider id, or None.

    Notes:
      RaceRider currently has no creation timestamp, so its increasing primary
      key is the established durable proxy for assignment recency.
    """
    latest = (
        session.query(RaceRider.device_id)
        .filter(RaceRider.rider_id == rider_id)
        .order_by(RaceRider.id.desc())
        .first()
    )
    return latest[0] if latest else None


def list_active_race_categories(session, race_id: int) -> list[Category]:
    """
    Return active categories in configured display order.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.

    Output:
      Ordered, non-archived Category list.
    """
    return list_race_category_records(session, race_id, include_archived=False)


def load_race_entry_page_data(
    session,
    race_id: int,
    selected_rider_id: int | None,
    include_rider_selector: bool,
    selected_category_id: int | None = None,
) -> dict:
    """
    Build race-entry form data for a rider or administrator.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      selected_rider_id: resolved rider whose previous device should be shown.
      include_rider_selector: whether the caller may choose any Rider.
      selected_category_id: optional active category selected for the next stage.

    Output:
      Dictionary containing race, riders, categories, selected rider, previous
      device id, and any existing race entry.

    Raises:
      RaceEntryValidationError when the race or selected rider does not exist.
    """
    race = session.get(Race, race_id)
    if race is None:
        raise RaceEntryValidationError("Race not found.")
    if race.status not in {"upcoming", "live"}:
        raise RaceEntryValidationError("This race is not open for entry.")

    selected_rider = None
    if selected_rider_id is not None:
        selected_rider = session.get(Rider, selected_rider_id)
        if selected_rider is None:
            raise RaceEntryValidationError("Rider not found.")
    existing_entry = (
        session.query(RaceRider)
        .filter(
            RaceRider.race_id == race_id,
            RaceRider.rider_id == selected_rider_id,
        )
        .one_or_none()
        if selected_rider_id is not None
        else None
    )
    categories = list_active_race_categories(session, race_id)
    selected_category = None
    if selected_category_id is not None:
        selected_category = next(
            (
                category
                for category in categories
                if category.id == selected_category_id
            ),
            None,
        )
        if selected_category is None:
            raise RaceEntryValidationError(
                "Select an active category for this race."
            )
    return {
        "race": race,
        "riders": list_riders(session) if include_rider_selector else [],
        "categories": categories,
        "selected_category": selected_category,
        "selected_rider": selected_rider,
        "previous_device_id": (
            get_rider_previous_device_id(session, selected_rider_id)
            if selected_rider_id is not None
            else None
        ),
        "existing_entry": existing_entry,
        "include_rider_selector": include_rider_selector,
    }


def _device_is_unused_in_race(session, race_id: int, device_id: str) -> bool:
    """Return whether a device has no RaceRider assignment in the race."""
    return (
        session.query(RaceRider.id)
        .filter(
            RaceRider.race_id == race_id,
            RaceRider.device_id == device_id,
        )
        .first()
        is None
    )


def _lock_previous_device(
    session,
    race_id: int,
    device_id: str | None,
) -> Device | None:
    """Lock and return an active, race-unused previous Device when possible."""
    if not device_id:
        return None
    device = (
        session.query(Device)
        .filter(Device.id == device_id, Device.active.is_(True))
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if device is None or not _device_is_unused_in_race(
        session,
        race_id,
        device.id,
    ):
        return None
    return device


def _lock_available_device(
    session,
    race_id: int,
    preferred_device_id: str | None = None,
) -> Device | None:
    """
    Lock one active, physically returned device unused in the requested race.

    PostgreSQL's FOR UPDATE SKIP LOCKED prevents concurrent entry transactions
    from selecting the same candidate before either RaceRider insert commits.
    """
    assigned_in_race = exists().where(
        RaceRider.race_id == race_id,
        RaceRider.device_id == Device.id,
    )
    preferred_first = case(
        (Device.id == preferred_device_id, 0),
        else_=1,
    )
    return (
        session.query(Device)
        .filter(
            Device.active.is_(True),
            Device.returned.is_(True),
            ~assigned_in_race,
        )
        .order_by(preferred_first.asc(), Device.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )


def _stage_assignment(
    session,
    race_id: int,
    rider_id: int,
    category_id: int,
    device: Device,
) -> RaceRider:
    """Stage and flush one RaceRider while the selected Device row is locked."""
    race_rider = create_race_rider(
        session,
        race_id,
        rider_id,
        device.id,
        category_id,
    )
    session.flush()
    return race_rider


def assign_device_and_create_entry(
    session,
    race_id: int,
    rider_id: int,
    category_id: int,
    has_device: bool,
    confirms_previous_device: bool,
) -> DeviceAssignmentResult:
    """
    Lock a suitable device and create one race entry atomically.

    Input Args:
      session: active SQLAlchemy session and transaction.
      race_id: Race primary key.
      rider_id: Rider primary key.
      category_id: active Category primary key.
      has_device: rider response about current physical possession.
      confirms_previous_device: whether the suggested previous device is theirs.

    Output:
      DeviceAssignmentResult describing assignment, replacement, discrepancy,
      or the no-device outcome. The caller must commit successful results.

    Raises:
      RaceEntryValidationError for missing rows, cross-race/archived category,
      or a duplicate rider entry in the requested race.

    Inventory rule:
      This function never changes Device.returned. A confirmed previous device
      may be reused while returned=False. If it is unexpectedly returned=True,
      assignment proceeds and inventory_discrepancy is reported for admin review.
    """
    race = session.get(Race, race_id)
    rider = session.get(Rider, rider_id)
    category = (
        session.query(Category)
        .filter(
            Category.id == category_id,
            Category.race_id == race_id,
            Category.archived.is_(False),
        )
        .one_or_none()
    )
    if race is None:
        raise RaceEntryValidationError("Race not found.")
    if race.status not in {"upcoming", "live"}:
        raise RaceEntryValidationError("This race is not open for entry.")
    if rider is None:
        raise RaceEntryValidationError("Rider not found.")
    if category is None:
        raise RaceEntryValidationError("Select an active category for this race.")
    existing_entry = (
        session.query(RaceRider.id)
        .filter(
            RaceRider.race_id == race_id,
            RaceRider.rider_id == rider_id,
        )
        .with_for_update()
        .first()
    )
    if existing_entry is not None:
        raise RaceEntryValidationError("The rider is already entered in this race.")

    previous_device_id = get_rider_previous_device_id(session, rider_id)

    if has_device and confirms_previous_device:
        previous_device = _lock_previous_device(
            session,
            race_id,
            previous_device_id,
        )
        if previous_device is not None:
            discrepancy = bool(previous_device.returned)
            race_rider = _stage_assignment(
                session,
                race_id,
                rider_id,
                category_id,
                previous_device,
            )
            message = f"Assigned confirmed device {previous_device.id}."
            if discrepancy:
                message += (
                    " Inventory discrepancy: the rider reports possession, "
                    "but the device is marked returned. An admin must review it."
                )
            return DeviceAssignmentResult(
                outcome="reused_previous",
                race_rider=race_rider,
                assigned_device_id=previous_device.id,
                previous_device_id=previous_device_id,
                assigned_category_id=category.id,
                assigned_category_name=category.name,
                inventory_discrepancy=discrepancy,
                message=message,
            )

    if not has_device:
        available_device = _lock_available_device(
            session,
            race_id,
            preferred_device_id=previous_device_id,
        )
        if available_device is not None:
            race_rider = _stage_assignment(
                session,
                race_id,
                rider_id,
                category_id,
                available_device,
            )
            reused_previous = available_device.id == previous_device_id
            return DeviceAssignmentResult(
                outcome=(
                    "reused_previous" if reused_previous else "assigned_available"
                ),
                race_rider=race_rider,
                assigned_device_id=available_device.id,
                previous_device_id=previous_device_id,
                assigned_category_id=category.id,
                assigned_category_name=category.name,
                message=f"Assigned device {available_device.id}.",
            )
    else:
        replacement = _lock_available_device(session, race_id)
        if replacement is not None:
            race_rider = _stage_assignment(
                session,
                race_id,
                rider_id,
                category_id,
                replacement,
            )
            return DeviceAssignmentResult(
                outcome="replacement_required",
                race_rider=race_rider,
                assigned_device_id=replacement.id,
                previous_device_id=previous_device_id,
                assigned_category_id=category.id,
                assigned_category_name=category.name,
                message=(
                    "Return the device currently held or contact the organiser. "
                    f"Replacement device {replacement.id} has been assigned."
                ),
            )

    return DeviceAssignmentResult(
        outcome="none_available",
        race_rider=None,
        assigned_device_id=None,
        previous_device_id=previous_device_id,
        message="No active, returned device is available. No race entry was created.",
    )
