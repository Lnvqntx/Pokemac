import os
from datetime import datetime, timezone
from random import Random
from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from shiboken6 import isValid

from pokemac import engine
from pokemac.cli import game as game_cli
from pokemac.engine import game
from pokemac.engine.database import database
from pokemac.integrations import Completion
from pokemac.overlay import app
from pokemac.overlay import widgets
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


def test_github_issue_opens_its_link_while_sync_runs_in_background(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (4, 4), "green")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, False)))
    finished = Event()
    url = "https://github.com/owner/repo/issues/3"

    def sync():
        with database(write=True) as db:
            db.execute("INSERT INTO github_connection (id, login, last_success_at) VALUES (1, 'trainer', 1)")
            db.execute("INSERT INTO github_repositories VALUES ('owner/repo', 0, NULL)")
            db.execute("""
                INSERT INTO integration_open_tasks
                VALUES ('github', 'owner/repo', '304', 'Polish search', 'quest',
                        'medium', '2026-06-01T00:00:00+00:00', ?, NULL, NULL)
            """, (url,))
        finished.set()

    opened = Mock()
    monkeypatch.setattr(widgets, "QDesktopServices", Mock(openUrl=opened))
    monkeypatch.setattr(app, "sync_github", sync)
    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: finished.is_set() and "bulbasaur" in overlay._art)
        _click_companion(overlay.companions[1], monkeypatch)
        _wait_until(application, lambda: _button(overlay.panel, "Open issue") is not None)
        link = _button(overlay.panel, "Open issue")
        assert link.toolTip() == url
        link.click()
        assert opened.call_args.args[0].toString() == url
        assert _button(overlay.panel, "Done") is None
        assert overlay.tray_actions["github"].text() == "GitHub · up to date"
        assert engine.list_tasks("completed") == []
        overlay.tray_actions["board"].trigger()
        overlay.board.tabs.setCurrentIndex(1)
        _button(overlay.board.quests, "Open issue").click()
        assert opened.call_args.args[0].toString() == url
        assert _button(overlay.board.quests, "Done") is None
    finally:
        _destroy_overlay(overlay)


def test_quest_board_covers_all_tabs_and_shows_completion_and_encounter_toasts(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    clock = datetime(2026, 11, 20, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(game, "_now", lambda now: clock if now is None else now)
    monkeypatch.setattr(app, "time", clock.timestamp)
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(Image.new("RGBA", (4, 4), "green"), True)))
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("muk", 75)))
    monkeypatch.setattr(application, "applicationState", lambda: Qt.ApplicationState.ApplicationInactive)
    native = Mock()
    monkeypatch.setattr(app, "notify_macos", native)
    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: "bulbasaur" in overlay._art)
        overlay.tray_actions["board"].trigger()
        board = overlay.board
        assert board.isVisible()
        assert [board.tabs.tabText(i) for i in range(board.tabs.count())] == [
            "Today", "Quests", "Completed", "Collection",
        ]
        assert board.today.rows.count() == 3
        assert board.collection.rowCount() == 1
        board.reroll.click()
        _wait_until(application, lambda: not engine.get_quest_board()["can_reroll"])
        _wait_until(application, lambda: not board.reroll.isEnabled() and board.add_button.isEnabled())

        board.tabs.setCurrentIndex(1)
        board.title_input.setText("Prepare the release")
        board.difficulty.setCurrentIndex(board.difficulty.findData("medium"))
        board.add_button.click()
        _wait_until(application, lambda: _button(board.quests, "Done") is not None)
        _button(board.quests, "Done").click()
        _wait_until(application, lambda: overlay.reward_toast.label.text() == "Quest complete: +40 XP, +4 balls")
        assert board.completed.rows.count() == 1
        assert any("+40 XP" in label.text() for label in board.completed.findChildren(QLabel))
        assert engine.get_quest_board()["completed"][0]["title"] == "Prepare the release"
        assert overlay._wild_species == "muk"
        overlay._reward_timer.stop()
        overlay._show_next_reward()
        assert overlay.reward_toast.label.text() == "A wild Muk appeared!"
        _wait_until(application, lambda: native.call_count == 2)

        board.hide()
        _click_companion(overlay.companions[1], monkeypatch)
        _button(overlay.panel, "Quest board").click()
        assert board.isVisible()
    finally:
        _destroy_overlay(overlay)


