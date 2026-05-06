import ctypes
import sys
import threading
from ctypes import wintypes

from app_config import AudioSettings, DictationSettings
from dictation_session import listen


HOTKEY_ID_TOGGLE_LISTENING = 1
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_SPACE = 0x20
WM_HOTKEY = 0x0312


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    # RegisterHotKey posts WM_HOTKEY messages into a standard Win32 message
    # queue. Keeping the MSG layout explicit lets Python receive that queue
    # without adding a GUI framework or tray icon yet.
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class HotkeyDictationApp:
    def __init__(
        self,
        language: str,
        audio_settings: AudioSettings,
        dictation_settings: DictationSettings,
    ):
        self.language = language
        self.audio_settings = audio_settings
        self.dictation_settings = dictation_settings
        self._user32 = ctypes.windll.user32
        self._stop_event: threading.Event | None = None
        self._worker: threading.Thread | None = None

        # ctypes signatures are declared here because the hotkey loop is the
        # only owner of RegisterHotKey/GetMessageW. Keeping this local makes the
        # keyboard control behavior easy to audit without scanning other files.
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.GetMessageW.restype = wintypes.BOOL

    def run(self) -> None:
        # RegisterHotKey asks Windows to deliver Ctrl+Alt+Space even when another
        # app has focus. This keeps dictation control global without injecting
        # polling code into the Google streaming loop.
        if not self._user32.RegisterHotKey(
            None,
            HOTKEY_ID_TOGGLE_LISTENING,
            MOD_CONTROL | MOD_ALT,
            VK_SPACE,
        ):
            raise ctypes.WinError()

        print("Status: Idle. Press Ctrl+Alt+Space to start or pause.")
        print("Press Ctrl+C in this terminal to exit.\n")

        msg = MSG()
        try:
            while True:
                message_result = self._user32.GetMessageW(
                    ctypes.byref(msg), None, 0, 0
                )
                if message_result == -1:
                    raise ctypes.WinError()
                if message_result == 0:
                    break
                if (
                    msg.message == WM_HOTKEY
                    and msg.wParam == HOTKEY_ID_TOGGLE_LISTENING
                ):
                    self.toggle_listening()
        finally:
            if self._stop_event is not None:
                self._stop_event.set()
            if self._worker is not None and self._worker.is_alive():
                self._worker.join(timeout=5)
            self._user32.UnregisterHotKey(None, HOTKEY_ID_TOGGLE_LISTENING)

    def toggle_listening(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            # Stopping is done by signalling the microphone generator to finish.
            # That prevents new audio chunks from being sent to Google after the
            # user pauses dictation.
            if self._stop_event is not None:
                self._stop_event.set()
            print("Status: Stopping current dictation session...")
            return

        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self.run_listening_session, daemon=True)
        self._worker.start()
        print("Status: Listening. Press Ctrl+Alt+Space to pause.")

    def run_listening_session(self) -> None:
        try:
            listen(
                self.language,
                self.audio_settings,
                self.dictation_settings,
                self._stop_event,
            )
        except Exception as exc:
            print(f"\nDictation session error: {exc}", file=sys.stderr, flush=True)
        finally:
            print("Status: Idle. Press Ctrl+Alt+Space to start.")
