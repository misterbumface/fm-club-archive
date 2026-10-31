# New pages: plan

Status: approved and built, 14 October 2026. The pages live under `/world` (`app/routes/world.py`,
`app/templates/world/`, queries in `app/services/world_queries.py`, tests in `tests/test_world_pages.py`)
and move to `/` at the cut-over. Mock-ups with real data: `instance/mockups/` (git-ignored, because they
copy club logos and trophy images); open `index.html` through a local web server.

Cup rounds have no names in the save. `world_queries.round_names` joins second legs and replays
to their round (the same pairs of teams again) and names the rounds back from the final while each
round has twice the teams of the next one; a stage with as many games as teams is a group.

Decisions: a new design and a new page list (the old templates are not reused);
Flask + Jinja, rendered on the server with little JavaScript; the whole world is
treated equally (every club, nation, competition and player gets the same kind of page;
Chelsea is one club among many); mock-ups are approved before page code is written.

**The main use:** seeing players with the trophies they won, and writing articles about
players, seasons and matches. So every player, club season and match page has an honours
section with trophy images, and each page gives the facts an article needs (the story
context that the MCP server and the story writer read in steps 4 and 5 uses the same queries).

All pages read the world database (`instance/fm_world.sqlite3`, step 2). URLs use FM
uids, so a link stays the same after every import.

## Page list

| Page | URL | What it shows |
|---|---|---|
| Home | `/` | The game date; the latest results of the big leagues; the newest winners; the current league leaders; search. |
| Search | `/search?q=` | People, clubs, national teams, competitions and awards, in one list. |
| Club | `/clubs/<uid>` | Header (name, current league, honours count). Tabs: **Overview** (last results, current season), **Seasons** (one row per season: league, finish, cup runs, top scorer), **Honours**, **Players** (everyone who played, with appearances and goals), **Transfers** (in and out, by season), **Best elevens**, **Records** (top scorers, most appearances, biggest wins). |
| Club season | `/clubs/<uid>/<season>` | The trophies won that season and the squad that won them; results in every competition (finals marked); the squad with statistics; transfers in and out; the best eleven; the awards its players won. Everything an article about the season needs. |
| National team | `/nations/<uid>` | The senior team and its youth teams: results, tournaments and finishes, top scorers. |
| Competition | `/competitions/<uid>` | All editions: winner, runner-up, third, date of the final; most titles. |
| Edition | `/competitions/<uid>/<year>` | Results by round or matchday, the table built from the results, top scorers. |
| Player | `/people/<uid>` | Name, birth date, age; **trophy cabinet** (every trophy won, with trophy image, club and season; runner-up finishes below); career table (league, and the other kinds of competition on demand); transfers and loans; awards and best elevens; goals by season, with the goals in finals; latest attributes. Kept content (biography, photo) once it moves across (step 5). |
| Match | `/matches/<id>` | Score (extra time, penalties), goals with minutes, attendance and away fans, the competition and round, earlier meetings of the two teams. |
| Award | `/awards/<uid>` | Winners, runners-up and third places by year. |
| World Soccer, stories, settings | as now | Kept; they move to the new data in steps 4 to 6. |

## Player honours (derived)

The save does not list a player's trophies, so the importer works them out into a
`player_honours` table [person, competition, year; team, place]: a player gets a
trophy (or a runner-up place) when his club finished first (or second) in an edition
and he has appearances for that club, that season, in that kind of competition (league,
cup, continental). Limit: all domestic cups of a club share one statistics record, so a
player who played only in the FA Cup also gets a League Cup the club won that season;
a goal in a cup's fixtures proves which cup he played in.

## Shared elements

- One header with search on every page; breadcrumbs (Competition > Edition > Match).
- Team names always link to the team page, people to the player page.
- Tables sort by clicking a column (a few lines of JavaScript); long lists page by season.
- Badges and photos from the existing images, by FM uid (step 6).
- Dark and light colour schemes; the layout works on a phone.

## Data the pages cannot show yet

- The nation of a club or a competition (not decoded), so a nation page lists its
  national teams only, not its clubs and leagues.
- Single-cup statistics for a player (the save keeps one record for all cups of a club).
- Line-ups and appearances per match (only goals are decoded).
- 78 clubs without a team record (about 0.7% of fixtures).

## Pictures

Every person page has a picture, and lists (squads, scorers, best elevens) show small faces.
The archive takes the first one it finds:

1. **The game's face**: the FM face pack (`graphics/Faces/**/config.xml` maps FM uids to images;
   3,414 faces in October 2026).
2. **An archive portrait** generated earlier (235; moved across by FM uid).
3. **A generated portrait**, made locally with Draw Things through its HTTP API
   (`/sdapi/v1/txt2img`), from the player's age, position, club and nationality.
4. A neutral silhouette, with a "Generate" button.

Generated portraits are cut out of the studio background (macOS Vision's subject lifting, so
the game shows them like its other faces), saved by FM uid in the archive (512 px) and copied
into a separate folder of the face pack (`graphics/Faces/Archive generated/`, 250 px, with its
own `config.xml` using the standard `r-<uid>` form), so the game shows the same face after the
cache is cleared and the skin reloaded. The user's own faces are never changed; deleting that
folder undoes it. Portraits are made **on demand only**, no big batches: a "Generate picture"
button on the player page (`flask portraits generate <uid>`) and a "Generate missing pictures"
button on the club-season page for its squad (`flask portraits team <team uid> [--season]`). The prompt uses age, ethnicity,
nationality, skin tone and hair from the save, the position from the best elevens, and the
club's name for the shirt colours.
