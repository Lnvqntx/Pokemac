import os
from datetime import datetime, timezone
from random import Random
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QRadioButton
from shiboken6 import isValid

from pokemac import engine
from pokemac.cli import game as game_cli
from pokemac.engine import game
from pokemac.overlay import app
from pokemac.overlay.motion import SpawnSchedule
from pokemac.overlay.widgets import SpriteWindow, sprite_pixmap


@pytest.fixture(scope="module")
def application():
    # Allow QT_QPA_PLATFORM=cocoa to exercise the native tray with the same tests.
    with pytest.MonkeyPatch.context() as env:
        if "QT_QPA_PLATFORM" not in os.environ:
            env.setenv("QT_QPA_PLATFORM", "offscreen")
        qt_app = QApplication.instance() or QApplication([])
        original = qt_app.quitOnLastWindowClosed()
        qt_app.setQuitOnLastWindowClosed(False)
        try:
            yield qt_app
        finally:
            qt_app.setQuitOnLastWindowClosed(original)


def _destroy_overlay(overlay):
    overlay.stop()
    overlay.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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


def _click_companion(member, monkeypatch):
    point = member.window.rect().center()
    assert member.window.mask().contains(point)
    with monkeypatch.context() as frozen:
        frozen.setattr(member.motion, "advance", lambda _seconds: None)
        QTest.mouseClick(member.window, Qt.MouseButton.LeftButton, pos=point)


def test_sprite_click_survives_an_artwork_change_between_press_and_release(application):
    window = SpriteWindow()
    window.set_species("eevee", sprite_pixmap(Image.new("RGBA", (4, 4), "green")))
    window.show()
    try:
        point = window.rect().center()
        clicked = QSignalSpy(window.clicked)
        dragged = QSignalSpy(window.drag_started)
        assert window.mask().contains(point)
        QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=point)
        assert window.pressed

        artwork = Image.new("RGBA", (4, 4))
        artwork.putpixel((0, 0), (0, 255, 0, 255))
        artwork.putpixel((3, 3), (0, 255, 0, 255))
        window.set_species("eevee", sprite_pixmap(artwork))
        assert not window.mask().contains(point)
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=point)
        assert clicked.count() == 1 and dragged.count() == 0
        assert not window.pressed

        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
        assert clicked.count() == 1
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_overlay_click_drag_poll_and_wild_catch_offscreen(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (3, 2), "green")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, False)))
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 255)))
    clock = Mock(return_value=0)
    overlay = app.Overlay(application, schedule=SpawnSchedule(clock=clock), poll_interval_ms=50)

    try:
        _wait_until(application, lambda: 1 in overlay.companions)
        member = overlay.companions[1]
        companion = member.window
        _wait_until(application, lambda: companion.isVisible() and "bulbasaur" in overlay._art)
        flags = companion.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert flags & Qt.WindowType.NoDropShadowWindowHint
        assert companion.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert companion.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert companion.art.pixmap().deviceIndependentSize().width() == 96
        assert companion.grab().toImage().pixelColor(0, 0).alpha() == 0
        assert not companion.mask().contains(QPoint(0, 0))
        QTest.mouseClick(companion, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        assert not overlay.panel.isVisible()

        retina = sprite_pixmap(art, ratio=2, smooth=True)
        assert retina.width() == 192
        assert retina.deviceIndependentSize().width() == 96
        assert retina.devicePixelRatio() == 2

        _click_companion(member, monkeypatch)
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
        starting_y = companion.y()
        point = QPoint(48, 48)
        QTest.mousePress(companion, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(companion, point + QPoint(25, -35))
        assert companion.y() < starting_y
        dropped = companion.pos()
        QTest.mouseRelease(companion, Qt.MouseButton.LeftButton, pos=point)
        assert companion.pos() == dropped
        _wait_until(application, lambda: companion.pos() != dropped)
        assert (companion.pos() - dropped).manhattanLength() < 12
        assert not overlay.panel.isVisible()

        clock.return_value = 1_200
        _wait_until(application, lambda: overlay.wild.isVisible() and "pikachu" in overlay._art)
        assert overlay.wild.toolTip() == "Catch Pikachu"
        QTest.mouseClick(overlay.wild, Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: overlay.message.label.text() == "Caught Pikachu!")
        assert not overlay.wild.isVisible()
        assert engine.get_status()["caught_count"] == 2
        assert engine.get_status()["inventory"]["poke-ball"] == 3
    finally:
        _destroy_overlay(overlay)


def test_overlay_reacts_to_dev_commands_and_switches_from_the_tray(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (32, 40), "gold")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, True)))

    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 255)))
    overlay = app.Overlay(application, poll_interval_ms=50)

    try:
        _wait_until(application, lambda: 1 in overlay.companions)
        companion = overlay.companions[1].window
        assert companion.isVisible()
        assert not overlay.tray.icon().isNull()
        actions = overlay.tray_actions
        toggle = actions["toggle"]
        assert toggle.text() == "Hide overlay" and actions["quit"].text() == "Quit"
        assert actions["party"].text() == "Party" and actions["collection"].text() == "Collection"

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
        _wait_until(application, lambda: overlay._wild_species == "pikachu" and "pikachu" in overlay._art)
        assert not overlay.wild.isVisible()
        toggle.trigger()
        assert companion.isVisible() and overlay.wild.isVisible()

        QTest.mouseClick(overlay.wild, Qt.MouseButton.LeftButton)
        party_actions = overlay.tray_party_actions
        _wait_until(application, lambda: 2 in party_actions["add"])
        assert party_actions["add"][2].text().startswith("Pikachu")
        party_actions["add"][2].trigger()
        _wait_until(application, lambda: 2 in party_actions["lead"])
        party_actions["lead"][2].trigger()
        _wait_until(application, lambda: next(iter(party_actions["remove"]), None) == 2)
        assert engine.get_status()["active_pokemon"]["species"] == "pikachu"
        _click_companion(overlay.companions[2], monkeypatch)
        assert overlay.panel.name.text() == "Pikachu"
    finally:
        _destroy_overlay(overlay)


