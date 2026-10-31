# The media: the world told as news (draft for approval)

Status: draft, 15 October 2026.

The archive is the record; the media are how the world is read. Every import of a save
becomes a news cycle: a story finder looks for what happened since the last editions, ranks
it, and several outlets write their editions in their own voices. You read the drafts, edit,
rewrite or drop articles, and publish. The archive pages stay one click away, for the facts
behind every story.

## Decisions (15 October 2026)

- **Editions are made automatically on import,** as drafts that you publish.
- **Several outlets from the start,** each with its own voice and beat.
- **The home page is the newsstand:** the latest front page of every outlet.
- **News and some history:** each cycle also has a few features from the world's past.

## 1. The outlets

Names are fictional (no real publications), and you can rename every outlet and change its
voice on a settings page. A first set:

| Outlet | Voice | Beat |
|---|---|---|
| **World Soccer** | measured, global monthly | the biggest stories anywhere, finals, nations |
| **A national daily per big football country** (England, Spain, Italy, Germany, France at first) | local, partisan about its own clubs | its country's leagues, cups, clubs and players abroad |
| **The Transfer Wire** | fast, tabloid | transfers, records, homecomings, moves between rivals |
| **Next Gen** | enthusiastic, scouting eye | young players, breakthroughs, young award winners, debuts |
| **The Long Ball** | reflective, long features | history: anniversaries, retrospectives, "where are they now", all-time lists |
| **By the Numbers** | dry, data-led | records, streaks, milestones, extremes |

## 2. The story finder

`story_leads.py` reads the world database for one window (since the last cycle) and returns
**leads**: a type, the facts (a small notebook), the people, clubs, nations and matches
involved, and a newsworthiness score. A first set of finders:

| Kind | Examples of what it finds |
|---|---|
| Underdogs | a nation's or club's first-ever title (Gabon wins the World Cup); a title after a long wait; a final reached for the first time |
| Cinderella | clubs promoted two or more seasons running; a lower-league club deep in a cup; a newly promoted champion |
| Giant-killings | cup wins against clubs from a much higher level; big upsets in leagues |
| Title races and dynasties | champions decided, the margin, the last day; runs of titles (Chelsea's sixteen); relegations of big clubs |
| Transfers | world, league and club record fees; the biggest moves of the window; moves between rivals; returns to a former club; free transfers of stars |
| Rising stars | young players with high ratings, many goals or a starting place at a big club; young award winners; national-team debuts and first goals |
| Records and milestones | 100th, 200th… goal; most goals in a season; biggest wins; hat-tricks in finals; unbeaten runs |
| Awards | the Golden Ball and the other awards of the year, with the podium |
| Departures | long careers that ended (no games for a season after many years) |
| History (for The Long Ball) | 10, 20, 25… years since a title, a final or a debut in the window; retrospectives of a dynasty; "where are they now" of former stars |

The score weighs rarity (a first title in 40 years beats a fifth in a row), size (crowds and
level of the competition, fee in proportion to the league), and surprise (the gap between the
sides). Before the writers come, you get a ranked list of leads from a real window to check
that they are the stories you would want.

## 3. The data the finders still need

Some finders need facts the world database does not have yet:

- **The country of each club and competition** (for the national dailies and "around the
  world"). To be read from the save's club and competition records, or worked out from the
  competition names and the clubs' leagues.
- **The level of each league** (for Cinderella runs and giant-killings): from the save, or
  from promotions and relegations between leagues, and their crowds.
- **The stature of nations and clubs** (for underdogs): from their titles and crowds over
  the 40 years.

This is the first step, with the same method as before (the save first; the game's editor
and your screenshots for checks).

## 4. Writing an edition

- Each outlet takes the leads of its beat, best first, and fills its sections: a front-page
  lead, three to eight articles, briefs.
- An article is written by the AI model chosen in Settings, from the lead's notebook, in the
  outlet's voice, with the checks the bios and stories have (every year and score in the
  notebook; no names that are not in it).
- Each article gets its links (people, clubs, matches, editions) and a picture chosen from the
  faces, crests, trophies and the archive's own pictures.
- A whole cycle is about 30–50 articles: a few minutes in the background, with progress shown.
- Stories continue across editions through the existing story threads (a transfer saga, a
  title race, a rising star).

## 5. Pages

- **Newsstand (home):** the latest front page of every outlet, and the biggest headlines.
- **An outlet:** its latest edition, its back issues, its threads.
- **An edition:** the newspaper layout World Soccer has now, for every outlet.
- **The desk (drafts):** read, edit, "rewrite", drop, reorder, publish. "Make editions now" for
  a window without a new save.
- The archive pages link back: a player's page lists the articles about the player.

## 6. MCP

The chat gets the leads and outlets too (`get_story_leads`, `get_outlet`), so you can also
work an edition in conversation, as now with World Soccer.

## Order of work

1. The data the finders need (country, league level, stature).
2. The story finder, and a ranked list of leads from a real window for you to check.
3. Outlets (table, settings page) and the article writer.
4. Editions on import, the desk, the newsstand and the outlet pages.
5. History features, story threads across editions, MCP tools.

Each step ends with a check by you before the next.

## Progress (15 October 2026)

- **Step 1 (done):** club and competition countries, league levels.
- **Step 2 (done):** `story_leads.py`, weighted by the game's own reputations (competitions 0-200,
  teams and players 0-10 000, read from the save and checked in the editor), with the followed
  clubs (Chelsea, Boreham Wood) boosted. The leads page is `/world-soccer/leads`.
