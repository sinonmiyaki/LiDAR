"""LiDAR desktop workspace for SteamVR scanning."""

import ctypes
from queue import Empty, SimpleQueue
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import tkinter as tk
from tkinter import font as tkfont, messagebox
import webbrowser

from PIL import Image, ImageTk

from codex_bridge import CodexBridge, CodexError
from vrqr import open_result, watch_sessions, web_url
from languages import LANGUAGES, TARGET_LANGUAGES, load_settings, load_target, save_settings, save_target
from steam_startup import MANIFEST, SteamStartup, unregister
from typography import FAMILY, SEMIBOLD, configure_scale, prepare_desktop, px
from updater import VERSION, download_installer, latest_release


PAPER, WHITE, INK = "#f4f4f5", "#ffffff", "#151515"
MUTED, LINE = "#626268", "#e8e8ea"
VIOLET, LAVENDER = "#6600CC", "#f0e6fa"


def label(parent, text="", *, variable=None, size=10, weight="normal", fg=INK, bg=None, wrap=0):
    options = {"textvariable": variable} if variable is not None else {"text": text}
    font = (SEMIBOLD, size) if weight == "semibold" else (FAMILY, size, weight)
    return tk.Label(parent, **options, font=font, fg=fg,
                    bg=bg or parent["bg"], anchor="w", justify="left", wraplength=px(wrap))


class HoverButton(tk.Canvas):
    def __init__(self, parent, text, command, bg, hover, fg, padding=(16, 9), *,
                 anchor="center", textvariable=None, wraplength=0, justify="center", disabledforeground=MUTED):
        super().__init__(parent, bg=parent["bg"], cursor="hand2", bd=0, highlightthickness=0, takefocus=True)
        self.command = command
        self.normal, self.hover = bg, hover
        self.foreground, self.disabled_foreground = fg, disabledforeground
        self.padding = tuple(px(value) for value in padding)
        self.anchor = anchor
        self.variable = textvariable
        self.caption = text
        self.button_font = (SEMIBOLD, 10)
        self.enabled = True
        self.animation = None
        self.background = self.create_polygon(0, 0, fill=bg, smooth=True, splinesteps=18)
        self.title = self.create_text(0, 0, text=text, anchor=anchor, fill=fg, font=self.button_font,
                                      width=wraplength, justify=justify)
        self.trace = textvariable.trace_add("write", lambda *_: self.update_text()) if textvariable is not None else None
        self.bind("<Configure>", self.draw)
        self.bind("<Enter>", lambda _: self.tint(self.hover) if self.enabled else None)
        self.bind("<Leave>", lambda _: self.tint(self.normal))
        self.bind("<FocusIn>", lambda _: self.itemconfigure(self.background, outline=VIOLET, width=1))
        self.bind("<FocusOut>", lambda _: self.itemconfigure(self.background, outline=""))
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
            self.caption = text
        if font is not None:
            self.button_font = font
        if state is not None:
            self.enabled = state != "disabled"
            super().configure(cursor="hand2" if self.enabled else "arrow", takefocus=self.enabled)
            self.itemconfigure(self.title, fill=self.foreground if self.enabled else self.disabled_foreground)
            if not self.enabled:
                self.tint(self.normal)
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
        w, h = event.width - 1, event.height - 1
        r = min(px(9), h / 2, w / 2)
        self.coords(self.background, 1, r, 1, 1, r, 1, w - r, 1, w, 1, w, r,
                    w, h - r, w, h, w - r, h, r, h, 1, h, 1, h - r)
        self.coords(self.title, self.padding[0] if self.anchor == "w" else event.width / 2, event.height / 2)

    def palette(self, bg, hover, fg):
        self.normal, self.hover = bg, hover
        self.foreground = fg
        self.itemconfigure(self.title, fill=fg if self.enabled else self.disabled_foreground)
        self.tint(bg)

    def tint(self, color):
        if self.animation is not None:
            self.after_cancel(self.animation)
        start = self.winfo_rgb(self.itemcget(self.background, "fill"))
        finish = self.winfo_rgb(color)

        def animate(step):
            mix = 1 - (1 - step / 5) ** 2
            value = "#" + "".join(f"{round((a + (b - a) * mix) / 257):02x}" for a, b in zip(start, finish))
            self.itemconfigure(self.background, fill=value)
            self.animation = self.after(16, animate, step + 1) if step < 5 else None

        animate(1)

    def destroy(self):
        if self.animation is not None:
            self.after_cancel(self.animation)
        if self.trace is not None:
            self.variable.trace_remove("write", self.trace)
        super().destroy()


