"""Image files saved under the uploads directory, one folder per conversation (Stage 4)."""

import logging
import shutil
from pathlib import Path

from .images import DecodedImage

logger = logging.getLogger(__name__)


def save_image(uploads_dir: Path, conversation_id: str, attachment_id: str, image: DecodedImage) -> str:
    """Write the image and return its path relative to `uploads_dir`."""
    relative = Path(conversation_id) / f"{attachment_id}.{image.extension}"
    target = uploads_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image.data)
    return relative.as_posix()


def resolve(uploads_dir: Path, relative: str) -> Path | None:
    """The absolute path of a stored file, or None if it is missing or points outside `uploads_dir`."""
    root = uploads_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def delete_files(uploads_dir: Path, relatives: list[str]) -> None:
    for relative in relatives:
        path = resolve(uploads_dir, relative)
        if path is not None:
            path.unlink(missing_ok=True)


def delete_conversation_files(uploads_dir: Path, conversation_id: str) -> None:
    folder = (uploads_dir / conversation_id).resolve()
    if folder.is_relative_to(uploads_dir.resolve()) and folder.is_dir():
        try:
            shutil.rmtree(folder)
        except OSError:
            logger.warning("Could not delete the uploads of conversation %s", conversation_id, exc_info=True)
