import datetime
import os
import threading

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf

from .db import DATA_DIR, save_metadata

FOLDER = os.path.join(DATA_DIR, "recordings")
os.makedirs(FOLDER, exist_ok=True)

MIC_RATE = 44100


def list_input_devices() -> list[tuple[int, str]]:
    """Return (device_index, name) for every available input device."""
    pa = pyaudio.PyAudio()
    try:
        return [
            (i, pa.get_device_info_by_index(i)["name"])
            for i in range(pa.get_device_count())
            if pa.get_device_info_by_index(i)["maxInputChannels"] > 0
        ]
    finally:
        pa.terminate()


def _rms_level(chunk: np.ndarray) -> float:
    """Return RMS amplitude of the chunk, scaled to [0, 1]."""
    # scaled by 10 so typical speech sits in a visible range on the meter
    return min(float(np.sqrt(np.mean(chunk ** 2))) * 10, 1.0)


def _make_filepath() -> str:
    """Return a timestamped WAV filepath inside the recordings folder."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(FOLDER, f"recording_{ts}.wav")


def _mix_audio(
    mic_chunks: list[np.ndarray],
    app_chunks: list[np.ndarray],
    app_rate: int,
    app_channels: int,
    mic_rate: int = MIC_RATE,
) -> tuple[np.ndarray, int]:
    """Blend mic and loopback audio into a single stereo array at app_rate."""
    mic_audio = np.concatenate(mic_chunks).flatten().astype(np.float32)
    app_audio = np.concatenate(app_chunks).reshape(-1, app_channels).astype(np.float32)

    # resample mic to app_rate so both streams align in time
    n = int(len(mic_audio) * app_rate / mic_rate)
    mic_resampled = np.interp(
        np.linspace(0, len(mic_audio) - 1, n),
        np.arange(len(mic_audio)),
        mic_audio,
    ).astype(np.float32)

    # duplicate mono mic across all channels before mixing
    mic_stereo = np.stack([mic_resampled] * app_channels, axis=1)
    length = min(len(mic_stereo), len(app_audio))  # trim to the shorter stream
    return 0.5 * mic_stereo[:length] + 0.5 * app_audio[:length], app_rate


class Recorder:
    """Records audio from the microphone, WASAPI loopback, or both."""

    def __init__(self, stop_event: threading.Event, set_mic_level, set_app_level, on_done):
        self._stop = stop_event
        self._set_mic_level = set_mic_level
        self._set_app_level = set_app_level
        self._on_done = on_done  # signature: (meta | None, reason: str | None)
        self.mic_device_index: int | None = None
        # Live transcription buffer — drained periodically by LiveTranscriber
        self._live_lock = threading.Lock()
        self._live_chunks: list[np.ndarray] = []
        self._live_rate: int = 0
        self._live_channels: int = 1

    def drain_live_chunks(self) -> tuple[list[np.ndarray], int, int]:
        """Return (chunks_since_last_drain, rate, channels) and clear the buffer."""
        with self._live_lock:
            chunks = self._live_chunks[:]
            self._live_chunks.clear()
            return chunks, self._live_rate, self._live_channels

    def _get_loopback(self, pa: pyaudio.PyAudio) -> dict | None:
        """Return the WASAPI loopback device that mirrors the default system output."""
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            return next(
                (d for d in pa.get_loopback_device_info_generator()
                 if default_out["name"] in d["name"]),
                None,
            )
        except Exception:
            return None

    def _get_mic_device(self, pa: pyaudio.PyAudio) -> dict:
        """Return mic device info; uses mic_device_index when set, otherwise auto-detects."""
        if self.mic_device_index is not None:
            return pa.get_device_info_by_index(self.mic_device_index)
        count = pa.get_device_count()
        for i in range(count):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0 and "Microphone Array" in info["name"]:
                return info
        return pa.get_default_input_device_info()

    def run_mic(self):
        """Record from the microphone until stop_event is set."""
        pa = pyaudio.PyAudio()
        try:
            mic_info = self._get_mic_device(pa)
        except Exception:
            pa.terminate()
            self._on_done(None, "mic_device_error")
            return

        rate = int(mic_info["defaultSampleRate"])
        self._live_rate = rate
        self._live_channels = 1
        start_time = datetime.datetime.now()
        filepath = _make_filepath()
        chunks: list[np.ndarray] = []

        def cb(in_data, _fc, _ti, _st):
            chunk = np.frombuffer(in_data, dtype=np.float32)
            chunks.append(chunk.copy())
            with self._live_lock:
                self._live_chunks.append(chunk.copy())
            self._set_mic_level(_rms_level(chunk))
            self._set_app_level(0.0)
            return None, pyaudio.paContinue

        try:
            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=rate,
                input=True,
                input_device_index=mic_info["index"],
                stream_callback=cb,
            )
            stream.start_stream()
            self._stop.wait()
            stream.stop_stream()
            stream.close()
        except Exception:
            self._on_done(None, "mic_stream_error")
            return
        finally:
            pa.terminate()

        end_time = datetime.datetime.now()
        if chunks:
            sf.write(filepath, np.concatenate(chunks), rate)
            self._on_done(save_metadata(filepath, "mic", start_time, end_time, rate, 1), None)
        else:
            self._on_done(None, "mic_no_chunks")

    def run_loopback(self):
        """Record WASAPI loopback (system audio) until stop_event is set."""
        pa = pyaudio.PyAudio()
        device = self._get_loopback(pa)
        if device is None:
            pa.terminate()
            self._on_done(None, "loopback_device_missing")
            return

        # use the device's native rate/channels to avoid quality loss
        rate, channels = int(device["defaultSampleRate"]), device["maxInputChannels"]
        self._live_rate = rate
        self._live_channels = channels
        start_time = datetime.datetime.now()
        filepath = _make_filepath()
        chunks: list[np.ndarray] = []

        def cb(in_data, _fc, _ti, _st):
            chunk = np.frombuffer(in_data, dtype=np.float32)
            chunks.append(chunk.copy())
            with self._live_lock:
                self._live_chunks.append(chunk.copy())
            self._set_mic_level(0.0)
            self._set_app_level(_rms_level(chunk))
            return None, pyaudio.paContinue

        try:
            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device["index"],
                stream_callback=cb,
            )
            stream.start_stream()
            self._stop.wait()
            stream.stop_stream()
            stream.close()
        except Exception:
            self._on_done(None, "loopback_stream_error")
            return
        finally:
            pa.terminate()

        end_time = datetime.datetime.now()
        if chunks:
            sf.write(filepath, np.concatenate(chunks).reshape(-1, channels), rate)
            self._on_done(save_metadata(filepath, "app", start_time, end_time, rate, channels), None)
        else:
            self._on_done(None, "loopback_no_chunks")

    def run_both(self):
        """Record mic + loopback simultaneously and mix them into one file."""
        pa = pyaudio.PyAudio()
        device = self._get_loopback(pa)
        if device is None:
            pa.terminate()
            self._on_done(None, "loopback_device_missing")
            return

        try:
            mic_info = self._get_mic_device(pa)
        except Exception:
            pa.terminate()
            self._on_done(None, "mic_device_error")
            return

        app_rate     = int(device["defaultSampleRate"])
        app_channels = device["maxInputChannels"]
        mic_rate     = int(mic_info["defaultSampleRate"])
        # Live transcription captures mic audio (mono) so the local speaker's voice is always heard
        self._live_rate = mic_rate
        self._live_channels = 1
        mic_chunks: list[np.ndarray] = []
        app_chunks: list[np.ndarray] = []
        # level vars are shared across two callbacks running on different threads
        mic_lv = 0.0
        app_lv = 0.0
        start_time = datetime.datetime.now()
        filepath = _make_filepath()

        def mic_cb(in_data, _fc, _ti, _st):
            nonlocal mic_lv
            chunk = np.frombuffer(in_data, dtype=np.float32)
            mic_chunks.append(chunk.copy())
            with self._live_lock:
                self._live_chunks.append(chunk.copy())
            mic_lv = _rms_level(chunk)
            self._set_mic_level(mic_lv)
            return None, pyaudio.paContinue

        def app_cb(in_data, _fc, _ti, _st):
            nonlocal app_lv
            chunk = np.frombuffer(in_data, dtype=np.float32)
            app_chunks.append(chunk.copy())
            app_lv = _rms_level(chunk)
            self._set_app_level(app_lv)
            return None, pyaudio.paContinue

        try:
            mic_stream = pa.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=mic_rate,
                input=True,
                input_device_index=mic_info["index"],
                stream_callback=mic_cb,
            )
            app_stream = pa.open(
                format=pyaudio.paFloat32,
                channels=app_channels,
                rate=app_rate,
                input=True,
                input_device_index=device["index"],
                stream_callback=app_cb,
            )
            mic_stream.start_stream()
            app_stream.start_stream()
            self._stop.wait()
            mic_stream.stop_stream()
            app_stream.stop_stream()
            mic_stream.close()
            app_stream.close()
        except Exception:
            self._on_done(None, "stream_open_error")
            return
        finally:
            pa.terminate()

        end_time = datetime.datetime.now()
        if mic_chunks and app_chunks:
            mixed, rate = _mix_audio(mic_chunks, app_chunks, app_rate, app_channels, mic_rate)
            sf.write(filepath, mixed, rate)
            self._on_done(save_metadata(filepath, "both", start_time, end_time, rate, app_channels), None)
        elif mic_chunks:
            sf.write(filepath, np.concatenate(mic_chunks), mic_rate)
            self._on_done(save_metadata(filepath, "mic", start_time, end_time, mic_rate, 1), None)
        elif app_chunks:
            sf.write(filepath, np.concatenate(app_chunks).reshape(-1, app_channels), app_rate)
            self._on_done(save_metadata(filepath, "app", start_time, end_time, app_rate, app_channels), None)
        else:
            self._on_done(None, "both_no_chunks")

    def monitor(self, stop: threading.Event):
        """Open mic + loopback streams for level monitoring only (no saving)."""
        pa = pyaudio.PyAudio()
        streams = []

        try:
            mic_info = self._get_mic_device(pa)
            mic_rate = int(mic_info["defaultSampleRate"])

            def mic_cb(in_data, _fc, _ti, _st):
                chunk = np.frombuffer(in_data, dtype=np.float32)
                self._set_mic_level(_rms_level(chunk))
                return None, pyaudio.paContinue

            streams.append(pa.open(
                format=pyaudio.paFloat32, channels=1, rate=mic_rate,
                input=True, input_device_index=mic_info["index"],
                stream_callback=mic_cb,
            ))
        except Exception:
            pass

        device = self._get_loopback(pa)
        if device:
            try:
                channels = device["maxInputChannels"]
                rate = int(device["defaultSampleRate"])

                def app_cb(in_data, _fc, _ti, _st):
                    chunk = np.frombuffer(in_data, dtype=np.float32)
                    self._set_app_level(_rms_level(chunk))
                    return None, pyaudio.paContinue

                streams.append(pa.open(
                    format=pyaudio.paFloat32, channels=channels, rate=rate,
                    input=True, input_device_index=device["index"],
                    stream_callback=app_cb,
                ))
            except Exception:
                pass

        for s in streams:
            s.start_stream()

        stop.wait()

        for s in streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
        pa.terminate()
        self._set_mic_level(0.0)
        self._set_app_level(0.0)
