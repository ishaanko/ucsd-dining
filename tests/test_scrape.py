"""Parser tests against trimmed copies of real HDH pages (saved 2026-09-19)."""

from datetime import date
from pathlib import Path

from selectolax.parser import HTMLParser

from ucsd_dining import scrape

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_venues():
    venues = scrape.parse_venues((FIXTURES / "venues.html").read_text())
    degrees, bistro = venues
    assert (degrees.id, degrees.name, degrees.area) == ("64", "64 Degrees", "Revelle College")
    assert degrees.hours["FRI"] == "7:00 AM - 9:00 PM"
    assert degrees.url.endswith("Venue_V3?locId=64&locDetID=37&dayNum=0")
    assert bistro.hours["MON"] == "Closed"


def test_parse_day_menu():
    menu = scrape.parse_day_menu((FIXTURES / "venue_day.html").read_text())
    assert menu.date == date(2026, 9, 19)
    # The page has each item twice (large and small screen). Closed meals have no items.
    assert len(menu.servings) == 11
    assert {s.meal for s in menu.servings} == {"Lunch"}

    alfredo = menu.servings[0]
    assert alfredo.item_id == 357
    assert alfredo.name == "Roasted Crimini Fettuccini Alfredo"
    assert (alfredo.station, alfredo.category) == ("Al Dente", "Bowls")
    assert (alfredo.calories, alfredo.price) == (676, 10.5)
    assert alfredo.diet == ["vegetarian"]
    assert alfredo.allergens == ["dairy", "wheat", "gluten"]

    # Categories follow page order, and add-ons can have no price.
    assert [s.category for s in menu.servings].count("Proteins") == 4
    assert any(s.price is None for s in menu.servings)


def test_allergen_tag_wins_over_diet_tag():
    row = HTMLParser(
        '<div><img title="Vegan"><img title="Contains Fish"><img title="Contains TreeNuts"></div>'
    ).css_first("div")
    assert scrape._parse_tags(row, "Seared Salad") == ([], ["fish", "tree nuts"])


def test_meat_name_wins_over_diet_tag():
    row = HTMLParser('<div><img title="Vegan"></div>').css_first("div")
    assert scrape._parse_tags(row, "Blackened Chicken") == ([], [])
    assert scrape._parse_tags(row, "Southwest Grain Bowl With Chicken") == ([], [])
    # Plant-based items keep the tag.
    assert scrape._parse_tags(row, "Beyond Beef Picadillo Tacos") == (["vegan"], [])
    assert scrape._parse_tags(row, "Veggie Sausage") == (["vegan"], [])
    assert scrape._parse_tags(row, "Black Bean Chipotle Burger") == (["vegan"], [])


def test_parse_nutrition():
    nutrition = scrape.parse_nutrition((FIXTURES / "nutrition.html").read_text())
    assert nutrition.serving_size == "10.3 oz"
    assert nutrition.ingredients and nutrition.ingredients.startswith("Water, Tap, Pasta")
    assert nutrition.values == {
        "fat_g": 40.0, "sat_fat_g": 24.8, "trans_fat_g": 0.0, "cholesterol_mg": 144.4,
        "sodium_mg": 828.9, "carbs_g": 64.2, "fiber_g": 3.9, "sugar_g": 2.8, "protein_g": 14.7,
    }
