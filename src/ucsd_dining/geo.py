"""Campus place lookup and walking times. Uses OpenStreetMap services, cached in SQLite.

Nominatim finds a place name inside the UCSD campus box. OSRM (foot profile) gives real
path times from that place to all venues in one request.
"""

import math
import re
import sqlite3
from dataclasses import dataclass

import httpx

from . import scrape

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM_TABLE = "https://routing.openstreetmap.de/routed-foot/table/v1/foot/"
CAMPUS_BOX = "-117.2560,32.8940,-117.2080,32.8640"  # left, top, right, bottom

# Venue entrances from OpenStreetMap, keyed by locId. HDH does not publish coordinates.
VENUE_COORDS = {
    "64": (32.874797, -117.242039),
    "01": (32.879030, -117.242487),
    "05": (32.883085, -117.242682),
    "18": (32.886150, -117.242837),
    "24": (32.884195, -117.233190),
    "11": (32.878806, -117.230415),
    "37": (32.880002, -117.241996),
    "27": (32.887954, -117.242045),
    "15": (32.875411, -117.235000),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS places (query TEXT PRIMARY KEY, lat REAL, lon REAL, label TEXT);
CREATE TABLE IF NOT EXISTS walks (
    origin TEXT, venue_id TEXT, minutes REAL, meters REAL, PRIMARY KEY (origin, venue_id)
);
"""


class PlaceNotFound(Exception):
    pass


@dataclass
class Place:
    lat: float
    lon: float
    label: str


@dataclass
class Walk:
    minutes: float
    meters: float


async def locate(db: sqlite3.Connection, query: str) -> Place:
    """Turn "lat,lon" or a campus place name ("Geisel", "muir", "Peterson Hall") into a Place."""
    coords = re.fullmatch(r"\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*", query)
    if coords:
        return Place(float(coords.group(1)), float(coords.group(2)), "given coordinates")

    db.executescript(SCHEMA)
    key = " ".join(query.lower().split())
    row = db.execute("SELECT * FROM places WHERE query = ?", (key,)).fetchone()
    if row:
        return Place(row["lat"], row["lon"], row["label"])

    async with scrape.client() as http:
        try:
            response = await http.get(
                NOMINATIM,
                params={"q": key, "format": "jsonv2", "limit": 1, "bounded": 1, "viewbox": CAMPUS_BOX},
            )
            response.raise_for_status()
            results = response.json()
        except httpx.HTTPError as error:
            raise PlaceNotFound(f"The place lookup service failed ({error}). Give near as \"lat,lon\".") from None
    if not results:
        raise PlaceNotFound(
            f'No place "{query}" on the UCSD campus. Use a building, library, or college name '
            '("Geisel Library", "Warren Lecture Hall", "Muir"), or "lat,lon".'
        )
    # Keep the first two parts of the OSM name: "Geisel Library, Snake Path".
    label = ", ".join(results[0]["display_name"].split(", ")[:2])
    place = Place(float(results[0]["lat"]), float(results[0]["lon"]), label)
    with db:
        db.execute("INSERT OR REPLACE INTO places VALUES (?, ?, ?, ?)", (key, place.lat, place.lon, label))
    return place


def _estimate(place: Place, lat: float, lon: float) -> Walk:
    """Straight line times 1.3 for paths, at 80 m/min. Used only when OSRM fails."""
    dx = math.radians(lon - place.lon) * math.cos(math.radians(lat)) * 6_371_000
    dy = math.radians(lat - place.lat) * 6_371_000
    meters = math.hypot(dx, dy) * 1.3
    return Walk(minutes=meters / 80, meters=meters)


async def walks_from(db: sqlite3.Connection, place: Place) -> dict[str, Walk]:
    """Walking time and distance from a place to each venue, keyed by venue id."""
    db.executescript(SCHEMA)
    origin = f"{place.lat:.4f},{place.lon:.4f}"  # about 11 m precision
    cached = db.execute("SELECT * FROM walks WHERE origin = ?", (origin,)).fetchall()
    if len(cached) == len(VENUE_COORDS):
        return {r["venue_id"]: Walk(r["minutes"], r["meters"]) for r in cached}

    ids = list(VENUE_COORDS)
    points = ";".join(f"{lon},{lat}" for lat, lon in [(place.lat, place.lon), *VENUE_COORDS.values()])
    try:
        async with scrape.client() as http:
            response = await http.get(
                OSRM_TABLE + points, params={"sources": 0, "annotations": "duration,distance"}
            )
            response.raise_for_status()
            table = response.json()
        durations, distances = table["durations"][0][1:], table["distances"][0][1:]
        walks = {i: Walk(d / 60, m) for i, d, m in zip(ids, durations, distances, strict=True)}
    except (httpx.HTTPError, KeyError, TypeError):
        # Estimates are not cached, so the next call tries OSRM again.
        return {i: _estimate(place, *VENUE_COORDS[i]) for i in ids}
    with db:
        for venue_id, walk in walks.items():
            db.execute(
                "INSERT OR REPLACE INTO walks VALUES (?, ?, ?, ?)",
                (origin, venue_id, walk.minutes, walk.meters),
            )
    return walks
