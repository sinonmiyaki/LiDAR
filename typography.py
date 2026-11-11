"""Register bundled fonts and use the native Windows display scale."""

import ctypes
from pathlib import Path
from i18n import get_locale


FONT_DIR = Path(__file__).with_name("fonts")
LOCALIZED_FONTS = {
    "ja": ("LiDARNotoSansJP.ttc", "LiDAR Noto Sans JP"),
    "zh": ("LiDARNotoSansSC.ttc", "LiDAR Noto Sans SC"),
}
_scale = 1.0


def prepare_desktop():
    user = ctypes.WinDLL("user32")
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    # The packaged manifest sets this first. This call also covers source runs.
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    register = ctypes.WinDLL("gdi32").AddFontResourceExW
    register.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    for face in (*FONT_DIR.glob("*.ttf"), *FONT_DIR.glob("*.ttc")):
        register(str(face), 0x10, None)  # Private to this process.


def ui_font(size, weight="normal", *, locale=None):
    family = LOCALIZED_FONTS.get(locale or get_locale(), (None, "Pretendard"))[1]
    if weight == "semibold":
        return family + " SemiBold", size
    return family, size, weight


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
