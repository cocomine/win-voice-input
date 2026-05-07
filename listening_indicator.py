import ctypes
import sys
import threading
from ctypes import wintypes

LRESULT = wintypes.LPARAM
UINT_PTR = wintypes.WPARAM


class POINT(ctypes.Structure):
    # ClientToScreen writes into POINT, so the Win32 layout must match the C
    # structure exactly when converting caret coordinates into screen pixels.
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    # GUITHREADINFO returns the caret rectangle as a Win32 RECT. The indicator
    # uses the caret's lower-left point as the anchor for the listening bubble.
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class GUITHREADINFO(ctypes.Structure):
    # Microsoft documents rcCaret as client coordinates relative to hwndCaret,
    # so the value must be converted with ClientToScreen before the overlay is
    # positioned on the desktop.
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


class MSG(ctypes.Structure):
    # The indicator owns a tiny Win32 message loop. MSG is required so the
    # background thread can receive timer and quit messages without a GUI toolkit.
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class PAINTSTRUCT(ctypes.Structure):
    # BeginPaint fills this structure while handling WM_PAINT. The overlay draws
    # its own icon, so it uses only hdc and rcPaint.
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


WindowProcedure = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSEXW(ctypes.Structure):
    # RegisterClassExW needs a WNDCLASSEXW definition before CreateWindowExW can
    # create the transparent indicator window.
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WindowProcedure),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HANDLE),
    ]


