import argparse
import ctypes
import queue
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

import sounddevice as sd
from google.cloud import speech


DEFAULT_LANGUAGE = "yue-Hant-HK"
DEFAULT_RATE = 16000
DEFAULT_CHUNK_MS = 100
DEFAULT_FINAL_DEDUPE_SECONDS = 0.8
VK_BACK = 0x08
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

PUNCTUATION_WORDS = {
    "逗號": "，",
    "句號": "。",
    "問號": "？",
    "感嘆號": "！",
    "感歎號": "！",
    "冒號": "：",
    "分號": "；",
    "頓號": "、",
    "空格": " ",
    "開括號": "（",
    "關括號": "）",
    "右括號": "）",
    "左括號": "（",
    "換行": "\n",
    "新一行": "\n",
}
BACKSPACE_COMMANDS = {"刪除", "退格", "刪走", "del", "delete"}
TRAILING_COMMAND_PUNCTUATION = "，。！？,.!?"


@dataclass(frozen=True)
class AudioSettings:
    rate: int
    chunk_ms: int
    device: int | None

    @property
    def frames_per_chunk(self) -> int:
        return int(self.rate * self.chunk_ms / 1000)


@dataclass(frozen=True)
class DictationSettings:
    paste_final: bool
    command_words: bool
    append_space: bool
    final_dedupe_seconds: float


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


class MicrophoneStream:
    def __init__(self, settings: AudioSettings):
        self.settings = settings
        self.closed = True
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stream = None

    def __enter__(self):
        self.closed = False
        self._stream = sd.RawInputStream(
            samplerate=self.settings.rate,
            blocksize=self.settings.frames_per_chunk,
            device=self.settings.device,
            dtype="int16",
            channels=1,
            callback=self._callback,
        )
        self._stream.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._stream is not None:
            self._stream.__exit__(exc_type, exc_value, traceback)
        self.closed = True
        self._queue.put(None)

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"\nAudio warning: {status}", file=sys.stderr)
        self._queue.put(bytes(indata))

    def generator(self):
        while not self.closed:
            chunk = self._queue.get()
            if chunk is None:
                return
            data = [chunk]

            while True:
                try:
                    chunk = self._queue.get(block=False)
                except queue.Empty:
                    break
                if chunk is None:
                    return
                data.append(chunk)

            yield b"".join(data)


def list_devices() -> None:
    print(sd.query_devices())


def build_requests(audio_generator):
    for content in audio_generator:
        yield speech.StreamingRecognizeRequest(audio_content=content)


class WindowsTextOutput:
    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._configure_winapi()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def _configure_winapi(self) -> None:
        self._user32.OpenClipboard.argtypes = [wintypes.HWND]
        self._user32.OpenClipboard.restype = wintypes.BOOL
        self._user32.EmptyClipboard.argtypes = []
        self._user32.EmptyClipboard.restype = wintypes.BOOL
        self._user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self._user32.SetClipboardData.restype = wintypes.HANDLE
        self._user32.CloseClipboard.argtypes = []
        self._user32.CloseClipboard.restype = wintypes.BOOL
        self._user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT
        self._kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self._kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        self._kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalUnlock.restype = wintypes.BOOL
        self._kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalFree.restype = wintypes.HGLOBAL

    def paste_text(self, text: str) -> None:
        self.set_clipboard_text(text)
        time.sleep(0.08)
        send_ctrl_v()

    def press_backspace(self) -> None:
        send_key(VK_BACK)

    def set_clipboard_text(self, text: str) -> None:
        encoded = text.encode("utf-16le") + b"\x00\x00"
        handle = self._kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not handle:
            raise ctypes.WinError()

        locked = self._kernel32.GlobalLock(handle)
        if not locked:
            self._kernel32.GlobalFree(handle)
            raise ctypes.WinError()

        ctypes.memmove(locked, encoded, len(encoded))
        self._kernel32.GlobalUnlock(handle)

        opened = False
        try:
            for _ in range(20):
                if self._user32.OpenClipboard(None):
                    opened = True
                    break
                time.sleep(0.025)
            if not opened:
                raise ctypes.WinError()

            if not self._user32.EmptyClipboard():
                raise ctypes.WinError()
            if not self._user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise ctypes.WinError()
            handle = None
        finally:
            if opened:
                self._user32.CloseClipboard()
            if handle:
                self._kernel32.GlobalFree(handle)


