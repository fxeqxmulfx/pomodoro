import time
import sys
import subprocess
import json
import os
import numpy as np
import sounddevice as sd
from datetime import datetime

WORK_MINUTES = 25
SHORT_BREAK_MINUTES = 5
LONG_BREAK_MINUTES = 15
SESSIONS_BEFORE_LONG_BREAK = 4
VOLUME = 1
SAMPLE_RATE = 48000
LOOP_DURATION = 10
STATS_FILE = "pomodoro_stats.json"


class StatsManager:
    def __init__(self):
        self.file_path = STATS_FILE
        self.data = self.load_stats()

    def load_stats(self):
        if os.path.exists(self.file_path):
            with open(self.file_path, "r") as f:
                return json.load(f)
        return {
            "total_sessions": 0,
            "total_focus_minutes": 0,
            "total_break_minutes": 0,
            "days_active": 0,
            "last_run": "",
        }

    def save_stats(self):
        with open(self.file_path, "w") as f:
            json.dump(self.data, f, indent=4)

    def update(self, minutes, is_work=True):
        if is_work:
            self.data["total_sessions"] += 1
            self.data["total_focus_minutes"] += minutes
        else:
            self.data["total_break_minutes"] += minutes

        today = datetime.now().strftime("%Y-%m-%d")
        if self.data["last_run"] != today:
            self.data["days_active"] += 1
            self.data["last_run"] = today

        self.save_stats()

    def display_summary(self, session_count):
        total_hr = self.data["total_focus_minutes"] // 60
        total_min = self.data["total_focus_minutes"] % 60

        print("\n" + "=" * 40)
        print("📊 PROGRESS REPORT")
        print(f"Current Streak:  {session_count} sessions today")
        print(f"Lifetime Focus:  {total_hr}h {total_min}m")
        print(f"Total Sessions:  {self.data['total_sessions']}")
        print(f"Days Active:     {self.data['days_active']}")
        print("=" * 40 + "\n")


def notify_gnome(title, message):
    try:
        subprocess.run(["notify-send", "-u", "critical", title, message])
    except Exception:
        pass


def generate_brown_noise_seamless(duration_sec):
    n_samples = int(SAMPLE_RATE * duration_sec)
    if n_samples % 2 != 0:
        n_samples += 1
    num_bins = n_samples // 2 + 1
    real = np.random.normal(0, 1, num_bins)
    imag = np.random.normal(0, 1, num_bins)
    f = np.arange(num_bins)
    f[0] = 1
    filter_curve = 1 / f
    spectrum = (real + 1j * imag) * filter_curve
    spectrum[0] = 0
    noise = np.fft.irfft(spectrum)
    noise /= np.max(np.abs(noise)) + 1e-5
    return noise * VOLUME


def countdown(minutes, label, play_sound=False):
    total_seconds = int(minutes * 60)
    noise_data = generate_brown_noise_seamless(LOOP_DURATION) if play_sound else None

    if play_sound:
        sd.play(noise_data, SAMPLE_RATE, loop=True)

    while total_seconds >= 0:
        try:
            mins, secs = divmod(total_seconds, 60)
            sys.stdout.write(f"\r[{label}] {mins:02d}:{secs:02d} (Ctrl+C to Pause) ")
            sys.stdout.flush()
            time.sleep(1)
            total_seconds -= 1
        except KeyboardInterrupt:
            sd.stop()
            print(f"\n\n⏸️  PAUSED: [{label}]")
            choice = input("Options: [R]esume, [S]kip, [Q]uit: ").lower()

            if choice == "s":
                print(f"Skipping {label}...")
                return "skipped"
            elif choice == "q":
                print("Exiting...")
                sys.exit()
            else:
                print(f"Resuming {label}...")
                if play_sound:
                    sd.play(noise_data, SAMPLE_RATE, loop=True)
                continue

    sd.stop()
    print(f"\n🔔 {label} finished!")
    notify_gnome("Pomodoro", f"{label} finished!")
    return "completed"


def main():
    stats = StatsManager()
    session_count = 0

    print("--- 🍅 Gnome Pomodoro + Brown Noise 🍅 ---")
    stats.display_summary(session_count)

    while True:
        session_count += 1

        status = countdown(
            WORK_MINUTES, f"Focus Session {session_count}", play_sound=True
        )

        if status == "completed":
            stats.update(WORK_MINUTES, is_work=True)
            stats.display_summary(session_count)

        if session_count % SESSIONS_BEFORE_LONG_BREAK == 0:
            b_min, b_lbl = LONG_BREAK_MINUTES, "Long Break"
        else:
            b_min, b_lbl = SHORT_BREAK_MINUTES, "Short Break"

        input(f"\nPress Enter to start {b_lbl} (or Ctrl+C to quit)...")

        status = countdown(b_min, b_lbl, play_sound=False)
        if status == "completed":
            stats.update(b_min, is_work=False)

        print("\n" + "-" * 20)
        input("Break over! Press Enter for next Work Session...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit()
