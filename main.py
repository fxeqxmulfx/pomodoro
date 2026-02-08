"""
Pomodoro Timer with Audio Focus and Statistics Tracking.

This module provides a command-line interface for a Pomodoro timer including
brown noise generation for focus, desktop notifications, and persistent
statistics tracking in JSON format.
"""

import time
import sys
import subprocess
import json
import os
import numpy as np
import sounddevice as sd
from datetime import datetime
import doctest

WORK_MINUTES: int = 25
SHORT_BREAK_MINUTES: int = 5
LONG_BREAK_MINUTES: int = 15
SESSIONS_BEFORE_LONG_BREAK: int = 4
VOLUME: float = 1.0
SAMPLE_RATE: int = 48000
LOOP_DURATION: int = 10
STATS_FILE: str = "pomodoro_stats.json"


def minutes_to_seconds(minutes: float) -> int:
    """
    Convert a minute value to an integer number of seconds.

    :param minutes: The duration in minutes.
    :type minutes: float
    :return: The duration in seconds (clamped at 0).
    :rtype: int

    >>> minutes_to_seconds(25)
    1500
    >>> minutes_to_seconds(0)
    0
    >>> minutes_to_seconds(-1)
    0
    """
    return int(max(0, minutes * 60))


def format_timer(seconds: float) -> str:
    """
    Convert seconds into a MM:SS string format.

    :param seconds: Total seconds to format.
    :type seconds: float
    :return: Formatted time string.
    :rtype: str

    >>> format_timer(1500)
    '25:00'
    >>> format_timer(59)
    '00:59'
    >>> format_timer(-1)
    '00:00'
    """
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def get_empty_stats() -> dict[str, int | str]:
    """
    Generate a dictionary representing empty/initial statistics.

    :return: A dictionary with default keys for tracking pomodoros.
    :rtype: dict[str, int | str]

    >>> get_empty_stats()['total_sessions']
    0
    """
    return {
        "total_sessions": 0,
        "total_focus_minutes": 0,
        "total_break_minutes": 0,
        "days_active": 0,
        "last_run": "",
    }


def calculate_stats(
    data: dict[str, int | str], minutes: int, is_work: bool, date_str: str
) -> dict[str, int | str]:
    """
    Update a statistics dictionary with new session data.

    :param data: Current statistics data.
    :type data: dict[str, int | str]
    :param minutes: Minutes spent in the current session.
    :type minutes: int
    :param is_work: True if the session was a work/focus session, False for a break.
    :type is_work: bool
    :param date_str: Current date in YYYY-MM-DD format.
    :type date_str: str
    :return: A new updated dictionary.
    :rtype: dict[str, int | str]

    >>> d = get_empty_stats()
    >>> d = calculate_stats(d, 25, True, "2025-01-01")
    >>> int(d['total_sessions']), int(d['days_active'])
    (1, 1)
    >>> d = calculate_stats(d, 5, False, "2025-01-01")
    >>> int(d['total_break_minutes'])
    5
    >>> d = calculate_stats(d, 25, True, "2025-01-02") # Next day
    >>> int(d['days_active'])
    2
    """
    new_data = data.copy()
    if is_work:
        new_data["total_sessions"] = int(new_data["total_sessions"]) + 1
        new_data["total_focus_minutes"] = int(new_data["total_focus_minutes"]) + minutes
    else:
        new_data["total_break_minutes"] = int(new_data["total_break_minutes"]) + minutes

    if new_data["last_run"] != date_str:
        new_data["days_active"] = int(new_data["days_active"]) + 1
        new_data["last_run"] = date_str
    return new_data


