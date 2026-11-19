"""Download and cache the default front sprites listed by PokéAPI."""

from io import BytesIO

from PIL import Image
import requests

from pokemac.core.api import (
    PokeAPIError,
    get_front_sprite_url,
    get_official_artwork_url,
    normalize_name,
)
from pokemac.core.cache import CACHE_DIR, write_cache


SPRITE_CACHE_DIR = CACHE_DIR / "sprites"
ARTWORK_CACHE_DIR = CACHE_DIR / "artwork"


class SpriteError(PokeAPIError):
    """The Pokémon's sprite is missing or could not be loaded."""


def _load_image(source):
    with Image.open(source) as image:
        return image.convert("RGBA")


def _get_image(name, directory, image_url, kind):
    slug = normalize_name(name)
    cache_path = directory / f"{slug}.png"
    try:
        return _load_image(cache_path)
    except OSError:
        pass

    url = image_url(name)
    if not url:
        raise SpriteError(f"PokéAPI has no {kind} for {name!r}.")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        raise SpriteError(f"Could not download the {kind} for {name!r}.") from error

    try:
        sprite = _load_image(BytesIO(response.content))
    except OSError as error:
        raise SpriteError(f"Could not decode the {kind} for {name!r}.") from error

    try:
        write_cache(cache_path, response.content)
    except OSError:
        # The downloaded image can still be displayed without a writable cache.
        pass
    return sprite


def get_sprite(name: str) -> Image.Image:
    """Return the Pokémon's front sprite as a loaded RGBA image."""
    return _get_image(name, SPRITE_CACHE_DIR, get_front_sprite_url, "front sprite")


def get_official_artwork(name: str) -> Image.Image:
    """Return the Pokémon's official artwork as a loaded RGBA image."""
    return _get_image(name, ARTWORK_CACHE_DIR, get_official_artwork_url, "official artwork")
