# Step 4: the MCP server on the world database

Status: approved and built, 15 October 2026. Step 4 of `docs/rewrite-plan.md`.

## Today

`app/mcp_server.py` (1,221 lines, `mcp` SDK, stdio or streamable HTTP on port 8001) has 30
tools. All of them call `app/services/universe_curator.py` and read or write the **old**
archive database; none uses the world database. The Custom GPT Actions API
(`app/actions_api.py`, port 8002) offers the same tools through the same module.

## The idea

A new module `app/services/world_tools.py` holds every tool as a plain function that takes
simple arguments and returns JSON-ready dicts. It reads through `world_queries` and
`player_notebook`, so a chat answer, a page and a bio always agree. The MCP server and the
Actions API both call it; neither keeps its own logic. Transport, ports, launchd scripts and
the server name stay as they are.

Every answer carries names (not only ids), season labels such as "2062/63", and the
`page` path of the matching `/world` page. Long lists are capped (for example 50 rows) and
say when they are.

## Tools

**Read (world database):**

| Tool | Gives |
|---|---|
| `get_archive_overview` | Game date, imported saves, counts, how ids work |
| `search` | People, clubs and national teams, competitions, awards (as the search page) |
| `get_player` | The bio notebook (career by season, honours, lost finals, awards, best elevens, transfers, goals in finals, teammates, milestones, status) and the saved bio |
| `get_club` | Honours, every season (league position, trophies, top scorer), top scorers, most games, last results |
| `get_club_season` | Record, trophies, squad numbers, results by competition, finals, best eleven, signings and departures |
| `get_competition` | Every edition with its first three; the parts of a league without history (MLS); most titles |
| `get_edition` | Rounds (named, with second legs, replays and group tables), league table, final, top scorers |
| `get_match` | Score, extra time, penalties, goals with minutes, crowd, head-to-head, report and preview |
| `list_matches` | Results and fixtures by team, competition and dates (for "this week" articles) |
| `get_nation` | National teams, titles, last results, top scorers |
| `get_award` | Top three by year, most wins |

**Write:**

| Tool | Does |
|---|---|
| `save_match_report` | Saves a report or preview for a world match. Preview first; saved only with `confirm=true`. |
| `write_player_bio` | Runs the same writer as the "Write bio" button (about 30 seconds). |

Match reports and previews go to a new table `match_texts` in the main database, keyed by
date, home team and away team (FM ids), so they survive a rebuild of the world database. The
match page shows them.

**Kept as they are for now:** the World Soccer tools (`get_newsroom_brief`,
`list_world_soccer_issues`, `create_world_soccer_issue`, `write_world_soccer_article`,
`find_archive_images`, `update_world_soccer_story_thread`, `publish_world_soccer_issue`).
They still read the old database. They move to the world data in step 5, together with the
other story builders and the old match reports.

**Removed** from the MCP server and the Actions API (19 tools): the scorer, identity, merge,
repair, career-spell, honour, award, winner, table-import and results-entry tools. The save
gives these facts now. Their code is deleted in step 7.

## Tests

`tests/test_mcp_server.py` is rewritten on a small world: the tool list, each read tool, the
preview and confirm of `save_match_report`, and the Actions API calling the same functions.
