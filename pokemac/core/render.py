"""Render transparent sprites as ANSI true-color terminal text."""

from PIL import Image


def _rgb(pixel):
    red, green, blue, _ = pixel
    return f"{red};{green};{blue}"


def _half_block(top, bottom):
    if top[3] and bottom[3]:
        return f"\x1b[38;2;{_rgb(top)};48;2;{_rgb(bottom)}m▀\x1b[0m"
    if top[3]:
        return f"\x1b[38;2;{_rgb(top)}m▀\x1b[0m"
    if bottom[3]:
        return f"\x1b[38;2;{_rgb(bottom)}m▄\x1b[0m"
    return " "


def render_sprite(image: Image.Image, max_size: int = 32) -> str:
    """Fit the sprite into a square of terminal pixels and render two per cell."""
    image = image.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        return ""

    image = image.crop(bounds)
    image.thumbnail((max_size, max_size), Image.Resampling.NEAREST)
    if image.height % 2:
        padded = Image.new("RGBA", (image.width, image.height + 1))
        padded.paste(image)
        image = padded

    pixels = image.load()
    return "\n".join(
        "".join(_half_block(pixels[x, y], pixels[x, y + 1]) for x in range(image.width))
        for y in range(0, image.height, 2)
    )
