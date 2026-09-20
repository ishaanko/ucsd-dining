"""Command line entry point: run the MCP server, refresh the cache, or query from a shell."""

import argparse
import asyncio
import json

from mcp.server.mcpserver.exceptions import ToolError

from . import server, store


def main() -> None:
    parser = argparse.ArgumentParser(prog="ucsd-dining", description="UCSD dining menus for AI assistants.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the MCP server (stdio by default)")
    serve.add_argument("--http", action="store_true", help="use streamable HTTP, for remote hosting")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    refresh = sub.add_parser("refresh", help="fetch all 7 days now, so that later queries are fast")
    refresh.add_argument("--force", action="store_true", help="ignore cache age")

    venues = sub.add_parser("venues", help="list venues and hours")
    venues.add_argument("--date", default="today")

    menu = sub.add_parser("menu", help="show one venue menu")
    menu.add_argument("venue")
    menu.add_argument("--date", default="today")
    menu.add_argument("--meal")

    search = sub.add_parser("search", help="search items")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--venue")
    search.add_argument("--date", default="today")
    search.add_argument("--meal")
    search.add_argument("--diet", choices=["vegan", "vegetarian"])
    search.add_argument("--exclude-allergens", nargs="*", default=[])
    search.add_argument("--max-calories", type=int)
    search.add_argument("--min-protein-g", type=float)
    search.add_argument("--max-price", type=float)
    search.add_argument("--sort-by", default="protein")
    search.add_argument("--limit", type=int, default=25)

    item = sub.add_parser("item", help="show full nutrition for one item id")
    item.add_argument("item_id", type=int)

    args = vars(parser.parse_args())
    command = args.pop("command")

    if command == "serve":
        if args["http"]:
            server.mcp.run("streamable-http", host=args["host"], port=args["port"])
        else:
            server.mcp.run("stdio")
        return
    if command == "refresh":
        asyncio.run(store.ensure_fresh(store.connect(), store.week(), force=args["force"]))
        print("Cache is current for 7 days.")
        return

    tool = {
        "venues": server.list_venues,
        "menu": server.get_menu,
        "search": server.search_items,
        "item": server.get_item,
    }[command]
    try:
        print(json.dumps(asyncio.run(tool(**args)), indent=2, ensure_ascii=False))
    except ToolError as error:
        raise SystemExit(str(error)) from None
