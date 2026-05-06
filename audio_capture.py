import queue
import sys
import threading

import sounddevice as sd

from app_config import AudioSettings


class MicrophoneStream:
    def __init__(
        self,
        settings: AudioSettings,
        stop_event: threading.Event | None = None,
    ):
        self.settings = settings
        self.stop_event = stop_event
        self.closed = True
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stream = None

    def __enter__(self):
        self.closed = False
        self._stream = sd.RawInputStream(
            samplerate=self.settings.rate,
            blocksize=int(self.settings.rate * self.settings.chunk_ms / 1000),
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
            if self.stop_event is not None and self.stop_event.is_set():
                return

            # A timeout is required because V3 can pause from a hotkey thread.
            # Without it, the generator could block forever waiting for the next
            # microphone buffer after the user has already asked it to stop.
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                return
            data = [chunk]

            while True:
                if self.stop_event is not None and self.stop_event.is_set():
                    return
                try:
                    chunk = self._queue.get(block=False)
                except queue.Empty:
                    break
                if chunk is None:
                    return
                data.append(chunk)

            yield b"".join(data)