class ListeningIndicator:
    # The indicator is a visual status cue shown only during active listening.
    # It is implemented with Win32 instead of Tkinter because the bundled Python
    # runtime used by this project does not include Tcl/Tk files reliably.
    WINDOW_SIZE = 48
    CARET_GAP_PX = 8
    TIMER_ID = 1
    POLL_INTERVAL_MS = 80
    CLASS_NAME = "WinVoiceInputListeningIndicator"

    TRANSPARENT_COLOR = 0x00FF00FF
    BUBBLE_FILL_COLOR = 0x00FF840A
    BUBBLE_OUTLINE_COLOR = 0x00FFC57D
    ICON_COLOR = 0x00FFFFFF

    WM_DESTROY = 0x0002
    WM_PAINT = 0x000F
    WM_TIMER = 0x0113
    WM_QUIT = 0x0012
    WS_POPUP = 0x80000000
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_TOPMOST = 0x00000008
    WS_EX_NOACTIVATE = 0x08000000
    LWA_COLORKEY = 0x00000001
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    PS_SOLID = 0
    TRANSPARENT_BK_MODE = 1

    def __init__(self):
        self._visible_event = threading.Event()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._kernel32 = ctypes.windll.kernel32
        self._hwnd: int | None = None
        self._thread_id: int | None = None
        self._class_name = f"{self.CLASS_NAME}{id(self)}"
        self._window_procedure = WindowProcedure(self._handle_window_message)

        # ctypes signatures are declared once so Win32 failures appear as
        # predictable return values instead of argument-conversion surprises.
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetGUIThreadInfo.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(GUITHREADINFO),
        ]
        self._user32.GetGUIThreadInfo.restype = wintypes.BOOL
        self._user32.ClientToScreen.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(POINT),
        ]
        self._user32.ClientToScreen.restype = wintypes.BOOL
        self._user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self._user32.RegisterClassExW.restype = wintypes.ATOM
        self._user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.LPVOID,
        ]
        self._user32.CreateWindowExW.restype = wintypes.HWND
        self._user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.DefWindowProcW.restype = LRESULT
        self._user32.DestroyWindow.argtypes = [wintypes.HWND]
        self._user32.DestroyWindow.restype = wintypes.BOOL
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL
        self._user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND,
            wintypes.COLORREF,
            ctypes.c_byte,
            wintypes.DWORD,
        ]
        self._user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        self._user32.SetTimer.argtypes = [
            wintypes.HWND,
            UINT_PTR,
            wintypes.UINT,
            wintypes.LPVOID,
        ]
        self._user32.SetTimer.restype = UINT_PTR
        self._user32.KillTimer.argtypes = [wintypes.HWND, UINT_PTR]
        self._user32.KillTimer.restype = wintypes.BOOL
        self._user32.BeginPaint.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(PAINTSTRUCT),
        ]
        self._user32.BeginPaint.restype = wintypes.HDC
        self._user32.EndPaint.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(PAINTSTRUCT),
        ]
        self._user32.EndPaint.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.GetMessageW.restype = wintypes.BOOL
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self._user32.TranslateMessage.restype = wintypes.BOOL
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self._user32.DispatchMessageW.restype = LRESULT
        self._user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.PostThreadMessageW.restype = wintypes.BOOL
        self._user32.FillRect.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(RECT),
            wintypes.HBRUSH,
        ]
        self._user32.FillRect.restype = ctypes.c_int
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HANDLE
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self._gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self._gdi32.CreatePen.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            wintypes.COLORREF,
        ]
        self._gdi32.CreatePen.restype = wintypes.HPEN
        self._gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self._gdi32.SelectObject.restype = wintypes.HANDLE
        self._gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        self._gdi32.DeleteObject.restype = wintypes.BOOL
        self._gdi32.Ellipse.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._gdi32.Ellipse.restype = wintypes.BOOL
        self._gdi32.RoundRect.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._gdi32.RoundRect.restype = wintypes.BOOL
        self._gdi32.MoveToEx.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(POINT),
        ]
        self._gdi32.MoveToEx.restype = wintypes.BOOL
        self._gdi32.LineTo.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._gdi32.LineTo.restype = wintypes.BOOL
        self._gdi32.Arc.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._gdi32.Arc.restype = wintypes.BOOL
        self._gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self._gdi32.SetBkMode.restype = ctypes.c_int

        self._thread = threading.Thread(target=self._run_window, daemon=True)
        self._thread.start()

    def show(self) -> None:
        self._visible_event.set()

    def hide(self) -> None:
        self._visible_event.clear()

    def shutdown(self) -> None:
        self._visible_event.clear()
        self._stop_event.set()
        if self._thread_id is not None:
            self._user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run_window(self) -> None:
        self._thread_id = self._kernel32.GetCurrentThreadId()
        try:
            instance = self._kernel32.GetModuleHandleW(None)
            window_class = WNDCLASSEXW()
            window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
            window_class.lpfnWndProc = self._window_procedure
            window_class.hInstance = instance
            window_class.lpszClassName = self._class_name

            atom = self._user32.RegisterClassExW(ctypes.byref(window_class))
            if not atom:
                raise ctypes.WinError()

            ex_style = (
                self.WS_EX_TOPMOST
                | self.WS_EX_LAYERED
                | self.WS_EX_TRANSPARENT
                | self.WS_EX_TOOLWINDOW
                | self.WS_EX_NOACTIVATE
            )
            self._hwnd = self._user32.CreateWindowExW(
                ex_style,
                self._class_name,
                None,
                self.WS_POPUP,
                0,
                0,
                self.WINDOW_SIZE,
                self.WINDOW_SIZE,
                None,
                None,
                instance,
                None,
            )
            if not self._hwnd:
                raise ctypes.WinError()

            if not self._user32.SetLayeredWindowAttributes(
                self._hwnd,
                self.TRANSPARENT_COLOR,
                255,
                self.LWA_COLORKEY,
            ):
                raise ctypes.WinError()
            if not self._user32.SetTimer(
                self._hwnd,
                self.TIMER_ID,
                self.POLL_INTERVAL_MS,
                None,
            ):
                raise ctypes.WinError()

            self._ready_event.set()
            msg = MSG()
            while True:
                message_result = self._user32.GetMessageW(
                    ctypes.byref(msg),
                    None,
                    0,
                    0,
                )
                if message_result == -1:
                    raise ctypes.WinError()
                if message_result == 0:
                    break
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            print(
                f"\nListening indicator warning: {exc}",
                file=sys.stderr,
                flush=True,
            )
        finally:
            if self._hwnd is not None:
                self._user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            self._thread_id = None
            self._ready_event.set()

    def _handle_window_message(
        self,
        hwnd: int,
        message: int,
        w_param: int,
        l_param: int,
    ) -> int:
        if message == self.WM_TIMER:
            try:
                self._update_window()
            except Exception as exc:
                print(
                    f"\nListening indicator warning: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                if self._hwnd is not None:
                    self._user32.ShowWindow(self._hwnd, self.SW_HIDE)
            return 0
        if message == self.WM_PAINT:
            self._paint_window(hwnd)
            return 0
        if message == self.WM_DESTROY:
            self._user32.KillTimer(hwnd, self.TIMER_ID)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, w_param, l_param)

    def _update_window(self) -> None:
        if self._hwnd is None:
            return
        if self._stop_event.is_set():
            self._user32.DestroyWindow(self._hwnd)
            return
        if not self._visible_event.is_set():
            self._user32.ShowWindow(self._hwnd, self.SW_HIDE)
            return

        caret_position = self._get_caret_screen_position()
        if caret_position is None:
            # Some apps draw custom carets that are not exposed via the system
            # caret API. Hiding is safer than drawing the bubble in the wrong
            # place.
            self._user32.ShowWindow(self._hwnd, self.SW_HIDE)
            return

        x, y = caret_position
        if not self._user32.SetWindowPos(
            self._hwnd,
            wintypes.HWND(-1),
            x,
            y,
            self.WINDOW_SIZE,
            self.WINDOW_SIZE,
            self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW,
        ):
            print(
                f"\nListening indicator warning: {ctypes.WinError()}",
                file=sys.stderr,
                flush=True,
            )
            self._user32.ShowWindow(self._hwnd, self.SW_HIDE)
            return
        self._user32.ShowWindow(self._hwnd, self.SW_SHOWNOACTIVATE)

    def _paint_window(self, hwnd: int) -> None:
        paint = PAINTSTRUCT()
        hdc = self._user32.BeginPaint(hwnd, ctypes.byref(paint))
        if not hdc:
            return
        try:
            self._draw_indicator_icon(hdc)
        finally:
            self._user32.EndPaint(hwnd, ctypes.byref(paint))

    def _draw_indicator_icon(self, hdc: int) -> None:
        background = self._gdi32.CreateSolidBrush(self.TRANSPARENT_COLOR)
        bubble_brush = self._gdi32.CreateSolidBrush(self.BUBBLE_FILL_COLOR)
        outline_pen = self._gdi32.CreatePen(
            self.PS_SOLID,
            2,
            self.BUBBLE_OUTLINE_COLOR,
        )
        icon_pen = self._gdi32.CreatePen(self.PS_SOLID, 3, self.ICON_COLOR)
        icon_brush = self._gdi32.CreateSolidBrush(self.ICON_COLOR)
        try:
            full_rect = RECT(0, 0, self.WINDOW_SIZE, self.WINDOW_SIZE)
            self._user32.FillRect(hdc, ctypes.byref(full_rect), background)
            self._gdi32.SetBkMode(hdc, self.TRANSPARENT_BK_MODE)

            old_pen = self._gdi32.SelectObject(hdc, outline_pen)
            old_brush = self._gdi32.SelectObject(hdc, bubble_brush)
            self._gdi32.Ellipse(hdc, 3, 3, self.WINDOW_SIZE - 3, self.WINDOW_SIZE - 3)

            self._gdi32.SelectObject(hdc, icon_pen)
            self._gdi32.SelectObject(hdc, icon_brush)
            center = self.WINDOW_SIZE // 2
            self._gdi32.RoundRect(hdc, center - 5, 12, center + 5, 28, 8, 8)
            self._gdi32.Arc(
                hdc,
                center - 11,
                16,
                center + 11,
                35,
                center - 9,
                24,
                center + 9,
                24,
            )
            self._gdi32.MoveToEx(hdc, center, 34, None)
            self._gdi32.LineTo(hdc, center, 40)
            self._gdi32.MoveToEx(hdc, center - 7, 40, None)
            self._gdi32.LineTo(hdc, center + 7, 40)

            self._gdi32.SelectObject(hdc, old_pen)
            self._gdi32.SelectObject(hdc, old_brush)
        finally:
            for gdi_object in (
                background,
                bubble_brush,
                outline_pen,
                icon_pen,
                icon_brush,
            ):
                if gdi_object:
                    self._gdi32.DeleteObject(gdi_object)

    def _get_caret_screen_position(self) -> tuple[int, int] | None:
        foreground_hwnd = self._user32.GetForegroundWindow()
        if not foreground_hwnd:
            return None

        thread_id = self._user32.GetWindowThreadProcessId(foreground_hwnd, None)
        if not thread_id:
            return None

        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not self._user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None
        if not info.hwndCaret:
            return None

        caret_rect = info.rcCaret
        if (
            caret_rect.left == 0
            and caret_rect.top == 0
            and caret_rect.right == 0
            and caret_rect.bottom == 0
        ):
            return None

        point = POINT(caret_rect.left, caret_rect.bottom)
        if not self._user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
            return None

        return (
            point.x - (self.WINDOW_SIZE // 2),
            point.y + self.CARET_GAP_PX,
        )
