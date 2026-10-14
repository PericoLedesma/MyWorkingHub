import datetime
import logging
import os
import threading

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf

from .db import DATA_DIR, save_metadata

log = logging.getLogger(__name__)

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

    # Mic is authoritative for length — pad loopback with silence if it's shorter
    # (WASAPI loopback delivers no chunks during silence, so app_audio can be much
    # shorter than the actual recording time; we must never truncate the mic).
    mic_len = len(mic_stereo)
    app_len = len(app_audio)
    if app_len < mic_len:
        app_audio = np.vstack([app_audio, np.zeros((mic_len - app_len, app_channels), dtype=np.float32)])
    elif mic_len < app_len:
        mic_stereo = np.vstack([mic_stereo, np.zeros((app_len - mic_len, app_channels), dtype=np.float32)])

    return 0.5 * mic_stereo + 0.5 * app_audio, app_rate


def _get_all_loopbacks(pa: pyaudio.PyAudio) -> list[dict]:
    """Return every available WASAPI loopback device (one per output device)."""
    try:
        devices = list(pa.get_loopback_device_info_generator())
        log.info("Loopback devices found: %s", [d["name"] for d in devices])
        return devices
    except Exception as e:
        log.warning("No loopback devices: %s", e)
        return []


def _merge_loopbacks(
    streams: list[tuple[list[np.ndarray], int, int]],
) -> tuple[np.ndarray, int, int] | None:
    """Merge multiple loopback streams (different rates/channels OK) into one array.

    Uses the first non-empty stream as the reference rate/channels.
    All others are resampled and channel-converted to match, then averaged.
    Returns (audio_2d, rate, channels) or None if all streams are empty.
    """
    filled = [(chunks, rate, ch) for chunks, rate, ch in streams if chunks]
    if not filled:
        return None

    ref_rate, ref_ch = filled[0][1], filled[0][2]
    arrays: list[np.ndarray] = []

    for chunks, rate, ch in filled:
        audio = np.concatenate(chunks).reshape(-1, ch).astype(np.float32)

        if rate != ref_rate:
            n = int(len(audio) * ref_rate / rate)
            resampled = np.zeros((n, ch), dtype=np.float32)
            for c in range(ch):
                resampled[:, c] = np.interp(
                    np.linspace(0, len(audio) - 1, n),
                    np.arange(len(audio)),
                    audio[:, c],
                )
            audio = resampled

        if ch < ref_ch:
            audio = np.hstack([audio[:, :1]] * ref_ch)
        elif ch > ref_ch:
            audio = audio[:, :ref_ch]

        arrays.append(audio)

    if len(arrays) == 1:
        return arrays[0], ref_rate, ref_ch

    max_len = max(len(a) for a in arrays)
    padded = []
    for a in arrays:
        if len(a) < max_len:
            a = np.vstack([a, np.zeros((max_len - len(a), ref_ch), dtype=np.float32)])
        padded.append(a)

    merged = np.mean(np.stack(padded, axis=0), axis=0).astype(np.float32)
    return merged, ref_rate, ref_ch