def format_report(data: dict[str, int | str], session_today: int) -> str:
    """
    Generate a human-readable progress report string.

    :param data: The cumulative statistics dictionary.
    :type data: dict[str, int | str]
    :param session_today: Count of sessions completed in the current run.
    :type session_today: int
    :return: Formatted ASCII report.
    :rtype: str

    >>> d = {'total_focus_minutes': 130, 'total_sessions': 5, 'days_active': 2}
    >>> "2h 10m" in format_report(d, 3)
    True
    >>> "Current Streak:  3" in format_report(d, 3)
    True
    """
    focus_mins = int(data.get("total_focus_minutes", 0))
    h, m = focus_mins // 60, focus_mins % 60
    return (
        f"\n{'=' * 40}\n📊 PROGRESS REPORT\n"
        f"Current Streak:  {session_today} sessions today\n"
        f"Lifetime Focus:  {h}h {m}m\n"
        f"Total Sessions:  {data.get('total_sessions', 0)}\n"
        f"Days Active:     {data.get('days_active', 0)}\n{'=' * 40}\n"
    )


def get_session_config(count: int, long_freq: int = 4) -> tuple[int, str]:
    """
    Determine the duration and label of the next break.

    :param count: The number of sessions completed so far.
    :type count: int
    :param long_freq: Frequency of long breaks (e.g., every 4th session).
    :type long_freq: int
    :return: A tuple of (minutes, session_label).
    :rtype: tuple[int, str]

    >>> get_session_config(4)
    (15, 'Long Break')
    >>> get_session_config(1)
    (5, 'Short Break')
    """
    if count > 0 and count % long_freq == 0:
        return LONG_BREAK_MINUTES, "Long Break"
    return SHORT_BREAK_MINUTES, "Short Break"


def normalize_audio(audio_array: np.ndarray, target_vol: float) -> np.ndarray:
    """
    Normalize a numpy audio array to a target peak volume.

    :param audio_array: The raw audio signal.
    :type audio_array: np.ndarray
    :param target_vol: Target peak amplitude (0.0 to 1.0).
    :type target_vol: float
    :return: Normalized audio signal.
    :rtype: np.ndarray

    >>> arr = np.array([-2.0, 0.0, 2.0])
    >>> normalized = normalize_audio(arr, 0.5)
    >>> float(np.max(normalized))
    0.5
    """
    peak = np.max(np.abs(audio_array))
    if peak > 0:
        audio_array = audio_array / peak
    return audio_array * target_vol


def parse_pause_input(user_str: str) -> str:
    """
    Map user input characters to standard control actions.

    :param user_str: Raw input string from user.
    :type user_str: str
    :return: Action keyword ('skip', 'quit', or 'resume').
    :rtype: str

    >>> parse_pause_input("s")
    'skip'
    >>> parse_pause_input("  R  ")
    'resume'
    >>> parse_pause_input("q")
    'quit'
    >>> parse_pause_input("")
    'resume'
    """
    cleaned = user_str.strip().lower()
    mapping = {"s": "skip", "q": "quit", "r": "resume"}
    return mapping.get(cleaned, "resume")


def get_notification_cmd(title: str, msg: str) -> list[str]:
    """
    Generate the system command for desktop notifications (Linux/notify-send).

    :param title: Notification title.
    :type title: str
    :param msg: Notification body text.
    :type msg: str
    :return: Command list for subprocess.run.
    :rtype: list[str]

    >>> get_notification_cmd("Hi", "Bye")
    ['notify-send', '-u', 'critical', 'Hi', 'Bye']
    """
    return ["notify-send", "-u", "critical", title, msg]


class StatsManager:
    """
    Handles loading, saving, and updating Pomodoro statistics from a JSON file.
    """

    def __init__(self, path: str) -> None:
        """
        Initialize the StatsManager with a file path.

        :param path: Path to the JSON statistics file.
        :type path: str
        """
        self.path: str = path
        self.data: dict[str, int | str] = self.load()

    def load(self) -> dict[str, int | str]:
        """
        Load statistics from the file or return defaults if file is missing/corrupt.

        :return: Loaded statistics.
        :rtype: dict[str, int | str]
        """
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError, IOError:
                pass
        return get_empty_stats()

    def save(self) -> None:
        """
        Write the current data dictionary to the JSON file.

        :return: None
        """
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=4)

    def update(self, minutes: int, is_work: bool) -> None:
        """
        Calculate new stats based on a finished session and save to disk.

        :param minutes: Duration of the session finished.
        :type minutes: int
        :param is_work: Whether it was a work session.
        :type is_work: bool
        :return: None
        """
        today = datetime.now().strftime("%Y-%m-%d")
        self.data = calculate_stats(self.data, minutes, is_work, today)
        self.save()


