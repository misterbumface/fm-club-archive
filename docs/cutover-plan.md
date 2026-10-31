# Step 7: the cut-over

Status: done, 15 October 2026 (the curator chose to drop the old tables). Step 7 of `docs/rewrite-plan.md`: delete the old import path
and the old pages, and move `/world` to `/`.

## What the analysis found

A static import graph from the new system (the world pages, World Soccer, the settings page,
the logos route, the MCP server, the Actions API and the new CLI commands) gives:

| | Files | Lines |
|---|---|---|
| Needed by the new system | 83 | 15,614 |
| Not used by it at all | 140 | 57,804 |
| Used only through `universe_curator.py` (the old curator tools) | 71 | 34,669 |
| Tests of old code only | 96 | about 35,500 |
| Templates of old pages only | 51 | about 6,400 |

The World Soccer tools use only 8 small wrappers in `universe_curator.py`; everything else in
it, and the 71 modules behind it, belongs to the removed curator tools.

Packages used only by old code: `beautifulsoup4`, `ocrmac`, `python-dateutil`, `rapidfuzz`.

## Things the new system still takes from the old code

| What | Today | After step 7 |
|---|---|---|
| Old bios (345 texts) given to the bio writer as colour | `players.career_narrative` | a new table `person_old_texts` by FM id (players matched as for the images) |
| The archive date of World Soccer issues | `archive_clock` (old matches) | `world_queries.game_date` |
| Player names linked in World Soccer articles | `narrative_links` (old players) | names of the article's own person links, to `/people/<uid>` |
| Article links without a world reference (4 of 218) | old paths such as `/players/…` | shown as plain labels |
| World Soccer, Settings and error pages | old `base.html` and its menu of old pages | the new layout; the menu gets World Soccer and Settings |
| The 8 World Soccer wrappers | `universe_curator.py` | a small `world_soccer_tools.py` |
| `images_of` | `image_migration.py` | `image_library.py` |

## Work, in order

1. **Safety:** a backup of both databases and a git tag `pre-cutover`.
2. **Move what is still needed:** the old bios to `person_old_texts`; the replacements in the
   table above.
3. **New layout** for World Soccer, Settings and the error pages.
4. **`/world` to `/`:** the world pages become the site; `/world/...` addresses redirect to
   the same page without `/world`, so saved links keep working. Page paths in MCP answers
   and World Soccer links lose the `/world` prefix.
5. **Delete:** the old routes, services, models, schemas, templates and tests; the curator
   and the 71 modules behind it; the one-time move commands (`stories move-old`,
   `images move-old`; they have run, and git keeps them); the old watchers and folders made
   at start; the four packages; old tools in `tools/` (`fm_save_reader.py` stays).
6. **The old tables** (39, for example `players`, `matches`, `seasons`): see the question below.
7. **Check:** all tests, every page in the browser, the MCP server, then restart the services.
   Update the README and the rewrite plan.

Files under `app/static/generated/` stay: the archive's pictures point to them.

## Done (15 October 2026)

- 329 old bios moved to `person_old_texts` (13 players could not be placed by FM id).
- Deleted: 184 app modules (about 62,000 lines), 103 test files, 51 templates, the old stylesheet,
  7 old tools, the HTML test fixtures and four packages. Kept: 44 app modules (about 10,000 lines).
- The 39 old tables were dropped; `fm_archive.sqlite3` went from 93 MB to 4.4 MB. Backups:
  `instance/backups/fm_archive.pre-cutover-20261015.sqlite3` and `fm_archive.pre-drop-20261015.sqlite3`.
- The Settings page lost the WaveSpeed portrait model (its service is gone; portraits come from
  Draw Things).

## The question that was asked

**The 39 old tables:** drop them with a migration (the backup from step 1 and the earlier
backups keep them), or leave them in the database unused? Dropping keeps the database clean
and small; every fact in them comes from the saves again, and every text has moved.
