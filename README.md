# FM Club Archive

*Level up your autism with a fake sports website based on your FM24 save.*

FM Club Archive turns your Football Manager 2024 save into a newspaper and a sports website. It
reads your saved game, finds the stories in it (title races, upsets, record transfers, wonderkids,
managers under pressure, legends retiring) and has an AI write them up as newspapers. You can see
what it makes at [worldsport.uk](https://worldsport.uk), and read how it works on its
[about page](https://worldsport.uk/about).

It runs on a Mac. You do not need to know how to program. You will copy a few lines into the
Terminal app once, and after that it is a normal app.

## What you need

- **A Mac.** It is made and tested on Apple silicon (M1 or newer).
- **Football Manager 2024 from Steam**, and a save you play.
- **Python 3.12.** Get the macOS installer from [python.org](https://www.python.org/downloads/macos/)
  (any 3.12 version) and install it like any other app.
- **An AI writer.** The cheapest is a [DeepSeek](https://platform.deepseek.com/) API key: make an
  account, add a little credit, and make a key. An [OpenAI](https://platform.openai.com/) key also
  works. If your Mac has a lot of memory you can use a free local model with
  [Ollama](https://ollama.com/) instead, but it is slower and writes less well.
- **Optional: graphics packs** for logos, flags, faces and stadiums (see below).

## Install it

1. **Download the code.** On this page, click the green **Code** button, then **Download ZIP**.
   Open the ZIP in your Downloads folder. You now have a folder called `fm-club-archive-main`.
2. **Open Terminal.** Press Cmd + Space, type `Terminal` and press Return.
3. **Copy these lines into Terminal**, one at a time, and press Return after each one. Wait for
   each to finish before the next. The third one takes a few minutes.

   ```bash
   cd ~/Downloads/fm-club-archive-main
   python3.12 -m venv .venv
   .venv/bin/pip install -r requirements-dev.txt
   zsh scripts/build_mac_app.sh --install
   ```

   When the last line says `Installed: /Applications/FM Club Archive.app`, you are done with
   Terminal. You can close it.
4. **Open "FM Club Archive"** from your Applications folder.

If a line gives an error, check that the folder name in the first line is right, and that you
installed Python 3.12. You can safely run the lines again.

## First start

1. Click **New archive**. An archive is one save's world; it keeps everything it learns.
2. Fill in the form:
   - **Name:** anything, like "Arsenal save".
   - **Game folder:** your Football Manager 2024 folder, the one with `games` and `graphics` inside.
     On a Mac the game keeps it in a hidden folder: `Library/Application Support/Sports
     Interactive/Football Manager 2024` in your home folder. The app fills this in for you. To
     choose it by hand, click **Choose…**, press Cmd + Shift + G and paste
     `~/Library/Application Support/Sports Interactive/Football Manager 2024`. If you moved it (to
     Dropbox, for example, in the game's preferences), choose that one.
   - **Installed game:** the game itself. The app finds it in Steam by itself.
   - **The save:** the save you play.
3. Click **Create and import**. Reading a save takes two to three minutes. It reads the whole
   world, not just your club.
4. Open **Settings** and paste your AI key (DeepSeek or OpenAI). The key stays on your Mac.

## Using it

- **Play the game as usual and save.** Then, in the app, import the new save. The app shows a
  button when it sees a newer save. Turn on **Automatic** and it does this by itself while it is
  open.
- **Make the papers.** After an import, the app finds the news since the last papers and writes
  them. Each paper has its own country, writers and style. You can read them, correct them,
  write a story again, or drop it, before you publish.
- **Browse the world.** Every player, club, competition, match and award has a page, with
  careers, trophy cabinets, records and history.
- **Pictures** of players and stories are optional. They need an AI picture maker: an OpenAI
  key, or a picture app on your own computer. The app puts the faces it makes into the game too,
  as a face pack.
- **A public website**, like worldsport.uk, is optional. It needs a free Cloudflare account and a
  domain. See the Cloudflare part of Settings.

## Graphics packs (optional)

The app uses the same graphics packs as the game, from your game folder's `graphics` folder. If
you already have packs installed for the game, the app finds them by itself. Without packs it
still works, with plain badges instead of logos.

- **Logos and flags:** a logo megapack for FM24. Two popular ones are the
  [TCM Logos Megapack](https://sortitoutsi.net/graphics/style/13/tcm-logos-megapack) on
  sortitoutsi and the [FMG logo packs for FM24](https://footballmanagergraphics.com/files/category/59-fm24-fmg-logos)
  on Football Manager Graphics. Take the FM24 version, not FM26.
- **Stadiums:** a stadium pack, such as the ones in
  [sortitoutsi's background and stadium packs](https://sortitoutsi.net/graphics/download/17).
  Stadium pictures go on club pages and stories.
- **Trophies:** the [Trophies Pack](https://sortitoutsi.net/graphics/style/32/trophies-megapack) on
  sortitoutsi. Trophies go on competition pages and in stories about finals and titles. Unpack it
  into your game's `graphics` folder, into a folder with "trophies" in its name.
- **Faces:** a face pack is useful in the early seasons, while real players are still playing.
  Later the players are the game's own, and the app can draw them.

Any FM24 pack works if it has `config.xml` files in its folders, which nearly all packs do. Follow
each pack's own install guide, and restart the game and the app after.

## Good to know

- Everything stays on your Mac: your saves, the archive and your keys. Only the AI writer (and
  the website, if you publish one) sees anything, and only the facts it needs.
- The app never changes your saves.
- The app backs up its library every day and keeps two weeks of copies.
- It is made by a fan. It is not made by, or connected with, Sports Interactive or SEGA.

---

# For developers

A local history archive of one Football Manager world, built from the game's own saved games
(`.fm`). It reads every club, nation, competition, match, player season, transfer and award of
the whole world into a database, and shows them as pages: players with their trophy cabinets,
clubs with their squads and seasons, competitions with every edition, matches with every goal.
Bios, season stories, competition stories and match reports are written by an AI model from
these facts only, and checked before they are saved.

Every fact comes from the saves; nothing is typed in by hand. The design is in `docs/`:
`rewrite-plan.md` (the whole rewrite), `pages-plan.md`, `player-bios-plan.md`, `media-plan.md`,
`stories-plan.md`, `images-plan.md` and `cutover-plan.md`.

### Two databases

- **`instance/fm_world.sqlite3` — the world.** Rebuilt from the saves by `flask import-save`:
  clubs, teams, nations, people (with nationality, appearance and current team), fixtures
  and goals, competition editions and their first places, season statistics, awards, best
  elevens and transfers, all keyed by FM unique id. It can always be made again from the saves,
  so it is not part of `flask backup`.
- **`instance/fm_archive.sqlite3` — what a save cannot give.** Written texts (bios, season and
  competition stories, match reports and previews, old diary chapters, club texts), the
  archive's own pictures and their links, World Soccer issues and articles, and settings.
  Everything is keyed by FM id, so it survives a rebuild of the world.

### Archives

An archive is one folder: one game's world, its library, its pictures (`generated/`, served at
`/static/generated/...`) and its settings (`archive.json`: FM's user folder with `games/` and
`graphics/`, the installed game, the archive's own save and its own face-pack folder).
`FM_ARCHIVE_DIR` chooses the folder; the first archive is `instance/`. Two games must not share an
archive: their new players share FM uids.

```bash
flask archive                                    # this archive's folder and settings
flask new-archive ~/Archives/Barcelona --name "Barcelona 2024" --fm-user-dir "~/Library/CloudStorage/Dropbox/Football Manager 2024"
FM_ARCHIVE_DIR=~/Archives/Barcelona flask run --port 5051   # makes its databases on the first start
```

### Installation

```bash
cd fm_club_archive
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # for development and tests: requirements-dev.txt

export FLASK_APP=run.py
flask db upgrade                 # creates instance/fm_archive.sqlite3 (a new archive does it on its first start)
```

The AI model is chosen on the Settings page. Its API key goes in the git-ignored `.env`
file (`OPENAI_API_KEY` or `DEEPSEEK_API_KEY`).

### Reading a saved game

```bash
flask import-save "~/Library/CloudStorage/Dropbox/Football Manager 2024/games/My Save.fm"
```

It takes about two and a half minutes for the whole world. The game's `lang_db.dat` (for the
names of competitions, nations and awards) is found in the macOS Steam folder; `--lang-db`
gives another path. Importing the same save again changes nothing, and saves add up: an
older save adds what a newer one no longer has, but never overwrites newer values. Unplayed
fixtures come only from the newest save.

`tools/fm_save_reader.py` reads a save from the command line (players, fixtures, tables,
winners, careers, awards, transfers, and a byte `diff` of two saves). `app/services/fm_save.py`
explains the format and what is still unknown. To learn a new field, change one value in the
game's editor, save under a new name, and diff the two saves.

### Running the app

The archive is a Mac app, "FM Club Archive" (docs/mac-app-plan.md). Build it and put it in
/Applications (quit the app first):

```bash
scripts/build_mac_app.sh --install
```

The archives live in `~/Library/Application Support/FM Club Archive/archives/`, one folder per
game; the app opens the last one and makes the new ones (File › New Archive…). Its logs are in
`~/Library/Logs/FM Club Archive/`, the AI keys in `~/Library/Application Support/FM Club
Archive/keys.json`. A new build changes no archive: the app brings each one up to date when it
opens it, and the previous app is kept beside the archives. The build signs with this Mac's
certificate "FM Club Archive Local Signing" when there is one, else ad hoc.

To work on the code, run the window from the source code, or one archive in a browser:

```bash
source .venv/bin/activate
python -m macapp.main                                    # the app's window
FM_ARCHIVE_DIR=~/path/to/archive flask --app app run --port 5050   # one archive at http://127.0.0.1:5050
```

(Port 5000 belongs to the macOS AirPlay Receiver.) Pages that lived under `/world/...` before
the cut-over redirect to the same page without `/world`.

### Pages

- **Today, search, competitions, nations, awards** — the whole world, every club and nation
  treated the same.
- **Players** — career by season, trophy cabinet and lost finals, awards, best elevens,
  transfers, goals in finals, photos, and the bio ("Write bio").
- **Clubs** — honours, every season, the squad now (with loans in and out), top scorers,
  stadium and pictures; **a club's season** — results, finals, squad numbers, transfers, the
  season story and "Write missing pictures / bios" for the squad.
- **Competitions and editions** — every edition, tables (also for groups and league phases),
  named cup rounds, finals and top scorers, and the competition story.
- **Matches** — score, extra time, penalties, goals, crowd, head to head, report and preview.
- **World Soccer** — the archive's newspaper: issues, articles and story threads.
- **Settings** — the AI model that writes.

Portraits come from the game's face pack first, then the archive's own pictures; missing
ones can be made on demand with a local Draw Things server (`flask portraits generate UID`,
`flask portraits team TEAM_UID`), cut out and copied into the face pack's own
"Archive generated" folder.

### Running tests

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
```

The tests build a small world in memory. Tests that read a real save run when
`FM_SAVE_TEST_PATH` points to one.

### Backups

The app saves a consistent copy of an archive's library (`fm_archive.sqlite3`) in the archive's
`backups/` folder once a day (at start when the last copy is a day old, then daily while it
runs), and keeps the newest 14.
`flask backup` does the same by hand (`--keep N`). The world database is not backed up: the
saves are its backup.

Logos, flags, faces and stadiums come from the game's own graphics folder (the archive's game
folder); `FM_LOGO_DIR` and `FM_FLAGS_DIR` point elsewhere.

### Project structure

```text
fm_club_archive/
    app/
        models/       world.py (the world database) and the kept content tables
        services/     fm_save (the save reader), save_reading and save_import, world_queries (the
                      pages' facts), story leads, notebooks and writers, editions, pictures
        routes/       world.py (the site), news.py (papers and desk), world_soccer.py, settings.py,
                      assets.py (logos)
        templates/    world/, world_soccer/, settings/, errors/
        static/       css, and generated/ (the archive's pictures); trophies and logos come from the user's packs
    migrations/       Alembic migrations of fm_archive.sqlite3
    tests/
    tools/            fm_save_reader.py
    docs/             the plans of the rewrite
```
