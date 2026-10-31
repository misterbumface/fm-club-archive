# Archive rewrite: plan

Status: done (14–15 October 2026). Steps 0–6 are described in their own plans in `docs/`; step 7, the cut-over,
in `docs/cutover-plan.md`. The old code is in git under the tag `pre-cutover`.

## Goal

Rebuild the archive so that all game facts come from Football Manager saves (`.fm`),
read by `app/services/fm_save.py`. Remove the HTML import path and the repair tools that
exist only because HTML imports had no reliable ids. Keep the web pages, the MCP server,
the stories and the images.

## Principles

1. **One source of facts: saves.** Importing is "read a save, insert or update by FM id".
   No name matching, aliases, merges or curation queues.
2. **FM ids are the keys.** People, clubs and competitions are keyed by FM unique id.
   Other rows use natural keys built from FM ids:
   - season: (club FM id, first year, last year)
   - competition edition: (competition FM id, season label)
   - match: (competition FM id, date, home club FM id, away club FM id)
3. **Saves add up.** Each save you import adds to the archive and never removes facts.
   An older save keeps what a newer save has lost (for example, players who later retire).
   A `saves` table records each imported file and its game date.
4. **The save's own numbers stay inside the importer.** Team, competition-stage and
   person numbers change between saves. They are translated to FM ids at import time and
   never stored.
5. **Your content is kept** and moved across by FM id or natural key.

## What a save gives now (decoded and tested)

