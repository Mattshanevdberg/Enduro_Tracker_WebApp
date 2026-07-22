"""
Persistent profile-image storage operations.

Functions
---------
store_profile_image
    Normalize an upload and atomically store it under a generated key.
delete_profile_image
    Remove an obsolete generated image without following arbitrary paths.
is_profile_image_key
    Validate the application-generated storage-key format.

This service coordinates durable filesystem state. Image-content validation is
delegated to src.utils.profile_images, while Flask controllers retain ownership
checks and database transaction boundaries.
"""

import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from src.utils.profile_images import prepare_profile_image


PROFILE_IMAGE_KEY_PATTERN = re.compile(
    r"^rider-(?P<rider_id>[1-9][0-9]*)-(?P<token>[0-9a-f]{32})\.webp$"
)


class ProfileImageStorageError(OSError):
    """Report a server-side persistent profile-image storage failure."""


def is_profile_image_key(value: str | None, rider_id: int | None = None) -> bool:
    """
    Validate an application-generated profile-image storage key.

    Input Args:
      value: candidate filename stored on a Rider row.
      rider_id: optional owner id that the embedded key id must match.

    Output:
      True only for a flat generated WebP key, optionally belonging to the
      requested Rider row.
    """
    match = PROFILE_IMAGE_KEY_PATTERN.fullmatch(value or "")
    if match is None:
        return False
    return rider_id is None or int(match.group("rider_id")) == int(rider_id)


def store_profile_image(
    stream,
    original_filename: str | None,
    upload_directory: str,
    max_bytes: int,
    rider_id: int,
) -> str:
    """
    Normalize and atomically persist one Rider profile image.

    Input Args:
      stream: binary stream from the submitted upload.
      original_filename: untrusted client filename used only by validation.
      upload_directory: configured persistent-volume mount path.
      max_bytes: maximum submitted upload size.
      rider_id: owning Rider primary key embedded in the generated key.

    Output:
      Generated flat WebP key suitable for Rider.profile_image_filename.

    Raises:
      ProfileImageValidationError from the utility layer for invalid uploads.
      ProfileImageStorageError when the normalized file cannot be persisted.

    Notes:
      A same-directory temporary file and os.replace provide an atomic final
      write. The user's original filename is never used as a path component.
    """
    normalized_bytes = prepare_profile_image(stream, original_filename, max_bytes)
    storage_directory = Path(upload_directory)
    key = f"rider-{int(rider_id)}-{uuid4().hex}.webp"
    final_path = storage_directory / key
    temporary_path = None

    try:
        storage_directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".profile-image-",
            suffix=".tmp",
            dir=storage_directory,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(normalized_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, final_path)
        return key
    except OSError as error:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            final_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProfileImageStorageError(
            "The profile picture could not be saved. Please try again."
        ) from error


def delete_profile_image(
    upload_directory: str,
    key: str | None,
    rider_id: int | None = None,
) -> bool:
    """
    Delete one obsolete generated profile image.

    Input Args:
      upload_directory: configured persistent-volume mount path.
      key: stored profile-image key to remove.
      rider_id: optional owner id that the key must match.

    Output:
      True when a file was removed; False for a missing or invalid key.

    Raises:
      ProfileImageStorageError when a valid stored file exists but cannot be
      removed. Invalid legacy/user-controlled paths are never touched.
    """
    if not is_profile_image_key(key, rider_id=rider_id):
        return False
    image_path = Path(upload_directory) / str(key)
    try:
        image_path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ProfileImageStorageError(
            "The previous profile picture could not be removed."
        ) from error