class Recorder:
    """Records audio from the microphone, WASAPI loopback, or both."""

    def __init__(self, stop_event: threading.Event, on_done):
        self._stop = stop_event
        self._on_done = on_done  # signature: (meta | None, reason: str | None)
        self.mic_device_index: int | None = None
        # Live transcription buffers — drained periodically by LiveTranscriber
        self._live_lock = threading.Lock()
        self._live_chunks: list[np.ndarray] = []
        self._live_rate: int = 0
        self._live_channels: int = 1
        # Separate app-audio buffer used when source=both (enables speaker labeling)
        self._live_app_lock = threading.Lock()
        self._live_app_chunks: list[np.ndarray] = []
        self._live_app_rate: int = 0
        self._live_app_channels: int = 1
        self._live_source: str = "mic"

    def drain_live_chunks(self) -> list[tuple[str, list[np.ndarray], int, int]]:
        """Return [(label, chunks, rate, channels)] per audio source and clear the buffers.

        When source=both, returns two entries labeled "You" (mic) and "Them" (app).
        Otherwise returns a single entry with an empty label.
        """
        with self._live_lock:
            mic_chunks = self._live_chunks[:]
            self._live_chunks.clear()

        if self._live_source != "both":
            return [("", mic_chunks, self._live_rate, self._live_channels)]

        with self._live_app_lock:
            app_chunks = self._live_app_chunks[:]
            self._live_app_chunks.clear()

        sources: list[tuple[str, list[np.ndarray], int, int]] = []
        if mic_chunks:
            sources.append(("You", mic_chunks, self._live_rate, self._live_channels))
        if app_chunks:
            sources.append(("Them", app_chunks, self._live_app_rate, self._live_app_channels))
        return sources

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
        """Return mic device info. Prefers WASAPI (shared mode) over MME/DirectSound.

        pyaudiowpatch does not expose PaWasapiStreamInfo, but WASAPI shared mode is
        PortAudio's default when the device's hostApi is WASAPI. Selecting the WASAPI
        device index (rather than the MME alias for the same physical mic) is sufficient
        to ensure shared-mode capture that coexists with Teams, Meet, etc.

        Priority: WASAPI + preferred name > any WASAPI input > non-WASAPI preferred name
        > system default. When mic_device_index is set explicitly, that device is used
        as-is with no override.
        """
        if self.mic_device_index is not None:
            return pa.get_device_info_by_index(self.mic_device_index)

        try:
            wasapi_idx = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        except Exception:
            wasapi_idx = -1

        wasapi_any: dict | None = None   # any WASAPI non-loopback input (no name match)
        legacy_match: dict | None = None  # non-WASAPI device with preferred name
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] <= 0 or info.get("isLoopbackDevice"):
                continue
            if info["hostApi"] == wasapi_idx:
                if "Microphone Array" in info["name"]:
                    return info  # optimal: WASAPI shared mode + preferred device name
                if wasapi_any is None:
                    wasapi_any = info
            elif "Microphone Array" in info["name"] and legacy_match is None:
                legacy_match = info

        return wasapi_any or legacy_match or pa.get_default_input_device_info()

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
        self._live_source = "mic"
        start_time = datetime.datetime.now()
        filepath = _make_filepath()
        chunks: list[np.ndarray] = []

        def cb(in_data, _fc, _ti, _st):
            chunk = np.frombuffer(in_data, dtype=np.float32)
            chunks.append(chunk.copy())
            with self._live_lock:
                self._live_chunks.append(chunk.copy())
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
        except Exception as e:
            log.error("mic stream open failed: %s", e)
            pa.terminate()
            self._on_done(None, "mic_stream_error")
            return

        self._stop.wait()
        # Close separately so a teardown error never discards already-captured chunks.
        try:
            stream.stop_stream()
            stream.close()
        except Exception as e:
            log.warning("mic stream close error (chunks preserved): %s", e)
        pa.terminate()

        end_time = datetime.datetime.now()
        if not chunks:
            self._on_done(None, "mic_no_chunks")
            return
        try:
            sf.write(filepath, np.concatenate(chunks), rate)
        except Exception as e:
            log.error("mic sf.write failed: %s", e)
            self._on_done(None, "mic_write_error")
            return
        try:
            self._on_done(save_metadata(filepath, "mic", start_time, end_time, rate, 1), None)
        except Exception as e:
            # WAV is safely on disk — surface it as a partial success so the UI
            # can show it and the user can rescan rather than losing the file.
            log.error("mic save_metadata failed (WAV intact at %s): %s", filepath, e)
            self._on_done(None, "mic_metadata_error")

    def run_loopback(self):
        """Record all WASAPI loopback devices until stop_event is set."""
        pa = pyaudio.PyAudio()
        devices = _get_all_loopbacks(pa)
        if not devices:
            pa.terminate()
            self._on_done(None, "loopback_device_missing")
            return

        self._live_source = "app"
        start_time = datetime.datetime.now()
        filepath = _make_filepath()

        device_data: list[tuple] = []  # (stream, chunks, rate, channels, name)
        live_set = False

        for device in devices:
            rate = int(device["defaultSampleRate"])
            channels = device["maxInputChannels"]
            chunks: list[np.ndarray] = []
            feed_live = not live_set

            def make_cb(buf: list, feed: bool):
                def cb(in_data, _fc, _ti, _st):
                    chunk = np.frombuffer(in_data, dtype=np.float32)
                    buf.append(chunk.copy())
                    if feed:
                        with self._live_lock:
                            self._live_chunks.append(chunk.copy())
                    return None, pyaudio.paContinue
                return cb

            try:
                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=device["index"],
                    stream_callback=make_cb(chunks, feed_live),
                )
                if not live_set:
                    self._live_rate = rate
                    self._live_channels = channels
                    live_set = True
                device_data.append((stream, chunks, rate, channels, device["name"]))
            except Exception as e:
                log.warning("Failed to open loopback '%s': %s", device["name"], e)

        if not device_data:
            pa.terminate()
            self._on_done(None, "loopback_stream_error")
            return

        for stream, _, _, _, _ in device_data:
            stream.start_stream()
        self._stop.wait()
        for stream, _, _, _, _ in device_data:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()

        for _, chunks, rate, ch, name in device_data:
            log.info("Loopback '%s': %d chunks (rate=%s ch=%s)", name, len(chunks), rate, ch)

        end_time = datetime.datetime.now()
        result = _merge_loopbacks([(c, r, ch) for _, c, r, ch, _ in device_data])
        if result is None:
            self._on_done(None, "loopback_no_chunks")
            return
        audio, rate, channels = result
        try:
            sf.write(filepath, audio, rate)
        except Exception as e:
            log.error("loopback sf.write failed: %s", e)
            self._on_done(None, "app_write_error")
            return
        try:
            self._on_done(save_metadata(filepath, "app", start_time, end_time, rate, channels), None)
        except Exception as e:
            log.error("loopback save_metadata failed (WAV intact at %s): %s", filepath, e)
            self._on_done(None, "app_metadata_error")

    def run_both(self):
        """Record mic + all WASAPI loopback devices, mix into one file."""
        pa = pyaudio.PyAudio()
        devices = _get_all_loopbacks(pa)

        try:
            mic_info = self._get_mic_device(pa)
        except Exception:
            pa.terminate()
            self._on_done(None, "mic_device_error")
            return

        if not devices:
            pa.terminate()
            self.run_mic()
            return

        mic_rate = int(mic_info["defaultSampleRate"])
        self._live_rate = mic_rate
        self._live_channels = 1
        self._live_source = "both"
        mic_chunks: list[np.ndarray] = []
        start_time = datetime.datetime.now()
        filepath = _make_filepath()

        def mic_cb(in_data, _fc, _ti, _st):
            chunk = np.frombuffer(in_data, dtype=np.float32)
            mic_chunks.append(chunk.copy())
            with self._live_lock:
                self._live_chunks.append(chunk.copy())
            return None, pyaudio.paContinue

        # Open one stream per loopback device; first feeds live app chunks
        device_data: list[tuple] = []
        live_app_set = False

        for device in devices:
            rate = int(device["defaultSampleRate"])
            channels = device["maxInputChannels"]
            chunks: list[np.ndarray] = []
            feed_live = not live_app_set

            def make_app_cb(buf: list, feed: bool):
                def cb(in_data, _fc, _ti, _st):
                    chunk = np.frombuffer(in_data, dtype=np.float32)
                    buf.append(chunk.copy())
                    if feed:
                        with self._live_app_lock:
                            self._live_app_chunks.append(chunk.copy())
                    return None, pyaudio.paContinue
                return cb

            try:
                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=device["index"],
                    stream_callback=make_app_cb(chunks, feed_live),
                )
                if not live_app_set:
                    self._live_app_rate = rate
                    self._live_app_channels = channels
                    live_app_set = True
                device_data.append((stream, chunks, rate, channels, device["name"]))
            except Exception as e:
                log.warning("Failed to open loopback '%s': %s", device["name"], e)

        try:
            mic_stream = pa.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=mic_rate,
                input=True,
                input_device_index=mic_info["index"],
                stream_callback=mic_cb,
            )
        except Exception:
            for stream, _, _, _, _ in device_data:
                try: stream.close()
                except Exception: pass
            pa.terminate()
            self._on_done(None, "mic_stream_error")
            return

        if not device_data:
            # All loopback opens failed — fall back to mic-only
            mic_stream.start_stream()
            self._stop.wait()
            mic_stream.stop_stream()
            mic_stream.close()
            pa.terminate()
            end_time = datetime.datetime.now()
            if mic_chunks:
                try:
                    sf.write(filepath, np.concatenate(mic_chunks), mic_rate)
                    self._on_done(save_metadata(filepath, "mic", start_time, end_time, mic_rate, 1), None)
                except Exception:
                    self._on_done(None, "mic_write_error")
            else:
                self._on_done(None, "mic_no_chunks")
            return

        mic_stream.start_stream()
        for stream, _, _, _, _ in device_data:
            stream.start_stream()

        self._stop.wait()

        mic_stream.stop_stream()
        mic_stream.close()
        for stream, _, _, _, _ in device_data:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()

        for _, chunks, rate, ch, name in device_data:
            log.info("Loopback '%s': %d chunks (rate=%s ch=%s)", name, len(chunks), rate, ch)

        end_time = datetime.datetime.now()
        try:
            loopback = _merge_loopbacks([(c, r, ch) for _, c, r, ch, _ in device_data])
            if mic_chunks and loopback is not None:
                app_audio, app_rate, app_channels = loopback
                mixed, rate = _mix_audio(mic_chunks, [app_audio], app_rate, app_channels, mic_rate)
                audio_to_write, write_rate, write_ch, write_source = mixed, rate, app_channels, "both"
            elif mic_chunks:
                audio_to_write, write_rate, write_ch, write_source = np.concatenate(mic_chunks), mic_rate, 1, "mic"
            elif loopback is not None:
                app_audio, app_rate, app_channels = loopback
                audio_to_write, write_rate, write_ch, write_source = app_audio, app_rate, app_channels, "app"
            else:
                self._on_done(None, "both_no_chunks")
                return
        except Exception as e:
            log.error("run_both mix failed: %s", e)
            self._on_done(None, "both_write_error")
            return
        try:
            sf.write(filepath, audio_to_write, write_rate)
        except Exception as e:
            log.error("run_both sf.write failed: %s", e)
            self._on_done(None, "both_write_error")
            return
        try:
            self._on_done(save_metadata(filepath, write_source, start_time, end_time, write_rate, write_ch), None)
        except Exception as e:
            log.error("run_both save_metadata failed (WAV intact at %s): %s", filepath, e)
            self._on_done(None, "both_metadata_error")

