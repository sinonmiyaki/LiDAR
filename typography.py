"""Use the bundled Pretendard faces and the native Windows display scale."""

import ctypes
from pathlib import Path


FONT_DIR = Path(__file__).with_name("fonts")
FAMILY = "Pretendard"
SEMIBOLD = "Pretendard SemiBold"
_scale = 1.0


def prepare_desktop():
    user = ctypes.WinDLL("user32")
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    # The packaged manifest sets this first. This call also covers source runs.
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    register = ctypes.WinDLL("gdi32").AddFontResourceExW
    register.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    for face in FONT_DIR.glob("Pretendard-*.ttf"):
        register(str(face), 0x10, None)  # Private to this process.


def configure_scale(root):
    global _scale
    user = ctypes.WinDLL("user32")
    get_dpi = user.GetDpiForWindow
    get_dpi.argtypes = [ctypes.c_void_p]
    get_dpi.restype = ctypes.c_uint
    dpi = get_dpi(root.winfo_id()) or 96
    _scale = dpi / 96
    root.tk.call("tk", "scaling", dpi / 72)


def px(value):
    return round(value * _scale)


def set_titlebar_theme(root, dark):
    user = ctypes.WinDLL("user32")
    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetAncestor.restype = ctypes.c_void_p
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    set_attribute = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
    set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    value = ctypes.c_int(dark)
    set_attribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