def make_keyboard_input(vk_code: int, flags: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(
                wVk=vk_code,
                wScan=0,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )


def send_input(*inputs: INPUT) -> None:
    array_type = INPUT * len(inputs)
    array = array_type(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise ctypes.WinError()


def send_key(vk_code: int) -> None:
    send_input(make_keyboard_input(vk_code))
    time.sleep(0.02)
    send_input(make_keyboard_input(vk_code, KEYEVENTF_KEYUP))


def send_ctrl_v() -> None:
    send_input(make_keyboard_input(VK_CONTROL))
    time.sleep(0.02)
    try:
        send_input(make_keyboard_input(VK_V))
        time.sleep(0.02)
        send_input(make_keyboard_input(VK_V, KEYEVENTF_KEYUP))
    finally:
        time.sleep(0.02)
        send_input(make_keyboard_input(VK_CONTROL, KEYEVENTF_KEYUP))


def normalize_command(text: str) -> str:
    return "".join(text.split()).strip(TRAILING_COMMAND_PUNCTUATION).lower()


def normalize_final_key(text: str) -> str:
    return " ".join(text.split())


class FinalTranscriptDeduper:
    def __init__(self, window_seconds: float):
        self.window_seconds = window_seconds
        self._last_key = ""
        self._last_time = 0.0

    def should_output(self, transcript: str) -> bool:
        if self.window_seconds <= 0:
            return True

        now = time.monotonic()
        key = normalize_final_key(transcript)
        if key and key == self._last_key and now - self._last_time <= self.window_seconds:
            return False

        self._last_key = key
        self._last_time = now
        return True


def prepare_text(text: str, settings: DictationSettings) -> tuple[str, str | None]:
    if not settings.command_words:
        return add_spacing(text, settings.append_space), None

    command = normalize_command(text)
    if command in BACKSPACE_COMMANDS:
        return "", "backspace"

    prepared = text
    for word, replacement in PUNCTUATION_WORDS.items():
        prepared = prepared.replace(word, replacement)

    return add_spacing(prepared, settings.append_space), None


def add_spacing(text: str, append_space: bool) -> str:
    if not append_space or not text:
        return text
    if text[-1].isspace() or text[-1] in "，。！？、；：,.!?;:":
        return text
    return f"{text} "


def output_final_text(
    text_output: WindowsTextOutput | None,
    transcript: str,
    settings: DictationSettings,
) -> None:
    if text_output is None:
        return

    text, action = prepare_text(transcript, settings)
    try:
        if action == "backspace":
            text_output.press_backspace()
            return
        if text:
            text_output.paste_text(text)
    except OSError as exc:
        print(f"\nPaste warning: {exc}", file=sys.stderr, flush=True)


def listen(
    language: str,
    audio_settings: AudioSettings,
    dictation_settings: DictationSettings,
) -> None:
    client = speech.SpeechClient()

    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=audio_settings.rate,
        language_code=language,
        enable_automatic_punctuation=True,
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=True,
        single_utterance=False,
    )

    if dictation_settings.paste_final:
        print("Listening and pasting final text. Press Ctrl+C to stop.\n")
    else:
        print("Listening. Press Ctrl+C to stop.\n")

    text_output_context = (
        WindowsTextOutput() if dictation_settings.paste_final else None
    )
    text_output = (
        text_output_context.__enter__() if text_output_context is not None else None
    )

    try:
        stream_context = MicrophoneStream(audio_settings)
        stream = stream_context.__enter__()
        requests = build_requests(stream.generator())
        responses = client.streaming_recognize(streaming_config, requests)
        final_deduper = FinalTranscriptDeduper(
            dictation_settings.final_dedupe_seconds
        )

        for response in responses:
            if not response.results:
                continue

            for result in response.results:
                if not result.alternatives:
                    continue

                transcript = result.alternatives[0].transcript.strip()
                if not transcript:
                    continue

                prefix = "FINAL" if result.is_final else "..."
                end = "\n" if result.is_final else "\r"
                print(f"{prefix}: {transcript}", end=end, flush=True)

                if result.is_final:
                    if not final_deduper.should_output(transcript):
                        print("Skipped duplicate final paste.", flush=True)
                        continue
                    output_final_text(text_output, transcript, dictation_settings)
    finally:
        if "stream_context" in locals():
            stream_context.__exit__(*sys.exc_info())
        if text_output_context is not None:
            text_output_context.__exit__(*sys.exc_info())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream microphone audio to Google Speech-to-Text."
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help=f"BCP-47 language code. Default: {DEFAULT_LANGUAGE}",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=DEFAULT_RATE,
        help=f"Microphone sample rate. Default: {DEFAULT_RATE}",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=DEFAULT_CHUNK_MS,
        help=f"Audio chunk size in milliseconds. Default: {DEFAULT_CHUNK_MS}",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Input device index. Use --list-devices to find one.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Show audio devices and exit.",
    )
    parser.add_argument(
        "--paste-final",
        action="store_true",
        help="Paste final transcripts into the active app with Ctrl+V.",
    )
    parser.add_argument(
        "--command-words",
        action="store_true",
        help="Enable command words like 換行, 逗號, 句號, 刪除.",
    )
    parser.add_argument(
        "--no-command-words",
        action="store_true",
        help="Keep command words disabled. This is the default.",
    )
    parser.add_argument(
        "--append-space",
        action="store_true",
        help="Append a space after pasted final text when it has no ending punctuation.",
    )
    parser.add_argument(
        "--final-dedupe-seconds",
        type=float,
        default=DEFAULT_FINAL_DEDUPE_SECONDS,
        help=(
            "Skip pasting the same final transcript again inside this many "
            f"seconds. Default: {DEFAULT_FINAL_DEDUPE_SECONDS}"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_devices:
        list_devices()
        return 0

    audio_settings = AudioSettings(
        rate=args.rate,
        chunk_ms=args.chunk_ms,
        device=args.device,
    )
    dictation_settings = DictationSettings(
        paste_final=args.paste_final,
        command_words=args.command_words and not args.no_command_words,
        append_space=args.append_space,
        final_dedupe_seconds=args.final_dedupe_seconds,
    )

    try:
        listen(args.language, audio_settings, dictation_settings)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
