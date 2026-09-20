"""Fetch and parse the public HDH dining pages (hdh-web.ucsd.edu).

The parse_* functions are pure (HTML in, dataclasses out). The fetch_* functions add HTTP.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from selectolax.parser import HTMLParser, Node

BASE = "https://hdh-web.ucsd.edu/dining/apps/diningservices/"
USER_AGENT = "ucsd-dining-mcp/0.1 (student project; cached, low request rate)"

# The MCP SDK sets logging to INFO. One log line per request is too much.
logging.getLogger("httpx").setLevel(logging.WARNING)

DIET_TAGS = {"vegan", "vegetarian", "wellness", "sustainability"}

# HDH does not publish where a venue is. Keyed by locId. Used for queries such as "near Muir".
AREAS = {
    "64": "Revelle College",
    "01": "Muir College",
    "05": "Marshall College",
    "18": "Eleanor Roosevelt College (ERC)",
    "24": "Warren College",
    "11": "Pepper Canyon",
    "37": "Sixth College",
    "27": "Seventh College (The Village)",
    "15": "School of Medicine",
}

# Nutrition table label -> Item field name.
NUTRIENTS = {
    "total fat": "fat_g",
    "sat. fat": "sat_fat_g",
    "trans fat": "trans_fat_g",
    "cholesterol": "cholesterol_mg",
    "sodium": "sodium_mg",
    "tot. carb.": "carbs_g",
    "dietary fiber": "fiber_g",
    "sugars": "sugar_g",
    "protein": "protein_g",
}


@dataclass
class Venue:
    id: str  # locId
    name: str
    description: str
    url: str  # menu page for dayNum=0
    hours: dict[str, str]  # "MON" -> "7:00 AM - 11:00 PM" or "Closed"
    area: str | None = None


@dataclass
class Serving:
    """One menu line: an item at a station, in a meal period."""

    item_id: int
    nutrition_url: str
    name: str
    description: str
    meal: str
    station: str
    category: str
    calories: int | None
    price: float | None
    diet: list[str] = field(default_factory=list)
    allergens: list[str] = field(default_factory=list)


@dataclass
class DayMenu:
    date: date
    servings: list[Serving]


@dataclass
class Nutrition:
    serving_size: str | None
    ingredients: str | None
    values: dict[str, float]  # keys are the values of NUTRIENTS


def _text(node: Node | None) -> str:
    return " ".join(node.text().split()) if node else ""


def parse_venues(html: str) -> list[Venue]:
    """Parse the Restaurants index page."""
    venues: list[Venue] = []
    for heading in HTMLParser(html).css("#accordion > h2"):
        body = heading.next
        while body is not None and body.tag != "div":
            body = body.next
        link = body.css_first("a.info-link") if body else None
        if body is None or link is None:
            continue
        url = urljoin(BASE, link.attributes.get("href") or "")
        loc_id = parse_qs(urlsplit(url).query)["locId"][0]
        hours: dict[str, str] = {}
        table = body.css_first("table.tblstyle")
        for cell in table.css("td") if table else []:
            day, start, end = (_text(d) for d in cell.css("div")[:3])
            hours[day] = "Closed" if start == "Closed" else f"{start} - {end}"
        venues.append(
            Venue(
                id=loc_id,
                name=_text(heading),
                description=_text(body.css_first("td > div")),
                url=url,
                hours=hours,
                area=AREAS.get(loc_id),
            )
        )
    return venues


def _parse_tags(row: Node) -> tuple[list[str], list[str]]:
    diet: list[str] = []
    allergens: list[str] = []
    for img in row.css("img[title]"):
        title = (img.attributes.get("title") or "").strip().lower()
        if title in DIET_TAGS:
            diet.append(title)
        elif title.startswith("contains "):
            allergen = title.removeprefix("contains ")
            allergens.append("tree nuts" if allergen == "treenuts" else allergen)
    # HDH data has errors, for example a seared ahi salad marked vegan.
    # When a diet tag and an allergen tag disagree, trust the allergen tag.
    if {"fish", "shellfish"} & set(allergens):
        diet = [d for d in diet if d not in ("vegan", "vegetarian")]
    elif {"dairy", "eggs"} & set(allergens):
        diet = [d for d in diet if d != "vegan"]
    return diet, allergens


def parse_day_menu(html: str) -> DayMenu:
    """Parse one Venue_V3 page. The page holds all meal periods for one date."""
    tree = HTMLParser(html)
    shown = _text(tree.css_first("h2.datenow"))  # "Saturday, September 19 2026"
    day = datetime.strptime(shown, "%A, %B %d %Y").date()

    servings: list[Serving] = []
    for meal_node in tree.css("div.meal-category"):
        meal = (meal_node.id or "").replace("_", " ")
        for section in meal_node.css("div.menu-category-section"):
            station = _text(section.css_first("h3"))
            category = ""
            # Category headings and item lists are siblings, in page order.
            first = section.css_first("div.panel-heading, div.station-list")
            for node in first.parent.iter() if first and first.parent else []:
                classes = node.attributes.get("class") or ""
                if "panel-heading" in classes:
                    category = _text(node)
                    continue
                if "station-list" not in classes:
                    continue
                # Each item is in the page twice (large and small screen). Read the large one.
                for row in node.css("div.d-lg-block"):
                    link = row.css_first("a.sublocsitem")
                    if link is None:
                        continue
                    url = urljoin(BASE, link.attributes.get("href") or "")
                    item_id = parse_qs(urlsplit(url).query).get("id", [""])[0]
                    if not item_id.isdigit():
                        continue
                    cals = re.search(r"\d+", _text(row.css_first("span.cals")))
                    price = re.search(r"\d+(\.\d+)?", _text(row.css_first("span.item-price")))
                    diet, allergens = _parse_tags(row)
                    servings.append(
                        Serving(
                            item_id=int(item_id),
                            nutrition_url=url,
                            name=_text(link),
                            description=_text(row.css_first("div.proI")),
                            meal=meal,
                            station=station,
                            category=category,
                            calories=int(cals.group()) if cals else None,
                            price=float(price.group()) if price else None,
                            diet=diet,
                            allergens=allergens,
                        )
                    )
    return DayMenu(date=day, servings=servings)


def parse_nutrition(html: str) -> Nutrition:
    """Parse one Nutritionfacts2 page."""
    tree = HTMLParser(html)
    values: dict[str, float] = {}
    for cell in tree.css("table td"):
        match = re.match(r"(.+?)\s*([\d.]+)\s*m?g$", _text(cell))
        if match and (key := NUTRIENTS.get(match.group(1).strip().lower())):
            values[key] = float(match.group(2))

    serving_size = None
    ingredients = None
    for p in tree.css("p"):
        text = _text(p)
        if text.startswith("Serving Size"):
            serving_size = text.removeprefix("Serving Size").strip() or None
    for h2 in tree.css("h2"):
        if _text(h2) == "Ingredients":
            nxt = h2.next
            while nxt is not None and nxt.tag != "p":
                nxt = nxt.next
            ingredients = _text(nxt) or None
    return Nutrition(serving_size=serving_size, ingredients=ingredients, values=values)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    )


async def _get(http: httpx.AsyncClient, url: str) -> str:
    response = await http.get(url)
    response.raise_for_status()
    return response.text


async def fetch_venues(http: httpx.AsyncClient) -> list[Venue]:
    return parse_venues(await _get(http, urljoin(BASE, "Restaurants/Restaurants")))


async def fetch_day_menu(http: httpx.AsyncClient, venue_url: str, day_num: int) -> DayMenu:
    """day_num is 0 (today, Pacific time) to 6."""
    url = re.sub(r"dayNum=\d+", f"dayNum={day_num}", venue_url)
    return parse_day_menu(await _get(http, url))


async def fetch_nutrition(http: httpx.AsyncClient, nutrition_url: str) -> Nutrition:
    return parse_nutrition(await _get(http, nutrition_url))
