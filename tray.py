"""Windows notification-area icon for the LiDAR desktop window."""

from threading import Event, Thread

import win32api
import win32con as con
import win32gui as gui

from i18n import tr


WINDOW_CLASS = "LiDAR_0E19C5A3_NotificationArea"
TRAY_EVENT = con.WM_APP + 1
SHOW_WINDOW = con.WM_APP + 2
TASKBAR_CREATED = gui.RegisterWindowMessage("TaskbarCreated")
ICON_ID = 1


def show_existing():
    window = gui.FindWindow(WINDOW_CLASS, None)
    if window:
        try:
            gui.PostMessage(window, SHOW_WINDOW, 0, 0)
        except gui.error:
            pass


class TrayIcon:
    def __init__(self, icon_path, report):
        self.icon_path = icon_path
        self.report = report
        self.instance = win32api.GetModuleHandle(None)
        self.visible = False
        self.window = None
        self.icon = None
        self.ready = Event()
        self.thread = Thread(target=self.run, daemon=True, name="notification-area")
        self.thread.start()

    def run(self):
        window_class = gui.WNDCLASS()
        window_class.hInstance = self.instance
        window_class.lpszClassName = WINDOW_CLASS
        window_class.lpfnWndProc = self.handle_message
        registered = False
        try:
            gui.RegisterClass(window_class)
            registered = True
            self.window = gui.CreateWindow(WINDOW_CLASS, "LiDAR", 0, 0, 0, 0, 0, 0, 0, self.instance, None)
            self.icon = gui.LoadImage(0, str(self.icon_path), con.IMAGE_ICON, 0, 0,
                                      con.LR_LOADFROMFILE | con.LR_DEFAULTSIZE)
            self.show()
            self.ready.set()
            gui.PumpMessages()
        except gui.error:
            pass
        finally:
            self.remove_icon()
            if self.window:
                gui.DestroyWindow(self.window)
                self.window = None
            if self.icon:
                gui.DestroyIcon(self.icon)
            if registered:
                gui.UnregisterClass(WINDOW_CLASS, self.instance)
            self.ready.set()

    def show(self):
        if not self.visible:
            try:
                gui.Shell_NotifyIcon(gui.NIM_ADD, (self.window, ICON_ID,
                                    gui.NIF_ICON | gui.NIF_MESSAGE | gui.NIF_TIP, TRAY_EVENT, self.icon, "LiDAR"))
            except gui.error:
                return False
            self.visible = True
        return True

    def handle_message(self, hwnd, message, wparam, lparam):
        if message == TASKBAR_CREATED:
            self.visible = False
            self.show()
        elif message == SHOW_WINDOW or (message == TRAY_EVENT and lparam == con.WM_LBUTTONUP):
            self.report("restore")
        elif message == TRAY_EVENT and lparam == con.WM_RBUTTONUP:
            self.open_menu()
        elif message == con.WM_CLOSE:
            gui.PostQuitMessage(0)
            return 0
        return gui.DefWindowProc(hwnd, message, wparam, lparam)

    def open_menu(self):
        if not self.window:
            return
        menu = gui.CreatePopupMenu()
        try:
            gui.AppendMenu(menu, con.MF_STRING, 1, tr("LiDAR 열기"))
            gui.AppendMenu(menu, con.MF_STRING, 2, tr("종료"))
            try:
                gui.SetForegroundWindow(self.window)
            except gui.error:
                pass
            x, y = gui.GetCursorPos()
            try:
                command = gui.TrackPopupMenu(menu, con.TPM_RETURNCMD | con.TPM_NONOTIFY | con.TPM_RIGHTBUTTON,
                                             x, y, 0, self.window, None)
            except gui.error:
                return
            finally:
                gui.PostMessage(self.window, con.WM_NULL, 0, 0)
        finally:
            gui.DestroyMenu(menu)
        if command == 1:
            self.report("restore")
        elif command == 2:
            self.report("quit")

    def remove_icon(self):
        if self.visible:
            try:
                gui.Shell_NotifyIcon(gui.NIM_DELETE, (self.window, ICON_ID))
            except gui.error:
                pass
            self.visible = False
    def close(self):
        self.ready.wait(.3)
        if self.window:
            try:
                gui.PostMessage(self.window, con.WM_CLOSE, 0, 0)
            except gui.error:
                pass
        self.thread.join(.3)
