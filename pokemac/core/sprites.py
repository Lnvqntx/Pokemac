"""Download and cache the default front sprites listed by PokéAPI."""

from io import BytesIO

from PIL import Image
import requests

from pokemac.core.api import PokeAPIError, get_front_sprite_url, normalize_name
from pokemac.core.cache import CACHE_DIR, write_cache


SPRITE_CACHE_DIR = CACHE_DIR / "sprites"


class SpriteError(PokeAPIError):
    """The Pokémon's sprite is missing or could not be loaded."""


def _load_image(source):
    with Image.open(source) as image:
        return image.convert("RGBA")


def get_sprite(name: str) -> Image.Image:
    """Return the Pokémon's front sprite as a loaded RGBA image."""
    slug = normalize_name(name)
    cache_path = SPRITE_CACHE_DIR / f"{slug}.png"
    try:
        return _load_image(cache_path)
    except OSError:
        pass

    sprite_url = get_front_sprite_url(name)
    if not sprite_url:
        raise SpriteError(f"PokéAPI has no front sprite for {name!r}.")

    try:
        response = requests.get(sprite_url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        raise SpriteError(f"Could not download the sprite for {name!r}.") from error

    try:
        sprite = _load_image(BytesIO(response.content))
    except OSError as error:
        raise SpriteError(f"Could not decode the sprite for {name!r}.") from error

    try:
        write_cache(cache_path, response.content)
    except OSError:
        # The downloaded image can still be displayed without a writable cache.
        pass
    return sprite
