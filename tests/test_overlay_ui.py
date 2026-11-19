from time import monotonic, sleep
from unittest.mock import Mock

from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from pokemac import engine
from pokemac.cli import game as game_cli
from pokemac.core import api
from pokemac.overlay import app
from pokemac.overlay.motion import SpawnSchedule
from pokemac.overlay.widgets import sprite_pixmap


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
    art = Image.new("RGBA", (3, 2), "green")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, False)))
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
        assert flags & Qt.WindowType.NoDropShadowWindowHint
        assert companion.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert companion.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert companion.art.pixmap().deviceIndependentSize().width() == 96
        assert companion.grab().toImage().pixelColor(0, 0).alpha() == 0

        retina = sprite_pixmap(art, ratio=2, smooth=True)
        assert retina.width() == 192
        assert retina.deviceIndependentSize().width() == 96
        assert retina.devicePixelRatio() == 2

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


def test_overlay_reacts_to_dev_commands_and_switches_from_the_tray(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (32, 40), "gold")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, True)))

    def species(name):
        if name.isdecimal():
            return {"name": "pikachu", "capture_rate": 255}
        return {"evolution_chain_url": "https://pokeapi.co/api/v2/evolution-chain/1/"}

    monkeypatch.setattr(api, "get_species", species)
    monkeypatch.setattr(api, "get_evolution_chain", Mock(return_value={
        "species": {"name": "bulbasaur"},
        "evolves_to": [{
            "species": {"name": "ivysaur"}, "evolves_to": [],
            "evolution_details": [{"trigger": {"name": "level-up"}, "min_level": 16}],
        }],
    }))
    application = QApplication.instance() or QApplication([])
    overlay = app.Overlay(application, poll_interval_ms=50)

    try:
        _wait_until(application, overlay.companion.isVisible)
        assert not overlay.tray.icon().isNull()
        toggle, switch, _, quit_action = overlay.tray.contextMenu().actions()
        assert toggle.text() == "Hide overlay" and quit_action.text() == "Quit"
        assert switch.text() == "Switch active Pokémon"

        assert game_cli.main(["dev", "give-xp", "10"]) == 0
        _wait_until(application, lambda: overlay.message.label.text() == "Bulbasaur reached level 2!")
        assert game_cli.main(["dev", "give-xp", "2240"]) == 0
        _wait_until(
            application, lambda: overlay.companion._flash.state() == QAbstractAnimation.State.Running,
        )
        assert overlay.message.label.text() == "Bulbasaur evolved into Ivysaur at level 16!"

        toggle.trigger()
        assert not overlay.companion.isVisible()
        assert not overlay.message.isVisible()
        assert toggle.text() == "Show overlay"
        assert game_cli.main(["dev", "spawn-wild"]) == 0
        _wait_until(application, lambda: overlay._wild_species == "pikachu")
        assert not overlay.wild.isVisible()
        toggle.trigger()
        assert overlay.companion.isVisible() and overlay.wild.isVisible()

        QTest.mouseClick(overlay.wild, Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: len(switch.menu().actions()) == 2)
        ivysaur, pikachu = switch.menu().actions()
        assert ivysaur.isChecked() and not pikachu.isChecked()
        assert pikachu.text().startswith("Pikachu")
        pikachu.trigger()
        _wait_until(application, lambda: switch.menu().actions()[1].isChecked())
        assert engine.get_status()["active_pokemon"]["species"] == "pikachu"
        assert overlay.panel.name.text() == "Pikachu"
    finally:
        overlay.stop()
