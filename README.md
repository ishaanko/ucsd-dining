# ucsd-dining

Ask Claude (or any MCP client) what's good to eat at UCSD dining halls.

## What it can answer

- "High protein vegan dinner near Geisel?"
- "What's at Pines for lunch tomorrow?"
- "Anything without dairy under 600 calories?"
- "Which dining halls are open now, and can I get there before they close?"

Covers all 9 HDH dining halls for today and the next 6 days: menus, prices, nutrition, allergens, hours, and walking times.

## Setup

- Install [uv](https://docs.astral.sh/uv/).
- Clone this repo and run `uv sync`.
- Add it to Claude Code:
  ```sh
  claude mcp add ucsd-dining -- uv run --directory /path/to/ucsd-dining ucsd-dining
  ```
- For Claude Desktop or Cursor, add the same command to your MCP config.
- Optional: `uv run ucsd-dining --refresh` preloads the whole week (about a minute), so first answers are fast.

## Adapt it for another college

- `scrape.py`: point `BASE` at your dining site and rewrite the `parse_*` functions for its pages. This is the main work.
- `scrape.py`: fill `AREAS` with where each dining hall is on campus.
- `geo.py`: set `CAMPUS_BOX` to your campus bounds and `VENUE_COORDS` to each hall's location (from OpenStreetMap).
- `server.py`: update `INSTRUCTIONS` and the tool descriptions with your school and hall names.
- Save a few real pages to `tests/fixtures` and update the tests.

## Good to know

- Data comes from the public HDH site. This is not an official UCSD project.
- HDH allergen tags can be incomplete. For a serious allergy, check with dining staff.
- Markets and cafes are not included.