def generate_brown_noise(duration: int) -> np.ndarray:
    """
    Generate a brown noise signal using FFT.

    :param duration: Duration of noise in seconds.
    :type duration: int
    :return: Normalized brown noise as a numpy array.
    :rtype: np.ndarray
    """
    n = int(SAMPLE_RATE * duration)
    f = np.arange(n // 2 + 1)
    f[0] = 1
    spec = (np.random.normal(0, 1, len(f)) + 1j * np.random.normal(0, 1, len(f))) * (
        1 / f
    )
    spec[0] = 0
    return normalize_audio(np.fft.irfft(spec, n=n), VOLUME)


def notify(title: str, msg: str) -> None:
    """
    Trigger a desktop notification. Fails silently if notify-send is unavailable.

    :param title: Title of notification.
    :type title: str
    :param msg: Content of notification.
    :type msg: str
    :return: None
    """
    try:
        subprocess.run(get_notification_cmd(title, msg), check=False)
    except subprocess.SubprocessError, FileNotFoundError:
        pass


def countdown(minutes: int, label: str, play_sound: bool = False) -> str:
    """
    Display a countdown timer in the console and optionally play background noise.

    :param minutes: Duration to count down.
    :type minutes: int
    :param label: Text label to display (e.g., 'Focus Session').
    :type label: str
    :param play_sound: If True, loops brown noise during the countdown.
    :type play_sound: bool
    :return: A status string indicating 'completed' or 'skipped'.
    :rtype: str
    """
    total_sec = minutes_to_seconds(minutes)
    noise = generate_brown_noise(LOOP_DURATION) if play_sound else None

    if play_sound and noise is not None:
        sd.play(noise, SAMPLE_RATE, loop=True)

    while total_sec >= 0:
        try:
            sys.stdout.write(
                f"\r[{label}] {format_timer(total_sec)} (Ctrl+C to Pause) "
            )
            sys.stdout.flush()
            time.sleep(1)
            total_sec -= 1
        except KeyboardInterrupt:
            sd.stop()
            print("\n\n⏸️  PAUSED")
            action = parse_pause_input(input("Options: [R]esume, [S]kip, [Q]uit: "))
            if action == "skip":
                return "skipped"
            if action == "quit":
                sys.exit()
            if play_sound and noise is not None:
                sd.play(noise, SAMPLE_RATE, loop=True)

    sd.stop()
    print(f"\n🔔 {label} finished!")
    notify("Pomodoro", f"{label} finished!")
    return "completed"


def main() -> None:
    """
    Main entry point for the Pomodoro application. Runs doctests and then the loop.

    :return: None
    """
    test_results = doctest.testmod()
    if test_results.failed > 0:
        print("❌ Logic validation failed. Safety shutdown.")
        sys.exit(1)

    stats = StatsManager(STATS_FILE)
    session_count: int = 0

    print("Starting 🍅\n")
    while True:
        session_count += 1
        print(format_report(stats.data, session_count - 1))

        if (
            countdown(WORK_MINUTES, f"Focus Session {session_count}", True)
            == "completed"
        ):
            stats.update(WORK_MINUTES, True)

        b_min, b_lbl = get_session_config(session_count, SESSIONS_BEFORE_LONG_BREAK)
        input(f"\nNext: {b_lbl}. Press Enter to start...")

        if countdown(b_min, b_lbl, False) == "completed":
            stats.update(b_min, False)

        print("\n" + "-" * 20)
        input("Break over! Press Enter for next Work Session...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit()
