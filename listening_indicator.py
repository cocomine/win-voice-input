import ctypes
import sys
import threading
import traceback
from ctypes import wintypes

from win32_message_types import MSG, POINT

LRESULT = wintypes.LPARAM
UINT_PTR = wintypes.WPARAM


class RECT(ctypes.Structure):
    # RECT is used both by SystemParametersInfoW to describe the desktop work
    # area and by GDI drawing calls to describe the status window paint region.
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class PAINTSTRUCT(ctypes.Structure):
    # BeginPaint fills this structure while handling WM_PAINT. The listening
    # status window draws its own panel, icon, and text, so it uses hdc/rcPaint.
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
    # create the listening status window.
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
    # It is a small independent Win32 status window rather than a caret overlay:
    # browser-rendered text fields often draw their own caret and do not expose
    # a reliable system caret position, while a separate window remains visible
    # across Notepad, browsers, and other app-rendered input surfaces.
    WINDOW_WIDTH = 176
    WINDOW_HEIGHT = 56
    WORK_AREA_BOTTOM_GAP_PX = 24
    TIMER_ID = 1
    POLL_INTERVAL_MS = 120
    STARTUP_TIMEOUT_SECONDS = 5
    SHUTDOWN_TIMEOUT_SECONDS = 2
    CLASS_NAME = "WinVoiceInputListeningIndicator"
    STATUS_TEXT = "Listening"

    # Win32 COLORREF values are 0x00BBGGRR, not RGB. The constants below are:
    # dark panel fill, subtle panel border, blue microphone circle, pale-blue
    # circle outline, and white text/icon. Keeping that convention documented
    # prevents accidental RGB-style edits from producing the wrong colors.
    PANEL_FILL_COLOR = 0x00302A24
    PANEL_BORDER_COLOR = 0x006B5F55
    BUBBLE_FILL_COLOR = 0x00FF840A
    BUBBLE_OUTLINE_COLOR = 0x00FFC57D
    CONTENT_COLOR = 0x00FFFFFF

    WM_DESTROY = 0x0002
    WM_PAINT = 0x000F
    WM_TIMER = 0x0113
    WM_QUIT = 0x0012
    WS_POPUP = 0x80000000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_TOPMOST = 0x00000008
    WS_EX_NOACTIVATE = 0x08000000
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    PS_SOLID = 0
    TRANSPARENT_BK_MODE = 1
    SPI_GETWORKAREA = 0x0030
    DT_LEFT = 0x0000
    DT_VCENTER = 0x0004
    DT_SINGLELINE = 0x0020

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
        self._user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(RECT),
            wintypes.UINT,
        ]
        self._user32.SystemParametersInfoW.restype = wintypes.BOOL
        self._user32.DrawTextW.argtypes = [
            wintypes.HDC,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(RECT),
            wintypes.UINT,
        ]
        self._user32.DrawTextW.restype = ctypes.c_int
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
        self._gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self._gdi32.SetTextColor.restype = wintypes.COLORREF

        self._thread = threading.Thread(target=self._run_window, daemon=True)
        self._thread.start()
        if not self._ready_event.wait(self.STARTUP_TIMEOUT_SECONDS):
            raise RuntimeError(
                "Timed out while creating the listening indicator window."
            )

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
            # The status window is a background visual cue. Shutdown waits
            # briefly so normal exits clean up the Win32 window, but it does not
            # block app exit forever if Windows message dispatch is tearing down.
            self._thread.join(timeout=self.SHUTDOWN_TIMEOUT_SECONDS)

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

            ex_style = self.WS_EX_TOPMOST | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE
            self._hwnd = self._user32.CreateWindowExW(
                ex_style,
                self._class_name,
                None,
                self.WS_POPUP,
                0,
                0,
                self.WINDOW_WIDTH,
                self.WINDOW_HEIGHT,
                None,
                None,
                instance,
                None,
            )
            if not self._hwnd:
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
            self._print_exception_warning(exc)
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
                self._print_exception_warning(exc)
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

    def _print_exception_warning(self, exc: Exception) -> None:
        print(
            "\nListening indicator warning "
            f"({type(exc).__name__}): {exc}\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )

    def _update_window(self) -> None:
        if self._hwnd is None:
            return
        if self._stop_event.is_set():
            self._user32.DestroyWindow(self._hwnd)
            return
        if not self._visible_event.is_set():
            self._user32.ShowWindow(self._hwnd, self.SW_HIDE)
            return

        work_area = RECT()
        if not self._user32.SystemParametersInfoW(
            self.SPI_GETWORKAREA,
            0,
            ctypes.byref(work_area),
            0,
        ):
            raise ctypes.WinError()

        work_width = work_area.right - work_area.left
        x = work_area.left + ((work_width - self.WINDOW_WIDTH) // 2)
        y = work_area.bottom - self.WINDOW_HEIGHT - self.WORK_AREA_BOTTOM_GAP_PX
        if not self._user32.SetWindowPos(
            self._hwnd,
            wintypes.HWND(-1),
            x,
            y,
            self.WINDOW_WIDTH,
            self.WINDOW_HEIGHT,
            self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW,
        ):
            raise ctypes.WinError()
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
        panel_brush = self._gdi32.CreateSolidBrush(self.PANEL_FILL_COLOR)
        panel_pen = self._gdi32.CreatePen(
            self.PS_SOLID,
            1,
            self.PANEL_BORDER_COLOR,
        )
        bubble_brush = self._gdi32.CreateSolidBrush(self.BUBBLE_FILL_COLOR)
        outline_pen = self._gdi32.CreatePen(
            self.PS_SOLID,
            2,
            self.BUBBLE_OUTLINE_COLOR,
        )
        icon_pen = self._gdi32.CreatePen(self.PS_SOLID, 3, self.CONTENT_COLOR)
        icon_brush = self._gdi32.CreateSolidBrush(self.CONTENT_COLOR)
        try:
            full_rect = RECT(0, 0, self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
            self._user32.FillRect(hdc, ctypes.byref(full_rect), panel_brush)
            self._gdi32.SetBkMode(hdc, self.TRANSPARENT_BK_MODE)

            old_pen = self._gdi32.SelectObject(hdc, panel_pen)
            old_brush = self._gdi32.SelectObject(hdc, panel_brush)
            self._gdi32.RoundRect(
                hdc,
                0,
                0,
                self.WINDOW_WIDTH,
                self.WINDOW_HEIGHT,
                10,
                10,
            )

            self._gdi32.SelectObject(hdc, outline_pen)
            self._gdi32.SelectObject(hdc, bubble_brush)
            self._gdi32.Ellipse(hdc, 14, 10, 50, 46)

            center = 32
            self._gdi32.SelectObject(hdc, icon_pen)
            self._gdi32.SelectObject(hdc, icon_brush)
            self._gdi32.RoundRect(hdc, center - 5, 18, center + 5, 31, 8, 8)
            self._gdi32.Arc(
                hdc,
                center - 11,
                21,
                center + 11,
                39,
                center - 9,
                29,
                center + 9,
                29,
            )
            self._gdi32.MoveToEx(hdc, center, 38, None)
            self._gdi32.LineTo(hdc, center, 43)
            self._gdi32.MoveToEx(hdc, center - 7, 43, None)
            self._gdi32.LineTo(hdc, center + 7, 43)

            text_rect = RECT(64, 0, self.WINDOW_WIDTH - 14, self.WINDOW_HEIGHT)
            self._gdi32.SetTextColor(hdc, self.CONTENT_COLOR)
            self._user32.DrawTextW(
                hdc,
                self.STATUS_TEXT,
                -1,
                ctypes.byref(text_rect),
                self.DT_LEFT | self.DT_SINGLELINE | self.DT_VCENTER,
            )

            self._gdi32.SelectObject(hdc, old_pen)
            self._gdi32.SelectObject(hdc, old_brush)
        finally:
            for gdi_object in (
                panel_brush,
                panel_pen,
                bubble_brush,
                outline_pen,
                icon_pen,
                icon_brush,
            ):
                if gdi_object:
                    self._gdi32.DeleteObject(gdi_object)