class Card(tk.Canvas):
    def __init__(self, parent, *, height, color=WHITE, border=LINE, inset=18):
        super().__init__(parent, bg=PAPER, height=px(height), highlightthickness=0)
        self.inset = px(inset)
        self.background = self.create_polygon(0, 0, fill=color, outline=border, smooth=True, splinesteps=16)
        self.body = tk.Frame(self, bg=color)
        self.content = self.create_window(self.inset, self.inset, anchor="nw", window=self.body)
        self.bind("<Configure>", self.resize)

    def resize(self, event):
        w, h, r = event.width - 2, event.height - 2, px(15)
        self.coords(self.background, 1, r, 1, 1, r, 1, w - r, 1, w, 1, w, r,
                    w, h - r, w, h, w - r, h, r, h, 1, h, 1, h - r)
        self.itemconfigure(self.content, width=max(1, w - 2 * self.inset), height=max(1, h - 2 * self.inset))

    def fit_height(self):
        self.body.update_idletasks()
        self.configure(height=max(int(self["height"]), self.body.winfo_reqheight() + 2 * self.inset + 2))


class ScannerWindow:
    def __init__(self, root):
        self.root = root
        self.stop = Event()
        self.events = SimpleQueue()
        preferences = load_settings()
        self.startup_preference = preferences.get("steamvr_auto_start")
        self.startup_status = tk.StringVar(value="자동 실행 켜짐" if self.startup_preference else "자동 실행 꺼짐")
        self.startup_button = None
        self.startup = SteamStartup(lambda: self.startup_preference,
                                    lambda *status: self.events.put(("startup", status)))
        self.skipped_version = preferences.get("skipped_version")
        self.update_popup = None
        self.update_installer = None
        self.codex = CodexBridge(self.stop)
        self.login_cancel = Event()
        self.luna_enabled = Event()
        self.luna_signed_in = False
        self.luna_operation = None
        self.luna_status = tk.StringVar(value="연결 확인 중")
        self.luna_controls = None
        self.connection = tk.StringVar(value="SteamVR 연결 확인 중")
        self.scan_state = tk.StringVar(value="대기 중")
        self.status = tk.StringVar(value="")
        self.recent_kind = tk.StringVar(value="대기 중")
        self.recent_source = tk.StringVar(value="아직 스캔 결과가 없습니다.")
        self.recent_output = tk.StringVar(value="")
        self.recent_url = None
        self.recent_widgets = None
        self.latest_text = ("아직 읽은 텍스트가 없습니다.", "번역 결과가 여기에 표시됩니다.")
        self.target = load_target()
        self.target_label = tk.StringVar(value=LANGUAGES[self.target])
        self.result_label = tk.StringVar(value="번역 결과")
        self.detail_boxes = None
        self.current_page = None
        self.language_back = "dashboard"
        self.transition_job = None

        root.title("LiDAR")
        icon = Path(__file__).with_name("LiDAR.ico")
        if icon.is_file():
            root.iconbitmap(default=str(icon))
        root.geometry(f"{px(1080)}x{px(700)}")
        root.minsize(px(940), px(645))
        root.configure(bg=PAPER)
        root.option_add("*Font", (FAMILY, 10))

        sidebar = tk.Frame(root, width=px(188), bg=WHITE, highlightthickness=1, highlightbackground=LINE)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        with Image.open(Path(__file__).with_name("brand") / "lidar-wordmark.png") as original:
            logo = original.convert("RGBA")
        logo.thumbnail((px(158), px(55)), Image.Resampling.LANCZOS)
        self.wordmark = ImageTk.PhotoImage(logo, master=root)
        tk.Label(sidebar, image=self.wordmark, bg=WHITE, borderwidth=0).pack(
            anchor="w", padx=px(15), pady=(px(17), px(19)))

        label(sidebar, "워크스페이스", size=9, weight="semibold", fg=MUTED).pack(anchor="w", padx=px(19), pady=(0, px(8)))
        self.nav = {}
        for key, caption in (("dashboard", "▦   대시보드"), ("translation", "文   AI 번역"), ("assistant", "✦   AI 어시스턴트")):
            button = HoverButton(sidebar, caption, lambda page=key: self.show(page), WHITE, "#efeff1", INK, anchor="w")
            button.pack(fill="x", padx=px(12), pady=px(2))
            self.nav[key] = button
        self.nav["settings"] = HoverButton(sidebar, "⚙   설정", lambda: self.show("settings"),
                                            WHITE, "#efeff1", INK, anchor="w")
        self.nav["settings"].pack(side="bottom", fill="x", padx=px(12), pady=px(14))
        main = tk.Frame(root, bg=PAPER)
        main.pack(side="left", fill="both", expand=True, padx=px(26), pady=(px(22), px(18)))
        header = tk.Frame(main, bg=PAPER)
        header.pack(fill="x", pady=(0, px(15)))
        labels = tk.Frame(header, bg=PAPER)
        labels.pack(side="left", fill="x", expand=True)
        self.title = label(labels, "대시보드", size=27, weight="bold")
        self.title.pack(anchor="w", pady=(px(3), 0))
        self.subtitle = label(labels, "", size=10, fg=MUTED)
        indicator = tk.Frame(header, bg=WHITE, highlightthickness=1, highlightbackground=LINE)
        indicator.pack(side="right", anchor="n", pady=(px(16), 0))
        dot = tk.Canvas(indicator, width=px(10), height=px(10), bg=WHITE, highlightthickness=0)
        dot.pack(side="left", padx=(px(12), px(5)))
        mark = dot.create_oval(1, 1, px(9), px(9), fill=MUTED, outline="")
        label(indicator, variable=self.connection, size=10, weight="semibold").pack(
            side="left", padx=(0, px(12)), pady=px(8))
        self.connection.trace_add("write", lambda *_: dot.itemconfigure(
            mark, fill=VIOLET if self.connection.get() == "SteamVR 연결됨" else MUTED))
        viewport = tk.Frame(main, bg=PAPER)
        viewport.pack(fill="both", expand=True)
        self.view = tk.Frame(viewport, bg=PAPER)
        self.view.place(x=0, y=0, relwidth=1, relheight=1)
        footer = tk.Frame(main, bg=PAPER)
        label(footer, variable=self.status, fg=MUTED, size=9, wrap=690).pack(side="left", fill="x", expand=True)

        def update_footer(*_):
            if self.status.get():
                footer.pack(fill="x", pady=(px(12), 0))
            else:
                footer.pack_forget()

        self.status.trace_add("write", update_footer)

        self.show("dashboard")
        root.bind("<Escape>", self.dismiss_settings)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = Thread(target=self.scan, daemon=True)
        self.worker.start()
        self.refresh_job = root.after(70, self.refresh)
        self.check_luna("status", self.codex.status, "연결 확인 중")
        Thread(target=self.check_updates, daemon=True).start()

    def show(self, page):
        if page == self.current_page:
            return
        animate = self.current_page is not None
        self.current_page = page
        self.detail_boxes = None
        self.recent_widgets = None
        self.luna_controls = None
        self.startup_button = None
        for child in self.view.winfo_children():
            child.destroy()
        active_tab = self.language_back if page == "languages" else page
        for key, button in self.nav.items():
            button.palette(LAVENDER, "#e5d3f7", VIOLET) if key == active_tab else button.palette(WHITE, "#efeff1", INK)

        if page == "dashboard":
            self.title.configure(text="대시보드")
            self.subtitle.configure(text="손 제스처로 VR 화면의 QR과 텍스트를 읽습니다.")
            self.subtitle.pack(anchor="w", pady=(px(5), 0))
            self.dashboard()
        elif page == "translation":
            self.title.configure(text="AI 번역")
            self.subtitle.pack_forget()
            self.translation()
        elif page == "settings":
            self.title.configure(text="설정")
            self.subtitle.pack_forget()
            self.settings()
        elif page == "languages":
            self.title.configure(text="번역 언어 설정")
            self.subtitle.pack_forget()
            self.translation_settings()
        else:
            self.title.configure(text="AI 어시스턴트")
            self.subtitle.pack_forget()
            card = Card(self.view, height=145)
            card.pack(fill="x")
            label(card.body, "준비 중", size=19, weight="bold").pack(anchor="w", pady=px(24))
        if animate:
            self.slide_page()

    def slide_page(self):
        if self.transition_job is not None:
            self.root.after_cancel(self.transition_job)

        def draw(step):
            self.view.place_configure(y=px(8 * (1 - step / 7) ** 3))
            self.transition_job = self.root.after(16, draw, step + 1) if step < 7 else None

        draw(0)

    def dashboard(self):
        summary = tk.Frame(self.view, bg=PAPER)
        summary.pack(fill="x", pady=(0, px(13)))
        for column in range(3):
            summary.columnconfigure(column, weight=1, uniform="summary")
        tiles = (
            ("스캔 상태", self.scan_state, "QR 및 텍스트 인식", INK, WHITE, "#c8c8cb"),
            ("AI 번역", self.target_label, "원문 언어 자동 감지", WHITE, INK, MUTED),
            ("AI 어시스턴트", "준비 중", "", WHITE, INK, MUTED),
        )
        for index, (title, value, caption, bg, fg, sub) in enumerate(tiles):
            card = Card(summary, height=115, color=bg, border=bg if index == 0 else LINE)
            card.grid(row=0, column=index, sticky="nsew", padx=(0, px(12) if index < 2 else 0))
            if index == 1:
                header = tk.Frame(card.body, bg=WHITE)
                header.pack(fill="x")
                label(header, title, size=9, weight="semibold", fg=sub).pack(side="left")
                gear = HoverButton(header, "⚙", self.choose_language, PAPER, LAVENDER, VIOLET, padding=(7, 1))
                gear.configure(font=("Segoe UI Symbol", 12))
                gear.pack(side="right")
            else:
                label(card.body, title, size=9, weight="semibold", fg=sub).pack(anchor="w")
            label(card.body, value if isinstance(value, str) else "", variable=value if isinstance(value, tk.StringVar) else None,
                  size=19, weight="bold", fg=fg).pack(anchor="w", pady=(px(4), px(2)))
            if caption:
                label(card.body, caption, size=9, fg=sub).pack(anchor="w")
            card.fit_height()

        lower = tk.Frame(self.view, bg=PAPER)
        lower.pack(fill="both", expand=True)
        lower.columnconfigure(0, weight=5)
        lower.columnconfigure(1, weight=4)
        lower.rowconfigure(0, weight=1)
        guide = Card(lower, height=260)
        guide.grid(row=0, column=0, sticky="nsew", padx=(0, px(12)))
        label(guide.body, "스캔 제스처", size=16, weight="bold").pack(anchor="w")
        diagram = tk.Canvas(guide.body, height=px(74), bg="#f6f1fa", highlightthickness=0)
        diagram.pack(fill="x", pady=(px(11), px(13)))
        diagram.create_rectangle(px(26), px(14), px(100), px(59), outline="#a17cc2", dash=(3, 3))
        diagram.create_line(*(px(n) for n in (26, 35, 26, 59, 52, 59)), fill=VIOLET, width=px(3))
        diagram.create_line(*(px(n) for n in (74, 14, 100, 14, 100, 38)), fill=VIOLET, width=px(3))
        diagram.create_text(px(118), px(36), anchor="w", text="L자 프레임", font=(SEMIBOLD, 11), fill=INK)
        for number, step in (("01", "양손의 엄지와 검지로 L자를 만듭니다."),
                             ("02", "손 사이 창에 QR이나 텍스트를 맞춥니다."),
                             ("03", "자세를 풀어 읽습니다.")):
            row = tk.Frame(guide.body, bg=WHITE)
            row.pack(fill="x", pady=px(4))
            label(row, number, size=9, weight="semibold", fg=VIOLET).pack(side="left", padx=(0, px(10)))
            label(row, step, size=10).pack(side="left")

        recent = Card(lower, height=260)
        recent.grid(row=0, column=1, sticky="nsew")
        label(recent.body, "최근 스캔", size=16, weight="bold").pack(anchor="w")
        label(recent.body, variable=self.recent_kind, size=9, weight="semibold", fg=VIOLET).pack(anchor="w", pady=(px(16), px(8)))
        source = label(recent.body, variable=self.recent_source, size=10, fg=MUTED, wrap=242)
        link = HoverButton(recent.body, "", self.open_recent, WHITE, LAVENDER, VIOLET,
                           padding=(3, 2), textvariable=self.recent_source,
                           wraplength=px(242), anchor="w", justify="left")
        details = tk.Frame(recent.body, bg=WHITE)
        tk.Frame(details, bg=LINE, height=1).pack(fill="x", pady=px(14))
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

    def open_recent(self):
        if self.recent_url:
            try:
                self.status.set(self.summary(open_result(self.recent_url, activate=True)))
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
            label(card.body, "인식한 원문", variable=self.result_label if index else None,
                  size=16, weight="bold").pack(anchor="w", pady=(0, px(15)))
            box = tk.Text(card.body, bg=WHITE, fg=INK, bd=0, highlightthickness=0, wrap="word",
                          font=(FAMILY, 12), padx=0, pady=px(3), spacing2=px(4), spacing3=px(8))
            box.pack(fill="both", expand=True)
            boxes.append(box)
        self.detail_boxes = boxes
        self.render_translation()

    def settings(self):
        language = Card(self.view, height=90, inset=22)
        language.pack(fill="x", pady=(0, px(12)))
        change = HoverButton(language.body, "변경", self.choose_language, PAPER, LAVENDER, VIOLET)
        change.pack(side="right")
        label(language.body, "번역 언어", size=15, weight="bold").pack(anchor="w")
        label(language.body, variable=self.target_label, fg=MUTED).pack(anchor="w", pady=(px(4), 0))
        language.fit_height()

        card = Card(self.view, height=225, inset=22)
        card.pack(fill="x", pady=(0, px(12)))
        body = card.body
        label(body, "ChatGPT 연결", size=15, weight="bold").pack(anchor="w")
        label(body, "GPT-6 Luna · 추론 없음", size=11, weight="semibold").pack(anchor="w", pady=(px(10), 0))
        label(body, "연결하면 OCR로 읽은 텍스트를 Luna로 번역합니다. 연결이 끊기면 PC에서 번역합니다.",
              fg=MUTED, wrap=465).pack(anchor="w", pady=(px(5), 0))
        label(body, variable=self.luna_status, fg=VIOLET, wrap=465).pack(anchor="w", pady=(px(11), px(9)))
        actions = tk.Frame(body, bg=WHITE)
        actions.pack(fill="x")
        action = HoverButton(actions, "", self.toggle_luna, VIOLET, "#4e009e", WHITE,
                             disabledforeground="#dfc9f5")
        action.pack(side="left")
        check = HoverButton(actions, "상태 확인", lambda: self.check_luna("status", self.codex.status, "연결 확인 중"),
                            PAPER, LAVENDER, INK)
        check.pack(side="left", padx=(px(8), 0))
        self.luna_controls = action, check
        self.render_luna()
        card.fit_height()

        startup = Card(self.view, height=92, inset=22)
        startup.pack(fill="x")
        self.startup_button = HoverButton(startup.body, "", self.toggle_startup, PAPER, LAVENDER, VIOLET)
        self.startup_button.pack(side="right")
        label(startup.body, "SteamVR 기동 시 자동 실행", size=15, weight="bold").pack(anchor="w")
        label(startup.body, variable=self.startup_status, fg=MUTED).pack(anchor="w", pady=(px(4), 0))
        self.render_startup()
        startup.fit_height()
        label(self.view, f"LiDAR {VERSION}", fg=MUTED, size=9).pack(anchor="e", pady=(px(9), 0))

    def render_startup(self):
        if self.startup_button is not None:
            self.startup_button.configure(text="끄기" if self.startup_preference else "켜기",
                                          state="normal" if MANIFEST.is_file() else "disabled")

    def toggle_startup(self):
        desired = not self.startup_preference
        try:
            save_settings(steamvr_auto_start=desired)
        except OSError:
            self.startup_status.set("자동 실행 설정을 저장하지 못했습니다.")
            return
        self.startup_status.set("SteamVR가 실행되면 적용됩니다.")
        self.startup.applied = None
        self.startup_preference = desired
        self.render_startup()

    def check_updates(self):
        try:
            release = latest_release(self.skipped_version)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return
        if release:
            self.events.put(("update", release))

    def show_update(self, release):
        if self.update_popup is not None:
            return
        popup = self.update_popup = tk.Toplevel(self.root, bg=WHITE)
        popup.title("LiDAR 업데이트")
        popup.resizable(False, False)
        popup.transient(self.root)
        panel = tk.Frame(popup, bg=WHITE, padx=px(24), pady=px(21))
        panel.pack(fill="both", expand=True)
        label(panel, "새 업데이트가 있습니다", size=17, weight="bold").pack(anchor="w")
        caption = tk.StringVar(value=f"LiDAR {VERSION} → {release.version}")
        label(panel, variable=caption, fg=MUTED).pack(anchor="w", pady=(px(7), px(15)))
        row = tk.Frame(panel, bg=WHITE)
        row.pack(fill="x")

        def later():
            self.update_popup = None
            popup.destroy()

        def skip():
            try:
                save_settings(skipped_version=release.version)
            except OSError:
                caption.set("건너뛸 버전을 저장하지 못했습니다.")
                return
            self.skipped_version = release.version
            later()

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

        install = HoverButton(row, "지금 업데이트", download, VIOLET, "#4e009e", WHITE)
        bypass = HoverButton(row, "이번 버전 건너뛰기", skip, PAPER, LAVENDER, INK)
        defer = HoverButton(row, "나중에", later, PAPER, LAVENDER, INK)
        for button in (install, bypass, defer):
            button.pack(side="left", padx=(0, px(7)))

        def finish(path):
            if path is None:
                caption.set("업데이트를 다운로드하지 못했습니다. 다시 시도하세요.")
                for button in (install, bypass, defer):
                    button.configure(state="normal")
                return
            self.update_installer = path
            later()
            self.close()

        self.update_progress = caption
        self.update_finished = finish
        popup.protocol("WM_DELETE_WINDOW", lambda: defer.invoke())
        popup.bind("<Escape>", lambda _: defer.invoke())
        popup.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - popup.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - popup.winfo_reqheight()) // 2
        popup.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        popup.grab_set()
        install.focus_set()

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
                result = str(error) if isinstance(error, CodexError) else "Codex 연결을 확인할 수 없습니다."
            self.events.put(("luna", result))

        Thread(target=run, daemon=True).start()

    def toggle_luna(self):
        if self.luna_operation == "login":
            self.login_cancel.set()
            self.luna_status.set("로그인 취소 중")
            self.render_luna()
        elif self.luna_signed_in:
            self.luna_enabled.clear()
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
        label(body, "번역 결과를 표시할 언어를 선택하세요.", fg=MUTED).pack(anchor="w", pady=(px(5), px(13)))

        footer = tk.Frame(body, bg=WHITE)
        footer.pack(side="bottom", fill="x", pady=(px(14), 0))
        error = label(footer, "", fg="#8e1531", wrap=290)
        error.pack(side="left", fill="x", expand=True)
        draft = self.target
        choices = {}

        def apply():
            try:
                save_target(draft)
            except OSError:
                error.configure(text="설정을 저장하지 못했습니다. LiDAR 폴더에 쓰기 권한이 필요합니다.")
                return
            self.target = draft
            self.target_label.set(LANGUAGES[draft])
            self.show(self.language_back)

        HoverButton(footer, "적용", apply, VIOLET, "#4e009e", WHITE).pack(side="right")
        HoverButton(footer, "취소", lambda: self.show(self.language_back), PAPER, "#e6e6e8", INK).pack(
            side="right", padx=(0, px(8)))

        region = tk.Frame(body, bg=WHITE)
        region.pack(fill="both", expand=True)
        canvas = tk.Canvas(region, bg=WHITE, height=1, highlightthickness=0)
        scrollbar = tk.Scrollbar(region, command=canvas.yview, bg="#c4b4d7", troughcolor=PAPER,
                                 relief="flat", bd=0, highlightthickness=0, width=px(9))
        scrollbar.pack(side="right", fill="y", padx=(px(6), 0))
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        listing = tk.Frame(canvas, bg=WHITE)
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
            button.configure(text=f"{'●' if selected else '○'}   {LANGUAGES[code]}")
            button.palette(LAVENDER, "#e5d3f7", VIOLET) if selected else button.palette(PAPER, "#e9e9ec", INK)

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
                                                 PAPER, "#e9e9ec", INK, padding=(12, 8), anchor="w")
            button.grid(row=index // 2, column=index % 2, sticky="ew",
                        padx=(0, px(8) if index % 2 == 0 else 0), pady=(0, px(6)))
            button.bind("<MouseWheel>", wheel)
            button.bind("<FocusIn>", lambda event: reveal(event.widget))
            button.bind("<Return>", lambda event: event.widget.invoke())
            style_choice(code)
        selected = choices[draft]
        self.root.after_idle(lambda: selected.focus_set() if selected.winfo_exists() else None)

    def dismiss_settings(self, _):
        if self.current_page == "languages":
            self.show(self.language_back)
            return "break"
        if self.current_page == "settings":
            self.show("dashboard")
            return "break"

    def render_translation(self):
        if self.detail_boxes is None:
            return
        for box, value in zip(self.detail_boxes, self.latest_text):
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", value)
            box.configure(state="disabled")

    def scan(self):
        watch_sessions(self.stop, lambda msg: self.events.put(("status", msg)),
                       lambda *values: self.events.put(("result", values)),
                       lambda connected: self.events.put(("connection", connected)),
                       target_language=lambda: self.target, cloud_translate=self.translate_with_luna,
                       sync_startup=self.startup.sync)

    def translate_with_luna(self, source, target_language):
        if self.luna_enabled.is_set() and not self.stop.is_set():
            return self.codex.translate(source, target_language)
        return None

    def refresh(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "result":
                    mode, source, output, *details = value
                    self.recent_kind.set(mode)
                    self.recent_source.set(self.summary(source))
                    self.recent_url = web_url(source) if mode == "QR" else None
                    self.recent_output.set(self.summary(output.removesuffix(f" · {source}") if self.recent_url else output))
                    self.render_recent()
                    if mode != "QR":
                        self.latest_text = source, output
                        target, provider = details
                        caption = f"{LANGUAGES[target]} 결과" if mode == "AI 번역" else "인식 결과"
                        self.result_label.set(f"{caption} · Luna" if provider == "Luna" else caption)
                        self.render_translation()
                elif kind == "luna":
                    self.luna_operation = None
                    if isinstance(value, str):
                        self.luna_status.set(value)
                    else:
                        self.luna_signed_in, available = value
                        if available:
                            self.luna_enabled.set()
                            self.luna_status.set("연결됨 · Luna 사용 중")
                        else:
                            self.luna_enabled.clear()
                            self.luna_status.set("연결됨 · 이 계정에서 Luna를 사용할 수 없습니다."
                                                 if self.luna_signed_in else "연결 안 됨 · PC 번역 사용 중")
                    self.render_luna()
                elif kind == "startup":
                    desired, status = value
                    if desired == self.startup_preference:
                        self.startup_status.set(status)
                elif kind == "update":
                    self.show_update(value)
                elif kind == "update-progress":
                    self.update_progress.set(f"설치 파일 다운로드 중 · {value}%")
                elif kind == "update-finished":
                    self.update_finished(value)
                    if self.stop.is_set():
                        return
                elif kind == "connection":
                    self.connection.set("SteamVR 연결됨" if value else "SteamVR 연결 안 됨")
                    self.scan_state.set("스캔 대기" if value else "대기 중")
                    self.status.set("제스처를 기다리고 있습니다" if value else "")
                else:
                    self.status.set(self.summary(value))
                    state = "스캔 대기"
                    if value.startswith("조준"):
                        state = "조준 중"
                    elif value.startswith(("QR 인식 ·", "텍스트 인식 및")):
                        state = "인식 중"
                    self.scan_state.set(state)
        except Empty:
            pass
        self.refresh_job = self.root.after(70, self.refresh)

    @staticmethod
    def summary(text):
        flat = " ".join(text.split())
        return flat if len(flat) <= 105 else flat[:102] + "…"

    def close(self):
        self.stop.set()
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        if self.transition_job is not None:
            self.root.after_cancel(self.transition_job)
            self.transition_job = None
        if self.worker.is_alive():
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
    instance = kernel.CreateMutexW(None, 0, r"Local\LiDAR_0E19C5A3_A965_496D_AC19_92666A80AB6D")
    if instance and ctypes.get_last_error() == 183:
        kernel.CloseHandle(instance)
        return
    prepare_desktop()
    root = tk.Tk()
    configure_scale(root)
    app = ScannerWindow(root)
    try:
        root.mainloop()
    finally:
        if instance:
            kernel.CloseHandle(instance)
    if app.update_installer:
        try:
            subprocess.Popen([str(app.update_installer)], cwd=app.update_installer.parent)
        except OSError:
            messagebox.showerror("LiDAR", "업데이트 설치 프로그램을 열지 못했습니다.")


if __name__ == "__main__":
    main()