- **Step 3 (done):** the outlets (Settings › Outlets) and `article_writer.py`. Writing rule, from
  you: this is a fictional world told with life. The hard facts of the save (names, dates, scores,
  results, scorers, fees, moves) stay true; the colour around them (emotion, atmosphere, crowds,
  drama, quotes) is free. This applies to every writer, bios and stories too.
- **Step 4 (built):** `editions.py` makes one draft edition per active outlet per news cycle
  (after each `flask import-save`, with `flask editions`, or "Make editions now" on the desk);
  the newsstand is the home page (the old home is `/today`); outlets at `/news/<slug>`; the desk
  at `/desk`; player and club pages list the articles about them. Each outlet has a staff of
  fictional writers (persona, style, quirks; `/writers/<id>`), and each article of an edition has
  its own byline, opening and headline form. Scouting reports use the save's current and
  potential ability, ranked by how far ahead of his age a teenager is, and written in words.
- **Step 5 (built):** story threads: articles about one player or club join one thread across
  outlets and editions, the next writer sees the earlier coverage, and `/threads/<id>` tells the
  story so far. History features for The Long Ball, each told once: legend profiles, dynasties
  and great finals. MCP and Actions tools: `get_story_leads`, `list_outlets`, `write_story_lead`.
- **Step 6 (built, 19 October 2026):** the news of the season (`story_leads_now.py`), from you: a
  weekly reports what happened, what is happening and what will happen. For the 14 strongest top
  divisions and the followed clubs' leagues: the round-up of each league (the cycle's results, the
  table, who rose and fell, unbeaten and winless), runs (winning, losing for a well-known club with
  its manager under pressure, unbeaten and winless starts), derbies, the next two weeks' big games,
  injuries that hurt (how long, the games missed), big names below their level and small names far
  above it (average ratings), clubs far from the place their standing suggests, and the scoring pace.
  A transfer's facts say his position, whom he replaces, who is injured there and whom he competes
  with. While the leagues play, a news paper (the world or a nation beat) leads with the season's
  news, ranks it higher, and prints at most one story that looks back, one scouting report and three
  transfers; The Long Ball, Next Gen and The Transfer Wire are as before.
