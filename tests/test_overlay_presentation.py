from unittest.mock import Mock

from pokemac.catalog.data import load_catalog, select_form
from pokemac.core.api import PokeAPIError
from pokemac.overlay import notifications, presentation


def test_artwork_is_preferred_and_front_sprite_is_the_fallback(monkeypatch):
    official = Mock(return_value="official artwork")
    front = Mock(return_value="front sprite")
    monkeypatch.setattr(presentation, "get_official_artwork", official)
    monkeypatch.setattr(presentation, "get_sprite", front)

    assert presentation.load_artwork("bulbasaur") == ("official artwork", True)
    front.assert_not_called()

    official.side_effect = PokeAPIError("No official artwork")
    assert presentation.load_artwork("bulbasaur") == ("front sprite", False)
    front.assert_called_once_with("bulbasaur")


def test_notice_distinguishes_switching_leveling_and_evolving():
    starter = {"id": 1, "nickname": "Bud", "species": "bulbasaur", "level": 1}
    assert presentation.progress_notice(None, starter) is None
    assert presentation.progress_notice(starter, dict(starter, xp=3)) is None
    assert presentation.progress_notice(starter, dict(starter, id=2, level=20)) is None
    assert presentation.progress_notice(starter, dict(starter, level=2)) == (
        presentation.ProgressNotice("Bud reached level 2!")
    )
    at_threshold = dict(starter, level=16)
    assert presentation.progress_notice(at_threshold, dict(at_threshold, species="ivysaur")) == (
        presentation.ProgressNotice("Bulbasaur evolved into Ivysaur at level 16!", evolved=True)
    )


def test_form_artwork_uses_catalog_urls_and_falls_back_to_normal(monkeypatch):
    official = Mock(return_value="form artwork")
    monkeypatch.setattr(presentation, "get_official_artwork", official)
    species, _ = load_catalog().find("charizard")
    for form in ("mega-x", "mega-y", "gmax"):
        variety, _ = select_form(species, form)
        assert presentation.load_artwork("charizard", form) == ("form artwork", True)
        official.assert_called_with(variety["name"], url=variety["official_artwork_url"])

    official.side_effect = [PokeAPIError("Form artwork unavailable"), "normal artwork"]
    assert presentation.load_artwork("charizard", "mega-y") == ("normal artwork", True)
    official.assert_called_with("charizard")


def test_macos_notification_passes_message_as_an_applescript_argument(monkeypatch):
    runner = Mock()
    monkeypatch.setattr(notifications.subprocess, "run", runner)
    monkeypatch.setattr(notifications.sys, "platform", "linux")
    notifications.notify_macos("Quest complete")
    runner.assert_not_called()
    monkeypatch.setattr(notifications.sys, "platform", "darwin")
    message = 'Quest complete: +40 XP, +3 balls "today"'
    notifications.notify_macos(message)
    args = runner.call_args.args[0]
    assert args == ["/usr/bin/osascript", "-", message]
    assert "display notification (item 1 of argv)" in runner.call_args.kwargs["input"]
