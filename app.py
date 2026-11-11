"""LiDAR desktop workspace for SteamVR scanning."""

from collections import deque
import ctypes
from dataclasses import dataclass
from functools import lru_cache
from math import cos, tau
from queue import Empty, SimpleQueue
from pathlib import Path
import re
import subprocess
import sys
from threading import Event, Thread
from time import perf_counter
import tkinter as tk
from tkinter import font as tkfont
import webbrowser

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageGrab, ImageOps, ImageTk

from assistant import AssistantConfig
from codex_bridge import CodexBridge, CodexError, REASONING_EFFORTS, UnsupportedReasoning
from i18n import Message, UI_LANGUAGES, get_locale, set_locale, tr
from vrqr import open_result, watch_sessions, web_url
from languages import LANGUAGES, TARGET_LANGUAGES, load_settings, load_target, save_settings, save_target
from local_model import MODELS_DIR, available, download_model
from speech import (DEFAULT_THRESHOLD, MAX_THRESHOLD, MIN_THRESHOLD, SpeechError, calibrated_threshold,
                    input_devices, measure_microphone)
from steam_startup import MANIFEST, SteamStartup, unregister, watch_runtime
from translation_api import TranslationError, decrypt_key, encrypt_key, translate
from typography import configure_scale, prepare_desktop, px, set_titlebar_theme, ui_font
from tray import TrayIcon, show_existing
from updater import VERSION, download_installer, latest_release


VIOLET, BUTTON_TEXT = "#6600CC", "#ffffff"


@dataclass(frozen=True)
class Palette:
    paper: str
    surface: str
    ink: str
    muted: str
    line: str
    accent: str
    selected: str
    selected_hover: str
    hover: str
    diagram: str
    warning: str
    scrim: str
    titlebar_dark: bool = False


LIGHT = Palette("#f4f4f5", "#ffffff", "#151515", "#626268", "#e8e8ea", VIOLET, "#f0e6fa",
                "#e5d3f7", "#e9e9ec", "#f6f1fa", "#a31934", "#35323c")
DARK = Palette("#141318", "#211f26", "#f7f5fa", "#bdb7c7", "#37333f", "#c89bff", "#38264f",
               "#493065", "#302d37", "#2c2238", "#ff879d", "#0c0a11", titlebar_dark=True)
TRANSLATION_SERVICES = {"luna": "GPT-6 Luna", "deepl": "DeepL", "local": "Local (M2M-100 418M INT8)"}
MODEL_DESCRIPTIONS = {
    "luna": "ChatGPT 계정으로 연결해 OCR 텍스트를 번역합니다.",
    "deepl": "DeepL API 키로 연결해 온라인으로 번역합니다.",
    "local": "모델을 다운로드한 뒤 오프라인으로 번역합니다.",
}
FEATURES = {"qr", "translation", "assistant"}


def palette_for(widget):
    return getattr(widget.winfo_toplevel(), "theme", LIGHT)


def label(parent, text="", *, variable=None, size=10, weight="normal", fg=None, bg=None, wrap=0):
    options = {"textvariable": variable} if variable is not None else {"text": tr(text)}
    font = ui_font(size, weight)
    return tk.Label(parent, **options, font=font, fg=fg or palette_for(parent).ink,
                    bg=bg or parent["bg"], anchor="w", justify="left", wraplength=px(wrap))


class DisplayVar(tk.StringVar):
    def __init__(self, value="", formatter=None):
        super().__init__()
        self.formatter = formatter
        self.set(value)

    def set(self, value):
        self.message = value
        self.relocalize()

    def relocalize(self):
        text = tr(self.message)
        text = self.formatter(text) if self.formatter else text
        if self.get() != text:
            super().set(text)


def blend_color(start, end, progress):
    return "#" + "".join(f"{round((a + (b - a) * progress) / 257):02x}" for a, b in zip(start, end))


@lru_cache(maxsize=16)
def rounded_mask(width, height, radius, inset):
    scale = 3
    mask = Image.new("L", (width * scale, height * scale))
    margin = inset * scale
    bounds = (margin, margin, mask.width - 1 - margin, mask.height - 1 - margin)
    ImageDraw.Draw(mask).rounded_rectangle(bounds, radius=max(0, radius - inset) * scale, fill=255)
    return mask.resize((width, height), Image.Resampling.LANCZOS)