| Data | Status |
|---|---|
| Players: FM id, names, birth date, attributes, height, weight | Done (living players only) |
| Clubs and teams (first, B, U21, U19, U18) with FM ids and names | Done |
| Fixtures and results with attendance, away fans and goal minutes | Done for every season since 2022, each fixture linked to its competition through the stage table (`read_stages`) |
| Current league tables | Done |
| Competition names | Done (from the installed game's `lang_db.dat`) |
| Competition winners, runners-up and 3rd place, 1872 to last season | Done |
| Club relationships: rival clubs, favourite and disliked people | Done (17 Oct 2026, `read_club_relations`): each club record holds count-prefixed lists of 16-byte entries (subject, reason, kind, level 0-100, permanent, can name the stadium). Legends are permanent favourites at 100, Icons the other permanent ones. Only the newest save writes them |
| Club finances: bank balance, transfer and wage budgets, income streams, debts | Done (17 Oct 2026, `read_club_finances`): pounds, in each club record (budgets after the names, the bank before three placeholder dates, the income list 45 bytes on); debt lists in their own area, each after a header with the club index + 1. Checked on 10 clubs; 1,314 clubs have finances. Each import keeps a snapshot; the newest save writes the income streams and debts |
| Staff and manager history | Done (18 Oct 2026): every staff contract's job, team, start, weekly wage (exact pounds) and end (`read_staff_contracts`); the managers' spells per team (`read_manager_history`: dates, won/drawn/lost, reason left, titles). The history lists do not name their team: `manager_history.link_lists` finds it from the results (743 of 3,890 lists; nearly all top-division clubs; the rest are mostly lists of one spell that ended when the game began) |
| Injuries | Done (18 Oct 2026): every player's injury history (injury, date, days out, cause, treatment, in a match or in training, the team; `read_injury_history`, linked by `read_injury_groups`: 43,573 players) and current injuries with the expected time out (`read_current_injuries`: 2,623 players). Person page Injuries tab, injured marks in the squad, injury updates on the Feed |
| Holders (the latest finished edition) | Done: winner, runner-up and 3rd for every competition (`read_latest_editions`); full final order for the about 220 competitions the game plays in detail (`read_holders`) |

## Gaps to close in the decoder (step 1)

| Gap | Why it matters | Lead |
|---|---|---|
| Goal scorers: save person number to FM id | Goals need a scorer | The person table in `game_db.dat` maps person number to FM id |
| Retired players' careers | Club legends such as Paul Malcolm | Names and birth dates done (`read_person_names`); careers: `hall_of_fame.dat`, history files |
| Player season statistics per single cup | Cup pages | Done per club, season and kind (league, cup, continental, friendly, national team) for 82,684 players including retired ones; the save keeps one record for all cups of a club, so single-cup totals must come from fixtures |
| Finished league tables with clubs | Season tables before the current one | `tc_league_history_dt.cmt` (club link not found yet) |
| Transfers | Player and club pages | Done (`read_transfers`, 844,877 moves since 2022, with kind, fee and date) |
| Awards: monthly awards, club awards, owners of national-team best elevens | Player and club pages | Yearly awards and seasonal best elevens done (`read_award_history`, `read_best_elevens`) |

## New data model (small)

- `saves`: file name, game date, imported at.
- `clubs`: FM id, name, short name, nation.
- `people`: FM id, first name, surname, common name, birth date, nationality, status
  (playing, retired, staff), first and last save seen.
- `player_snapshots`: person, save, club, attributes, height, weight (one row per save).
- `competitions`: FM id, name, short name, abbreviation, nation, type.
- `editions`: competition, season label, years.
- `matches`: edition, stage, date, home club, away club, score, attendance.
- `goals`: match, scorer (person), side, minute, added time, kind.
- `standings`: edition, club, position, played, won, drawn, lost, goals, points.
- `edition_results`: edition, final order (winner, runner-up, 3rd ...), date of the final.
- Kept content tables: season stories and chapters, competition chapters and reports,
  match reports and previews, World Soccer issues/articles/threads/links, images and image
  links, player and club texts, settings. Their links change from internal numbers to the
  keys above.

## Step 2 design (14 Oct 2026; importer working, see README)

- **A second database.** The new tables live in `instance/fm_world.sqlite3` (the SQLAlchemy
  bind `world`), so the old archive keeps working while pages move over, and table names
  do not clash with the old ones. At step 7 this database becomes the main one and the
  kept content moves into it. The tables are created by the importer for now; Alembic
  migrations for them start at the cut-over.
- **Tables** (keys in brackets; FM uids everywhere, never the save's own numbers):
  `saves` [file name] · `competitions` [FM uid] · `clubs` [first-team FM uid] · `teams`
  [FM uid; club, age limit] · `people` [FM uid; names, birth date] ·
  `player_snapshots` [person, save; attributes, height, weight] · `competition_editions`
  [competition, year; season label, date of the final] · `edition_places`
  [competition, year, place; team] · `fixtures` [date, home team, away team; competition,
  stage, scores, attendance, away fans] · `goals` [fixture, order; scorer, side, minute,
  kind] · `player_seasons` [person, team, season, kind, order; statistics, fee, loan] ·
  `awards` [FM uid] · `award_places` [award, year, place; person, club, numbers] ·
  `best_elevens` [team (or the save's list number for national teams), season, slot; person, position, numbers] ·
  `transfers` [person, date, kind, from team, to team; squad, fees].
- **Importer.** `flask import-save PATH` reads the save once and upserts table by table in
  chunks. A row from a save keeps its value unless a save with a later game date (the
  last played match) changes it, so saves add up in any order and nothing is deleted.
  Fixtures and goals whose teams have no FM uid (some national teams) are counted and
  skipped until those teams are decoded. Played fixtures are facts and add up; unplayed
  fixtures are only the newest save's schedule (older saves write none, and the newest
  removes those that older saves wrote).
- **First run** (Rosetta AC, August 2063, 2 minutes): 1,398,292 fixtures (54,084 skipped:
  national teams), 2,827,601 goals, 874,362 people, 1,583,939 season statistics rows,
  838,501 transfers, 100,145 competition editions, 17,312 award places, 243,641 best-eleven
  places. Importing it twice changes nothing; an older save (v04, August 2062) adds 6,561
  old results that the newer save no longer keeps and changes no newer row.

## Steps

0. **Safety.** Back up the database. Tag the repository `pre-rewrite`. Export all kept
   content to JSON with natural keys, so that it can be loaded into the new schema.
   Give archive players FM ids from the face-pack `config.xml` files where possible
   (for example, Paul Malcolm is 2002312311), so that their stories and photos can move.
1. **Decoder gaps** (table above), scorers and holders first.
2. **New schema and importer.** `flask import-save PATH` reads a save and fills the
   tables. It can run again on the same or a newer save without making duplicates.
3. **Pages.** Re-point the kept pages (dashboard, clubs, seasons, players, competitions,
   matches, World Soccer, settings) to the new tables. Remove import and repair pages.
4. **MCP server.** Keep the read tools, `save_match_report`, the newsroom and the World
   Soccer tools. Remove the identity, merge, scorer and import tools.
5. **Stories.** Re-point the story context builders to the new tables. Move the texts.
6. **Images.** Logos by FM id (as now). Trophies by competition FM id, not by name.
   Rename portrait and stadium files from internal numbers to FM ids.
7. **Delete the import path:** about 30,000 lines of app code and 26,000 lines of tests,
   and the packages `beautifulsoup4`, `ocrmac`, `python-dateutil` and `rapidfuzz`.
8. **Tests.** New tests build a small synthetic save. Real-save tests run when
   `FM_SAVE_TEST_PATH` is set.

## Decisions

Made on 14 Oct 2026:

- **Custom GPT Actions API** (port 8002): keep. It must be re-pointed to the new tables;
  its curation commands go with the curator code.
- **Scope:** the whole world from each save.
- **National teams:** keep the nations pages, rebuilt on the new data.
- **Player biographies:** keep the existing texts; remove the screenshot (OCR) pipeline.

- **Old matches: resolved, they are in the save.** `fix_man.dat` contains inner compressed
  blocks with every archived fixture of the whole world from 2022 to 2061 (994,958
  fixtures, 991,026 played), in the same layout as current fixtures. Checked against the
  game: Chelsea 12-0 QPR (27 Sep 2025) and a 0-19 friendly (20 Jul 2024), with attendance,
  away fans and every goal minute. So the old matches can be rebuilt from the save; no
  frozen history is needed.

Other files with inner compressed blocks, to decode in step 1: `comp_archive.dat`
(7,149 blocks, probably past seasons of competitions), `award_man.dat` (awards),
`retirement_manager.dat` (probably retired players), `transfer_man.dat`, `rgman.dat`,
`match_stats_cfg.dat`.
