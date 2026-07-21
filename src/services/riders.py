"""
Rider profile business rules and persistence operations.

Functions
---------
list_riders
    Return rider profiles in stable name order.
get_rider
    Load one rider profile by primary key.
rider_account_has_profile
    Check whether a rider account already owns a linked profile.
create_rider
    Validate and stage a new profile, linking rider accounts when required.
update_rider
    Validate and stage changes to an existing profile.

The service reuses shared authorization and UTC helpers, coordinates Rider/User
models, and remains independent of Flask requests, templates, and responses.
"""

from src.auth.decorators import user_has_role
from src.db.models import Rider, User
from src.utils.riders import validate_rider_form
from src.utils.time import utc_now


class RiderValidationError(ValueError):
    """Report one or more user-correctable rider form errors."""

    def __init__(self, errors: list[str]):
        """
        Store rider validation messages as one service-layer exception.

        Input Args:
          errors: ordered list of validation messages for the caller to display.
        """
        self.errors = errors
        super().__init__(" ".join(errors))


class RiderProfileLinkError(PermissionError):
    """Report that a rider account cannot create or link another profile."""


def list_riders(session):
    """
    Return all rider profiles in display order.

    Input Args:
      session: active SQLAlchemy session.

    Output:
      List of Rider rows ordered by rider name.
    """
    return session.query(Rider).order_by(Rider.name.asc()).all()


def get_rider(session, rider_id: int) -> Rider | None:
    """
    Load a rider profile by primary key.

    Input Args:
      session: active SQLAlchemy session.
      rider_id: Rider primary key.

    Output:
      Matching Rider row, or None when it does not exist.
    """
    return session.get(Rider, rider_id)


def rider_account_has_profile(user) -> bool:
    """
    Check whether a rider account already owns a linked profile.

    Input Args:
      user: current User row or compatible authenticated user object.

    Output:
      True only for an active rider account with a non-null rider_id.

    Notes:
      Shared user_has_role logic is reused so role and active-account checks do
      not diverge from the application's route authorization behavior.
    """
    return user_has_role(user, {"rider"}) and getattr(user, "rider_id", None) is not None


def create_rider(
    session,
    form: dict,
    acting_user,
) -> Rider:
    """
    Validate and stage a new rider profile.

    Input Args:
      session: active SQLAlchemy session.
      form: normalized rider values from src.utils.riders.normalize_rider_form.
      acting_user: authenticated rider or admin initiating the operation.

    Output:
      Newly staged Rider row. The caller must commit the transaction.

    Raises:
      RiderValidationError when rider form rules fail.
      RiderProfileLinkError when a rider account is missing or already linked.

    Notes:
      Admin-created profiles remain unlinked. A rider-created profile is linked
      to the durable User row in the same transaction.
    """
    errors = validate_rider_form(form)
    if errors:
        raise RiderValidationError(errors)

    rider = Rider(
        name=form["name"],
        team=form.get("team"),
        bike=form.get("bike"),
        bio=form.get("bio"),
    )
    session.add(rider)
    session.flush()

    if user_has_role(acting_user, {"rider"}):
        user = session.get(User, getattr(acting_user, "id", None))
        if user is None or getattr(user, "rider_id", None) is not None:
            raise RiderProfileLinkError(
                "The rider account is missing or already linked to a profile."
            )
        user.rider_id = rider.id
        user.updated_at = utc_now()

    return rider


def update_rider(
    rider: Rider,
    form: dict,
) -> Rider:
    """
    Validate and stage changes to an existing rider profile.

    Input Args:
      rider: existing Rider row being edited.
      form: normalized rider values from src.utils.riders.normalize_rider_form.

    Output:
      Updated Rider row. The caller must commit the transaction.

    Raises:
      RiderValidationError when rider form rules fail.
    """
    errors = validate_rider_form(form)
    if errors:
        raise RiderValidationError(errors)

    rider.name = form["name"]
    rider.team = form.get("team")
    rider.bike = form.get("bike")
    rider.bio = form.get("bio")
    return rider
