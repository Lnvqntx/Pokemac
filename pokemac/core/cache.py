"""Local cache storage shared by PokéAPI responses and sprites."""

from pathlib import Path
from tempfile import NamedTemporaryFile


CACHE_DIR = Path(".cache")


def write_cache(path: Path, content: bytes) -> None:
    """Replace a cache entry atomically so readers only see complete files."""
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
