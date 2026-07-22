"""
Pure profile-image validation and normalization helpers.

Functions
---------
prepare_profile_image
    Validate an uploaded raster image and return metadata-free WebP bytes.

The helpers do not know about Flask requests, database rows, or storage paths.
They treat the supplied stream and filename as untrusted input, bound the read,
verify the decoded image, constrain pixel work, and emit one predictable format.
"""

from io import BytesIO
from pathlib import PurePath
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_PROFILE_IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png", ".webp"}
ALLOWED_PROFILE_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PROFILE_IMAGE_PIXELS = 20_000_000
PROFILE_IMAGE_MAX_DIMENSION = 1200
PROFILE_IMAGE_WEBP_QUALITY = 85


class ProfileImageValidationError(ValueError):
    """Report a user-correctable profile-image upload problem."""


def prepare_profile_image(
    stream,
    original_filename: str | None,
    max_bytes: int,
) -> bytes:
    """
    Validate and normalize one untrusted profile-image upload.

    Input Args:
      stream: binary file-like object supplied by the upload framework.
      original_filename: client-provided name used only for extension screening.
      max_bytes: maximum number of submitted bytes accepted before decoding.

    Output:
      Metadata-free WebP bytes with width and height capped at 1,200 pixels.

    Raises:
      ProfileImageValidationError when the name, size, signature, format,
      dimensions, or decoded image is unsafe or unsupported.

    Security:
      The caller-provided filename is never reused for storage. Both extension
      and decoded format are allowlisted, and decompression-bomb warnings are
      treated as validation failures before full pixel processing.
    """
    filename = (original_filename or "").strip()
    extension = PurePath(filename).suffix.lower()
    if extension not in ALLOWED_PROFILE_IMAGE_EXTENSIONS:
        raise ProfileImageValidationError(
            "Profile pictures must be JPEG, PNG, or WebP files."
        )
    if max_bytes <= 0:
        raise ProfileImageValidationError("Profile image uploads are unavailable.")

    payload = stream.read(max_bytes + 1)
    if not payload:
        raise ProfileImageValidationError("Choose a profile picture to upload.")
    if len(payload) > max_bytes:
        maximum_mb = max_bytes / (1024 * 1024)
        raise ProfileImageValidationError(
            f"Profile pictures must be {maximum_mb:g} MB or smaller."
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as source:
                if source.format not in ALLOWED_PROFILE_IMAGE_FORMATS:
                    raise ProfileImageValidationError(
                        "Profile pictures must be JPEG, PNG, or WebP files."
                    )
                if source.width * source.height > MAX_PROFILE_IMAGE_PIXELS:
                    raise ProfileImageValidationError(
                        "Profile pictures must contain 20 megapixels or fewer."
                    )

                # Loading verifies the compressed pixel data before the image
                # is copied, oriented, resized, and detached from its metadata.
                source.load()
                prepared = ImageOps.exif_transpose(source).copy()

        prepared.thumbnail(
            (PROFILE_IMAGE_MAX_DIMENSION, PROFILE_IMAGE_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        has_alpha = prepared.mode in {"RGBA", "LA"} or (
            prepared.mode == "P" and "transparency" in prepared.info
        )
        normalized = prepared.convert("RGBA" if has_alpha else "RGB")
        output = BytesIO()
        normalized.save(
            output,
            format="WEBP",
            quality=PROFILE_IMAGE_WEBP_QUALITY,
            method=6,
        )
        prepared.close()
        normalized.close()
        return output.getvalue()
    except ProfileImageValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as error:
        raise ProfileImageValidationError(
            "The selected file is not a valid JPEG, PNG, or WebP image."
        ) from error