def test_a_reward_from_the_startup_sync_surfaces_once_in_the_overlay(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    monkeypatch.setenv("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", "0")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(Image.new("RGBA", (4, 4), "green"), True)))
    monkeypatch.setattr(application, "applicationState", lambda: Qt.ApplicationState.ApplicationActive)
    completion = Completion("github", "board-sync-1", "Close upstream issue", "quest", "easy",
                            datetime.now().astimezone())
    monkeypatch.setattr(app, "sync_github", lambda: engine.reward_completion(completion))
    native = Mock()
    monkeypatch.setattr(app, "notify_macos", native)
    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: overlay.reward_toast.label.text() == "Quest complete: +20 XP, +3 balls")
        assert overlay._reward_cursor == 1
        overlay._reward_timer.stop()
        overlay._show_next_reward()
        overlay.refresh()
        _wait_until(application, lambda: overlay._poll_job is None)
        assert not overlay.reward_toast.isVisible()
        assert not overlay._reward_queue
        native.assert_not_called()
    finally:
        _destroy_overlay(overlay)


def test_popup_preserves_widgets_and_geometry_across_fifty_polls(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (4, 4), "green")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, True)))
    title = "Review the release notes 🚀 and verify the checklist " * 8
    engine.add_task(title.rstrip(), difficulty="hard")
    for index in range(11):
        engine.add_task(f"Optional goal {index}", task_type="side_quest")
    engine.add_task("Grass training", task_type="training", training_type="grass")
    engine.add_task("Electric training", task_type="training", training_type="electric")
    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: "bulbasaur" in overlay._art)
        member = overlay.companions[1]
        _click_companion(member, monkeypatch)
        panel = overlay.panel
        overlay._poll_timer.stop()
        overlay._frame_timer.stop()
        application.processEvents()
        original = tuple(panel.rows.itemAt(i).widget() for i in range(panel.rows.count()))
        geometry = panel.geometry()
        assert geometry.width() == 360 and geometry.height() <= 420
        assert overlay._work_area(overlay._panel_screen).contains(geometry)
        assert not panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert panel.grab().toImage().pixelColor(0, 0).alpha() == 255
        assert panel.items.verticalScrollBar().maximum() > 0
        labels = {label.text() for label in panel.items.findChildren(QLabel)}
        assert {"Quests (1)", "Side quests (11)", "Training (1)", "Hard", "Grass training"} <= labels
        assert "Electric training" not in labels
        long_title = next(label for label in panel.items.findChildren(widgets.TaskTitle)
                          if label.toolTip() == title.rstrip())
        assert len(long_title.text().splitlines()) == 2
        assert long_title.text().endswith("…")

        member.window.move(member.window.x() + 30, member.window.y())
        for _ in range(50):
            overlay.refresh()
            overlay._poll_job.result(timeout=3)
            overlay._finish_work()
            application.processEvents()
            current = tuple(panel.rows.itemAt(i).widget() for i in range(panel.rows.count()))
            assert len(original) == len(current)
            assert all(before is after for before, after in zip(original, current))
            assert panel.geometry() == geometry
        engine.add_task("One more quest")
        overlay.refresh()
        overlay._poll_job.result(timeout=3)
        overlay._finish_work()
        assert panel.rows.itemAt(0).widget().text() == "Quests (2)"
        assert panel.geometry() == geometry
    finally:
        _destroy_overlay(overlay)


