# ashyterm/terminal/clipboard_image.py

"""Turn images sitting in the clipboard into a path the terminal can paste.

CLI programs can't read image data from the Wayland clipboard: GNOME's
Mutter refuses to implement ``wlr-data-control``/``ext-data-control``, so
``wl-paste`` and friends fail. AshyTerm *is* a Wayland client, though, so
it can read the image itself, drop it in a cache file and paste that path
instead. Tools like Claude Code then just read the file.
"""

import time
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject

from ..utils.logger import get_logger

_logger = get_logger("ashyterm.terminal.clipboard_image")

# Formats GdkTexture can decode. Order doesn't matter; we only probe.
_IMAGE_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/bmp",
    "image/tiff",
    "image/webp",
    "image/gif",
)

_TEXT_MIME_TYPES = (
    "text/plain;charset=utf-8",
    "text/plain",
    "text/uri-list",
)

# Pasted images are throwaway; forget them after a day.
_MAX_AGE_SECONDS = 24 * 60 * 60


def get_clipboard_image_directory() -> Path:
    """Cache directory holding images pasted from the clipboard."""
    return Path(GLib.get_user_cache_dir()) / "ashyterm" / "clipboard"


def clipboard_has_image(clipboard: Gdk.Clipboard) -> bool:
    """True when the clipboard holds an image and no text alternative.

    Text wins whenever it's offered: copying from a browser often carries
    both, and the user almost always means the text.
    """
    try:
        formats = clipboard.get_formats()
    except Exception as exc:
        _logger.debug(f"Could not inspect clipboard formats: {exc}")
        return False
    if formats is None:
        return False
    if any(formats.contain_mime_type(mime) for mime in _TEXT_MIME_TYPES):
        return False
    if any(formats.contain_mime_type(mime) for mime in _IMAGE_MIME_TYPES):
        return True
    # Content offered inside the process carries a GType instead of a MIME
    # type, e.g. GdkMemoryTexture when another GTK widget did the copy.
    return any(
        GObject.type_is_a(gtype, Gdk.Texture.__gtype__)
        for gtype in (formats.get_gtypes() or ())
    )


def _cleanup_old_images(directory: Path) -> None:
    """Delete cached images older than ``_MAX_AGE_SECONDS``."""
    cutoff = time.time() - _MAX_AGE_SECONDS
    try:
        for entry in directory.glob("clipboard-*.png"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue
    except OSError as exc:
        _logger.debug(f"Could not clean clipboard image cache: {exc}")


def _build_image_path() -> Path:
    directory = get_clipboard_image_directory()
    directory.mkdir(parents=True, exist_ok=True)
    _cleanup_old_images(directory)
    # No spaces or shell metacharacters: the path is pasted verbatim.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"clipboard-{stamp}.png"
    counter = 1
    while path.exists():
        path = directory / f"clipboard-{stamp}-{counter}.png"
        counter += 1
    return path


def save_clipboard_image_async(
    clipboard: Gdk.Clipboard,
    on_done: Callable[[Optional[Path]], None],
) -> None:
    """Read the clipboard image, save it as PNG and report the path.

    ``on_done`` is always called, with ``None`` when nothing usable came
    out of the clipboard.
    """

    def on_texture(clip: Gdk.Clipboard, result) -> None:
        path: Optional[Path] = None
        try:
            texture = clip.read_texture_finish(result)
            if texture is not None:
                path = _build_image_path()
                texture.save_to_png(str(path))
                _logger.info(f"Clipboard image saved to {path}")
        except Exception as exc:
            _logger.error(f"Failed to save clipboard image: {exc}")
            path = None
        on_done(path)

    try:
        clipboard.read_texture_async(None, on_texture)
    except Exception as exc:
        _logger.error(f"Failed to read image from clipboard: {exc}")
        on_done(None)
