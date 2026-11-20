"""Send macOS notifications without interpolating text into AppleScript."""

import subprocess
import sys


_SCRIPT = """on run argv
    display notification (item 1 of argv) with title "Pokemac"
end run"""


def notify_macos(text):
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-", text],
            input=_SCRIPT, text=True, capture_output=True, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # The in-overlay toast is independent of notification permissions.
