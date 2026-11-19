from datetime import datetime, timezone
from random import Random
from time import monotonic, sleep
from unittest.mock import Mock

from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QRadioButton

from pokemac import engine
from pokemac.cli import game as game_cli
from pokemac.engine import game
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
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 255)))
    application = QApplication.instance() or QApplication([])
    clock = Mock(return_value=0)
    overlay = app.Overlay(application, schedule=SpawnSchedule(clock=clock), poll_interval_ms=50)

    try:
        _wait_until(application, lambda: 1 in overlay.companions)
        companion = overlay.companions[1].window
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

    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 255)))
    application = QApplication.instance() or QApplication([])
    overlay = app.Overlay(application, poll_interval_ms=50)

    try:
        _wait_until(application, lambda: 1 in overlay.companions)
        companion = overlay.companions[1].window
        assert companion.isVisible()
        assert not overlay.tray.icon().isNull()
        toggle, party_menu, collection, _, quit_action = overlay.tray.contextMenu().actions()
        assert toggle.text() == "Hide overlay" and quit_action.text() == "Quit"
        assert party_menu.text() == "Party" and collection.text() == "Collection"

        assert game_cli.main(["dev", "give-xp", "10"]) == 0
        _wait_until(application, lambda: overlay.message.label.text() == "Bulbasaur reached level 2!")
        assert game_cli.main(["dev", "give-xp", "2240"]) == 0
        _wait_until(
            application, lambda: companion._flash.state() == QAbstractAnimation.State.Running,
        )
        assert overlay.message.label.text() == "Bulbasaur evolved into Ivysaur at level 16!"

        toggle.trigger()
        assert not companion.isVisible()
        assert not overlay.message.isVisible()
        assert toggle.text() == "Show overlay"
        assert game_cli.main(["dev", "spawn-wild"]) == 0
        _wait_until(application, lambda: overlay._wild_species == "pikachu")
        assert not overlay.wild.isVisible()
        toggle.trigger()
        assert companion.isVisible() and overlay.wild.isVisible()

        QTest.mouseClick(overlay.wild, Qt.MouseButton.LeftButton)
        add, remove, lead = [action.menu() for action in party_menu.menu().actions()]
        _wait_until(application, lambda: len(add.actions()) == 1)
        assert add.actions()[0].text().startswith("Pikachu")
        add.actions()[0].trigger()
        _wait_until(application, lambda: len(overlay.companions) == 2)
        lead.actions()[0].trigger()
        _wait_until(application, lambda: remove.actions()[0].text().startswith("Pikachu"))
        assert engine.get_status()["active_pokemon"]["species"] == "pikachu"
        QTest.mouseClick(overlay.companions[2].window, Qt.MouseButton.LeftButton)
        assert overlay.panel.name.text() == "Pikachu"
    finally:
        overlay.stop()


def test_three_companions_walk_and_share_xp_and_collection_data(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(Image.new("RGBA", (4, 3), "green"), True)))
    monkeypatch.setattr(game, "_roll_wild", Mock(side_effect=[("pikachu", 255), ("eevee", 255)]))
    caught = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)
    for _ in range(2):
        engine.try_catch(rng=Random(5), now=caught)
    for pokemon in engine.list_pokemon()[1:]:
        engine.add_to_party(pokemon["id"])
    engine.add_task("Choose a party member", 9)

    application = QApplication.instance() or QApplication([])
    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: len(overlay.companions) == 3)
        starter, pikachu, eevee = [overlay.companions[pokemon_id] for pokemon_id in (1, 2, 3)]
        assert all(member.window.isVisible() for member in (starter, pikachu, eevee))
        assert len({member.window.x() for member in (starter, pikachu, eevee)}) == 3
        assert len({id(member.motion) for member in (starter, pikachu, eevee)}) == 3

        QTest.mouseClick(eevee.window, Qt.MouseButton.LeftButton)
        assert overlay.panel.name.text() == "Eevee"
        stationary_x, moving_x = eevee.motion.x, starter.motion.x
        _wait_until(application, lambda: starter.motion.x != moving_x)
        assert eevee.motion.x == stationary_x

        starter_choice, pikachu_choice, _ = overlay.panel.findChildren(QRadioButton)
        assert starter_choice.isChecked()
        QTest.mouseClick(
            pikachu_choice, Qt.MouseButton.LeftButton,
            pos=QPoint(8, pikachu_choice.height() // 2),
        )
        assert pikachu_choice.isChecked()
        QTest.mouseClick(_button(overlay.panel, "Done"), Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: overlay.panel.progress.value() == 4)
        assert [pokemon["xp"] for pokemon in engine.list_party()] == [4, 9, 4]
        assert [pokemon["is_active"] for pokemon in engine.list_party()] == [1, 0, 0]

        collection = next(
            action for action in overlay.tray.contextMenu().actions() if action.text() == "Collection"
        )
        collection.trigger()
        table = overlay.collection.table
        assert overlay.collection.isVisible()
        assert table.rowCount() == 3
        assert [table.item(1, column).text() for column in range(5)] == [
            "2", "Pikachu", "1", "2026-08-10", "Member",
        ]
        assert game_cli.main(["party", "remove", "3"]) == 0
        _wait_until(application, lambda: 3 not in overlay.companions)
        assert not overlay.panel.isVisible()
        assert table.item(2, 4).text() == "—"
    finally:
        overlay.stop()