def test_three_companions_walk_and_share_xp_and_collection_data(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(Image.new("RGBA", (4, 3), "green"), True)))
    monkeypatch.setattr(game, "_roll_wild", Mock(side_effect=[("pikachu", 255), ("eevee", 255)]))
    caught = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)
    for _ in range(2):
        engine.try_catch(rng=Random(5), now=caught)
    for pokemon in engine.list_pokemon()[1:]:
        engine.add_to_party(pokemon["id"])
    engine.add_task("Choose a party member", 9)

    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: len(overlay.companions) == 3 and "eevee" in overlay._art)
        starter, pikachu, eevee = [overlay.companions[pokemon_id] for pokemon_id in (1, 2, 3)]
        assert all(member.window.isVisible() for member in (starter, pikachu, eevee))
        assert len({member.window.x() for member in (starter, pikachu, eevee)}) == 3
        assert len({id(member.motion) for member in (starter, pikachu, eevee)}) == 3

        _click_companion(eevee, monkeypatch)
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

        overlay.tray_actions["collection"].trigger()
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
        _destroy_overlay(overlay)


def test_tray_survives_updates_and_two_overlay_lifetimes(application, monkeypatch, tmp_path):
    art = Image.new("RGBA", (3, 2), "green")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, False)))
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 255)))

    for index in range(2):
        monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / f"save-{index}.db"))
        assert engine.try_catch(rng=Random(5))["caught"]
        overlay = app.Overlay(application, poll_interval_ms=50)
        try:
            _wait_until(application, lambda: 2 in overlay.tray_party_actions["add"])
            assert overlay.tray.parent() is overlay
            assert overlay.tray_menu.parentWidget() is overlay.panel
            actions = overlay.tray_actions
            add = overlay.tray_party_actions["add"][2]
            remove = overlay.tray_party_actions["remove"][1]
            owned = (
                overlay, overlay.tray, overlay.tray_menu, *actions.values(),
                *overlay.tray.party_menus.values(), *overlay.tray.party_menu_actions.values(),
                add, remove,
            )

            engine.give_xp(10)
            _wait_until(application, lambda: "Lv. 2" in overlay.tray_party_actions["remove"][1].text())
            assert overlay.tray_party_actions["remove"][1] is remove
            assert overlay.tray_party_actions["add"][2] is add
            assert all(map(isValid, owned))

            actions["toggle"].trigger()
            assert actions["toggle"].text() == "Show overlay"
            actions["collection"].trigger()
            assert overlay.collection.isVisible()

            overlay.stop()
            assert overlay.tray.contextMenu() is None
        finally:
            _destroy_overlay(overlay)
        assert not any(map(isValid, owned))


