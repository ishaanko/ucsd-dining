# ucsd-dining

Ask Claude or ChatGPT what's good to eat at UCSD dining halls.

## What it can answer

- "High protein vegan dinner near Geisel?"
- "What's at Pines for lunch tomorrow?"
- "Anything without dairy under 600 calories?"
- "Which dining halls are open now, and can I get there before they close?"

Covers all 9 HDH dining halls for today and the next 6 days: menus, prices, nutrition, allergens, hours, and walking times.

## Put it online

Claude and ChatGPT reach it through a URL, so host your own copy on Vercel (free).

- Install [uv](https://docs.astral.sh/uv/) and the [Vercel CLI](https://vercel.com/docs/cli), then clone this repo.
- Preload this week's menus (about a minute):
  ```sh
  UCSD_DINING_DB=api/seed.db uv run ucsd-dining --refresh
  ```
- Create the project and pick a password (any long random string):
  ```sh
  vercel link
  vercel env add MCP_SECRET production
  vercel deploy --prod
  ```
- Your URL is `https://<your-project>.vercel.app/<password>/mcp`. Keep it private.
- Rerun the preload and deploy steps about once a month.

## Add it to Claude

- Go to Customize > Connectors, click +, then Add custom connector.
- Paste your URL. Leave authentication empty.

## Add it to ChatGPT

- Needs Plus or higher. Go to Settings > Apps & Connectors > Advanced settings and turn on Developer mode.
- Back in Apps & Connectors, click Create, paste your URL, and choose no authentication.

## Claude Code

No hosting needed:

```sh
claude mcp add ucsd-dining -- uv run --directory /path/to/ucsd-dining ucsd-dining
```

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