def test_showcase_switches_from_popup_and_tray_with_scaled_art(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (4, 4), "green")
    form_art = Image.new("RGBA", (4, 4), "orange")
    artwork = Mock(side_effect=lambda _, form="normal": (art if form == "normal" else form_art, True))
    monkeypatch.setattr(app, "load_artwork", artwork)
    with database(write=True) as db:
        db.execute("INSERT INTO pokemon (species, party_slot) VALUES ('charizard', 2)")
    engine.set_level(1, 100)
    engine.set_level(2, 100)
    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: "bulbasaur" in overlay._art and 2 in overlay.tray.form_actions)
        starter = overlay.companions[1]
        _click_companion(starter, monkeypatch)
        picker = overlay.panel.form_picker
        assert picker.isVisible() and picker.currentText() == "Normal"
        picker.setCurrentIndex(picker.findData("dynamax"))
        picker.activated.emit(picker.currentIndex())
        _wait_until(application, lambda: starter.window.width() == 144)
        assert starter.window.art.pixmap().deviceIndependentSize().width() == 144
        assert starter.window._glow.color().name() == "#ff303e"
        assert starter.window._pulse.state() == QAbstractAnimation.State.Running
        assert overlay.tray.form_actions[1]["dynamax"].isChecked()

        charizard_actions = overlay.tray.form_actions[2]
        mega_x = charizard_actions["mega-x"]
        mega_x.trigger()
        _wait_until(application, lambda: ("charizard", "mega-x") in overlay._art)
        artwork.assert_any_call("charizard", "mega-x")
        assert overlay.companions[2].window.width() == 96
        assert mega_x.isChecked()
        assert overlay.tray.form_actions[2]["mega-x"] is mega_x

        overlay.tray.form_actions[1]["normal"].trigger()
        _wait_until(application, lambda: starter.window.width() == 96)
        assert starter.window._pulse.state() == QAbstractAnimation.State.Stopped
        assert picker.currentText() == "Normal"
        assert [p["showcase_form"] for p in engine.list_pokemon()] == ["normal", "mega-x"]
    finally:
        _destroy_overlay(overlay)


def test_overlay_click_drag_poll_and_wild_catch_offscreen(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    art = Image.new("RGBA", (3, 2), "green")
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(art, False)))
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 45)))
    overlay = app.Overlay(application, poll_interval_ms=50)

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
        assert overlay.panel.progress_text.text() == f"Level 1 · 0 / {engine.xp_for_level(2)} XP"
        assert "5 balls" in overlay.panel.summary.text()

        assert game_cli.main(["task", "add", "A CLI task", "--type", "grass", "--xp", "5"]) == 0
        _wait_until(application, lambda: _button(overlay.panel, "Done") is not None)
        QTest.mouseClick(_button(overlay.panel, "Done"), Qt.MouseButton.LeftButton)
        level = engine.level_for_xp(5)
        _wait_until(application, lambda: overlay.panel.progress_text.text().startswith(f"Level {level} ·"))
        assert overlay.panel.progress.value() == 5 - engine.xp_for_level(level)
        assert engine.list_tasks("completed")[0]["title"] == "A CLI task"
        assert overlay.panel.summary.text() == "1 day streak · 5 balls"

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

        assert not overlay.wild.isVisible()
        monkeypatch.setattr(game, "random", Mock(random=Mock(side_effect=[0.9, 0.0])))
        engine.spawn_wild()
        _wait_until(application, lambda: overlay.wild.isVisible() and "pikachu" in overlay._art)
        assert overlay.wild.toolTip() == "Catch Pikachu"
        QTest.mouseClick(overlay.wild, Qt.MouseButton.LeftButton)
        assert overlay.ball_picker.isVisible()
        poke = overlay.ball_picker.buttons["poke-ball"]
        assert poke.text() == "Poké Ball · 3 available · 42.008403%"
        assert overlay.ball_picker.buttons["master-ball"].text() == "Master Ball · 0 available · 100%"
        assert not overlay.ball_picker.buttons["master-ball"].isEnabled()
        QTest.mouseClick(poke, Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: overlay.message.label.text() == "Pikachu broke free. 2 throws left.")
        assert overlay.wild.isVisible() and overlay.ball_picker.isVisible()
        assert "2 available" in poke.text()
        QTest.mouseClick(poke, Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: overlay.message.label.text() == "Caught Pikachu!")
        assert not overlay.ball_picker.isVisible()
        assert not overlay.wild.isVisible()
        assert engine.get_status()["caught_count"] == 2
        assert engine.get_status()["inventory"]["poke-ball"] == 1
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

        assert game_cli.main(["dev", "give-xp", str(engine.xp_for_level(2))]) == 0
        _wait_until(application, lambda: overlay.message.label.text() == "Bulbasaur reached level 2!")
        assert game_cli.main(["dev", "give-xp", str(engine.xp_for_level(16) - engine.xp_for_level(2))]) == 0
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
        QTest.mouseClick(overlay.ball_picker.buttons["poke-ball"], Qt.MouseButton.LeftButton)
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


