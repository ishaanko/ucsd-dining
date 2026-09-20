"""Tool tests against a seeded SQLite cache. No network."""

import asyncio
from datetime import date, datetime, timedelta

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ucsd_dining import geo, scrape, server, store


@pytest.fixture(autouse=True)
def seeded_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("UCSD_DINING_DB", str(tmp_path / "test.db"))

    async def no_refresh(*args, **kwargs): ...

    monkeypatch.setattr(store, "ensure_fresh", no_refresh)
    db = store.connect()
    db.execute(
        "INSERT INTO venues VALUES ('01', 'Pines', 'Muir College', '', '', '{}'),"
        " ('64', '64 Degrees', 'Revelle College', '', '', '{}')"
    )

    def serving(item_id, name, meal, protein, **tags):
        return scrape.Serving(item_id, "", name, "", meal, "Grill", "Entrees", 500, 8.0, **tags), protein

    pines = [
        serving(1, "Tofu Bowl", "Lunch", 25.0, diet=["vegan"], allergens=["soy"]),
        serving(1, "Tofu Bowl", "Dinner", 25.0, diet=["vegan"], allergens=["soy"]),
        serving(2, "Cheese Pizza", "Dinner", 18.0, diet=["vegetarian"], allergens=["dairy", "wheat"]),
        serving(3, "Chicken Plate", "Dinner", 40.0),
    ]
    store._save_day(db, "01", scrape.DayMenu(store.today(), [s for s, _ in pines]))
    for s, protein in pines:
        store._save_nutrition(db, s.item_id, scrape.Nutrition(None, None, {"protein_g": protein}))


def names(result: dict) -> list[str]:
    return [item["name"] for item in result["items"]]


def test_search_sorts_by_protein_and_merges_meals():
    result = asyncio.run(server.search_items(venue="muir"))
    assert names(result) == ["Chicken Plate", "Tofu Bowl", "Cheese Pizza"]
    assert result["items"][1]["meals"] == ["Lunch", "Dinner"]


def test_search_filters():
    assert names(asyncio.run(server.search_items(diet="vegetarian"))) == ["Tofu Bowl", "Cheese Pizza"]
    assert names(asyncio.run(server.search_items(diet="vegan"))) == ["Tofu Bowl"]
    assert names(asyncio.run(server.search_items(exclude_allergens=["dairy", "soy"]))) == ["Chicken Plate"]
    assert names(asyncio.run(server.search_items(query="pizza", meal="lunch"))) == []
    assert names(asyncio.run(server.search_items(min_protein_g=30))) == ["Chicken Plate"]


def test_unknown_venue_error_lists_venues():
    with pytest.raises(ToolError, match="Pines \\(Muir College\\)"):
        asyncio.run(server.get_menu("nope"))


def test_date_out_of_range_gives_note():
    result = asyncio.run(server.search_items(date=(store.today() + timedelta(days=30)).isoformat()))
    assert "next 6 days" in result["note"]


def test_parse_date(monkeypatch):
    monkeypatch.setattr(store, "today", lambda: date(2026, 9, 19))  # a Saturday
    assert server._parse_date("tomorrow") == date(2026, 9, 20)
    assert server._parse_date("Saturday") == date(2026, 9, 19)
    assert server._parse_date("fri") == date(2026, 9, 25)
    with pytest.raises(ToolError):
        server._parse_date("next year")


def test_near_adds_walk_times_and_filters(monkeypatch):
    async def fake_walks(db, place):
        return {"01": geo.Walk(minutes=4.4, meters=350), "64": geo.Walk(minutes=15, meters=1100)}

    monkeypatch.setattr(geo, "walks_from", fake_walks)
    # "lat,lon" needs no place lookup, so there is no network call.
    result = asyncio.run(server.search_items(near="32.8790,-117.2425", sort_by="walk_time"))
    assert result["items"][0]["walk_minutes"] == 4
    assert names(asyncio.run(server.search_items(near="32.8790,-117.2425", max_walk_minutes=3))) == []
    with pytest.raises(ToolError, match="near"):
        asyncio.run(server.search_items(sort_by="walk_time"))


def test_minutes_until_close():
    at = lambda h, m: datetime(2026, 9, 19, h, m)  # noqa: E731
    assert server._minutes_until_close("8:00 AM - 9:00 PM", at(19, 38)) == 82
    assert server._minutes_until_close("8:00 AM - 9:00 PM", at(21, 0)) is None
    assert server._minutes_until_close("Closed", at(12, 0)) is None
