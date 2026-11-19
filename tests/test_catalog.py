import json
from collections import Counter
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from PIL import Image

from pokemac import engine
from pokemac.catalog import data, sync
from pokemac.cli import data as cli_data, pokedex
from pokemac.core import api
from pokemac.core.retry import RequestRetry
from pokemac.engine import progression
from pokemac.overlay import presentation


FIXTURE = Path(__file__).with_name("fixtures") / "pokeapi_catalog.json"


@pytest.fixture
def server(monkeypatch, tmp_path):
    fixture = json.loads(FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "game.db"))
    monkeypatch.setattr(data, "CATALOG_PATH", tmp_path / "catalog.json")
    monkeypatch.setattr(sync, "CATALOG_PATH", tmp_path / "catalog.json")

    def fetch(url, *, timeout):
        target = urlsplit(url)
        assert target.netloc == "pokeapi.co" and timeout == 10
        parts = target.path.strip("/").split("/")
        resource = parts[2]
        if len(parts) == 3 and resource == "pokemon-species":
            names = list(fixture["species"])
            offset = int(parse_qs(target.query)["offset"][0])
            next_offset = offset + 4
            payload = {
                "count": len(names),
                "next": f"?offset={next_offset}&limit=100" if next_offset < len(names) else None,
                "results": [{"name": name} for name in names[offset:next_offset]],
            }
        elif resource == "generation":
            payload = {"pokemon_species": [
                {"name": name} for name, species in fixture["species"].items()
                if species["generation"]["url"].endswith(f"/{parts[3]}/")
            ]}
        else:
            sections = {"pokemon-species": "species", "pokemon": "varieties", "evolution-chain": "chains"}
            payload = fixture[sections[resource]][parts[3]]
        response = Mock(status_code=200)
        response.json.return_value = payload
        return response

    get = Mock(side_effect=fetch)
    monkeypatch.setattr(api.requests, "get", get)
    return fixture, get


@pytest.fixture
def catalog(server):
    sync.sync_catalog(progress=lambda _: None)
    _, get = server
    get.reset_mock()
    get.side_effect = AssertionError("Unexpected network request")
    return data.load_catalog()


def test_full_sync_deduplicates_chains_and_resumes_from_cache(server):
    fixture, get = server
    first = sync.sync_catalog(progress=lambda _: None)
    expected_requests = 2 + sum(len(records) for records in fixture.values())
    assert get.call_count == expected_requests
    urls = Counter(call.args[0] for call in get.call_args_list)
    assert urls[f"{api.API_URL}/evolution-chain/67/"] == 1
    assert all(count == 1 for count in urls.values())
    assert first["evolution_chains"] == fixture["chains"]
    assert len(first["species"]) == len(fixture["species"])
    assert all(species["dynamax_capable"] for species in first["species"])
    assert all("sprites" not in variety for species in first["species"] for variety in species["varieties"])

    content = sync.CATALOG_PATH.read_bytes()
    get.reset_mock()
    sync.sync_catalog(progress=lambda _: None)
    get.assert_not_called()
    assert sync.CATALOG_PATH.read_bytes() == content

    Path(".cache/charizard-mega-y.json").unlink()
    sync.sync_catalog(progress=lambda _: None)
    get.assert_called_once_with(f"{api.API_URL}/pokemon/charizard-mega-y/", timeout=10)
    assert sync.CATALOG_PATH.read_bytes() == content


def test_generation_sync_keeps_other_generations_and_warns_on_size(server, monkeypatch):
    data_gen2 = sync.sync_catalog(2, progress=lambda _: None)
    assert [species["name"] for species in data_gen2["species"]] == ["lugia"]
    monkeypatch.setattr(sync, "MAX_CATALOG_BYTES", 1)
    with pytest.warns(UserWarning, match="above the 3 MB target"):
        data_both = sync.sync_catalog(3, progress=lambda _: None)
    assert [species["name"] for species in data_both["species"]] == ["lugia", "groudon", "deoxys"]
    assert set(data_both["evolution_chains"]) == {"127", "199", "202"}


def test_form_detection_uses_separate_variety_slugs(catalog):
    charizard, default = catalog.find("Charizard")
    mega_x, dynamax = data.select_form(charizard, "Mega X")
    mega_y, _ = data.select_form(charizard, "charizard-mega-y")
    gmax, _ = data.select_form(charizard, "gigantamax")
    assert (mega_x["name"], mega_y["name"]) == ("charizard-mega-x", "charizard-mega-y")
    assert not dynamax and mega_x["is_mega"] and mega_y["is_mega"] and not mega_x["is_default"]
    assert gmax["is_gmax"] and not gmax["is_mega"]
    assert mega_x["official_artwork_url"].startswith("https://")
    assert data.select_form(charizard, "dynamax") == (default, True)
    with pytest.raises(data.CatalogError, match="mega-x, mega-y"):
        data.select_form(charizard, "mega")

    lugia, default_lugia = catalog.find("249")
    assert not default_lugia["is_mega"] and not default_lugia["is_gmax"]
    assert data.showcase_forms(lugia) == [("dynamax", "Dynamax effect")]
    _, alola = catalog.find("raichu-alola")
    assert alola["is_regional"] and alola["region"] == "alola"
    _, primal = catalog.find("groudon-primal")
    assert primal["is_primal"] and not primal["is_mega"]
    assert catalog.find("deoxys")[1]["name"] == "deoxys-normal"


