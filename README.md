# ucsd-dining

UCSD dining hall menus and walking times for Claude and other AI assistants. An MCP server.

The data comes from the public HDH pages at hdh-web.ucsd.edu. This project is not an official UCSD product.

## What it does

- Reads the menus of all 9 HDH restaurants for today and the next 6 days.
- Reads the nutrition page of each item: macros, serving size, ingredients.
- Keeps all data in a local SQLite cache. Menus refresh after 6 hours. Nutrition refreshes after 30 days.
- Finds a campus place by name and gives real walking times from it to each venue.
- Gives 4 MCP tools to the assistant.

| Tool | Use |
| --- | --- |
| `search_items` | Find items in all venues. Filters: words, venue, date, meal, diet, allergens to exclude, calories, protein, price, walking time. Sort by protein, calories, price, protein per dollar, protein per calorie, walking time. |
| `list_venues` | Venues, campus area, hours, open now, minutes until close, meals served, walking time and distance. |
| `get_menu` | Full menu of one venue, one line per item. |
| `get_item` | Full nutrition, ingredients, and the schedule of one item. |

Venue names accept a partial name or a campus area. "muir" finds Pines. Dates accept "today", "tomorrow", a weekday name, or YYYY-MM-DD.

## Walking times

`list_venues` and `search_items` accept `near`: a campus place name ("Geisel Library", "Warren Lecture Hall", "muir") or "lat,lon". Nominatim (OpenStreetMap) finds the place inside the campus box. OSRM with the foot profile gives the path time and distance to all venues in one request. Both results are cached in SQLite. If OSRM fails, the server estimates from the straight-line distance. The `near_matched` field shows which place was found.

## Install

```sh
uv sync
uv run ucsd-dining --refresh   # optional. Fetches 7 days in about 75 seconds.
```

Without `--refresh`, the first query for a date fetches that date. This takes about 40 seconds with an empty cache.

## Connect to an assistant

Claude Code:

```sh
claude mcp add ucsd-dining -- uv run --directory /path/to/food ucsd-dining
```

Claude Desktop, Cursor, and other stdio clients:

```json
{
  "mcpServers": {
    "ucsd-dining": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/food", "ucsd-dining"]
    }
  }
}
```

Remote clients (streamable HTTP, endpoint `/mcp`):

```sh
uv run ucsd-dining --http --host 0.0.0.0 --port 8000
```

The HTTP mode is tested on localhost only. Put it behind HTTPS before you add it to claude.ai as a custom connector.

## Deploy to Vercel

`api/index.py` serves the MCP server at `/<MCP_SECRET>/mcp`. All other paths give 404. The secret path is the only access control, so keep the URL private.

```sh
cp ~/.cache/ucsd-dining/dining.db api/seed.db   # after `ucsd-dining --refresh`
openssl rand -hex 24 | vercel env add MCP_SECRET production
vercel deploy --prod
```

Vercel instances lose `/tmp` when they stop. `api/seed.db` gives each new instance the nutrition data, so a cold start fetches only the menu pages. Make a new seed and deploy again each month. If the seed is older than 30 days, a cold start tries to fetch all nutrition pages and can exceed the 60 second limit.

## Data notes

- HDH data has errors. When an item has a vegan or vegetarian tag and also a fish, shellfish, dairy, or egg allergen tag, the diet tag is removed. When the macros of an item do not agree with its calories, the item gets a `warning` field.
- Allergen tags can be incomplete. For a serious allergy, confirm with dining staff.
- Campus areas and venue coordinates are not on the HDH site. They are small tables in `scrape.py` and `geo.py`. The coordinates come from OpenStreetMap.
- The scraper sends a maximum of 6 requests at the same time and identifies itself in the User-Agent.
- Markets and cafes are not included.

## Configuration

`UCSD_DINING_DB` sets the cache path. The default is `~/.cache/ucsd-dining/dining.db`.

## Tests

```sh
uv run pytest
```

The parser tests use trimmed copies of real HDH pages in `tests/fixtures`.
