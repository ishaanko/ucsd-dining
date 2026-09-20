"""MCP server for UCSD dining menus."""

import argparse
import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import geo, store

INSTRUCTIONS = """\
Menus, prices, nutrition, and allergens for UCSD HDH dining halls, from hdh-web.ucsd.edu.
Menus exist for today and the next 6 days (Pacific time). Start with search_items for
questions about food ("high protein vegan dinner", "what has no dairy at Pines"). Use
list_venues for hours, locations, and walking times, get_menu to browse one venue, get_item for full
nutrition and ingredients. When the user says where they are, pass it as "near" to get
real walking times. Items are a la carte; prices are in USD.
Allergen data comes from HDH icons. It can be incomplete. For a serious allergy, tell the
user to confirm with dining staff."""

mcp = MCPServer("ucsd-dining", instructions=INSTRUCTIONS)

Diet = Literal["vegan", "vegetarian"]
Allergen = Literal[
    "dairy", "eggs", "fish", "gluten", "peanuts", "sesame", "shellfish", "soy", "tree nuts", "wheat"
]
SortBy = Literal[
    "protein", "calories", "price", "protein_per_dollar", "protein_per_calorie", "walk_time"
]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_date(text: str) -> date:
    """Accept "today", "tomorrow", a weekday name (the next one, today included), or YYYY-MM-DD."""
    text = text.strip().lower()
    today = store.today()
    if text in ("", "today", "tonight"):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    for index, name in enumerate(WEEKDAYS):
        if text == name or text == name[:3]:
            return today + timedelta(days=(index - today.weekday()) % 7)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ToolError(
            f'Bad date "{text}". Use "today", "tomorrow", a weekday name, or YYYY-MM-DD.'
        ) from None


