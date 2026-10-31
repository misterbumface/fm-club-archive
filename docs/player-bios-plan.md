# Player bios from the save: plan

Status: approved and built, 14 October 2026. This comes before step 4 (MCP) of `docs/rewrite-plan.md`
and replaces its decision "keep the existing texts; remove the screenshot (OCR) pipeline"
for player bios.

## Decisions (14 October 2026)

- **Writer:** a button in the app. "Write bio" on the player page (`/world/people/<uid>`)
  and "Write missing bios (N)" on the club-season page, like the pictures. The AI model is
  the one chosen in Settings (now OpenAI `gpt-5.6-luna`; DeepSeek is the other choice).
- **Old bios:** 345 texts exist (312 AI rewrites, 24 hand edits, 8 FM texts word for word);
  173 have an FM id. When an old text exists, the writer gets it as **colour only**. The
  facts from the save win where the two disagree, because the old archive can be wrong.
- **Length:** a short summary (about 100 words) at the top of the player page, and a full
  profile (450–900 words, 3–5 headings of its own) under it.

## The notebook (the facts the writer gets)

A new `app/services/player_notebook.py` builds one JSON notebook from `world_queries`
(the same queries as the pages, so the page and the bio never disagree):

- **Player:** name, birth date, age on the game date, nationality, height and weight,
  positions (from the best elevens), and whether the player still plays (a season row in the
  newest season).
- **Career by season:** club, league and the club's league position, league and all
  apps, goals, assists, player of the match, average rating, national-team games and goals,
  and loans.
- **Honours:** trophies and lost finals or second places, with season and club, and
  whether the player scored in the final.
- **Awards:** wins and podium places, with year, club and votes. **Best elevens.**
- **Transfers:** date, from, to, kind and fee (pounds and euros).
- **Goals in finals,** with minute and score.
- **Teammates:** the players with the most shared seasons at the same club (both with 10 or
  more apps), and the trophies they won together.
- **Milestones,** worked out from the season rows: debut season, first goal, 100th and
  200th club goal, 100th, 300th and 500th club game, first trophy, best season.
- **Old bio** (`source_biography`), when one exists, marked as colour only.

## The writer

- A new `app/services/bio_writer.py` uses the request code in `ai_settings`
  (`ai_request_config`) and a prompt based on today's profile prompt
  (`ai_narrative_writer._SYSTEM_PROMPT`): facts only from the notebook, no invented quotes,
  scenes, motives or tactics, no inventory of trophies, no stock phrases.
- One request returns JSON: `{"summary": ..., "profile": ...}`.
- **Check before saving:** every year and every score in the text must be in the notebook.
  If one is not, the writer gets one more try with the list of faults; then the bio is
  refused with a message.

## Storage

- A new table `person_bios` in the main database (not the world database, which is rebuilt
  from the saves): `fm_uid` (key), `summary`, `profile`, `source` (`generated` or
  `curator_edit`), `model`, `game_date` (of the facts), `written_at`.
- The 345 old texts stay in `players.career_narrative` until the cut-over (step 7). The
  notebook reads them by FM id.

## Pages

- **Player page:** the summary under the name; the full profile in a section "The story of
  <name>"; a note "Written from the save of <game date>"; the button "Write bio" or "New bio".
- **Club-season page:** "Write missing bios (N)" runs in the background, one player after
  another (about 20–40 seconds each), like the pictures.

## Tests

- The notebook from a small world (career, honours, milestones, teammates).
- The writer with a fake AI answer: the check refuses a year or score that is not in the
  notebook, and accepts a correct text.
- The routes: the button saves a bio; an AI error shows a message and saves nothing.

## Not in this plan

Club and season stories, match reports and World Soccer move in step 5, as planned.
