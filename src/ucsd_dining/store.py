"""SQLite cache of HDH menus and walking times. Refreshes stale menus from the HDH site."""

import asyncio
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import scrape

PACIFIC = ZoneInfo("America/Los_Angeles")
MENU_MAX_AGE = timedelta(hours=6)
NUTRITION_MAX_AGE = timedelta(days=30)
DAYS_AHEAD = 6  # HDH publishes today plus 6 days
CONCURRENCY = 6
NUTRIENT_COLUMNS = list(scrape.NUTRIENTS.values())

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY, name TEXT, area TEXT, description TEXT, url TEXT, hours TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY, name TEXT, description TEXT, calories INTEGER,
    diet TEXT, allergens TEXT, nutrition_url TEXT,
    serving_size TEXT, ingredients TEXT, nutrition_fetched_at TEXT,
    {", ".join(f"{c} REAL" for c in NUTRIENT_COLUMNS)}
);
CREATE TABLE IF NOT EXISTS servings (
    venue_id TEXT, date TEXT, meal TEXT, station TEXT, category TEXT, item_id INTEGER, price REAL
);
CREATE INDEX IF NOT EXISTS servings_by_date ON servings (date, venue_id);
CREATE TABLE IF NOT EXISTS fetched_days (
    venue_id TEXT, date TEXT, fetched_at TEXT, PRIMARY KEY (venue_id, date)
);
CREATE TABLE IF NOT EXISTS places (query TEXT PRIMARY KEY, lat REAL, lon REAL, label TEXT);
CREATE TABLE IF NOT EXISTS walks (
    origin TEXT, venue_id TEXT, minutes REAL, meters REAL, PRIMARY KEY (origin, venue_id)
);
"""

_refresh_lock = asyncio.Lock()


def today() -> date:
    return datetime.now(PACIFIC).date()


def week() -> list[date]:
    return [today() + timedelta(days=n) for n in range(DAYS_AHEAD + 1)]


def connect() -> sqlite3.Connection:
    default = Path.home() / ".cache" / "ucsd-dining" / "dining.db"
    path = Path(os.environ.get("UCSD_DINING_DB", default))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _is_stale(fetched_at: str | None, max_age: timedelta) -> bool:
    return fetched_at is None or datetime.now(PACIFIC) - datetime.fromisoformat(fetched_at) > max_age


def _save_day(db: sqlite3.Connection, venue_id: str, menu: scrape.DayMenu) -> None:
    day = menu.date.isoformat()
    with db:
        db.execute("DELETE FROM servings WHERE venue_id = ? AND date = ?", (venue_id, day))
        for s in menu.servings:
            db.execute(
                "INSERT INTO servings VALUES (?, ?, ?, ?, ?, ?, ?)",
                (venue_id, day, s.meal, s.station, s.category, s.item_id, s.price),
            )
            db.execute(
                """INSERT INTO items (id, name, description, calories, diet, allergens, nutrition_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET name = excluded.name,
                       description = excluded.description, calories = excluded.calories,
                       diet = excluded.diet, allergens = excluded.allergens,
                       nutrition_url = excluded.nutrition_url""",
                (
                    s.item_id,
                    s.name,
                    s.description,
                    s.calories,
                    json.dumps(s.diet),
                    json.dumps(s.allergens),
                    s.nutrition_url,
                ),
            )
        db.execute(
            "INSERT OR REPLACE INTO fetched_days VALUES (?, ?, ?)",
            (venue_id, day, datetime.now(PACIFIC).isoformat()),
        )


def _save_nutrition(db: sqlite3.Connection, item_id: int, n: scrape.Nutrition) -> None:
    sets = ", ".join(f"{c} = ?" for c in NUTRIENT_COLUMNS)
    with db:
        db.execute(
            f"""UPDATE items SET serving_size = ?, ingredients = ?, nutrition_fetched_at = ?, {sets}
                WHERE id = ?""",
            (
                n.serving_size,
                n.ingredients,
                datetime.now(PACIFIC).isoformat(),
                *(n.values.get(c) for c in NUTRIENT_COLUMNS),
                item_id,
            ),
        )


async def ensure_fresh(db: sqlite3.Connection, days: list[date], force: bool = False) -> None:
    """Make sure the cache has current menus and nutrition for the given dates.

    Dates that HDH does not publish (past, or more than DAYS_AHEAD away) are skipped.
    A failed fetch is skipped too, so the cache still answers when HDH is down.
    """
    async with _refresh_lock:
        start = today()
        days = [d for d in days if 0 <= (d - start).days <= DAYS_AHEAD]
        if not days:
            return
        fetched = {
            (r["venue_id"], r["date"]): r["fetched_at"] for r in db.execute("SELECT * FROM fetched_days")
        }
        gate = asyncio.Semaphore(CONCURRENCY)
        venues = db.execute("SELECT id, url FROM venues").fetchall()

        def stale(venue: sqlite3.Row, day: date) -> bool:
            return force or _is_stale(fetched.get((venue["id"], day.isoformat())), MENU_MAX_AGE)

        async with scrape.client() as http:
            # Venue names and hours refresh together with any menu refresh.
            if not venues or any(stale(v, d) for v in venues for d in days):
                try:
                    scraped = await scrape.fetch_venues(http)
                except httpx.HTTPError:
                    scraped = []
                with db:
                    for v in scraped:
                        db.execute(
                            "INSERT OR REPLACE INTO venues VALUES (?, ?, ?, ?, ?, ?)",
                            (v.id, v.name, v.area, v.description, v.url, json.dumps(v.hours)),
                        )
                venues = db.execute("SELECT id, url FROM venues").fetchall()

            async def load_day(venue: sqlite3.Row, day: date) -> None:
                async with gate:
                    try:
                        menu = await scrape.fetch_day_menu(http, venue["url"], (day - start).days)
                    except (httpx.HTTPError, ValueError):
                        return
                # The page shows its own date. Trust it over day arithmetic.
                _save_day(db, venue["id"], menu)

            await asyncio.gather(*(load_day(v, d) for v in venues for d in days if stale(v, d)))

            async def load_nutrition(item: sqlite3.Row) -> None:
                async with gate:
                    try:
                        nutrition = await scrape.fetch_nutrition(http, item["nutrition_url"])
                    except httpx.HTTPError:
                        return
                _save_nutrition(db, item["id"], nutrition)

            wanted = db.execute(
                f"""SELECT DISTINCT i.id, i.nutrition_url, i.nutrition_fetched_at FROM items i
                    JOIN servings s ON s.item_id = i.id
                    WHERE s.date IN ({", ".join("?" for _ in days)})""",
                [d.isoformat() for d in days],
            ).fetchall()
            stale_items = [i for i in wanted if _is_stale(i["nutrition_fetched_at"], NUTRITION_MAX_AGE)]
            await asyncio.gather(*(load_nutrition(i) for i in stale_items))
