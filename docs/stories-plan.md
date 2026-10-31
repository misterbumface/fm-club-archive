# Step 5: stories on the world database

Status: approved and built, 15 October 2026. Step 5 of `docs/rewrite-plan.md`. It follows the way the
player bios work (`docs/player-bios-plan.md`): a notebook of save facts, a writer with a
checked prompt, and a table keyed by FM ids in the main database.

## Decisions (15 October 2026)

- **New stories:** a **season story** for a club's season, a **competition story** for one
  edition (league or cup), and an AI **match report**. No new diary chapters; the old ones
  are kept and shown.
- **Writer:** buttons on the pages, with the AI model chosen in Settings. The MCP server
  gives the same facts, and has tools to run the writers or save a text written in chat.
- **World Soccer** moves to the world data in this step.

## Today (old database)

| Text | Count | Keyed by |
|---|---|---|
| Season stories (`seasons.story_narrative`) | 166, for 40 clubs | old season id |
| Season diary chapters | 58 | old season id |
| Competition chapters | 29 | old edition id |
| League reports (`competition_editions.league_report_narrative`) | 3 | old edition id |
| Match reports / previews (`matches.report_text`, `preview_text`) | 114 / 18 | old match id |
| Club texts (`profile_summary`, `historical_context`) | 27 clubs | old club id |
| World Soccer: issues, articles, threads, entity links | 4, 33, 11, 218 | links by old ids |

The four old writers get their facts from builders on the old tables (the season builder
alone is 1,635 lines); the newsroom brief reads old matches, tables and transfers.

## New tables (main database)

- `club_season_stories`: key (club FM id, season start year); `text`, `source`
  (`generated`, `curator_edit`, `old_archive`), `model`, `game_date`, `written_at`.
- `club_season_chapters`: the 58 old diary chapters, read-only: club, season, order,
  heading, body, dates covered.
- `edition_stories`: key (competition FM id, edition year, which is the year it ended);
  same columns as season stories.
- `edition_chapters`: the 29 old competition chapters, read-only.
- `club_texts`: key club FM id; `profile_summary`, `historical_context`.
- `match_texts` (already there) gets a `source` column (`generated`, `curator`, `game`,
  `old_archive`), so a button never replaces your own report without asking.
- World Soccer entity links get a `world_ref` column: `person:<uid>`, `club:<uid>`,
  `competition:<uid>`, `edition:<uid>:<year>`, `match:<date>:<home uid>:<away uid>`,
  `club_season:<uid>:<season>`, `nation:<uid>`.

## Moving the old texts

One command, `flask stories move-old`, safe to run again. It maps each old row by FM id:
season → (club FM id, start year); edition → (competition FM id, end year); match → date,
the managed club and the opponent (home or away), checked against a world fixture;
World Soccer links → the `world_ref` above (national teams by nation name). It reports
every text it cannot map, and changes nothing in the old tables. Back up first.

## Notebooks and writers

Each writer gets a notebook from `world_queries` (the same queries as the pages):

- **Season story** (`club_season_notebook`): club, league, final or current table position
  and points (with the teams around it and the previous season), record, trophies and lost
  finals (score, scorers), every result in short form with scorers, the biggest wins and
  defeats, the squad's main players (age, nationality, position, games, goals, assists,
  rating, awards, international games), signings and departures with fees, the best eleven,
  saved match reports (as colour), the old story (as colour), and whether the season is over.
- **Competition story** (`edition_notebook`): the edition, its status, the first three, the
  final, every round (named, with scores) or the league table, group tables, top scorers,
  the biggest results, the parts of a league without history (MLS), the previous winner, the
  winner's title count, and old texts as colour.
- **Match report** (`match_notebook`): the match, extra time and penalties, goals with
  scorers (age, nationality, position), the shoot-out, the crowd, the round and edition,
  both teams' form and league positions before the day, head to head, and the saved
  preview (as colour).

The prompts start from today's prompts (season story, league report) and add the rules
that worked for bios: facts only from the notebook, no names of stadiums, managers or
people that are not in it, the right tense for a season or edition still in progress.
Every year and score in a text must be in the notebook; one more try, then a refusal.

## Pages

- **Club-season page:** the season story and "Write season story" / "New season story";
  the old diary chapters under it.
- **Edition page:** the competition story and "Write story" / "New story"; old chapters.
- **Match page:** "Write report" (only for a played match); "New report" asks first when
  the report is your own or the game's.
- **Club page:** the old club texts.
- **World Soccer:** the newsroom brief reads the world data. The world is large, so the
  brief takes the competitions with the highest average crowd in the window, plus every
  final decided in it and the senior national teams' games. Links in articles go to the
  `/world` pages.

## MCP and Actions API

New tools: `write_club_season_story`, `write_edition_story`, `write_match_report` (run the
app's writers; preview, then confirm) and `save_club_season_story`, `save_edition_story`
(save a text written in chat; preview, then confirm). `get_club_season`, `get_edition` and
`get_match` also return the saved stories. The World Soccer tools use the new links.

## Tests

Notebooks on the small world, the year and score check, the buttons with a fake AI answer,
moving old texts (a season, an edition, a match, a link) on a small old database, and the
newsroom brief.

## Built (15 October 2026)

- `flask stories move-old` moved 166 season stories, 58 season chapters, 3 edition stories,
  29 edition chapters, 120 match texts, 27 club texts and 214 World Soccer links. Not moved
  (no FM id in the old archive): one match against Dinamo București and four links. The old
  archive's home/away flag is sometimes wrong, so a match is looked up both ways.
- Code: `story_notebooks.py`, `story_writer.py`, `story_migration.py`, `world_newsroom.py`,
  models in `app/models/world_story.py`; tests in `tests/test_world_stories.py`.
- Found on the way: league tables now count only the league's main stage (play-offs share
  the competition id); a club's season uses its league's dates (calendar-year leagues);
  a league's deciding game is not a final.

## Not in this plan

Images and their links (step 6), deleting the old pages and builders (step 7).