- **Step 7 (built, 20 October 2026): team sheets.** The save's match reports hold each player's shirt number and the
  slot he filled at kick-off and at the end (verified on the game's match screen and on every side of a save).
  `tactics.py` reads them, without AI: each side's formation and changes during the game, a team's profile before a
  match (usual shape, who usually fills each slot, regulars, partnerships with the record with and without them), and
  the talking points of a selection: a change of shape, regulars missing and why (injured, on the bench, not in the
  squad, sold) and who took their slot, new signings and youngsters starting, stars on the bench, players out of their
  usual or natural slot. The writers get the line-ups and these notes with every match report, the fans the shape and
  the main talking points. Only the slots are known, not roles or instructions. `flask match-positions SAVE...` fills
  the reports imported before.
- **Step 8 (built, 20 October 2026): stories from the team sheets** (`story_leads_tactics.py`). Previews have each
  side's expected eleven (usual shape and players, without the injured and those gone), the regulars who are out and the
  flank duels with recent ratings. New kinds: a change of system (with the results in each, and a new manager), a
  player in a new role (and how he does there), a regular dropped (on the bench, no injury recorded, and who took his
  slot), a youngster breaking through, a partnership that makes or costs a team, and a league's systems and the points
  each wins (monthly). A regular out of every squad is not called dropped: the save may lack his injury.
- **Step 9 (built, 20 October 2026): pages and the game's names.** The match page shows both team sheets on pitches
  (shirt numbers, ratings, the changes and the talking points); the club season page has "How they play": style labels
  and figures against the league, formations used and faced (as the game's Data Hub, for every team: points a game,
  goals, a clear-cut chance every so many minutes), partnerships, rotation and the usual eleven. Formations carry the
  game's names ("4-3-3 DM Wide", "4-2-3-1 DM AM Wide"); combinations not checked on the game's screens follow its
  pattern. Previews give each side's record against the formation the other is expected to play.
- **Step 10 (built, 20 October 2026): managers and the game's partnerships.** The save's staff blocks give every
  manager's coaching attributes, his preferred formations (named where the game's screens have shown the number) and,
  for about one in six, his tactical style (Gegenpress, Vertical Tiki-Taka, Wing Play proven; the others wait for a
  screenshot); the coaching style is his highest coaching attribute. They show on the person and club pages and reach
  the writers. The human club's partnership table (games together for about an hour or more, average rating, goals)
  shows on its club page and in its match notes. Previews, scouting reports and every person's notebook now say each
  club of a career and how he joined his club now (a signing from Bielefeld was written up as an academy product).
- **Step 11 (built, 22 October 2026): morale, happiness and the dressing room.** The save gives each player's morale
  (1-20) and overall happiness (-100 to 100), his dated concerns (left out of a squad, wants to move on, a new deal, to go
  home, more games, and team-mates he thinks have been treated unfairly), the people he favours and dislikes, and the
  human club's social groups. The player page has a "Morale and happiness" box, the club's squad a morale column and the
  concerns, and the human club a dressing room (its groups, and who takes whose side). New kinds: an unsettled player, a
  dressing room taking sides, a concern that ends; a transfer story knows the concerns before the move. Writers and fans
  hear only what has reached the team-mates: a concern he keeps to himself is not told. The game's words for the other
  morale values and for happiness below "Happy" wait for a screenshot.
