# ashyterm/utils/wm_frame.py
"""Detect whether the X11 window manager draws a title bar around a window.

GTK4 always draws client-side decorations, but some X11 window managers
frame the window with a title bar of their own anyway (Window Maker with
IgnoreDecorationChanges, or a KWin rule that forces decorations). The window
then shows two sets of close/minimize/maximize buttons. The window manager
announces the frame it draws in the EWMH _NET_FRAME_EXTENTS property
(left, right, top, bottom); a top extent taller than a plain border means a
title bar.

Window managers that honour client-side decorations (Mutter, Muffin, KWin,
xfwm4) report no frame, and Wayland is never inspected.
"""

import ctypes
import ctypes.util
from typing import Optional, Tuple

from gi.repository import Gtk

from .logger import get_logger

logger = get_logger("ashyterm.utils.wm_frame")

FrameExtents = Tuple[int, int, int, int]

# A border is a few pixels; any title bar is taller than this.
TITLEBAR_MIN_HEIGHT = 8
_XA_CARDINAL = 6

_xlib: Optional[ctypes.CDLL] = None
_xlib_failed = False


def has_titlebar(extents: Optional[FrameExtents]) -> bool:
    """True when the top frame extent is taller than a plain border."""
    return extents is not None and extents[2] >= TITLEBAR_MIN_HEIGHT


def wm_draws_titlebar(window: Gtk.Window) -> bool:
    """True when the X11 window manager frames this window with a title bar."""
    xid = _x11_xid(window)
    if not xid:
        return False
    try:
        extents = read_frame_extents(xid, window.get_display().get_name())
    except Exception as e:  # never break the window over a hint
        logger.debug(f"Could not read _NET_FRAME_EXTENTS: {e}")
        return False
    logger.debug(f"_NET_FRAME_EXTENTS={extents}")
    return has_titlebar(extents)


def read_frame_extents(xid: int, display_name: Optional[str]) -> Optional[FrameExtents]:
    """Read _NET_FRAME_EXTENTS of an X11 window, or None if it is not set."""
    x = _load_xlib()
    if x is None:
        return None
    dpy = x.XOpenDisplay(display_name.encode() if display_name else None)
    if not dpy:
        return None
    try:
        atom = x.XInternAtom(dpy, b"_NET_FRAME_EXTENTS", 1)
        return _get_cardinals(x, dpy, xid, atom) if atom else None
    finally:
        x.XCloseDisplay(dpy)


def _x11_xid(window: Gtk.Window) -> Optional[int]:
    if type(window.get_display()).__name__ != "X11Display":
        return None
    surface = window.get_surface()
    if surface is None:
        return None
    try:
        import gi

        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11

        return GdkX11.X11Surface.get_xid(surface)
    except (ImportError, ValueError, TypeError, AttributeError):
        return None


def _get_cardinals(
    x: ctypes.CDLL, dpy: int, xid: int, atom: int
) -> Optional[FrameExtents]:
    actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
    nitems, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
    status = x.XGetWindowProperty(
        dpy,
        xid,
        atom,
        0,
        4,
        0,
        _XA_CARDINAL,
        ctypes.byref(actual_type),
        ctypes.byref(actual_format),
        ctypes.byref(nitems),
        ctypes.byref(after),
        ctypes.byref(data),
    )
    if status != 0 or not data.value:
        return None
    try:
        if actual_format.value != 32 or nitems.value < 4:
            return None
        # format 32 properties are returned as C longs
        values = ctypes.cast(data, ctypes.POINTER(ctypes.c_long))
        return (values[0], values[1], values[2], values[3])
    finally:
        x.XFree(data)


def _load_xlib() -> Optional[ctypes.CDLL]:
    global _xlib, _xlib_failed
    if _xlib is None and not _xlib_failed:
        name = ctypes.util.find_library("X11")
        try:
            _xlib = _declare_signatures(ctypes.cdll.LoadLibrary(name)) if name else None
        except OSError:
            _xlib = None
        _xlib_failed = _xlib is None
    return _xlib


def _declare_signatures(x: ctypes.CDLL) -> ctypes.CDLL:
    c_ulong_p = ctypes.POINTER(ctypes.c_ulong)
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x.XInternAtom.restype = ctypes.c_ulong
    x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x.XGetWindowProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_int,
        ctypes.c_ulong,
        c_ulong_p,
        ctypes.POINTER(ctypes.c_int),
        c_ulong_p,
        c_ulong_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    x.XFree.argtypes = [ctypes.c_void_p]
    return x
