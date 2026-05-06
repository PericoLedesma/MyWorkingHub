import datetime
import os
import threading

import numpy as np
import pyaudiowpatch as pyaudio
import sounddevice as sd
import soundfile as sf

from .db import save_metadata

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(_ROOT, "recordings")
os.makedirs(FOLDER, exist_ok=True)

MIC_RATE = 44100


def _rms_level(chunk: np.ndarray) -> float:
    return min(float(np.sqrt(np.mean(chunk ** 2))) * 10, 1.0)


def _make_filepath() -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(FOLDER, f"recording_{ts}.wav")


def _mix_audio(
    mic_chunks: list[np.ndarray],
    app_chunks: list[np.ndarray],
    app_rate: int,
    app_channels: int,
) -> tuple[np.ndarray, int]:
    mic_audio = np.concatenate(mic_chunks).flatten().astype(np.float32)
    app_audio = np.concatenate(app_chunks).reshape(-1, app_channels).astype(np.float32)

    n = int(len(mic_audio) * app_rate / MIC_RATE)
    mic_resampled = np.interp(
        np.linspace(0, len(mic_audio) - 1, n),
        np.arange(len(mic_audio)),
        mic_audio,
    ).astype(np.float32)

    mic_stereo = np.stack([mic_resampled] * app_channels, axis=1)
    length = min(len(mic_stereo), len(app_audio))
    return 0.5 * mic_stereo[:length] + 0.5 * app_audio[:length], app_rate


class Recorder:
    def __init__(self, stop_event: threading.Event, set_level, on_done):
        self._stop = stop_event
        self._set_level = set_level
        self._on_done = on_done

    def _get_loopback(self, pa: pyaudio.PyAudio) -> dict | None:
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

    def run_mic(self):
        start_time = datetime.datetime.now()
        filepath = _make_filepath()
        chunks: list[np.ndarray] = []

        def cb(indata, _f, _t, _s):
            chunks.append(indata.copy())
            self._set_level(_rms_level(indata))

        with sd.InputStream(samplerate=MIC_RATE, channels=1, callback=cb):
            self._stop.wait()

        end_time = datetime.datetime.now()
        if chunks:
            sf.write(filepath, np.concatenate(chunks), MIC_RATE)
            self._on_done(save_metadata(filepath, "mic", start_time, end_time, MIC_RATE, 1))
        else:
            self._on_done(None)

    def run_loopback(self):
        pa = pyaudio.PyAudio()
        device = self._get_loopback(pa)
        if device is None:
            pa.terminate()
            self._on_done(None)
            return

        rate, channels = int(device["defaultSampleRate"]), device["maxInputChannels"]
        start_time = datetime.datetime.now()
        filepath = _make_filepath()
        chunks: list[np.ndarray] = []

        def cb(in_data, _fc, _ti, _st):
            chunk = np.frombuffer(in_data, dtype=np.float32)
            chunks.append(chunk.copy())
            self._set_level(_rms_level(chunk))
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
        finally:
            pa.terminate()

        end_time = datetime.datetime.now()
        if chunks:
            sf.write(filepath, np.concatenate(chunks).reshape(-1, channels), rate)
            self._on_done(save_metadata(filepath, "app", start_time, end_time, rate, channels))
        else:
            self._on_done(None)

    def run_both(self):
        pa = pyaudio.PyAudio()
        device = self._get_loopback(pa)
        if device is None:
            pa.terminate()
            self._on_done(None)
            return

        app_rate = int(device["defaultSampleRate"])
        app_channels = device["maxInputChannels"]
        mic_chunks: list[np.ndarray] = []
        app_chunks: list[np.ndarray] = []
        mic_lv = 0.0
        app_lv = 0.0
        start_time = datetime.datetime.now()

        def mic_cb(indata, _f, _t, _s):
            nonlocal mic_lv
            mic_chunks.append(indata.copy())
            mic_lv = _rms_level(indata)
            self._set_level(max(mic_lv, app_lv))

        def app_cb(in_data, _fc, _ti, _st):
            nonlocal app_lv
            chunk = np.frombuffer(in_data, dtype=np.float32)
            app_chunks.append(chunk.copy())
            app_lv = _rms_level(chunk)
            self._set_level(max(mic_lv, app_lv))
            return None, pyaudio.paContinue

        try:
            app_stream = pa.open(
                format=pyaudio.paFloat32,
                channels=app_channels,
                rate=app_rate,
                input=True,
                input_device_index=device["index"],
                stream_callback=app_cb,
            )
            with sd.InputStream(samplerate=MIC_RATE, channels=1, callback=mic_cb):
                app_stream.start_stream()
                self._stop.wait()
                app_stream.stop_stream()
            app_stream.close()
        finally:
            pa.terminate()

        end_time = datetime.datetime.now()
        if mic_chunks and app_chunks:
            mixed, rate = _mix_audio(mic_chunks, app_chunks, app_rate, app_channels)
            filepath = _make_filepath()
            sf.write(filepath, mixed, rate)
            self._on_done(save_metadata(filepath, "both", start_time, end_time, rate, app_channels))
        else:
            self._on_done(None)