def _find_venues(db: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    """Match on venue name or campus area, case-insensitive substring. Example: "muir" finds Pines."""
    venues = db.execute("SELECT * FROM venues ORDER BY name").fetchall()
    needle = query.strip().lower()
    found = [v for v in venues if needle in v["name"].lower() or needle in (v["area"] or "").lower()]
    if not found:
        names = ", ".join(f'{v["name"]} ({v["area"]})' for v in venues)
        raise ToolError(f'No venue matches "{query}". Venues: {names}')
    return found


def _minutes_until_close(hours: str, now: datetime) -> int | None:
    """Minutes until closing time, or None when the venue is not open at this time."""
    if " - " not in hours:
        return None
    start, end = (datetime.strptime(t, "%I:%M %p").time() for t in hours.split(" - "))
    if not start <= now.time() < end:
        return None
    return (end.hour - now.hour) * 60 + end.minute - now.minute


async def _walks(db: sqlite3.Connection, near: str | None) -> tuple[geo.Place, dict[str, geo.Walk]] | None:
    if not near:
        return None
    try:
        place = await geo.locate(db, near)
    except geo.PlaceNotFound as error:
        raise ToolError(str(error)) from None
    return place, await geo.walks_from(db, place)


def _load(db: sqlite3.Connection, day: date, venue_ids: list[str] | None, meal: str | None) -> list[dict]:
    """Servings for one day, joined with item data. One dict per menu line."""
    sql = """SELECT s.venue_id, s.meal, s.station, s.category, s.price, v.name AS venue, i.*
             FROM servings s JOIN items i ON i.id = s.item_id JOIN venues v ON v.id = s.venue_id
             WHERE s.date = ?"""
    args: list[str] = [day.isoformat()]
    if venue_ids is not None:
        sql += f" AND s.venue_id IN ({', '.join('?' for _ in venue_ids)})"
        args += venue_ids
    if meal:
        sql += " AND lower(s.meal) = ?"
        args.append(meal.strip().lower().replace("_", " "))
    rows = [dict(r) for r in db.execute(sql, args)]
    for row in rows:
        row["diet"] = json.loads(row["diet"])
        row["allergens"] = json.loads(row["allergens"])
    return rows


def _brief(row: dict) -> dict:
    """Short item form for lists. Empty fields are left out to save tokens."""
    out = {
        "id": row["id"],
        "name": row["name"],
        "price": row["price"],
        "calories": row["calories"],
        "protein_g": row["protein_g"],
        "carbs_g": row["carbs_g"],
        "fat_g": row["fat_g"],
        "diet": [d for d in row["diet"] if d in ("vegan", "vegetarian")],
        "allergens": row["allergens"],
    }
    out = {k: v for k, v in out.items() if v not in (None, [])}
    if _macros_disagree(row):
        out["warning"] = "HDH nutrition data for this item is inconsistent. Do not rely on it."
    return out


def _macros_disagree(row: dict) -> bool:
    """True when the macros give far more calories than the listed calories (HDH data error)."""
    if None in (row["calories"], row["protein_g"], row["carbs_g"], row["fat_g"]):
        return False
    implied = 4 * row["protein_g"] + 4 * row["carbs_g"] + 9 * row["fat_g"]
    return implied > 1.5 * row["calories"] + 50


def _line(row: dict) -> str:
    """One-line item form for full menus. About 5 times smaller than the dict form."""
    parts = [row["name"]]
    if row["price"] is not None:
        parts.append(f'${row["price"]:.2f}')
    if row["calories"] is not None:
        parts.append(f'{row["calories"]} cal')
    if row["protein_g"] is not None:
        parts.append(f'{row["protein_g"]:g}g protein')
    parts += [d for d in row["diet"] if d in ("vegan", "vegetarian")]
    if row["allergens"]:
        parts.append("contains " + ", ".join(row["allergens"]))
    return " | ".join(parts) + f' | id {row["id"]}'


def _no_menu_note(day: date) -> str:
    offset = (day - store.today()).days
    if 0 <= offset <= store.DAYS_AHEAD:
        return "HDH has no menu for this selection. The venue can be closed for that day or meal."
    return f"No data. HDH publishes menus only for today and the next {store.DAYS_AHEAD} days."


@mcp.tool()
async def list_venues(date: str = "today", near: str | None = None) -> list[dict]:
    """List all UCSD dining halls with campus area, hours, meals served, and walking times.

    date: "today", "tomorrow", a weekday name, or YYYY-MM-DD.
    near: where the user is. A campus place name ("Geisel Library", "Warren Lecture Hall",
    "muir") or "lat,lon". Adds walk_minutes and walk_meters (real paths, from
    OpenStreetMap) and sorts the nearest venue first. near_matched shows the place found.
    open_now and minutes_until_close are given only when the date is today. Compare
    minutes_until_close with walk_minutes to know if the user can arrive in time.
    meals is empty when HDH has no menu for that day.
    """
    day = _parse_date(date)
    db = store.connect()
    await store.ensure_fresh(db, [day])
    walks = await _walks(db, near)
    now = datetime.now(store.PACIFIC)
    meals: dict[str, list[str]] = {}
    for row in db.execute(
        "SELECT DISTINCT venue_id, meal FROM servings WHERE date = ?", (day.isoformat(),)
    ):
        meals.setdefault(row["venue_id"], []).append(row["meal"])
    out = []
    for v in db.execute("SELECT * FROM venues ORDER BY name"):
        hours = json.loads(v["hours"]).get(day.strftime("%a").upper(), "unknown")
        entry = {
            "name": v["name"],
            "area": v["area"],
            "date": day.isoformat(),
            "hours": hours,
            "meals": meals.get(v["id"], []),
            "description": v["description"],
        }
        if day == now.date():
            closes_in = _minutes_until_close(hours, now)
            entry["open_now"] = closes_in is not None
            if closes_in is not None:
                entry["minutes_until_close"] = closes_in
        if walks and v["id"] in walks[1]:
            walk = walks[1][v["id"]]
            entry |= {
                "walk_minutes": round(walk.minutes),
                "walk_meters": round(walk.meters),
                "near_matched": walks[0].label,
            }
        out.append(entry)
    if walks:
        out.sort(key=lambda e: e.get("walk_minutes", float("inf")))
    return out


@mcp.tool()
async def get_menu(venue: str, date: str = "today", meal: str | None = None) -> dict:
    """Get the full menu of one dining hall, grouped by meal, station, and category.

    venue: name or campus area, partial match. Examples: "64 Degrees", "pines", "muir".
    meal: "Breakfast", "Lunch", "Dinner", "Brunch", or "Late Night". Give a meal when you
    can; a full day at a large venue is 300+ lines.
    Returns {venue, date, menu: {meal: {station: {category: [lines]}}}}. Each line is
    "name | price | calories | protein | diet | contains allergens | id N".
    Use get_item with the id for full nutrition.
    """
    day = _parse_date(date)
    db = store.connect()
    await store.ensure_fresh(db, [day])
    found = _find_venues(db, venue)
    if len(found) > 1:
        raise ToolError(f'"{venue}" matches more than one venue: {", ".join(v["name"] for v in found)}')
    menu: dict[str, dict[str, dict[str, list[str]]]] = {}
    for row in _load(db, day, [found[0]["id"]], meal):
        stations = menu.setdefault(row["meal"], {})
        stations.setdefault(row["station"], {}).setdefault(row["category"], []).append(_line(row))
    out: dict = {"venue": found[0]["name"], "date": day.isoformat(), "menu": menu}
    if not menu:
        out["note"] = _no_menu_note(day)
    return out


@mcp.tool()
async def search_items(
    query: str = "",
    venue: str | None = None,
    date: str = "today",
    meal: str | None = None,
    diet: Diet | None = None,
    exclude_allergens: list[Allergen] = [],
    max_calories: int | None = None,
    min_protein_g: float | None = None,
    max_price: float | None = None,
    near: str | None = None,
    max_walk_minutes: float | None = None,
    sort_by: SortBy = "protein",
    limit: int = 25,
) -> dict:
    """Search menu items across all dining halls (or one) with diet, allergen, and macro filters.

    All filters are optional and combine with AND.
    query: words that must all be in the item name or description. Example: "chicken bowl".
    venue: name or campus area, partial match ("pines", "muir"). Omit to search all venues.
    diet: "vegetarian" includes vegan items.
    exclude_allergens: drop items that HDH marks with any of these allergens.
    near: where the user is. A campus place name ("Geisel Library", "muir") or "lat,lon".
    Adds walk_minutes to each result. Necessary for max_walk_minutes and sort_by "walk_time".
    sort_by: "walk_time" (near first), "protein" (high first), "calories" (low first), "price" (low first),
    "protein_per_dollar", or "protein_per_calorie" (high first).
    Each result lists the venue, station, and meals where the item is served.
    Many results are sides and add-ons; use query, min_protein_g, or max_calories to narrow.
    """
    day = _parse_date(date)
    db = store.connect()
    await store.ensure_fresh(db, [day])
    venue_ids = [v["id"] for v in _find_venues(db, venue)] if venue else None
    walks = await _walks(db, near)
    if walks is None and (max_walk_minutes is not None or sort_by == "walk_time"):
        raise ToolError('Give "near" (where the user is) to use max_walk_minutes or sort_by "walk_time".')
    words = query.lower().split()
    wanted_diet = {"vegan"} if diet == "vegan" else {"vegan", "vegetarian"}

    # One result per item and venue. The same item in lunch and dinner becomes one entry.
    merged: dict[tuple[int, str], dict] = {}
    for row in _load(db, day, venue_ids, meal):
        text = f'{row["name"]} {row["description"]}'.lower()
        if (
            not all(w in text for w in words)
            or (diet and not wanted_diet & set(row["diet"]))
            or set(exclude_allergens) & set(row["allergens"])
            or (max_calories is not None and (row["calories"] is None or row["calories"] > max_calories))
            or (min_protein_g is not None and (row["protein_g"] or 0) < min_protein_g)
            or (max_price is not None and (row["price"] is None or row["price"] > max_price))
            or (max_walk_minutes is not None and walks and walks[1][row["venue_id"]].minutes > max_walk_minutes)
        ):
            continue
        entry = merged.setdefault(
            (row["id"], row["venue"]),
            _brief(row) | {"venue": row["venue"], "station": row["station"], "meals": []},
        )
        if walks:
            entry["walk_minutes"] = round(walks[1][row["venue_id"]].minutes)
        if row["meal"] not in entry["meals"]:
            entry["meals"].append(row["meal"])

    def rank(item: dict) -> float:
        protein = item.get("protein_g", 0)
        match sort_by:
            case "walk_time":
                return item["walk_minutes"] - protein / 1000  # protein breaks ties
            case "calories":
                return item.get("calories", float("inf"))
            case "price":
                return item.get("price", float("inf"))
            case "protein_per_dollar":
                return -protein / item["price"] if item.get("price") else 0
            case "protein_per_calorie":
                return -protein / item["calories"] if item.get("calories") else 0
            case _:
                return -protein

    results = sorted(merged.values(), key=rank)
    out: dict = {"date": day.isoformat(), "total_matches": len(results), "items": results[:limit]}
    if walks:
        out["near_matched"] = walks[0].label
    if not results and not _load(db, day, venue_ids, meal):
        out["note"] = _no_menu_note(day)
    return out


@mcp.tool()
async def get_item(item_id: int) -> dict:
    """Get full nutrition, ingredients, allergens, and this week's schedule for one item.

    item_id: the "id" field from search_items or get_menu results.
    """
    db = store.connect()
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ToolError(f"No item with id {item_id}. Get ids from search_items or get_menu.")
    item = dict(row)
    item["diet"] = json.loads(item["diet"])
    item["allergens"] = json.loads(item["allergens"])
    del item["nutrition_fetched_at"]
    item["served"] = [
        dict(r)
        for r in db.execute(
            """SELECT DISTINCT s.date, v.name AS venue, s.meal, s.station, s.price
               FROM servings s JOIN venues v ON v.id = s.venue_id
               WHERE s.item_id = ? AND s.date >= ? ORDER BY s.date""",
            (item_id, store.today().isoformat()),
        )
    ]
    return item


def main() -> None:
    """Entry point. Default: MCP server on stdio. --http: streamable HTTP. --refresh: fill the cache."""
    parser = argparse.ArgumentParser(prog="ucsd-dining")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP at /mcp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--refresh", action="store_true", help="fetch all 7 days, then exit")
    args = parser.parse_args()
    if args.refresh:
        asyncio.run(store.ensure_fresh(store.connect(), store.week(), force=True))
    elif args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")