class RoundedShape:
    def __init__(self, canvas, radius, fill, outline=None):
        self.canvas = canvas
        self.radius = radius
        self.fill = fill
        self.outline = outline
        self.item = canvas.create_image(0, 0, anchor="nw")
        self.image = None
        self.geometry = None
        self.last_render = None

    def resize(self, width, height):
        self.geometry = width, height
        self.paint()

    def paint(self):
        if not self.geometry or min(self.geometry) < 3:
            return
        width, height = self.geometry
        render = (width, height, self.fill, self.outline)
        if render == self.last_render:
            return
        self.last_render = render
        radius = min(self.radius, width // 2, height // 2)
        image = Image.new("RGBA", (width, height), self.outline or self.fill)
        image.putalpha(rounded_mask(width, height, radius, 0))
        if self.outline:
            inner = Image.new("RGBA", (width, height), self.fill)
            inner.putalpha(rounded_mask(width, height, radius, 1))
            image = Image.alpha_composite(image, inner)
        if self.image is not None and (self.image.width(), self.image.height()) == self.geometry:
            self.image.paste(image)
        else:
            self.image = ImageTk.PhotoImage(image, master=self.canvas)
            self.canvas.itemconfigure(self.item, image=self.image)


class MotionClock:
    def __init__(self, root):
        self.root = root
        self.active = {}
        self.job = None

    def schedule(self):
        if self.active and self.job is None:
            self.job = self.root.after(15, self.tick)

    def tick(self):
        self.job = None
        now = perf_counter()
        for motion, (started, duration, draw) in tuple(self.active.items()):
            if motion not in self.active:
                continue
            fraction = min((now - started) / duration, 1)
            if fraction == 1:
                self.active.pop(motion)
            draw(fraction * fraction * (3 - 2 * fraction))
        self.schedule()

    def remove(self, motion):
        self.active.pop(motion, None)
        if not self.active and self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None


class Motion:
    def __init__(self, widget):
        root = widget.winfo_toplevel()
        if not hasattr(root, "motion_clock"):
            root.motion_clock = MotionClock(root)
        self.clock = root.motion_clock

    def start(self, duration, draw):
        self.cancel()
        draw(0)
        self.clock.active[self] = perf_counter(), duration, draw
        self.clock.schedule()

    def cancel(self):
        self.clock.remove(self)


class HoverButton(tk.Canvas):
    def __init__(self, parent, text, command, bg, hover, fg, padding=(16, 9), *,
                 anchor="center", textvariable=None, wraplength=0, justify="center", disabledforeground=None):
        super().__init__(parent, bg=parent["bg"], cursor="hand2", bd=0, highlightthickness=0, takefocus=True)
        self.command = command
        self.normal, self.hover = bg, hover
        self.foreground = fg
        self.disabled_foreground = disabledforeground or palette_for(parent).muted
        self.padding = tuple(px(value) for value in padding)
        self.anchor = anchor
        self.variable = textvariable
        self.caption = tr(text)
        self.button_font = ui_font(10, "semibold")
        self.enabled = True
        self.motion = Motion(self)
        self.background = RoundedShape(self, px(9), bg)
        self.title = self.create_text(0, 0, text=text, anchor=anchor, fill=fg, font=self.button_font,
                                      width=wraplength, justify=justify)
        self.trace = textvariable.trace_add("write", lambda *_: self.update_text()) if textvariable is not None else None
        self.bind("<Configure>", self.draw)
        self.bind("<Enter>", lambda _: self.tint(self.hover) if self.enabled else None)
        self.bind("<Leave>", lambda _: self.tint(self.normal))
        self.bind("<FocusIn>", lambda _: self.draw_focus(palette_for(self).accent))
        self.bind("<FocusOut>", lambda _: self.draw_focus(None))
        self.bind("<ButtonRelease-1>", self.click)
        self.bind("<Return>", lambda _: self.invoke())
        self.bind("<space>", lambda _: self.invoke())
        self.update_text()

    def click(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if self.enabled:
            self.command()

    def configure(self, *, text=None, font=None, state=None, **options):
        if text is not None:
            self.caption = tr(text)
        if font is not None:
            self.button_font = font
        if state is not None:
            self.enabled = state != "disabled"
            super().configure(cursor="hand2" if self.enabled else "arrow", takefocus=self.enabled)
            self.itemconfigure(self.title, fill=self.foreground if self.enabled else self.disabled_foreground)
            if not self.enabled:
                self.tint(self.normal)
        if text is not None or font is not None:
            self.update_text()
        if options:
            return super().configure(**options)

    def update_text(self):
        text = self.variable.get() if self.variable is not None else self.caption
        self.itemconfigure(self.title, text=text, font=self.button_font)
        box = self.bbox(self.title)
        height = tkfont.Font(root=self, font=self.button_font).metrics("linespace")
        if box:
            height = max(height, box[3] - box[1])
        super().configure(width=(box[2] - box[0] if box else 1) + 2 * self.padding[0],
                          height=height + 2 * self.padding[1])

    def draw(self, event):
        self.background.resize(event.width, event.height)
        self.coords(self.title, self.padding[0] if self.anchor == "w" else event.width / 2, event.height / 2)

    def draw_focus(self, color):
        self.background.outline = color
        self.background.paint()

    def palette(self, bg, hover, fg):
        self.normal, self.hover = bg, hover
        self.foreground = fg
        self.itemconfigure(self.title, fill=fg if self.enabled else self.disabled_foreground)
        self.tint(bg)

    def tint(self, color):
        if self.background.fill == color:
            self.motion.cancel()
            return
        start = self.winfo_rgb(self.background.fill)
        finish = self.winfo_rgb(color)

        def draw(progress):
            self.background.fill = blend_color(start, finish, progress)
            self.background.paint()

        self.motion.start(.085, draw)

    def destroy(self):
        self.motion.cancel()
        if self.trace is not None:
            self.variable.trace_remove("write", self.trace)
        super().destroy()


class SidebarButton(HoverButton):
    def __init__(self, parent, caption, command, symbol):
        theme = palette_for(parent)
        super().__init__(parent, caption, command, theme.surface, theme.hover, theme.ink,
                         anchor="w", padding=(42, 9))
        if symbol is None:
            self.icon = (
                self.create_oval(0, 0, 0, 0, outline=theme.ink),
                self.create_oval(0, 0, 0, 0, outline=theme.ink),
                self.create_line(0, 0, 0, 0, fill=theme.ink),
            )
        else:
            self.icon = (self.create_text(0, 0, text=symbol, fill=theme.ink, font=("Segoe UI Symbol", 12)),)

    def draw(self, event):
        super().draw(event)
        x, y = px(22), event.height / 2
        if len(self.icon) == 1:
            self.coords(self.icon[0], x, y)
        else:
            radius = px(7)
            self.coords(self.icon[0], x - radius, y - radius, x + radius, y + radius)
            self.coords(self.icon[1], x - px(3), y - radius, x + px(3), y + radius)
            self.coords(self.icon[2], x - radius, y, x + radius, y)

    def palette(self, bg, hover, fg):
        super().palette(bg, hover, fg)
        for item in self.icon:
            self.itemconfigure(item, **{"outline" if self.type(item) == "oval" else "fill": fg})


class ModelChoice(HoverButton):
    def __init__(self, parent, title, description, command, selected, warning=""):
        theme = palette_for(parent)
        super().__init__(parent, title, command, theme.selected if selected else theme.paper,
                         theme.selected_hover if selected else theme.hover, theme.accent if selected else theme.ink,
                         padding=(12, 6), anchor="w", justify="left")
        self.description = self.create_text(0, 0, text=tr(description), anchor="nw", fill=theme.muted, font=ui_font(9))
        self.warning = self.create_text(0, 0, text=tr(warning), anchor="nw", fill=theme.warning, font=ui_font(9, "semibold"))
        self.configure(font=ui_font(11, "semibold"), height=px(136))

    def draw(self, event):
        super().draw(event)
        x = self.padding[0]
        width = max(1, event.width - 2 * x)
        self.coords(self.title, x, px(25))
        self.coords(self.description, x, px(51))
        self.coords(self.warning, x, px(96))
        for item in (self.title, self.description, self.warning):
            self.itemconfigure(item, width=width)


class Card(tk.Canvas):
    def __init__(self, parent, *, height, color=None, border=None, inset=18):
        theme = palette_for(parent)
        color, border = color or theme.surface, border or theme.line
        super().__init__(parent, bg=theme.paper, height=px(height), highlightthickness=0)
        self.inset = px(inset)
        self.background = RoundedShape(self, px(15), color, border)
        self.body = tk.Frame(self, bg=color)
        self.content = self.create_window(self.inset, self.inset, anchor="nw", window=self.body)
        self.bind("<Configure>", self.resize)

    def resize(self, event):
        self.background.resize(event.width, event.height)
        self.itemconfigure(self.content, width=max(1, event.width - 2 * self.inset),
                           height=max(1, event.height - 2 * self.inset))

    def fit_height(self):
        self.body.update_idletasks()
        self.configure(height=max(int(self["height"]), self.body.winfo_reqheight() + 2 * self.inset + 2))


class InAppDialog(tk.Frame):
    """Host modal app UI inside the main window."""

    def __init__(self, root, title, dismiss):
        self.theme = palette_for(root)
        super().__init__(root, bg=self.theme.scrim)
        self.dismiss = dismiss
        self.previous_focus = self.focus_get()
        self.motion = Motion(self)
        self.backdrop = tk.Canvas(self, bg=self.theme.scrim, bd=0, highlightthickness=0)
        self.backdrop.place(relwidth=1, relheight=1)
        self.backdrop.bind("<Button-1>", self.dismiss_outside)
        self.backdrop_item = self.backdrop.create_image(0, 0, anchor="nw")
        self.blurred = None
        self.backdrop_image = None
        self.bind("<Configure>", self.resize_backdrop)
        self.panel = RoundedShape(self.backdrop, px(15), self.theme.surface, self.theme.line)
        self.body = tk.Frame(self.backdrop, bg=self.theme.surface)
        self.content = self.backdrop.create_window(0, 0, anchor="nw", window=self.body)
        self.inset = px(24)
        self.offset = 0
        label(self.body, title, size=17, weight="bold").pack(anchor="w")

    def open(self, first_control):
        root = self.master
        root.update_idletasks()
        try:
            snapshot = ImageGrab.grab(window=root.winfo_id())
            if snapshot.size == (root.winfo_width(), root.winfo_height()) and snapshot.getbbox():
                snapshot.thumbnail((px(420), px(280)), Image.Resampling.BILINEAR)
                blurred = snapshot.filter(ImageFilter.GaussianBlur(3.2))
                tint = Image.new("RGB", blurred.size, self.theme.scrim)
                self.blurred = Image.blend(blurred, tint, .34)
        except (OSError, ValueError):
            pass
        self.panel.resize(self.body.winfo_reqwidth() + 2 * self.inset,
                          self.body.winfo_reqheight() + 2 * self.inset)
        self.place(relwidth=1, relheight=1)
        self.lift()
        self.grab_set()
        first_control.focus_set()
        self.motion.start(.12, lambda progress: self.center_panel(px(6 * (1 - progress))))

    def center_panel(self, offset):
        self.offset = offset
        if not self.panel.geometry:
            return
        width, height = self.panel.geometry
        x = max(0, (self.winfo_width() - width) // 2)
        y = max(0, (self.winfo_height() - height) // 2) + offset
        self.backdrop.coords(self.panel.item, x, y)
        self.backdrop.coords(self.content, x + self.inset, y + self.inset)

    def dismiss_outside(self, event):
        bounds = self.backdrop.bbox(self.panel.item)
        if bounds and not (bounds[0] <= event.x < bounds[2] and bounds[1] <= event.y < bounds[3]):
            self.dismiss()

    def resize_backdrop(self, event):
        if self.blurred is not None:
            image = self.blurred.resize((event.width, event.height), Image.Resampling.BILINEAR)
            self.backdrop_image = ImageTk.PhotoImage(image, master=self)
            self.backdrop.itemconfigure(self.backdrop_item, image=self.backdrop_image)
        self.center_panel(self.offset)

    def cycle_focus(self, reverse):
        current = self.focus_get() or self
        candidate = current
        while True:
            candidate = candidate.tk_focusPrev() if reverse else candidate.tk_focusNext()
            if candidate is None or candidate == current:
                return
            if str(candidate).startswith(f"{self}."):
                candidate.focus_set()
                return

    def destroy(self):
        self.motion.cancel()
        if self.grab_current() == self:
            self.grab_release()
        super().destroy()
        if self.previous_focus is not None and self.previous_focus.winfo_exists():
            self.previous_focus.focus_set()


class FeatureCard(Card):
    def __init__(self, parent, title, value, active, command, settings=None):
        super().__init__(parent, height=122)
        self.theme = palette_for(parent)
        self.header = tk.Frame(self.body, bg=self.theme.surface)
        self.header.pack(fill="x")
        self.title = label(self.header, title, size=10, weight="semibold")
        self.title.pack(side="left")
        self.switch = Switch(self.header, active, command)
        self.switch.pack(side="right")
        self.value = label(self.body, variable=value, size=14, weight="bold", wrap=188)
        self.value.pack(side="left", anchor="nw", pady=(px(9), 0), fill="x", expand=True)
        self.gear = None
        if settings:
            self.gear = HoverButton(self.body, "⚙", settings, self.theme.paper, self.theme.selected,
                                    self.theme.accent, padding=(6, 0))
            self.gear.configure(font=("Segoe UI Symbol", 11))
            self.gear.pack(side="right", anchor="n", pady=(px(8), 0))
        self.set_active(active)
        self.fit_height()

    def set_active(self, active):
        bg = "#09080c" if active else self.theme.surface
        self.background.fill = bg
        self.background.outline = bg if active else self.theme.line
        self.background.paint()
        for widget in (self.body, self.header, self.switch):
            widget.configure(bg=bg)
        foreground = BUTTON_TEXT if active else self.theme.ink
        self.title.configure(bg=bg, fg=foreground)
        self.value.configure(bg=bg, fg=foreground)
        if self.gear:
            self.gear.configure(bg=bg)

    def animate(self, active):
        self.set_active(active)
        self.switch.animate(active)


class Switch(tk.Canvas):
    def __init__(self, parent, active, command, *, enabled=True):
        theme = palette_for(parent)
        super().__init__(parent, width=px(39), height=px(23), bg=parent["bg"], highlightthickness=0, bd=0,
                         cursor="hand2" if enabled and command else "arrow", takefocus=bool(enabled and command))
        self.motion = Motion(self)
        self.colors = self.winfo_rgb("#aaaab1" if enabled and command else "#77727f"), self.winfo_rgb(VIOLET)
        self.track = RoundedShape(self, px(9), "#aaaab1")
        self.coords(self.track.item, px(1), px(3))
        self.track.resize(px(37), px(17))
        self.knob = RoundedShape(self, px(6), BUTTON_TEXT)
        self.knob.resize(px(12), px(12))
        self.level = float(bool(active))
        self.draw_level(self.level)
        if enabled and command:
            for event in ("<ButtonRelease-1>", "<Return>", "<space>"):
                self.bind(event, lambda _: command())
            self.bind("<FocusIn>", lambda _: self.draw_focus(theme.accent))
            self.bind("<FocusOut>", lambda _: self.draw_focus(None))

    def draw_focus(self, color):
        self.track.outline = color
        self.track.paint()

    def draw_level(self, level):
        self.level = level
        self.track.fill = blend_color(*self.colors, level)
        self.track.paint()
        self.coords(self.knob.item, px(4 + level * 19), px(5.5))

    def animate(self, active):
        start = self.level
        self.motion.start(.095, lambda progress: self.draw_level(start + (float(active) - start) * progress))

    def destroy(self):
        self.motion.cancel()
        super().destroy()


class SensitivitySlider(tk.Canvas):
    def __init__(self, parent, value):
        theme = palette_for(parent)
        super().__init__(parent, width=px(244), height=px(36), bg=parent["bg"], highlightthickness=0,
                         bd=0, cursor="hand2", takefocus=True)
        self.value = value
        self.value_text = tk.StringVar()
        self.center_y = px(18)
        self.left = px(12)
        self.right = px(232)
        self.track = self.create_line(self.left, self.center_y, self.right, self.center_y,
                                      width=px(5), capstyle="round", fill=theme.line)
        self.fill = self.create_line(self.left, self.center_y, self.right, self.center_y,
                                     width=px(5), capstyle="round", fill=theme.accent)
        self.thumb = RoundedShape(self, px(9), theme.ink, theme.accent)
        self.thumb.resize(px(18), px(18))
        self.focus_ring = self.create_oval(0, 0, 0, 0, outline=theme.accent, state="hidden")
        self.bind("<Configure>", self.resize)
        self.bind("<Button-1>", self.press)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<MouseWheel>", lambda event: self.adjust(-1 if event.delta > 0 else 1))
        self.bind("<FocusIn>", lambda _: self.itemconfigure(self.focus_ring, state="normal"))
        self.bind("<FocusOut>", lambda _: self.itemconfigure(self.focus_ring, state="hidden"))
        for key, delta in (("Left", 1), ("Down", 1), ("Right", -1), ("Up", -1)):
            self.bind(f"<{key}>", lambda _, step=delta: self.adjust(step))
        self.bind("<Home>", lambda _: self.adjust(MAX_THRESHOLD - self.value))
        self.bind("<End>", lambda _: self.adjust(MIN_THRESHOLD - self.value))
        self.draw()

    def get(self):
        return self.value

    def resize(self, event):
        self.right = max(self.left + 1, event.width - px(12))
        self.coords(self.track, self.left, self.center_y, self.right, self.center_y)
        self.draw()

    def draw(self):
        fraction = (MAX_THRESHOLD - self.value) / (MAX_THRESHOLD - MIN_THRESHOLD)
        x = self.left + (self.right - self.left) * fraction
        self.coords(self.fill, self.left, self.center_y, x, self.center_y)
        self.coords(self.thumb.item, round(x - px(18) / 2), self.center_y - px(18) // 2)
        radius = px(12)
        self.coords(self.focus_ring, x - radius, self.center_y - radius, x + radius, self.center_y + radius)
        self.value_text.set(f"{self.value} dBFS".replace("-", "\u2212"))

    def press(self, event):
        self.focus_set()
        self.drag(event)

    def drag(self, event):
        fraction = max(0., min(1., (event.x - self.left) / (self.right - self.left)))
        self.adjust(MAX_THRESHOLD - round(fraction * (MAX_THRESHOLD - MIN_THRESHOLD)) - self.value)

    def adjust(self, delta):
        value = max(MIN_THRESHOLD, min(MAX_THRESHOLD, self.value + delta))
        if value != self.value:
            self.value = value
            self.draw()
        return "break"


class TranslationLoading(tk.Canvas):
    def __init__(self, parent, theme):
        super().__init__(parent, width=px(56), height=px(28), bg=theme.surface, highlightthickness=0)
        self.create_line(px(14), px(14), px(42), px(14), width=px(27), capstyle="round", fill="#252525")
        self.dots = [self.create_oval(0, 0, 0, 0, fill="#ffffff", outline="") for _ in range(3)]
        self.job = None

    def show(self, before):
        if self.job is None:
            self.pack(anchor="w", before=before)
            self.started = perf_counter()
            self.tick()

    def tick(self):
        elapsed = perf_counter() - self.started
        for index, dot in enumerate(self.dots):
            phase = elapsed / 0.78 - index * 0.16
            y = 17 - 6 * max(0.0, cos(tau * phase))
            x = 16 + index * 12
            self.coords(dot, px(x - 3), px(y - 3), px(x + 3), px(y + 3))
        self.job = self.after(24, self.tick)

    def hide(self):
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None
            self.pack_forget()

    def destroy(self):
        self.hide()
        super().destroy()


class ScannerWindow:
    def __init__(self, root):
        self.root = root
        self.stop = Event()
        self.events = SimpleQueue()
        preferences = load_settings()
        set_locale(preferences.get("ui_language"))
        self.theme = root.theme = DARK if preferences.get("dark_mode") is True else LIGHT
        self.translation_config = self.load_translation_config(preferences)
        saved_features = preferences.get("enabled_features")
        scan_features = FEATURES - {"assistant"}
        self.features = (frozenset(key for key in saved_features if isinstance(key, str) and key in scan_features)
                         if isinstance(saved_features, list) else frozenset(("qr",)))
        if preferences.get("assistant_enabled", True) is True:
            self.features |= {"assistant"}
        self.feature_cards = {}
        self.dialog = None
        self.model_body = None
        self.local_downloading = False
        self.local_status = DisplayVar()
        startup_preference = preferences.get("steamvr_auto_start")
        self.startup_preference = startup_preference if isinstance(startup_preference, bool) else None
        self.startup_status = DisplayVar("자동 실행 켜짐" if self.startup_preference else "자동 실행 꺼짐")
        self.startup_button = None
        self.startup = SteamStartup(lambda: self.startup_preference,
                                    lambda *status: self.events.put(("startup", status)))
        self.skipped_version = preferences.get("skipped_version")
        self.pending_update = None
        self.update_installer = None
        self.codex = CodexBridge(self.stop)
        self.login_cancel = Event()
        self.luna_effort = None
        self.luna_fallback = None
        self.luna_signed_in = False
        self.luna_operation = None
        self.assistant_efforts = ()
        saved_effort = preferences.get("assistant_effort")
        self.assistant_effort = saved_effort if saved_effort in REASONING_EFFORTS else "low"
        self.assistant_tone = "casual" if preferences.get("assistant_tone") == "casual" else "polite"
        self.gemini_key = decrypt_key(preferences.get("gemini_api_key"))
        saved_threshold = preferences.get("assistant_mic_db")
        self.mic_threshold = (saved_threshold if type(saved_threshold) is int
                              and MIN_THRESHOLD <= saved_threshold <= MAX_THRESHOLD else DEFAULT_THRESHOLD)
        self.mic_calibrated = preferences.get("assistant_mic_calibrated") is True
        input_device = preferences.get("assistant_input_device")
        self.mic_device = (tuple(input_device) if isinstance(input_device, list) and len(input_device) == 2
                           and all(isinstance(part, str) for part in input_device) else None)
        self.calibration_cancel = None
        self.calibration_receiver = None
        self.assistant_effort_buttons = None
        self.assistant_tone_buttons = None
        self.luna_status = DisplayVar("연결 확인 중")
        self.luna_controls = None
        self.connection = DisplayVar("SteamVR 연결 확인 중")
        self.scan_state = DisplayVar("대기 중" if "qr" in self.features else "꺼짐")
        self.status = DisplayVar(formatter=self.summary)
        self.recent_kind = DisplayVar("대기 중")
        self.recent_source = tk.StringVar()
        self.recent_output = tk.StringVar(value="")
        self.recent_url = None
        self.recent_widgets = None
        self.latest_text = None
        self.translation_pending = False
        self.translation_loading = None
        self.recent_result = None
        self.target = load_target()
        self.target_label = DisplayVar(LANGUAGES[self.target])
        self.model_label = DisplayVar()
        self.selected_model_label = DisplayVar()
        self.assistant_label = DisplayVar("설정 필요" if "assistant" in self.features else "꺼짐")
        self.assistant_tip = DisplayVar()
        self.update_assistant_settings()
        stored_history = preferences.get("assistant_history")
        self.assistant_history = deque(maxlen=5)
        if isinstance(stored_history, list):
            self.assistant_history.extend((item[0], item[1]) for item in stored_history[:5]
                                          if isinstance(item, list) and len(item) == 2
                                          and all(isinstance(part, str) for part in item))
        self.refresh_model_labels()
        self.result_label = DisplayVar("번역 결과")
        self.relocalize_recent()
        self.detail_boxes = None
        self.current_page = None
        self.language_back = "dashboard"

        root.title("LiDAR")
        window_icon = Path(__file__).with_name("LiDAR-Taskbar.ico")
        if window_icon.is_file():
            root.iconbitmap(default=str(window_icon))
        root.geometry(f"{px(1080)}x{px(700)}")
        root.minsize(px(940), px(645))
        root.configure(bg=self.theme.paper)
        root.option_add("*Font", ui_font(10))
        set_titlebar_theme(root, self.theme.titlebar_dark)

        self.build_shell()
        self.show("dashboard")
        root.bind("<Escape>", self.dismiss_settings)
        root.bind("<Tab>", self.cycle_dialog_focus)
        root.bind("<Shift-Tab>", lambda _: self.cycle_dialog_focus(reverse=True))
        root.bind("<Map>", self.on_window_mapped, add="+")
        tray_icon = Path(__file__).with_name("LiDAR.ico")
        self.tray = TrayIcon(tray_icon, lambda action: self.events.put(("tray", action))) if tray_icon.is_file() else None
        root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.worker = Thread(target=self.scan, daemon=True)
        self.worker.start()
        Thread(target=watch_runtime, args=(self.stop, lambda: self.events.put(("quit", None))),
               daemon=True, name="steamvr-lifecycle").start()
        self.refresh_job = root.after(70, self.refresh)
        self.check_luna("status", self.codex.status, "연결 확인 중")
        Thread(target=self.check_updates, daemon=True).start()

    def build_shell(self):
        root = self.root
        self.shell = tk.Frame(root, bg=self.theme.paper)
        self.shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(self.shell, width=px(188), bg=self.theme.surface, highlightthickness=1, highlightbackground=self.theme.line)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        with Image.open(Path(__file__).with_name("brand") / "lidar-wordmark.png") as original:
            logo = original.convert("RGBA")
        logo.thumbnail((px(158), px(55)), Image.Resampling.LANCZOS)
        if self.theme.titlebar_dark:
            alpha = ImageChops.multiply(ImageOps.invert(logo.convert("L")), logo.getchannel("A"))
            logo = Image.new("RGBA", logo.size, self.theme.ink)
            logo.putalpha(alpha)
        self.wordmark = ImageTk.PhotoImage(logo, master=root)
        tk.Label(sidebar, image=self.wordmark, bg=self.theme.surface, borderwidth=0).pack(
            anchor="w", padx=px(15), pady=(px(17), px(19)))

        label(sidebar, "워크스페이스", size=9, weight="semibold", fg=self.theme.muted).pack(anchor="w", padx=px(19), pady=(0, px(8)))
        self.nav = {}
        for key, symbol, caption in (("dashboard", "▦", "대시보드"), ("translation", "文", "AI 번역"),
                                     ("assistant", "✦", "AI 어시스턴트")):
            button = SidebarButton(sidebar, caption, lambda page=key: self.show(page), symbol)
            button.pack(fill="x", padx=px(12), pady=px(2))
            self.nav[key] = button
        bottom = tk.Frame(sidebar, bg=self.theme.surface)
        bottom.pack(side="bottom", fill="x", pady=(0, px(12)))
        for key, symbol, caption in (("locale", None, "Language"), ("settings", "⚙", "설정")):
            button = SidebarButton(bottom, caption, lambda page=key: self.show(page), symbol)
            button.pack(fill="x", padx=px(12), pady=px(2))
            self.nav[key] = button
        main = tk.Frame(self.shell, bg=self.theme.paper)
        main.pack(side="left", fill="both", expand=True, padx=px(26), pady=(px(22), px(18)))
        header = tk.Frame(main, bg=self.theme.paper)
        header.pack(fill="x", pady=(0, px(15)))
        labels = tk.Frame(header, bg=self.theme.paper)
        labels.pack(side="left", fill="x", expand=True)
        self.title = label(labels, "대시보드", size=27, weight="bold")
        self.title.pack(anchor="w", pady=(px(3), 0))
        self.subtitle = label(labels, "", size=10, fg=self.theme.muted)
        indicator = tk.Frame(header, bg=self.theme.surface, highlightthickness=1, highlightbackground=self.theme.line)
        indicator.pack(side="right", anchor="n", pady=(px(16), 0))
        dot = tk.Canvas(indicator, width=px(10), height=px(10), bg=self.theme.surface, highlightthickness=0)
        dot.pack(side="left", padx=(px(12), px(5)))
        mark = dot.create_oval(1, 1, px(9), px(9), fill=self.theme.muted, outline="")
        label(indicator, variable=self.connection, size=10, weight="semibold").pack(
            side="left", padx=(0, px(12)), pady=px(8))

        def update_connection(*_):
            dot.itemconfigure(mark, fill=self.theme.accent if self.connection.message == "SteamVR 연결됨" else self.theme.muted)

        self.connection_trace = self.connection.trace_add("write", update_connection)
        update_connection()
        self.viewport = tk.Frame(main, bg=self.theme.paper)
        self.viewport.pack(fill="both", expand=True)
        self.view = tk.Frame(self.viewport, bg=self.theme.paper)
        self.view.place(x=0, y=0, relwidth=1, relheight=1)
        self.page_motion = Motion(self.title)
        footer = tk.Frame(main, bg=self.theme.paper)
        label(footer, variable=self.status, fg=self.theme.muted, size=9, wrap=690).pack(side="left", fill="x", expand=True)

        def update_footer(*_):
            if self.status.get():
                footer.pack(fill="x", pady=(px(12), 0))
            else:
                footer.pack_forget()

        self.status_trace = self.status.trace_add("write", update_footer)
        update_footer()

    def toggle_theme(self):
        dark = not self.theme.titlebar_dark
        try:
            save_settings(dark_mode=dark)
        except OSError:
            self.status.set("다크 모드 설정을 저장하지 못했습니다.")
            return
        self.theme = self.root.theme = DARK if dark else LIGHT
        self.root.configure(bg=self.theme.paper)
        set_titlebar_theme(self.root, dark)
        self.rebuild_shell()
        self.theme_switch.focus_set()

    def rebuild_shell(self):
        page = self.current_page
        self.page_motion.cancel()
        self.connection.trace_remove("write", self.connection_trace)
        self.status.trace_remove("write", self.status_trace)
        self.shell.destroy()
        self.current_page = None
        self.build_shell()
        self.show(page)

    def change_locale(self, locale):
        if locale == get_locale():
            return
        try:
            save_settings(ui_language=locale)
        except OSError:
            self.status.set("표시 언어를 저장하지 못했습니다.")
            return
        set_locale(locale)
        self.root.option_add("*Font", ui_font(10))
        self.relocalize_recent()
        for variable in vars(self).values():
            if isinstance(variable, DisplayVar):
                variable.relocalize()
        self.rebuild_shell()
        self.locale_buttons[locale].focus_set()

    def show(self, page):
        if page == self.current_page:
            return
        animate = self.current_page is not None
        self.current_page = page
        self.detail_boxes = None
        self.translation_loading = None
        self.recent_widgets = None
        self.feature_cards = {}
        self.luna_controls = None
        self.startup_button = None
        for child in self.view.winfo_children():
            child.destroy()
        active_tab = self.language_back if page == "languages" else page
        for key, button in self.nav.items():
            button.palette(self.theme.selected, self.theme.selected_hover, self.theme.accent) if key == active_tab else button.palette(self.theme.surface, self.theme.hover, self.theme.ink)

        if page == "dashboard":
            self.title.configure(text=tr("대시보드"))
            self.subtitle.configure(text=tr("손 제스처로 VR 화면의 QR과 텍스트를 읽습니다."))
            self.subtitle.pack(anchor="w", pady=(px(5), 0))
            self.dashboard()
        elif page == "translation":
            self.title.configure(text=tr("AI 번역"))
            self.subtitle.pack_forget()
            self.translation()
        elif page == "settings":
            self.title.configure(text=tr("설정"))
            self.subtitle.pack_forget()
            self.settings()
        elif page == "languages":
            self.title.configure(text=tr("번역 언어 설정"))
            self.subtitle.pack_forget()
            self.translation_settings()
        elif page == "locale":
            self.title.configure(text=tr("Language"))
            self.subtitle.pack_forget()
            self.locale_settings()
        else:
            self.title.configure(text=tr("AI 어시스턴트"))
            self.subtitle.pack_forget()
            self.assistant_page()
        if animate:
            self.animate_page()

    def animate_page(self):
        start = self.title.winfo_rgb(self.theme.muted)
        end = self.title.winfo_rgb(self.theme.ink)
        self.page_motion.start(.11, lambda progress: self.title.configure(fg=blend_color(start, end, progress)))

    def dashboard(self):
        summary = tk.Frame(self.view, bg=self.theme.paper)
        summary.pack(fill="x", pady=(0, px(13)))
        for column in range(3):
            summary.columnconfigure(column, weight=1, uniform="summary")
        tiles = (("qr", "QR 스캔", self.scan_state), ("translation", "AI 번역", self.model_label),
                 ("assistant", "AI 어시스턴트", self.assistant_label))
        for index, (feature, title, value) in enumerate(tiles):
            settings = {"translation": self.open_translation_card_settings,
                        "assistant": self.go_to_assistant_settings}.get(feature)
            toggle = (lambda key=feature: self.toggle_feature(key)) if feature in FEATURES else None
            card = FeatureCard(summary, title, value, feature in self.features, toggle, settings)
            self.feature_cards[feature] = card
            card.grid(row=0, column=index, sticky="nsew", padx=(0, px(12) if index < 2 else 0))

        lower = tk.Frame(self.view, bg=self.theme.paper)
        lower.pack(fill="both", expand=True)
        lower.columnconfigure(0, weight=5)
        lower.columnconfigure(1, weight=4)
        lower.rowconfigure(0, weight=1)
        guide = Card(lower, height=260)
        guide.grid(row=0, column=0, sticky="nsew", padx=(0, px(12)))
        label(guide.body, "스캔 제스처", size=16, weight="bold").pack(anchor="w")
        diagram = tk.Canvas(guide.body, height=px(74), bg=self.theme.diagram, highlightthickness=0)
        diagram.pack(fill="x", pady=(px(11), px(13)))
        diagram.create_rectangle(px(26), px(14), px(100), px(59), outline=self.theme.accent, dash=(3, 3))
        diagram.create_line(*(px(n) for n in (26, 35, 26, 59, 52, 59)), fill=self.theme.accent, width=px(3))
        diagram.create_line(*(px(n) for n in (74, 14, 100, 14, 100, 38)), fill=self.theme.accent, width=px(3))
        diagram.create_text(px(118), px(36), anchor="w", text=tr("L자 프레임"), font=ui_font(11, "semibold"), fill=self.theme.ink)
        for number, step in (("01", "양손의 엄지와 검지로 L자를 만듭니다."),
                             ("02", "대상을 창에 맞춘 뒤 자세를 풀어 읽습니다."),
                             ("03", "오른손 트리거로 스캔을 취소합니다.")):
            row = tk.Frame(guide.body, bg=self.theme.surface)
            row.pack(fill="x", pady=px(4))
            label(row, number, size=9, weight="semibold", fg=self.theme.accent).pack(side="left", padx=(0, px(10)))
            label(row, step, size=10, wrap=295).pack(side="left", fill="x", expand=True)

        recent = Card(lower, height=260)
        recent.grid(row=0, column=1, sticky="nsew")
        label(recent.body, "최근 스캔", size=16, weight="bold").pack(anchor="w")
        label(recent.body, variable=self.recent_kind, size=9, weight="semibold", fg=self.theme.accent).pack(anchor="w", pady=(px(16), px(8)))
        source = label(recent.body, variable=self.recent_source, size=10, fg=self.theme.muted, wrap=242)
        link = HoverButton(recent.body, "", self.open_recent, self.theme.surface, self.theme.selected, self.theme.accent,
                           padding=(3, 2), textvariable=self.recent_source,
                           wraplength=px(242), anchor="w", justify="left")
        details = tk.Frame(recent.body, bg=self.theme.surface)
        tk.Frame(details, bg=self.theme.line, height=1).pack(fill="x", pady=px(14))
        label(details, variable=self.recent_output, size=11, weight="semibold", wrap=242).pack(anchor="w")
        self.recent_widgets = source, link, details
        self.render_recent()

    def render_recent(self):
        if self.recent_widgets is None:
            return
        source, link, details = self.recent_widgets
        for widget in self.recent_widgets:
            widget.pack_forget()
        (link if self.recent_url else source).pack(anchor="w")
        if self.recent_output.get():
            details.pack(fill="x")

    def relocalize_recent(self):
        if self.recent_result is None:
            self.recent_source.set(tr("아직 스캔 결과가 없습니다."))
            return
        mode, source, output, *_ = self.recent_result
        self.recent_source.set(self.summary(tr(source) if isinstance(source, Message) else source))
        if mode == "QR":
            output = tr(output).removesuffix(f" · {source}") if self.recent_url else tr(output)
        self.recent_output.set(self.summary(output))

    def open_recent(self):
        if self.recent_url:
            try:
                self.status.set(open_result(self.recent_url, activate=True))
            except OSError:
                self.status.set("PC 브라우저에서 링크를 열지 못했습니다")

    def translation(self):
        self.view.columnconfigure(0, weight=1, uniform="translate")
        self.view.columnconfigure(1, weight=1, uniform="translate")
        self.view.rowconfigure(0, weight=1)
        boxes = []
        for index in range(2):
            card = Card(self.view, height=310)
            card.grid(row=0, column=index, sticky="nsew", padx=(0, px(12) if index == 0 else 0))
            label(card.body, "원문", variable=self.result_label if index else None,
                  size=16, weight="bold").pack(anchor="w", pady=(0, px(15)))
            if index:
                self.translation_loading = TranslationLoading(card.body, self.theme)
            box = tk.Text(card.body, bg=self.theme.surface, fg=self.theme.ink, bd=0, highlightthickness=0, wrap="word",
                          font=ui_font(12), padx=0, pady=px(3), spacing2=px(4), spacing3=px(8))
            box.pack(fill="both", expand=True)
            boxes.append(box)
        self.detail_boxes = boxes
        self.render_translation()

    def assistant_page(self):
        header = Card(self.view, height=86)
        header.pack(fill="x", pady=(0, px(12)))
        HoverButton(header.body, "설정으로 이동", self.go_to_assistant_settings, self.theme.paper,
                    self.theme.selected, self.theme.accent).pack(side="right")
        label(header.body, variable=self.assistant_label, size=13, weight="bold").pack(anchor="w")
        label(header.body, variable=self.assistant_tip,
              fg=self.theme.muted, wrap=390).pack(anchor="w", pady=(px(4), 0))
        header.fit_height()

        history = tk.Frame(self.view, bg=self.theme.paper)
        history.pack(fill="both", expand=True)
        history.columnconfigure(0, weight=2, uniform="history")
        history.columnconfigure(1, weight=3, uniform="history")
        history.rowconfigure(0, weight=1)
        recent = Card(history, height=284, inset=16)
        recent.grid(row=0, column=0, sticky="nsew", padx=(0, px(12)))
        label(recent.body, "최근 질문", size=14, weight="bold").pack(anchor="w", pady=(0, px(12)))
        self.assistant_history_list = tk.Frame(recent.body, bg=self.theme.surface)
        self.assistant_history_list.pack(fill="both", expand=True)
        detail = Card(history, height=284, inset=18)
        detail.grid(row=0, column=1, sticky="nsew")
        label(detail.body, "질문과 답변", size=14, weight="bold").pack(anchor="w", pady=(0, px(12)))
        self.assistant_history_text = tk.Text(detail.body, bg=self.theme.surface, fg=self.theme.ink,
                                              bd=0, highlightthickness=0, padx=0, wrap="word",
                                              font=ui_font(11), spacing3=px(6))
        self.assistant_history_text.tag_configure("heading", foreground=self.theme.accent, font=ui_font(10, "semibold"),
                                                  spacing1=px(6), spacing3=px(7))
        self.assistant_history_text.pack(fill="both", expand=True)
        self.render_assistant_history()

    def render_assistant_history(self):
        for widget in self.assistant_history_list.winfo_children():
            widget.destroy()
        self.assistant_history_buttons = []
        for i, (question, _) in enumerate(self.assistant_history):
            short = " ".join(question.split())
            short = short if len(short) <= 28 else short[:27] + "…"
            button = HoverButton(self.assistant_history_list, "", lambda index=i: self.select_assistant_history(index),
                                 self.theme.paper, self.theme.selected, self.theme.ink, anchor="w",
                                 textvariable=tk.StringVar(value=short), padding=(10, 6), wraplength=px(185),
                                 justify="left")
            button.pack(fill="x", pady=(0, px(6)))
            self.assistant_history_buttons.append(button)
        if not self.assistant_history:
            label(self.assistant_history_list, "아직 질문 기록이 없습니다.",
                  fg=self.theme.muted, wrap=185).pack(anchor="w")
        self.select_assistant_history(0)

    def select_assistant_history(self, index):
        for i, button in enumerate(self.assistant_history_buttons):
            selected = i == index
            button.palette(self.theme.selected if selected else self.theme.paper, self.theme.selected_hover,
                           self.theme.accent if selected else self.theme.ink)
        box = self.assistant_history_text
        box.configure(state="normal")
        box.delete("1.0", "end")
        if self.assistant_history:
            question, answer = self.assistant_history[index]
            box.insert("end", tr("질문") + "\n", "heading")
            box.insert("end", question + "\n\n")
            box.insert("end", tr("답변") + "\n", "heading")
            box.insert("end", answer)
        box.yview_moveto(0)
        box.configure(state="disabled")

    def go_to_assistant_settings(self):
        self.show("settings")
        self.assistant_settings_button.focus_set()

    def settings(self):
        language = Card(self.view, height=90, inset=22)
        language.pack(fill="x", pady=(0, px(12)))
        change = HoverButton(language.body, "변경", self.choose_language, self.theme.paper, self.theme.selected, self.theme.accent)
        change.pack(side="right")
        label(language.body, "번역 언어", size=15, weight="bold").pack(anchor="w")
        label(language.body, variable=self.target_label, fg=self.theme.muted).pack(anchor="w", pady=(px(4), 0))
        language.fit_height()

        card = Card(self.view, height=90, inset=22)
        card.pack(fill="x", pady=(0, px(12)))
        body = card.body
        HoverButton(body, "모델 선택", self.show_model_settings, self.theme.paper, self.theme.selected, self.theme.accent).pack(side="right")
        label(body, "번역 모델 설정", size=15, weight="bold").pack(anchor="w")
        label(body, variable=self.selected_model_label, fg=self.theme.muted).pack(anchor="w", pady=(px(4), 0))
        card.fit_height()

        startup = Card(self.view, height=92, inset=22)
        startup.pack(fill="x", pady=(0, px(12)))
        self.startup_button = Switch(startup.body, self.startup_preference, self.toggle_startup,
                                     enabled=MANIFEST.is_file())
        self.startup_button.pack(side="right")
        label(startup.body, "SteamVR 기동 시 자동 실행", size=15, weight="bold").pack(anchor="w")
        label(startup.body, variable=self.startup_status, fg=self.theme.muted).pack(anchor="w", pady=(px(4), 0))
        startup.fit_height()

        preferences = tk.Frame(self.view, bg=self.theme.paper)
        preferences.pack(fill="x")
        preferences.columnconfigure(0, weight=1, uniform="preferences")
        preferences.columnconfigure(1, weight=1, uniform="preferences")
        appearance = Card(preferences, height=82, inset=16)
        appearance.grid(row=0, column=0, sticky="nsew", padx=(0, px(12)))
        self.theme_switch = Switch(appearance.body, self.theme.titlebar_dark, self.toggle_theme)
        self.theme_switch.pack(side="right")
        label(appearance.body, "다크 모드", size=15, weight="bold").pack(anchor="w")
        appearance.fit_height()
        assistant_settings = Card(preferences, height=82, inset=16)
        assistant_settings.grid(row=0, column=1, sticky="nsew")
        self.assistant_settings_button = HoverButton(assistant_settings.body, "열기", self.show_assistant_settings,
                                                     self.theme.paper, self.theme.selected, self.theme.accent,
                                                     padding=(10, 6))
        self.assistant_settings_button.pack(side="right")
        label(assistant_settings.body, "어시스턴트 설정", size=13, weight="bold", wrap=170).pack(anchor="w")
        assistant_settings.fit_height()
        label(self.view, f"LiDAR {VERSION}", fg=self.theme.muted, size=9).pack(anchor="e", pady=(px(9), 0))

    def assistant_config(self):
        reason = ""
        if not self.luna_signed_in:
            reason = "어시스턴트를 사용하려면 Codex에 연결하세요."
        elif not self.assistant_efforts:
            reason = "연결된 계정에서 Luna를 사용할 수 없습니다."
        elif not self.gemini_key:
            reason = "설정에서 Gemini API 키를 입력하세요."
        elif not self.mic_calibrated:
            reason = "PC 앱에서 마이크 감도를 보정하세요."
        effort = self.assistant_effort if self.assistant_effort in self.assistant_efforts else (
            "low" if "low" in self.assistant_efforts else "none")
        return AssistantConfig(reason, self.mic_threshold, effort, self.gemini_key, self.assistant_tone, self.mic_device)

    def update_assistant_settings(self):
        config = self.assistant_config()
        enabled = "assistant" in self.features
        self.assistant_label.set("꺼짐" if not enabled else "설정 필요" if config.reason else "사용 가능")
        self.assistant_tip.set("오른손 트리거를 두 번 눌러 어시스턴트를 켭니다." if enabled
                               else "대시보드에서 AI 어시스턴트를 켜세요.")
        if self.assistant_effort_buttons is not None:
            for effort, button in self.assistant_effort_buttons.items():
                button.configure(state="normal" if effort in self.assistant_efforts else "disabled")
                self.mark_assistant_option(button, effort == config.effort)
        if self.assistant_tone_buttons is not None:
            for tone, button in self.assistant_tone_buttons.items():
                self.mark_assistant_option(button, tone == config.tone)

    def mark_assistant_option(self, button, selected):
        button.palette(self.theme.selected if selected else self.theme.paper, self.theme.selected_hover,
                       self.theme.accent if selected else self.theme.ink)

    def show_assistant_settings(self, initial="connection"):
        if self.dialog is not None:
            return

        def close():
            self.luna_controls = None
            self.assistant_effort_buttons = None
            self.assistant_tone_buttons = None
            self.close_dialog()

        self.dialog = InAppDialog(self.root, "어시스턴트 설정", close)
        body = self.dialog.body
        tabs = tk.Frame(body, bg=self.theme.surface)
        tabs.pack(fill="x", pady=(px(16), px(13)))
        region = tk.Frame(body, bg=self.theme.surface, width=px(526), height=px(245))
        region.pack(fill="x")
        region.pack_propagate(False)
        panes = {name: tk.Frame(region, bg=self.theme.surface) for name in ("connection", "answer", "mic")}
        buttons = {}

        def show_pane(name):
            for key, pane in panes.items():
                if key == name:
                    pane.pack(fill="both", expand=True)
                else:
                    pane.pack_forget()
                self.mark_assistant_option(buttons[key], key == name)

        for i, (name, caption) in enumerate((("connection", "연결"), ("answer", "답변"), ("mic", "마이크"))):
            tabs.columnconfigure(i, weight=1, uniform="section")
            button = HoverButton(tabs, caption, lambda key=name: show_pane(key), self.theme.paper,
                                 self.theme.selected, self.theme.ink, padding=(12, 7))
            button.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(6), 0))
            buttons[name] = button

        connection = panes["connection"]
        label(connection, "Codex 연결", size=12, weight="bold").pack(anchor="w")
        self.luna_settings(connection)
        label(connection, "Gemini 3.5 Transcribe API 키", size=12, weight="bold").pack(anchor="w", pady=(px(15), px(5)))
        fields = tk.Frame(connection, bg=self.theme.surface, height=px(38))
        fields.pack(fill="x")
        fields.pack_propagate(False)
        entry = tk.Entry(fields, show="•", bg=self.theme.paper, fg=self.theme.ink, insertbackground=self.theme.ink,
                         relief="flat", highlightthickness=1, highlightbackground=self.theme.line)
        entry.pack(side="left", expand=True, fill="both", padx=(0, px(6)), pady=px(2))
        notice = DisplayVar("API 키 저장됨" if self.gemini_key else "")

        def save_key(remove=False):
            key = "" if remove else entry.get().strip()
            if not remove and not re.fullmatch(r"[!-~]{1,256}", key):
                notice.set("API 키를 확인하세요.")
                return
            try:
                save_settings(gemini_api_key=encrypt_key(key) if key else "")
            except OSError:
                notice.set("API 키를 저장하지 못했습니다.")
                return
            self.gemini_key = key
            entry.delete(0, "end")
            notice.set("API 키 저장됨" if key else "")
            self.update_assistant_settings()

        HoverButton(fields, "저장", save_key, self.theme.paper, self.theme.selected, self.theme.accent).pack(side="right")
        HoverButton(fields, "키 지우기", lambda: save_key(True), self.theme.paper,
                    self.theme.selected, self.theme.ink).pack(side="right", padx=(0, px(5)))
        label(connection, "녹음은 Gemini로, 받아쓴 질문과 화면 캡처는 Codex로 전송합니다.",
              size=9, fg=self.theme.muted, wrap=500).pack(anchor="w", pady=(px(6), 0))

        answers = panes["answer"]
        label(answers, "말투", size=12, weight="bold").pack(anchor="w", pady=(0, px(6)))
        tone_choices = tk.Frame(answers, bg=self.theme.surface)
        tone_choices.pack(fill="x")
        self.assistant_tone_buttons = {}

        def choose_tone(tone):
            try:
                save_settings(assistant_tone=tone)
            except OSError:
                notice.set("설정을 저장하지 못했습니다. 다시 시도하세요.")
                return
            self.assistant_tone = tone
            self.update_assistant_settings()

        for tone, caption in (("polite", "존댓말"), ("casual", "반말")):
            button = HoverButton(tone_choices, caption, lambda value=tone: choose_tone(value), self.theme.paper,
                                 self.theme.selected, self.theme.ink, padding=(12, 6))
            button.pack(side="left", padx=(0, px(6)))
            self.assistant_tone_buttons[tone] = button

        label(answers, "Luna 추론 수준", size=12, weight="bold").pack(anchor="w", pady=(px(19), px(6)))
        choices = tk.Frame(answers, bg=self.theme.surface)
        choices.pack(fill="x")
        self.assistant_effort_buttons = {}

        def choose_effort(effort):
            try:
                save_settings(assistant_effort=effort)
            except OSError:
                notice.set("설정을 저장하지 못했습니다. 다시 시도하세요.")
                return
            self.assistant_effort = effort
            self.update_assistant_settings()

        for effort in REASONING_EFFORTS:
            button = HoverButton(choices, effort, lambda value=effort: choose_effort(value), self.theme.paper,
                                 self.theme.selected, self.theme.ink, padding=(8, 5))
            button.pack(side="left", padx=(0, px(4)))
            self.assistant_effort_buttons[effort] = button
        microphone = panes["mic"]
        label(microphone, "마이크 입력", size=12, weight="bold").pack(anchor="w")
        device_row = tk.Frame(microphone, bg=self.theme.surface)
        device_row.pack(fill="x", pady=(px(6), 0))

        def pick_microphone():
            close()
            self.show_microphone_picker()

        HoverButton(device_row, "변경", pick_microphone, self.theme.paper,
                    self.theme.selected, self.theme.accent, padding=(12, 6)).pack(side="right")
        selected_device = tk.StringVar(value=self.mic_device[1] if self.mic_device else tr("Windows 기본 마이크"))
        label(device_row, variable=selected_device, fg=self.theme.muted, wrap=385).pack(side="left", pady=px(5))
        label(microphone, "마이크 감도", size=12, weight="bold").pack(anchor="w", pady=(px(17), 0))
        controls = tk.Frame(microphone, bg=self.theme.surface)
        controls.pack(fill="x", pady=(px(4), 0))
        slider = SensitivitySlider(controls, self.mic_threshold)
        value = label(controls, variable=slider.value_text, size=10, weight="semibold", fg=self.theme.accent)
        value.configure(width=10, anchor="e")
        value.pack(side="right", padx=(px(8), 0))
        slider.pack(side="left", fill="x", expand=True)
        actions = tk.Frame(microphone, bg=self.theme.surface)
        actions.pack(fill="x", pady=(px(5), 0))
        label(actions, "오른쪽으로 옮기면 작은 목소리도 감지합니다.",
              size=9, fg=self.theme.muted, wrap=220).pack(side="left")

        def save_threshold():
            threshold = slider.get()
            try:
                save_settings(assistant_mic_db=threshold, assistant_mic_calibrated=True)
            except OSError:
                notice.set("설정을 저장하지 못했습니다. 다시 시도하세요.")
                return
            self.mic_threshold = threshold
            self.mic_calibrated = True
            notice.set("마이크 감도를 저장했습니다.")
            self.update_assistant_settings()

        def recalibrate():
            close()
            self.show_mic_calibration()

        HoverButton(actions, "감도 저장", save_threshold, self.theme.paper,
                    self.theme.selected, self.theme.accent).pack(side="right")
        HoverButton(actions, "다시 보정", recalibrate, self.theme.paper,
                    self.theme.selected, self.theme.ink).pack(side="right", padx=(0, px(6)))
        bottom = tk.Frame(body, bg=self.theme.surface)
        bottom.pack(fill="x", pady=(px(10), 0))
        label(bottom, variable=notice, size=9, fg=self.theme.muted, wrap=390).pack(side="left")
        HoverButton(bottom, "닫기", close, self.theme.paper, self.theme.selected, self.theme.ink).pack(side="right")
        self.update_assistant_settings()
        show_pane(initial)
        self.dialog.open(buttons[initial])

    def show_microphone_picker(self):
        if self.dialog is not None:
            return

        def back():
            self.close_dialog()
            self.show_assistant_settings("mic")

        self.dialog = InAppDialog(self.root, "마이크 입력", back)
        body = self.dialog.body
        status = DisplayVar()
        choices = [None]
        try:
            devices = sorted((identity for _, identity in input_devices()),
                             key=lambda identity: (identity[0] != "Windows WASAPI", identity[1].casefold()))
            displayed = set()
            for identity in devices:
                if identity[1] not in displayed or identity == self.mic_device:
                    choices.append(identity)
                    displayed.add(identity[1])
        except SpeechError as error:
            status.set(str(error))
        names = [tr("Windows 기본 마이크"), *(f"{name}  ·  {host}" for host, name in choices[1:])]
        inputs = tk.Listbox(body, listvariable=tk.StringVar(value=names), width=54, height=8, font=ui_font(10),
                            bg=self.theme.paper, fg=self.theme.ink, selectbackground=self.theme.selected,
                            selectforeground=self.theme.accent, bd=0, highlightthickness=1,
                            highlightcolor=self.theme.accent, highlightbackground=self.theme.line, exportselection=False)
        inputs.pack(fill="x", pady=(px(14), px(9)), ipadx=px(5), ipady=px(5))
        if self.mic_device not in choices:
            if not status.get():
                status.set("선택한 마이크를 찾을 수 없습니다.")
            index = 0
        else:
            index = choices.index(self.mic_device)
        inputs.selection_set(index)
        inputs.activate(index)
        inputs.see(index)
        label(body, variable=status, fg=self.theme.muted, size=9, wrap=450).pack(anchor="w")

        def save(_=None):
            selected = inputs.curselection()
            if not selected:
                return "break"
            device = choices[selected[0]]
            if device != self.mic_device:
                try:
                    save_settings(assistant_input_device=device, assistant_mic_calibrated=False)
                except OSError:
                    status.set("설정을 저장하지 못했습니다. 다시 시도하세요.")
                    return "break"
                self.mic_device, self.mic_calibrated = device, False
                self.update_assistant_settings()
            back()
            return "break"

        actions = tk.Frame(body, bg=self.theme.surface)
        actions.pack(fill="x", pady=(px(8), 0))
        HoverButton(actions, "저장", save, VIOLET, "#4e009e", BUTTON_TEXT).pack(side="right")
        HoverButton(actions, "취소", back, self.theme.paper, self.theme.selected, self.theme.ink).pack(side="right", padx=(0, px(6)))
        inputs.bind("<Return>", save)
        inputs.bind("<Double-Button-1>", save)
        self.dialog.open(inputs)

    def show_mic_calibration(self):
        if self.dialog is not None:
            return
        cancel = self.calibration_cancel = Event()
        device = self.mic_device

        def close():
            cancel.set()
            self.calibration_cancel = self.calibration_receiver = None
            self.close_dialog()

        self.dialog = InAppDialog(self.root, "마이크 감도 보정", close)
        body = self.dialog.body
        instruction = DisplayVar("아래 예문을 평소 목소리로 읽어 주세요.")
        example = DisplayVar("예문 · 이 화면에 보이는 내용을 알려줘.")
        label(body, variable=instruction, wrap=460).pack(anchor="w", pady=(px(15), px(7)))
        label(body, variable=example, size=13, weight="semibold", wrap=460).pack(anchor="w", pady=(0, px(12)))
        label(body, "설정의 어시스턴트 설정에서 감도를 조절하거나 다시 보정할 수 있어요.",
              size=9, fg=self.theme.muted, wrap=460).pack(anchor="w")
        controls = tk.Frame(body, bg=self.theme.surface)
        controls.pack(fill="x", pady=(px(16), 0))
        voice = None
        complete = False

        def begin():
            if complete:
                close()
                return
            speaking = voice is None
            instruction.set("지금 예문을 읽어 주세요." if speaking else "지금은 말하지 마세요.")
            start.configure(text="측정 중", state="disabled")

            def work():
                try:
                    result = measure_microphone(4 if speaking else 2, cancel, device)
                except SpeechError as error:
                    result = str(error)
                self.events.put(("mic-calibration", (cancel, speaking, result)))

            Thread(target=work, daemon=True).start()

        def measured(speaking, result):
            nonlocal voice, complete
            if isinstance(result, str):
                instruction.set(result)
                start.configure(text="다시 시도", state="normal")
            elif speaking:
                voice = result
                instruction.set("이제 말하지 않고 주변 소리를 측정합니다.")
                example.set("")
                start.configure(text="주변 소리 측정", state="normal")
            else:
                try:
                    level = calibrated_threshold(voice, result)
                    save_settings(assistant_mic_db=level, assistant_mic_calibrated=True)
                except (SpeechError, OSError) as error:
                    voice = None
                    instruction.set(str(error) if isinstance(error, SpeechError) else "설정을 저장하지 못했습니다. 다시 시도하세요.")
                    example.set("예문 · 이 화면에 보이는 내용을 알려줘.")
                    start.configure(text="다시 시도", state="normal")
                    return
                self.mic_threshold, self.mic_calibrated = level, True
                self.update_assistant_settings()
                complete = True
                instruction.set("마이크 감도를 저장했습니다.")
                example.set("VR에서 오른손 트리거를 두 번 누르면 시작할 수 있어요.")
                start.configure(text="완료", state="normal")

        self.calibration_receiver = measured
        HoverButton(controls, "닫기", close, self.theme.paper, self.theme.selected, self.theme.ink).pack(side="right")
        start = HoverButton(controls, "목소리 측정", begin, VIOLET, "#4e009e", BUTTON_TEXT)
        start.pack(side="right", padx=(0, px(8)))
        self.dialog.open(start)

    def locale_settings(self):
        card = Card(self.view, height=280, inset=24)
        card.pack(fill="x")
        body = card.body
        label(body, "앱 표시 언어", size=16, weight="bold").pack(anchor="w")
        label(body, "앱 메뉴와 안내에 사용할 언어를 선택하세요.", fg=self.theme.muted,
              wrap=500).pack(anchor="w", pady=(px(5), px(16)))
        choices = tk.Frame(body, bg=self.theme.surface)
        choices.pack(fill="x")
        self.locale_buttons = {}
        for index, (locale, name) in enumerate(UI_LANGUAGES.items()):
            selected = locale == get_locale()
            button = HoverButton(choices, f"{'●' if selected else '○'}   {name}",
                                 lambda code=locale: self.change_locale(code),
                                 self.theme.selected if selected else self.theme.paper,
                                 self.theme.selected_hover if selected else self.theme.hover,
                                 self.theme.accent if selected else self.theme.ink, anchor="w")
            row, column = divmod(index, 2)
            button.configure(font=ui_font(10, "semibold", locale=locale))
            choices.columnconfigure(column, weight=1, uniform="ui-language")
            button.grid(row=row, column=column, sticky="ew", padx=(0, px(8)), pady=(0, px(8)))
            self.locale_buttons[locale] = button
        card.fit_height()

    def refresh_model_labels(self):
        provider = self.translation_config[0]
        self.selected_model_label.set(TRANSLATION_SERVICES.get(provider, "번역 모델을 정해주세요"))
        self.model_label.set(LANGUAGES[self.target] if provider else "번역 모델을 정해주세요")

    def update_translation_connection(self):
        enabled = (self.translation_config[0] == "luna" and "translation" in self.features
                   and self.luna_effort is not None)
        self.codex.translation_connection.configure(enabled)

    def toggle_feature(self, feature):
        if feature not in FEATURES:
            return
        changed = self.features ^ {feature}
        try:
            if feature == "assistant":
                save_settings(assistant_enabled="assistant" in changed)
            else:
                save_settings(enabled_features=sorted(changed - {"assistant"}))
        except OSError:
            self.status.set("기능 설정을 저장하지 못했습니다.")
            return
        self.features = changed
        card = self.feature_cards.get(feature)
        if card:
            card.animate(feature in changed)
        if feature == "qr":
            self.scan_state.set("대기 중" if feature in changed else "꺼짐")
        elif feature == "translation":
            self.update_translation_connection()
        elif feature == "assistant":
            self.update_assistant_settings()

    def open_translation_card_settings(self):
        if self.translation_config[0]:
            self.choose_language()
        else:
            self.show_model_settings()

    def show_model_settings(self):
        if self.dialog is not None:
            return

        def close():
            self.luna_controls = None
            self.model_body = None
            self.close_dialog()

        self.dialog = InAppDialog(self.root, "번역 모델 설정", close)
        panel = self.dialog.body
        self.model_body = tk.Frame(panel, bg=self.theme.surface, width=px(624), height=px(330))
        self.model_body.pack(fill="x", pady=(px(14), px(8)))
        self.model_body.pack_propagate(False)
        HoverButton(panel, "닫기", close, self.theme.paper, self.theme.selected, self.theme.ink, padding=(13, 6)).pack(anchor="e")
        first_control = self.render_models()
        self.dialog.open(first_control)
        if self.translation_config[0] == "luna":
            self.check_luna("status", self.codex.status, "연결 확인 중")

    def render_models(self):
        if self.model_body is None:
            return
        body = self.model_body
        self.luna_controls = None
        for child in body.winfo_children():
            child.destroy()
        current = self.translation_config[0]
        grid = tk.Frame(body, bg=self.theme.surface)
        grid.pack(fill="x")
        first_control = None
        for column, (provider, name) in enumerate(TRANSLATION_SERVICES.items()):
            selected = provider == current
            warning = "VR 성능 하락이 있을 수 있음" if provider == "local" else ""
            button = ModelChoice(grid, f"●  {name}" if selected else name, MODEL_DESCRIPTIONS[provider],
                                 lambda value=provider: self.select_service(value), selected, warning)
            grid.columnconfigure(column, weight=1, uniform="models")
            button.grid(row=0, column=column, sticky="ew", padx=(px(8) if column else 0, 0))
            if first_control is None or selected:
                first_control = button
        self.service_notice = label(body, "", fg=self.theme.muted, wrap=605)
        self.service_notice.pack(anchor="w", pady=(px(15), px(6)))
        if current == "luna":
            self.service_notice.configure(text=tr("ChatGPT에 로그인하면 OCR 텍스트를 GPT-6 Luna로 처리합니다."))
            self.luna_settings(body)
        elif current == "deepl":
            self.api_settings(body)
        elif current == "local":
            self.local_settings(body)
        else:
            self.service_notice.configure(text=tr("번역 모델을 정해주세요"))
        if self.dialog.winfo_ismapped():
            first_control.focus_set()
        return first_control

    def select_service(self, provider):
        if provider == self.translation_config[0]:
            self.render_models()
            return
        try:
            save_settings(translation_service=provider)
        except OSError:
            self.service_notice.configure(text=tr("번역 모델 설정을 저장하지 못했습니다."))
            return
        self.translation_config = self.load_translation_config(load_settings())
        self.update_translation_connection()
        self.refresh_model_labels()
        self.render_models()
        if provider == "luna":
            self.check_luna("status", self.codex.status, "연결 확인 중")

    @staticmethod
    def load_translation_config(preferences):
        provider = preferences.get("translation_service")
        if not isinstance(provider, str) or provider not in TRANSLATION_SERVICES:
            provider = ""
        return provider, decrypt_key(preferences.get("deepl_api_key")) if provider == "deepl" else ""

    def api_settings(self, body):
        _, saved_key = self.translation_config
        self.service_notice.configure(text=tr("DeepL에서 발급받은 API 키를 입력하세요. Developer 키는 개인정보가 없는 텍스트에만 사용하세요."))
        fields = tk.Frame(body, bg=self.theme.surface)
        fields.pack(fill="x")
        label(fields, "DeepL API 키", size=9, fg=self.theme.muted).pack(anchor="w")
        key = tk.Entry(fields, show="•", bg=self.theme.paper, relief="flat", highlightthickness=1,
                       highlightbackground=self.theme.line, highlightcolor=self.theme.accent)
        key.pack(fill="x", ipady=px(5))

        actions = tk.Frame(body, bg=self.theme.surface)
        actions.pack(fill="x", pady=(px(9), 0))
        status = label(body, "API 키 저장됨" if saved_key else "", size=9, fg=self.theme.muted)
        status.pack(anchor="w", pady=(px(5), 0))

        def save(remove=False):
            secret = "" if remove else key.get().strip() or saved_key
            if not remove and not re.fullmatch(r"[!-~]{1,256}", secret):
                status.configure(text=tr("API 키를 확인하세요."))
                return
            try:
                save_settings(deepl_api_key=encrypt_key(secret) if secret else "")
            except OSError:
                status.configure(text=tr("API 키를 저장하지 못했습니다."))
                return
            self.translation_config = "deepl", secret
            self.render_models()

        HoverButton(actions, "API 키 발급", lambda: webbrowser.open("https://www.deepl.com/en/developers"),
                    self.theme.paper, self.theme.selected, self.theme.ink, padding=(12, 6)).pack(side="left")
        HoverButton(actions, "저장", save, VIOLET, "#4e009e", BUTTON_TEXT, padding=(14, 6)).pack(side="right")
        if saved_key:
            HoverButton(actions, "키 지우기", lambda: save(remove=True), self.theme.paper, self.theme.selected, self.theme.ink,
                        padding=(12, 6)).pack(side="right", padx=(0, px(6)))

    def local_settings(self, body):
        self.service_notice.configure(text=tr("모델을 한 번 다운로드하면 로그인 없이 오프라인으로 번역합니다."))
        ready = available(MODELS_DIR / "m2m100")
        button = HoverButton(body, "모델 다운로드 · 약 473 MiB", self.start_model_download,
                             VIOLET, "#4e009e", BUTTON_TEXT, padding=(14, 6))
        if not ready:
            button.pack(anchor="w")
            button.configure(state="disabled" if self.local_downloading else "normal")
        label(body, "다운로드 완료 · 오프라인 번역 가능" if ready else "",
              variable=None if ready else self.local_status, fg=self.theme.muted).pack(anchor="w", pady=(px(6), 0))

    def start_model_download(self):
        if self.local_downloading:
            return
        self.local_downloading = True
        self.local_status.set("다운로드 준비 중")
        self.render_models()

        def run():
            try:
                download_model(MODELS_DIR / "m2m100", self.stop,
                               lambda percent: self.events.put(("model-progress", percent)))
                self.events.put(("model-finished", True))
            except (OSError, ValueError):
                self.events.put(("model-finished", False))

        Thread(target=run, daemon=True).start()

    def luna_settings(self, body):
        label(body, variable=self.luna_status, fg=self.theme.accent, wrap=465).pack(anchor="w", pady=(px(11), px(9)))
        actions = tk.Frame(body, bg=self.theme.surface)
        actions.pack(fill="x")
        action = HoverButton(actions, "", self.toggle_luna, VIOLET, "#4e009e", BUTTON_TEXT,
                             disabledforeground="#dfc9f5")
        action.pack(side="left")
        check = HoverButton(actions, "상태 확인", lambda: self.check_luna("status", self.codex.status, "연결 확인 중"),
                            self.theme.paper, self.theme.selected, self.theme.ink)
        check.pack(side="left", padx=(px(8), 0))
        self.luna_controls = action, check
        self.render_luna()

    def toggle_startup(self):
        self.set_startup_preference(not self.startup_preference)

    def set_startup_preference(self, desired):
        try:
            save_settings(steamvr_auto_start=desired)
        except OSError:
            self.startup_status.set("자동 실행 설정을 저장하지 못했습니다.")
            return False
        self.startup_status.set("SteamVR가 실행되면 적용됩니다." if desired or self.startup_preference else "자동 실행 꺼짐")
        self.startup.applied = None
        self.startup_preference = desired
        if self.startup_button is not None:
            self.startup_button.animate(desired)
        return True

    def show_startup_prompt(self):
        if self.startup_preference is not None or self.dialog is not None or not self.root.winfo_viewable():
            return

        def choose(enabled):
            if self.set_startup_preference(enabled):
                self.close_dialog()
            else:
                caption.set("설정을 저장하지 못했습니다. 다시 시도하세요.")

        self.dialog = InAppDialog(self.root, "SteamVR와 함께 실행할까요?", lambda: choose(False))
        body = self.dialog.body
        caption = DisplayVar("SteamVR가 시작되면 LiDAR도 실행합니다. 나중에 설정에서 바꿀 수 있습니다.")
        label(body, variable=caption, fg=self.theme.muted, wrap=360).pack(anchor="w", pady=(px(8), px(15)))
        row = tk.Frame(body, bg=self.theme.surface)
        row.pack(anchor="e")
        no = HoverButton(row, "사용 안 함", lambda: choose(False), self.theme.paper, self.theme.selected, self.theme.ink)
        no.pack(side="left", padx=(0, px(8)))
        HoverButton(row, "같이 실행", lambda: choose(True), VIOLET, "#4e009e", BUTTON_TEXT).pack(side="left")
        self.dialog.open(no)

    def check_updates(self):
        try:
            release = latest_release(self.skipped_version)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return
        if release:
            self.events.put(("update", release))

    def show_update(self, release):
        if self.dialog is not None or not self.root.winfo_viewable():
            self.pending_update = release
            return
        if self.startup_preference is None:
            self.pending_update = release
            self.show_startup_prompt()
            return

        self.dialog = InAppDialog(self.root, "새 업데이트가 있습니다", lambda: defer.invoke())
        panel = self.dialog.body
        caption = DisplayVar(f"LiDAR {VERSION} → {release.version}")
        label(panel, variable=caption, fg=self.theme.muted, wrap=410).pack(anchor="w", pady=(px(7), px(15)))
        row = tk.Frame(panel, bg=self.theme.surface)
        row.pack(fill="x")

        def skip():
            try:
                save_settings(skipped_version=release.version)
            except OSError:
                caption.set("건너뛸 버전을 저장하지 못했습니다.")
                return
            self.skipped_version = release.version
            self.close_dialog()

        def download():
            for button in (install, bypass, defer):
                button.configure(state="disabled")
            caption.set("설치 파일 다운로드 중")

            def run():
                try:
                    path = download_installer(release, self.stop, lambda n: self.events.put(("update-progress", n)))
                except (OSError, ValueError):
                    path = None
                self.events.put(("update-finished", path))

            Thread(target=run, daemon=True).start()

        install = HoverButton(row, "지금 업데이트", download, VIOLET, "#4e009e", BUTTON_TEXT)
        bypass = HoverButton(row, "이번 버전 건너뛰기", skip, self.theme.paper, self.theme.selected, self.theme.ink)
        defer = HoverButton(row, "나중에", self.close_dialog, self.theme.paper, self.theme.selected, self.theme.ink)
        for button in (install, bypass, defer):
            button.pack(side="left", padx=(0, px(7)))

        def finish(path):
            if path is None:
                caption.set("업데이트를 다운로드하지 못했습니다. 다시 시도하세요.")
                for button in (install, bypass, defer):
                    button.configure(state="normal")
                return
            self.update_installer = path
            self.close_dialog()
            self.close()

        self.update_progress = caption
        self.update_finished = finish
        self.dialog.open(install)

    def show_installer_error(self):
        self.dialog = InAppDialog(self.root, "LiDAR 업데이트", self.close_dialog)
        panel = self.dialog.body
        label(panel, "업데이트 설치 프로그램을 열지 못했습니다.", fg=self.theme.muted).pack(anchor="w", pady=(px(8), px(15)))
        button = HoverButton(panel, "닫기", self.close_dialog, self.theme.paper, self.theme.selected, self.theme.ink, padding=(13, 6))
        button.pack(anchor="e")
        self.dialog.open(button)

    def close_dialog(self):
        dialog, self.dialog = self.dialog, None
        if dialog is not None:
            dialog.destroy()
        if not self.update_installer and not self.stop.is_set():
            self.show_pending_dialog()

    def cycle_dialog_focus(self, _=None, *, reverse=False):
        if self.dialog is not None:
            self.dialog.cycle_focus(reverse)
            return "break"

    def check_luna(self, operation, action, caption):
        if self.luna_operation:
            return
        self.luna_operation = operation
        self.luna_status.set(caption)
        self.render_luna()

        def run():
            try:
                result = action()
            except (CodexError, OSError, ValueError, KeyError, TypeError) as error:
                result = error.args[0] if isinstance(error, CodexError) and error.args else "Codex 연결을 확인할 수 없습니다."
            self.events.put(("luna", result))

        Thread(target=run, daemon=True).start()

    def toggle_luna(self):
        if self.luna_operation == "login":
            self.login_cancel.set()
            self.luna_status.set("로그인 취소 중")
            self.render_luna()
        elif self.luna_signed_in:
            self.luna_effort = None
            self.update_translation_connection()
            self.luna_fallback = None
            self.assistant_efforts = ()
            self.update_assistant_settings()
            self.check_luna("logout", self.codex.logout, "연결 해제 중")
        else:
            self.login_cancel.clear()
            self.check_luna("login", lambda: self.codex.login(webbrowser.open, self.login_cancel),
                            "브라우저에서 ChatGPT 로그인을 완료하세요.")

    def render_luna(self):
        if self.luna_controls is None:
            return
        action, check = self.luna_controls
        busy = self.luna_operation is not None
        waiting = self.luna_operation == "login" and not self.login_cancel.is_set()
        caption = "로그인 취소" if self.luna_operation == "login" else (
            "연결 해제" if self.luna_signed_in else "ChatGPT 연결")
        action.configure(text=caption, state="normal" if not busy or waiting else "disabled")
        check.configure(state="disabled" if busy else "normal")

    def choose_language(self):
        self.language_back = self.current_page
        self.show("languages")

    def translation_settings(self):
        card = Card(self.view, height=300, inset=22)
        card.pack(fill="both", expand=True)
        body = card.body
        label(body, "결과 언어", size=16, weight="bold").pack(anchor="w")
        label(body, "번역 결과를 표시할 언어를 선택하세요.", fg=self.theme.muted).pack(anchor="w", pady=(px(5), px(13)))

        footer = tk.Frame(body, bg=self.theme.surface)
        footer.pack(side="bottom", fill="x", pady=(px(14), 0))
        error = label(footer, "", fg=self.theme.warning, wrap=290)
        error.pack(side="left", fill="x", expand=True)
        draft = self.target
        choices = {}

        def apply():
            try:
                save_target(draft)
            except OSError:
                error.configure(text=tr("설정을 저장하지 못했습니다. LiDAR 폴더에 쓰기 권한이 필요합니다."))
                return
            self.target = draft
            self.target_label.set(LANGUAGES[draft])
            self.refresh_model_labels()
            self.show(self.language_back)

        HoverButton(footer, "적용", apply, VIOLET, "#4e009e", BUTTON_TEXT).pack(side="right")
        HoverButton(footer, "취소", lambda: self.show(self.language_back), self.theme.paper, self.theme.hover, self.theme.ink).pack(
            side="right", padx=(0, px(8)))

        region = tk.Frame(body, bg=self.theme.surface)
        region.pack(fill="both", expand=True)
        canvas = tk.Canvas(region, bg=self.theme.surface, height=1, highlightthickness=0)
        scrollbar = tk.Scrollbar(region, command=canvas.yview, bg=self.theme.line, troughcolor=self.theme.paper,
                                 relief="flat", bd=0, highlightthickness=0, width=px(9))
        scrollbar.pack(side="right", fill="y", padx=(px(6), 0))
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        listing = tk.Frame(canvas, bg=self.theme.surface)
        content = canvas.create_window(0, 0, window=listing, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content, width=event.width))
        listing.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox(content)))
        for column in range(2):
            listing.columnconfigure(column, weight=1, uniform="languages")

        def wheel(event):
            canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"

        def style_choice(code):
            selected = code == draft
            button = choices[code]
            button.configure(text=f"{'●' if selected else '○'}   {tr(LANGUAGES[code])}")
            button.palette(self.theme.selected, self.theme.selected_hover, self.theme.accent) if selected else button.palette(self.theme.paper, self.theme.hover, self.theme.ink)

        def select(code):
            nonlocal draft
            previous, draft = draft, code
            style_choice(previous)
            if draft != previous:
                style_choice(draft)

        def reveal(button):
            y = button.winfo_y()
            top, height = canvas.canvasy(0), canvas.winfo_height()
            if y < top or y + button.winfo_height() > top + height:
                canvas.yview_moveto(y / max(1, listing.winfo_height()))

        canvas.bind("<MouseWheel>", wheel)
        listing.bind("<MouseWheel>", wheel)
        for index, code in enumerate(TARGET_LANGUAGES):
            button = choices[code] = HoverButton(listing, "", lambda value=code: select(value),
                                                 self.theme.paper, self.theme.hover, self.theme.ink, padding=(12, 8), anchor="w")
            button.grid(row=index // 2, column=index % 2, sticky="ew",
                        padx=(0, px(8) if index % 2 == 0 else 0), pady=(0, px(6)))
            button.bind("<MouseWheel>", wheel)
            button.bind("<FocusIn>", lambda event: reveal(event.widget))
            button.bind("<Return>", lambda event: event.widget.invoke())
            style_choice(code)
        selected = choices[draft]
        self.root.after_idle(lambda: selected.focus_set() if selected.winfo_exists() else None)

    def dismiss_settings(self, _):
        if self.dialog is not None:
            self.dialog.dismiss()
            return "break"
        if self.current_page == "languages":
            self.show(self.language_back)
            return "break"
        if self.current_page == "settings":
            self.show("dashboard")
            return "break"

    def render_translation(self):
        if self.detail_boxes is None:
            return
        values = self.latest_text
        if values is None:
            values = tr("아직 읽은 텍스트가 없습니다."), tr("번역 결과가 여기에 표시됩니다.")
        for box, value in zip(self.detail_boxes, values):
            shown = tr(value) if isinstance(value, Message) else value
            if box.get("1.0", "end-1c") == shown:
                continue
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", shown)
            box.configure(state="disabled")
        if self.translation_pending:
            self.translation_loading.show(before=self.detail_boxes[1])
        else:
            self.translation_loading.hide()

    def cancel_translation_preview(self):
        if self.translation_pending:
            self.translation_pending = False
            self.result_label.set("번역 결과")
            self.render_translation()

    def scan(self):
        watch_sessions(self.stop, lambda msg: self.events.put(("status", msg)),
                       lambda *values: self.events.put(("result", values)),
                       lambda connected: self.events.put(("connection", connected)),
                       target_language=lambda: self.target, cloud_translate=self.translate_text,
                       sync_startup=self.startup.sync,
                       options=lambda: (self.features, self.translation_config[0]),
                       text_preview=lambda source: self.events.put(("text-preview", source)),
                       prepare_translation=self.codex.translation_connection.prepare,
                       assistant_hooks=(self.assistant_config, self.ask_assistant,
                                        lambda reason: self.events.put(("assistant-setup", reason)),
                                        lambda *result: self.events.put(("assistant-result", result))))

    def ask_assistant(self, question, capture, locale, effort, cancel, tone):
        return self.codex.ask(question, capture, LANGUAGES[locale], effort, cancel, tone)

    def translate_text(self, source, target, local):
        provider, key = self.translation_config
        if self.stop.is_set() or "translation" not in self.features:
            return None
        if not provider:
            raise TranslationError("번역 모델을 정해주세요")
        if provider == "local":
            return local(), "Local"
        if provider == "luna":
            effort = self.luna_effort
            if not effort:
                raise TranslationError("설정에서 ChatGPT를 연결하세요")
            try:
                return self.run_luna(self.codex.translate, source, LANGUAGES[target], effort), "Luna"
            except (CodexError, OSError, KeyError, TypeError, ValueError):
                raise TranslationError("Luna에 연결하지 못했습니다") from None
        if not key:
            raise TranslationError("설정에 DeepL API 키를 입력하세요")
        return translate(key, source, target), "DeepL"

    def run_luna(self, operation, source, target, effort):
        fallback = self.luna_fallback
        try:
            return operation(source, target, effort)
        except UnsupportedReasoning:
            if effort != "none" or fallback != "low":
                raise
            self.events.put(("luna-effort", fallback))
            return operation(source, target, fallback)

    def refresh(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "result":
                    mode, source, output, *details = value
                    self.recent_result = value
                    self.recent_kind.set(mode)
                    self.recent_url = web_url(source) if mode == "QR" else None
                    self.relocalize_recent()
                    self.render_recent()
                    if mode != "QR":
                        self.translation_pending = False
                        target, provider = details
                        self.latest_text = source, output
                        caption = (Message("{language} 결과", language=Message(LANGUAGES[target]))
                                   if mode == "AI 번역" else Message(mode if mode == "AI 어시스턴트" else "인식 결과"))
                        self.result_label.set(Message("{caption} · {provider}", caption=caption, provider=provider)
                                              if provider and provider != "Luna" else caption)
                        self.render_translation()
                elif kind == "text-preview":
                    if value is None:
                        self.cancel_translation_preview()
                    else:
                        self.latest_text = value, ""
                        self.translation_pending = True
                        self.result_label.set("번역 중")
                        self.render_translation()
                elif kind == "luna":
                    self.luna_operation = None
                    if isinstance(value, str):
                        self.luna_status.set(value)
                    else:
                        self.luna_signed_in, efforts = value
                        self.assistant_efforts = efforts
                        self.luna_effort = efforts[0] if efforts else None
                        self.luna_fallback = "low" if "low" in efforts else None
                        self.luna_status.set("연결됨" if self.luna_signed_in else "ChatGPT 연결 안 됨")
                    self.update_translation_connection()
                    self.render_luna()
                    self.update_assistant_settings()
                elif kind == "assistant-setup":
                    self.assistant_label.set(value)
                    if self.dialog is None:
                        self.restore_window()
                        if value == "PC 앱에서 마이크 감도를 보정하세요.":
                            self.show_mic_calibration()
                        else:
                            self.go_to_assistant_settings()
                            self.show_assistant_settings()
                elif kind == "assistant-result":
                    self.assistant_history.appendleft((value[0], value[1]))
                    try:
                        save_settings(assistant_history=list(self.assistant_history))
                    except OSError:
                        self.status.set("질문 기록을 저장하지 못했습니다.")
                    if self.current_page == "assistant":
                        self.render_assistant_history()
                elif kind == "mic-calibration":
                    token, speaking, result = value
                    if token is self.calibration_cancel and self.calibration_receiver is not None:
                        self.calibration_receiver(speaking, result)
                elif kind == "luna-effort":
                    if self.luna_effort == "none" and self.luna_fallback == value:
                        self.luna_effort = value
                elif kind == "model-progress":
                    self.local_status.set(Message("다운로드 중 · {value}%", value=value))
                elif kind == "model-finished":
                    self.local_downloading = False
                    self.local_status.set("" if value else "다운로드에 실패했습니다. 다시 시도하세요.")
                    if self.translation_config[0] == "local":
                        self.render_models()
                elif kind == "startup":
                    desired, status = value
                    if desired == self.startup_preference:
                        self.startup_status.set(status)
                elif kind == "update":
                    self.show_update(value)
                elif kind == "update-progress":
                    self.update_progress.set(Message("설치 파일 다운로드 중 · {value}%", value=value))
                elif kind == "update-finished":
                    self.update_finished(value)
                    if self.stop.is_set():
                        return
                elif kind == "quit" or (kind == "tray" and value == "quit"):
                    self.close()
                    return
                elif kind == "tray":
                    self.restore_window()
                elif kind == "connection":
                    if not value:
                        self.cancel_translation_preview()
                    self.connection.set("SteamVR 연결됨" if value else "SteamVR 연결 안 됨")
                    self.scan_state.set(("스캔 대기" if value else "대기 중") if "qr" in self.features else "꺼짐")
                    self.status.set("")
                else:
                    self.status.set("" if value.startswith("제스처 대기") else value)
                    state = "스캔 대기"
                    if value.startswith("조준"):
                        state = "조준 중"
                    elif value.startswith(("QR 인식 ·", "텍스트 인식 중")):
                        state = "인식 중"
                    if "qr" in self.features:
                        self.scan_state.set(state)
        except Empty:
            pass
        self.refresh_job = self.root.after(70, self.refresh)

    @staticmethod
    def summary(text):
        flat = " ".join(text.split())
        return flat if len(flat) <= 105 else flat[:102] + "…"

    def hide_window(self):
        if self.tray is not None and self.tray.visible:
            self.root.withdraw()
        else:
            self.root.iconify()

    def restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_window_mapped(self, event):
        if event.widget == self.root:
            self.root.after_idle(self.show_pending_dialog)

    def show_pending_dialog(self):
        if self.stop.is_set() or not self.root.winfo_viewable():
            return
        self.show_startup_prompt()
        if self.dialog is None and self.pending_update:
            release, self.pending_update = self.pending_update, None
            self.show_update(release)

    def close(self):
        self.stop.set()
        translation = self.codex.translation_connection
        translation.configure(False)
        if self.calibration_cancel is not None:
            self.calibration_cancel.set()
        if self.tray is not None:
            self.tray.close()
            self.tray = None
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        self.page_motion.cancel()
        if self.worker.is_alive() or (translation.worker is not None and translation.worker.is_alive()):
            self.root.withdraw()
            self.root.after(30, self.close)
        else:
            self.root.destroy()


def main():
    if sys.argv[1:] == ["--unregister-steamvr"]:
        unregister()
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    installer_error = False
    while True:
        instance = kernel.CreateMutexW(None, 0, r"Local\LiDAR_0E19C5A3_A965_496D_AC19_92666A80AB6D")
        if instance and ctypes.get_last_error() == 183:
            kernel.CloseHandle(instance)
            show_existing()
            return
        try:
            prepare_desktop()
            root = tk.Tk()
            configure_scale(root)
            app = ScannerWindow(root)
            if installer_error:
                root.after_idle(app.show_installer_error)
            root.mainloop()
        finally:
            if instance:
                kernel.CloseHandle(instance)
        if not app.update_installer:
            return
        try:
            subprocess.Popen([str(app.update_installer)], cwd=app.update_installer.parent)
        except OSError:
            installer_error = True
        else:
            return


if __name__ == "__main__":
    main()