- **Step 12 (built, 23 October 2026): expected goals and points.** A league's xG table (league page): each team's xG and xG
  against from its players' xG in every match with a report (equal to the game's Data Hub to the decimal), and expected
  points per match from the two xG values (a Poisson model; within about 0.2 of the game's, which works shot by shot). A new
  kind tells a team far above or below its expected points, and previews say each side's points against its expected. The
  archive's season totals (passes, shots, dribbles, key tackles, crosses, goals, xG) equal the game's own squad statistics
  for all 26 Chelsea players checked (31 October 2063). Shot positions, per-shot xG, pass maps and momentum are not stored
  in the save: the game runs the match again to draw them.
- **Step 13 (built, 24 October 2026): the club's tactics.** The save's tactic blocks are read: the human manager's three
  slots and his saved library (`tactics_man.dat`), and the tactic used in each match the save keeps a match file for.
  Each gives the formation, the style (Tiki-Taka, Gegenpress...), the mentality, the fluidity and the eleven slots'
  roles and duties, with the players' personalised roles on top. Seventeen roles are proven against the tactics screen;
  the rest show by position with their key until a screen names them. A match file holds its season and crowd, not its
  date: its fixture is found from them (23 of 23 in the Chelsea save). The public club page has "How they play" (the
  system drawn on a pitch, each slot's role, the personalised players) and "Systems over the years" (the spells, by
  season); previews carry the human club's system as a fact for the writers. A `.fmf` tactic file from the game or a
  forum is readable too (AES-128-CTR with the key in its header; a scratch decoder, not yet in the app).
- **Step 14 (built, 24 October 2026): the promise kept.** A game a paper previews gets its report in that paper's next
  edition once it is played (kind `promised_report`, always in): the result, the goals, the player of the match and what
  the preview said (shapes, duels, absentees, what each side needed), settled in a sentence or two; the writer chooses a
  full report or a short one. The preview and the report link to each other on the public site.
- **Step 15 (built, 24 October 2026): the drama of a game.** Previews rank by what is at stake, not only by the names:
  a title decider (first against second, three points apart, late in the season), a relegation six-pointer, a cup final
  or semi-final, a long unbeaten or losing run at stake, a manager's or a long-serving player's return to his old club, a
  scoring record or a hundredth cap within reach. Each is a fact for the writer and a bonus to the preview's score.
- **Step 16 (built, 24 October 2026): competition picture pools.** Every story about a competition showed its logo. The
  Desk's "Draw competition pictures" has Draw Things make a few pictures (six by default) for each competition in the news,
  kept in the image library; a story without its own picture takes one from its club's pool (stadium and photographs) or
  its competition's, chosen by the story's number, so stories differ and a story keeps its picture.
- **Step 17 (built, 24 October 2026): the transfer market.** The save's `transfer_man.dat` holds every club's list of wanted
  players (verified on four Chelsea players' Transfer tabs; an entry with no worked-out wage is the "rumoured" one) and every
  bid with its club, squad, fee in pounds, status (made, agreed, completed, collapsed) and dates (verified on Chelsea's
  2063/64 business, including the £43M for Plaßhenrich that fell through). Tables `transfer_interest` (the newest save's
  lists) and `transfer_offers`; "Transfer market" on the person page and "Transfer talk" on the public player page; two
  story kinds: clubs after a player, and a bid made, agreed, completed or collapsed. Loan bids are a different record, not
  read yet; the reports of internationals are read since the same day.

- **Step 18 (24 Oct 2026): every tracked player's recent games, and tournament mode.** The game keeps "Recent games for
  <team>" rows (up to twenty per team, with every figure) only for the players it tracks: the human's squads and the
  shortlist. They sit in `game_db.dat` inside the player's block, before his person row, and outlive the full match reports.
  Table `player_games`; "Recent games" on the person page's Stats tab and on the public player page; a match whose report
  is gone shows "The players we know of" on both match pages and the match notebook carries them in words. Tournament mode:
  when a major international tournament (the World Cup, the European Championship, the Copa América and the other
  continental finals) is in play, from a week before its first game to three days after its last, the front page opens with
  the tournament box, the nav gets a yellow item, the tournament page a "Today" section with the followed clubs' players;
  every game of the cycle gets a report lead, every game of the next two weeks a preview, the cycle a round-up and an
  "our players at the tournament" story, the final a "player of the tournament" piece (`tournament_leads.py`); the
  finals get picture scenes of their own and a bigger pool.

## Open questions

- The national dailies: which countries first (besides England, Spain, Italy, Germany,
  France)? One for the country of your own club is in any case.
- Do you want to name the outlets now, or after you read the first editions?