def test_eevee_chain_preserves_branches_and_unsupported_conditions(catalog, server):
    eevee, _ = catalog.find("eevee")
    chain = catalog.evolution_chains[str(eevee["evolution_chain_id"])]
    assert chain == server[0]["chains"]["67"]
    branches = {node["species"]["name"]: node["evolution_details"] for node in chain["chain"]["evolves_to"]}
    assert len(branches) == 8
    assert branches["vaporeon"][0]["item"]["name"] == "water-stone"
    assert branches["espeon"][0]["min_happiness"] > 0
    assert progression.evolved_species("eevee", 100) == "eevee"
    assert progression.evolved_species("rattata", 19) == "rattata"
    assert progression.evolved_species("rattata", 20) == "raticate"
    assert progression._follow_level_evolutions(catalog.evolution_chains["2"]["chain"], "charmander", 36) == (
        "charizard"
    )


def test_legendary_and_mythical_tiers_and_lugia_spawn(catalog):
    stats = data.catalog_stats(catalog)
    assert stats["rarity"] == {"common": 1, "uncommon": 1, "rare": 3, "legendary": 2, "mythical": 1}
    assert data.rarity_tier({"is_mythical": True, "is_legendary": True, "capture_rate": 255}) == "mythical"
    rng = Mock()
    rng.choices.return_value = ["legendary"]
    rng.choice.side_effect = lambda entries: next(entry for entry in entries if entry["name"] == "lugia")
    rng.random.return_value = 0

    assert engine.spawn_wild(rng=rng) == "lugia"
    rng.choices.assert_called_once_with(
        ["common", "uncommon", "rare", "legendary", "mythical"],
        weights=[7000, 2200, 730, 65, 5], k=1,
    )
    assert engine.get_status()["wild_species"] == "lugia"
    result = engine.try_catch(rng=rng)
    assert result["caught"] and result["chance"] == pytest.approx(3 / 255)
    assert rng.choices.call_count == 1


def test_catalog_cli_and_pokedex_forms_are_resolved_from_the_fixture(catalog, monkeypatch, capsys):
    image = Image.new("RGBA", (1, 1), (255, 180, 0, 255))
    monkeypatch.setattr(pokedex, "get_official_artwork", Mock(return_value=image))
    monkeypatch.setattr(pokedex, "get_sprite", Mock(return_value=image))

    assert cli_data.main(["stats"]) == 0
    assert cli_data.main(["forms", "charizard"]) == 0
    assert cli_data.main(["forms", "lugia"]) == 0
    assert pokedex.main(["deoxys"]) == 0
    assert pokedex.main(["charizard", "--form", "mega-y"]) == 0
    assert pokedex.main(["lugia", "--form", "dynamax"]) == 0
    assert pokedex.main(["lugia", "--form", "gmax"]) == 1
    output = capsys.readouterr()
    for label in ("Species: 8", "Gen 2: 1 species", "mega-x · Mega", "mega-y · Mega", "gmax · Gigantamax",
                  "Lugia showcase forms", "Deoxys Normal", "Charizard Mega Y", "Showcase: Dynamax effect"):
        assert label in output.out
    assert "lugia has no form named 'gmax'" in output.err


def test_overlay_resolves_a_species_with_a_different_default_variety(catalog, monkeypatch):
    official = Mock(side_effect=api.PokeAPIError("No artwork"))
    sprite = Mock(return_value="sprite")
    monkeypatch.setattr(presentation, "get_official_artwork", official)
    monkeypatch.setattr(presentation, "get_sprite", sprite)
    assert presentation.load_artwork("deoxys") == ("sprite", False)
    official.assert_called_once_with("deoxys-normal")
    sprite.assert_called_once_with("deoxys-normal")


def test_retry_backs_off_on_transport_errors_and_honors_retry_after():
    now = [0.0]
    delays = []

    def wait(delay):
        delays.append(delay)
        now[0] += delay

    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "6"
    request = Mock(side_effect=[requests.ConnectionError(), requests.HTTPError(response=response), "done"])
    retry = RequestRetry(clock=lambda: now[0], wait=wait)

    assert retry(request) == "done"
    assert delays == [1, 6]
    assert request.call_count == 3
