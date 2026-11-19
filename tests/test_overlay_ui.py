from time import monotonic, sleep
from unittest.mock import Mock

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from pokemac import engine
from pokemac.cli import game as game_cli
from pokemac.core import api
from pokemac.overlay import app
from pokemac.overlay.motion import SpawnSchedule


def _wait_until(application, predicate):
    deadline = monotonic() + 4
    while monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        sleep(0.01)
    assert predicate()


def _button(window, title):
    return next(
        (button for button in window.findChildren(QPushButton) if button.text() == title),
        None,
    )


def test_overlay_click_drag_poll_and_wild_catch_offscreen(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    monkeypatch.setattr(app, "get_sprite", Mock(return_value=Image.new("RGBA", (3, 2), "green")))
    monkeypatch.setattr(api, "get_species", Mock(return_value={"name": "pikachu", "capture_rate": 255}))
    application = QApplication.instance() or QApplication([])
    clock = Mock(return_value=0)
    overlay = app.Overlay(application, schedule=SpawnSchedule(clock=clock), poll_interval_ms=50)

    try:
        companion = overlay.companion
        _wait_until(application, lambda: companion.isVisible() and not companion.art.pixmap().isNull())
        flags = companion.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert companion.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert companion.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert companion.art.pixmap().width() == 96

        QTest.mouseClick(companion, Qt.MouseButton.LeftButton)
        assert overlay.panel.isVisible()
        assert overlay.panel.name.text() == "Bulbasaur"
        assert overlay.panel.progress_text.text() == "Level 1 · 0 / 10 XP"
        assert "5 balls" in overlay.panel.summary.text()

        assert game_cli.main(["task", "add", "A CLI task", "--xp", "5"]) == 0
        _wait_until(application, lambda: _button(overlay.panel, "Done") is not None)
        QTest.mouseClick(_button(overlay.panel, "Done"), Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: overlay.panel.progress.value() == 5)
        assert engine.list_tasks("completed")[0]["title"] == "A CLI task"
        assert overlay.panel.summary.text() == "1 day streak · 6 balls"

        QTest.mouseClick(_button(overlay.panel, "×"), Qt.MouseButton.LeftButton)
        assert not overlay.panel.isVisible()
        floor = companion.y()
        point = QPoint(48, 48)
        QTest.mousePress(companion, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(companion, point + QPoint(25, -35))
        assert companion.y() < floor
        QTest.mouseRelease(companion, Qt.MouseButton.LeftButton, pos=point)
        _wait_until(application, lambda: companion.y() == floor)
        assert not overlay.panel.isVisible()

        clock.return_value = 1_200
        _wait_until(application, overlay.wild.isVisible)
        assert overlay.wild.caption.text() == "Catch Pikachu"
        QTest.mouseClick(overlay.wild, Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: overlay.message.label.text() == "Caught Pikachu!")
        assert not overlay.wild.isVisible()
        assert engine.get_status()["caught_count"] == 2
        assert engine.get_status()["inventory"]["poke-ball"] == 3
    finally:
        overlay.stop()
