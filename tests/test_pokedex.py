import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from pokemac.cli.pokedex import main
from pokemac.core import api


@pytest.fixture
def pikachu():
    return {
        "name": "pikachu",
        "id": 25,
        "types": [{"type": {"name": "electric"}}],
        "stats": [
            {"stat": {"name": "hp"}, "base_stat": 35},
            {"stat": {"name": "attack"}, "base_stat": 55},
            {"stat": {"name": "defense"}, "base_stat": 40},
            {"stat": {"name": "special-attack"}, "base_stat": 50},
            {"stat": {"name": "special-defense"}, "base_stat": 50},
            {"stat": {"name": "speed"}, "base_stat": 90},
        ],
        "height": 4,
        "weight": 60,
    }


@pytest.fixture
def get(monkeypatch, tmp_path, pikachu):
    monkeypatch.chdir(tmp_path)
    response = Mock(status_code=200)
    response.json.return_value = pikachu
    get = Mock(return_value=response)
    monkeypatch.setattr(api.requests, "get", get)
    return get


def test_get_pokemon_summarizes_and_caches_response(get, pikachu):
    pokemon = api.get_pokemon(" Pikachu ")

    assert pokemon == {
        "name": "pikachu",
        "id": 25,
        "types": ["electric"],
        "base_stats": {
            "hp": 35,
            "attack": 55,
            "defense": 40,
            "special-attack": 50,
            "special-defense": 50,
            "speed": 90,
        },
        "height": 4,
        "weight": 60,
    }
    cache_file = Path(".cache/pikachu.json")
    assert json.loads(cache_file.read_text(encoding="utf-8")) == pikachu

    get.side_effect = requests.ConnectionError
    assert api.get_pokemon("pikachu") == pokemon
    get.assert_called_once_with("https://pokeapi.co/api/v2/pokemon/pikachu/", timeout=10)


def test_get_pokemon_replaces_invalid_cache(get, pikachu):
    cache_file = Path(".cache/pikachu.json")
    cache_file.parent.mkdir()
    cache_file.write_text("{", encoding="utf-8")

    assert api.get_pokemon("pikachu")["id"] == 25
    assert json.loads(cache_file.read_text(encoding="utf-8")) == pikachu


def test_get_pokemon_works_when_cache_is_unavailable(get):
    Path(".cache").touch()

    assert api.get_pokemon("pikachu")["id"] == 25


def test_get_pokemon_reports_unknown_name(get):
    get.return_value.status_code = 404

    with pytest.raises(api.PokemonNotFoundError, match="missingno"):
        api.get_pokemon("missingno")

    assert not Path(".cache/missingno.json").exists()


def test_get_pokemon_rejects_a_path_as_a_name(get):
    with pytest.raises(api.PokemonNotFoundError):
        api.get_pokemon("../pikachu")

    get.assert_not_called()


def test_get_pokemon_reports_connection_failure(get):
    get.side_effect = requests.ConnectionError

    with pytest.raises(api.PokeAPIError, match="Could not fetch data"):
        api.get_pokemon("pikachu")


def test_cli_prints_pokemon_summary(get, capsys):
    assert main(["pikachu"]) == 0

    output = capsys.readouterr()
    assert "Pikachu  #0025" in output.out
    assert "Types:  Electric" in output.out
    assert "Height: 0.4 m" in output.out
    assert "Weight: 6 kg" in output.out
    assert "Base stats" in output.out
    assert "HP" in output.out
    assert "Special Attack" in output.out
    assert output.err == ""


def test_cli_prints_unknown_name_to_stderr(get, capsys):
    get.return_value.status_code = 404

    assert main(["missingno"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Error: Unknown Pokémon: 'missingno'.\n"
