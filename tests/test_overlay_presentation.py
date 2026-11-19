from unittest.mock import Mock

from pokemac.core.api import PokeAPIError
from pokemac.overlay import presentation


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
