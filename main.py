import argparse
import datetime
import threading
import sounddevice as sd
import soundfile as sf
import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 1
BAR_WIDTH = 40


def draw_level(level):
    filled = int(level * BAR_WIDTH)
    bar = "#" * filled + "-" * (BAR_WIDTH - filled)
    print(f"\r  [{bar}] {level * 100:5.1f}%", end="", flush=True)


def record(duration=None):
    output_file = f"recording_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    recorded_chunks = []
    current_level = [0.0]

    def callback(indata, _frames, _time, _status):
        recorded_chunks.append(indata.copy())
        rms = float(np.sqrt(np.mean(indata ** 2)))
        current_level[0] = min(rms * 10, 1.0)  # scale to 0-1

    def level_display():
        while not stop_event.is_set():
            draw_level(current_level[0])
            stop_event.wait(0.05)
        draw_level(0.0)
        print()

    stop_event = threading.Event()
    display_thread = threading.Thread(target=level_display, daemon=True)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=callback):
        display_thread.start()
        if duration:
            print(f"Recording for {duration}s...")
            sd.sleep(int(duration * 1000))
        else:
            print("Recording... Press Enter to stop.")
            input()
        stop_event.set()

    display_thread.join()

    if not recorded_chunks:
        print("No audio captured.")
        return

    audio = np.concatenate(recorded_chunks, axis=0)
    sf.write(output_file, audio, SAMPLE_RATE)
    print(f"Saved to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=None, help="Record for N seconds")
    args = parser.parse_args()
    record(duration=args.duration)
