import logging
import queue
import sys
import threading

import sounddevice as sd

from app_config import AudioSettings

logger = logging.getLogger(__name__)


class MicrophoneStream:
    # This class is deliberately only responsible for audio capture. It does not
    # know about Google STT, final transcripts, or paste behavior, which keeps
    # review of microphone lifecycle and threading boundaries small.
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
        # sounddevice calls _callback from PortAudio's audio thread. The callback
        # must stay lightweight, so it only copies raw bytes into a queue; all
        # Google/network work happens later in generator().
        if self.settings.device is None:
            default_device = sd.query_devices(kind="input")
            logger.info(
                "Using default input device: %s (index %s)",
                default_device["name"],
                default_device["index"],
            )
            print(
                "Using default input device: "
                f"{default_device['name']} (index {default_device['index']})"
            )
        else:
            logger.info("Using configured input device index: %s", self.settings.device)
            print(f"Using configured input device index: {self.settings.device}")

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
        # Putting None into the queue wakes generator() if it is blocked waiting
        # for audio, allowing stream shutdown to finish deterministically.
        if self._stream is not None:
            self._stream.__exit__(exc_type, exc_value, traceback)
        self.closed = True
        self._queue.put(None)

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Audio callback status: %s", status)
            print(f"\nAudio warning: {status}", file=sys.stderr)
        # bytes(indata) copies the PortAudio buffer before the callback returns.
        # Without the copy, later code could read memory that PortAudio has
        # already reused for a newer audio block.
        self._queue.put(bytes(indata))

    def generator(self):
        # Google streaming_recognize consumes an iterator of byte chunks. This
        # generator is therefore the bridge from the PortAudio callback thread to
        # the synchronous Google client call.
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
                # Drain immediately available chunks into one request. This
                # lowers request overhead while still keeping latency bounded by
                # the first blocking queue wait above.
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