def test_three_companions_walk_and_route_training_xp_and_collection_data(application, monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    monkeypatch.setattr(app, "load_artwork", Mock(return_value=(Image.new("RGBA", (4, 3), "green"), True)))
    monkeypatch.setattr(game, "_roll_wild", Mock(side_effect=[("pikachu", 255), ("eevee", 255)]))
    caught = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)
    for _ in range(2):
        engine.spawn_wild(now=caught)
        engine.try_catch(rng=Random(5), now=caught)
    for pokemon in engine.list_pokemon()[1:]:
        engine.add_to_party(pokemon["id"])
    engine.add_task("Train Pikachu", 9, task_type="training", training_type="electric")

    overlay = app.Overlay(application, poll_interval_ms=50)
    try:
        _wait_until(application, lambda: len(overlay.companions) == 3 and "eevee" in overlay._art)
        starter, pikachu, eevee = [overlay.companions[pokemon_id] for pokemon_id in (1, 2, 3)]
        assert all(member.window.isVisible() for member in (starter, pikachu, eevee))
        assert len({member.window.x() for member in (starter, pikachu, eevee)}) == 3
        assert len({id(member.motion) for member in (starter, pikachu, eevee)}) == 3

        _click_companion(eevee, monkeypatch)
        assert overlay.panel.name.text() == "Eevee"
        assert not overlay.panel.items.isVisible()
        assert not overlay.panel.summary.isVisible()
        assert overlay.panel.set_lead.isVisible()
        opening_position = overlay.panel.pos()
        starting_position = eevee.window.pos()
        _wait_until(application, lambda: eevee.window.pos() != starting_position)
        assert overlay.panel.pos() == opening_position

        overlay.panel.hide()
        _wait_until(application, lambda: "pikachu" in overlay._art)
        _click_companion(pikachu, monkeypatch)
        overlay.panel.set_lead.click()
        _wait_until(application, lambda: overlay._lead_id == 2 and _button(overlay.panel, "Done") is not None)
        assert all(pokemon["xp"] == 0 for pokemon in engine.list_party())
        assert not overlay.panel.set_lead.isVisible()
        QTest.mouseClick(_button(overlay.panel, "Done"), Qt.MouseButton.LeftButton)
        _wait_until(application, lambda: overlay.companions[2].pokemon["xp"] == 9)
        assert [pokemon["xp"] for pokemon in engine.list_party()] == [9, 0, 0]
        assert [pokemon["is_active"] for pokemon in engine.list_party()] == [1, 0, 0]

        overlay.tray_actions["collection"].trigger()
        table = overlay.collection.table
        assert overlay.collection.isVisible()
        assert table.rowCount() == 3
        assert [table.item(1, column).text() for column in range(5)] == [
            "2", "Pikachu", str(engine.level_for_xp(9)), "2026-08-10", "Lead",
        ]
        overlay.collection.hide()
        overlay.panel.hide()
        _click_companion(eevee, monkeypatch)
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
        engine.spawn_wild()
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

            engine.give_xp(engine.xp_for_level(2))
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
        engine.spawn_wild()
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
