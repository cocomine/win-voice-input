import argparse
import queue
import sys
from dataclasses import dataclass

import sounddevice as sd
from google.cloud import speech


DEFAULT_LANGUAGE = "yue-Hant-HK"
DEFAULT_RATE = 16000
DEFAULT_CHUNK_MS = 100


@dataclass(frozen=True)
class AudioSettings:
    rate: int
    chunk_ms: int
    device: int | None

    @property
    def frames_per_chunk(self) -> int:
        return int(self.rate * self.chunk_ms / 1000)


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


def listen(language: str, settings: AudioSettings) -> None:
    client = speech.SpeechClient()

    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=settings.rate,
        language_code=language,
        enable_automatic_punctuation=True,
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=True,
        single_utterance=False,
    )

    print("Listening. Press Ctrl+C to stop.\n")

    with MicrophoneStream(settings) as stream:
        requests = build_requests(stream.generator())
        responses = client.streaming_recognize(streaming_config, requests)

        for response in responses:
            if not response.results:
                continue

            result = response.results[0]
            if not result.alternatives:
                continue

            transcript = result.alternatives[0].transcript.strip()
            if not transcript:
                continue

            prefix = "FINAL" if result.is_final else "..."
            end = "\n" if result.is_final else "\r"
            print(f"{prefix}: {transcript}", end=end, flush=True)


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_devices:
        list_devices()
        return 0

    settings = AudioSettings(
        rate=args.rate,
        chunk_ms=args.chunk_ms,
        device=args.device,
    )

    try:
        listen(args.language, settings)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
