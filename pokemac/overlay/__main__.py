"""Run the desktop companion with ``python -m pokemac.overlay``."""

import os
import signal
import sys


def main() -> int:
    if sys.platform == "darwin":
        # Keep the Cocoa plugin from turning the Python process into a foreground app.
        os.environ.setdefault("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", "1")

    from PySide6.QtWidgets import QApplication

    from pokemac.overlay.app import Overlay

    application = QApplication(sys.argv)
    application.setApplicationName("Pokemac")
    application.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGINT, lambda *_: application.quit())
    overlay = Overlay(application)
    try:
        return application.exec()
    finally:
        overlay.stop()


if __name__ == "__main__":
    raise SystemExit(main())
