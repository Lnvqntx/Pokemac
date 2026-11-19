import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, call

import pytest
import requests
from PIL import Image

from pokemac.cli import greet
from pokemac.core.render import render_sprite
from pokemac.core.sprites import SpriteError, get_sprite


SPRITE_URL = "https://example.com/front.png"
SPRITE_PATH = Path(".cache/sprites/pikachu.png")


@pytest.fixture
def sprite_png(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    output = BytesIO()
    Image.new("RGBA", (2, 2), (255, 215, 0, 255)).save(output, format="PNG")
    return output.getvalue()


def test_get_sprite_downloads_and_reuses_cached_image(monkeypatch, sprite_png):
    metadata = Mock(status_code=200)
    metadata.json.return_value = {
        "sprites": {
            "front_default": SPRITE_URL,
            "back_default": "https://example.com/back.png",
        }
    }
    get = Mock(side_effect=[metadata, Mock(content=sprite_png)])
    monkeypatch.setattr(requests, "get", get)

    image = get_sprite(" Pikachu ")

    assert image.mode == "RGBA"
    assert image.getpixel((0, 0)) == (255, 215, 0, 255)
    assert SPRITE_PATH.read_bytes() == sprite_png
    assert get.call_args_list == [
        call("https://pokeapi.co/api/v2/pokemon/pikachu/", timeout=10),
        call(SPRITE_URL, timeout=10),
    ]

    Path(".cache/pikachu.json").unlink()
    get.reset_mock()
    get.side_effect = requests.ConnectionError
    assert get_sprite("pikachu").tobytes() == image.tobytes()
    get.assert_not_called()


def test_get_sprite_replaces_corrupt_image_using_cached_url(monkeypatch, sprite_png):
    SPRITE_PATH.parent.mkdir(parents=True)
    SPRITE_PATH.write_bytes(b"not a valid PNG")
    Path(".cache/pikachu.json").write_text(
        json.dumps({"sprites": {"front_default": SPRITE_URL}}), encoding="utf-8"
    )
    get = Mock(return_value=Mock(content=sprite_png))
    monkeypatch.setattr(requests, "get", get)

    assert get_sprite("pikachu").size == (2, 2)
    assert SPRITE_PATH.read_bytes() == sprite_png
    get.assert_called_once_with(SPRITE_URL, timeout=10)


def test_get_sprite_does_not_cache_invalid_download(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    metadata = Mock(status_code=200)
    metadata.json.return_value = {"sprites": {"front_default": SPRITE_URL}}
    get = Mock(side_effect=[metadata, Mock(content=b"not a valid PNG")])
    monkeypatch.setattr(requests, "get", get)

    with pytest.raises(SpriteError, match="Could not decode"):
        get_sprite("pikachu")

    assert not SPRITE_PATH.exists()


def test_render_sprite_colors_each_half_and_preserves_transparency():
    image = Image.new("RGBA", (4, 2))
    image.putpixel((0, 0), (255, 0, 0, 255))
    image.putpixel((0, 1), (0, 255, 0, 255))
    image.putpixel((2, 0), (0, 0, 255, 255))
    image.putpixel((3, 1), (255, 255, 0, 255))

    assert render_sprite(image) == (
        "\x1b[38;2;255;0;0;48;2;0;255;0m▀\x1b[0m "
        "\x1b[38;2;0;0;255m▀\x1b[0m"
        "\x1b[38;2;255;255;0m▄\x1b[0m"
    )


def test_greet_prints_the_selected_pokemons_name_and_sprite(capsys, monkeypatch):
    monkeypatch.setattr(greet.random, "randint", Mock(return_value=25))
    lookup = Mock(return_value={"name": "pikachu"})
    sprite = Mock(return_value=Image.new("RGBA", (1, 1), (255, 215, 0, 255)))
    monkeypatch.setattr(greet, "get_pokemon", lookup)
    monkeypatch.setattr(greet, "get_sprite", sprite)

    assert greet.main([]) == 0

    lookup.assert_called_once_with("25")
    sprite.assert_called_once_with("25")
    output = capsys.readouterr()
    assert output.out == "Pikachu\n\x1b[38;2;255;215;0m▀\x1b[0m\n"
    assert output.err == ""