def test_six_companions_and_a_separate_wild_window_that_expires(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (3, 2), "green")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, False)))
    monkeypatch.setattr(game, "_roll_wild", Mock(side_effect=[
        (name, 255) for name in ("pikachu", "eevee", "pidgey", "gastly", "rattata", "lugia")
    ]))
    for ball in ["poke-ball"] * 3 + ["great-ball", "ultra-ball"]:
        assert engine.try_catch(ball, rng=Random(5))["caught"]
    for pokemon_id in range(2, 7):
        engine.add_to_party(pokemon_id)
    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        engine.spawn_wild()
        _wait_until(application, lambda: len(overlay.companions) == 6 and overlay.wild.isVisible())
        windows = [member.window for member in overlay.companions.values()]
        assert all(window.isVisible() and window.isWindow() for window in windows)
        assert len({(window.x(), window.y()) for window in windows}) == 6
        assert [member.pokemon["species"] for member in overlay.companions.values() if member.motion.floating] == [
            "pidgey", "gastly",
        ]
        assert overlay.wild not in windows
        assert overlay.wild.toolTip() == "Catch Lugia"
        deadline = engine.get_status()["wild_expires_at"]
        monkeypatch.setattr(app, "time", lambda: deadline + 1)
        _wait_until(application, lambda: not overlay.wild.isVisible())
        assert all(window.isVisible() for window in windows)
    finally:
        _destroy_overlay(overlay)


def test_drag_transfers_sprite_to_another_monitor_and_recovers_when_it_is_removed(
    application, monkeypatch, tmp_path,
):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(Image.new("RGBA", (3, 2), "green"), False)))
    overlay = app.Overlay(application)
    secondary = Mock()
    secondary.availableGeometry.return_value = QRect(-900, 100, 900, 600)
    try:
        _wait_until(application, lambda: 1 in overlay.companions)
        member = overlay.companions[1]
        primary = member.screen
        screen_at = overlay._screen_at
        monkeypatch.setattr(
            overlay, "_screen_at", lambda x, y: secondary if x < 0 and secondary in overlay._screens else screen_at(x, y),
        )
        overlay._screens.add(secondary)
        overlay._start_drag(1)
        overlay._drag(1, -300, 250)
        member.motion.end_drag()
        assert member.screen is secondary
        assert member.window.pos() == QPoint(-300, 250)
        assert member.motion.limits.left == -894
        overlay._screen_removed(secondary)
        assert member.screen is primary
        assert member.motion.limits.left <= member.window.x() <= member.motion.limits.right
    finally:
        overlay._screens.discard(secondary)
        _destroy_overlay(overlay)


def test_tray_quit_exits_the_overlay_event_loop_successfully(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(Image.new("RGBA", (3, 2), "green"), False)))
    overlay = app.Overlay(application)
    try:
        QTimer.singleShot(0, overlay.tray_actions["quit"].trigger)
        assert application.exec() == 0
    finally:
        _destroy_overlay(overlay)
