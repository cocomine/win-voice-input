import sys
import threading

from google.cloud import speech

from app_config import AudioSettings, DictationSettings
from audio_capture import MicrophoneStream
from text_processing import FinalTranscriptDeduper, prepare_text
from windows_text_output import WindowsTextOutput


def listen(
    language: str,
    audio_settings: AudioSettings,
    dictation_settings: DictationSettings,
    stop_event: threading.Event | None = None,
) -> None:
    client = speech.SpeechClient()

    # Google streaming recognition expects the first request metadata through
    # StreamingRecognitionConfig, then a continuous iterator of audio-content
    # requests. The microphone generator below is therefore the control point
    # for pausing: when it returns, no more audio is sent to Google.
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
        text_output = WindowsTextOutput()
    else:
        print("Listening. Press Ctrl+C to stop.\n")
        text_output = None

    stream_context = None
    try:
        stream_context = MicrophoneStream(audio_settings, stop_event)
        stream = stream_context.__enter__()
        responses = client.streaming_recognize(
            streaming_config,
            (
                speech.StreamingRecognizeRequest(audio_content=content)
                for content in stream.generator()
            ),
        )
        final_deduper = FinalTranscriptDeduper(
            dictation_settings.final_dedupe_seconds
        )

        for response in responses:
            if stop_event is not None and stop_event.is_set():
                break
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

                if not result.is_final:
                    continue

                if not final_deduper.should_output(transcript):
                    print("Skipped duplicate final paste.", flush=True)
                    continue

                if text_output is None:
                    continue

                # Final transcripts are the only text allowed to reach Windows
                # output. Interim transcripts are displayed for debugging but
                # never pasted, because Google can revise them as speech
                # recognition context improves.
                text, action = prepare_text(transcript, dictation_settings)
                try:
                    if action == "backspace":
                        text_output.press_backspace()
                    elif text:
                        text_output.paste_text(text)
                except OSError as exc:
                    print(f"\nPaste warning: {exc}", file=sys.stderr, flush=True)
    finally:
        if stream_context is not None:
            stream_context.__exit__(*sys.exc_info())
