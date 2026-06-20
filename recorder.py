"""Audio recorder using sounddevice. Records 16kHz mono 16-bit PCM, returns WAV bytes."""

import io
import threading
import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
BLOCK_SIZE = 1024


class Recorder:
    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.is_recording = False

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[recorder] {status}")
        self._frames.append(indata.copy())

    def start(self):
        with self._lock:
            self._frames.clear()
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SIZE,
                callback=self._callback,
            )
            self._stream.start()
            self.is_recording = True

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes."""
        with self._lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self.is_recording = False

            if not self._frames:
                return b""

            audio_data = np.concatenate(self._frames, axis=0)
            self._frames.clear()

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())

        return buf.getvalue()
