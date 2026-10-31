# The Mac app

**Goal:** "FM Club Archive.app", opened with a double-click. No Python, no terminal, no launchd services,
no `.env` file. Each game has its own archive. You choose the game folder and the save in the app.

**State (16 October 2026): built and in use.** Step 1 (an archive is a folder, `f419c86`), step 2 (a new
FM24 game reads correctly, `dc18fdf`) and this step 3 are done: part A (`f817c9f`), part B (`c87518d`),
the keys file and the local certificate (`6108414`), and part C (the archive moved; no code).

## 1. What the app is made of

| Part | Before | In the app |
|---|---|---|
| Python and the libraries | `.venv` | bundled by **PyInstaller** (a `.app` of about 85 MB with Python 3.12, Flask, SQLAlchemy, numpy, zstandard, Pillow) |
| Web server | gunicorn, started by launchd on port 5050 | **waitress** (pure Python, threaded) on `127.0.0.1` and a free port, one server process per archive |
| The window | a browser tab | **pywebview**: a native window with WebKit, and a native menu |
| Imports on several cores | spawn processes from a gunicorn worker | the same spawn processes; the entry point calls `multiprocessing.freeze_support()` |
| Backups | the launchd service `com.fmclubarchive.backup` | the app: at start when the newest copy is a day old, then daily while it runs |
| AI keys | `.env` | entered on the Settings page, kept in `keys.json` beside the archives (this user only) |

PyInstaller, not py2app or Briefcase: it handles numpy, zstandard and Pillow well, and it supports
spawned processes in a frozen app. The launchd services and their scripts (`scripts/*_archive.sh`,
`gunicorn.conf.py`) are gone.

## 2. Where the data lives

```
~/Library/Application Support/FM Club Archive/
    app.json                      the archives, the last one opened, the window size
    keys.json                     the AI keys (DEEPSEEK_API_KEY, OPENAI_API_KEY), readable by this user only
    previous app/                 the app before the last install, zipped, to go back to
    archives/
        Sam Archer - Chelsea v2/   one archive folder (step 1): fm_world.sqlite3, fm_archive.sqlite3,
                                      generated/, backups/, archive.json, secret_key
~/Library/Logs/FM Club Archive/   app.log, and one log per archive (its server and jobs)
```

An archive can also live elsewhere (the trial archive is in `~/Projects/FM24 Archive/archives/`); the
app remembers its path. The keys are kept outside the archives, so no archive or backup carries them.

The app's own files (code, templates, CSS, the shipped trophy pictures, migrations) are inside the
`.app` and are never written to. The large graphics stay where the game keeps them: the logos, flags,
faces and stadiums all come from the archive's game folder (`archive.json`).

## 3. Opening the app

1. **No archive yet:** a welcome page lists the known archives, with "New archive" and "Open an archive
   folder…".
2. **New archive** (step by step on one page):
   - the archive's name;
   - **the game folder:** FM's user folder, with `games/` and `graphics/`. The page proposes FM's normal
     place; "Choose…" opens a folder dialog (yours is in Dropbox). It shows what it finds there: the
     number of saves, and whether faces, logos and stadiums are present;
   - **the installed game:** found in Steam's folder, or chosen; needed for the names of competitions,
     nations and awards;
   - **the save:** the saves in `games/`, newest first;
   - "Create and import": the archive is made in `archives/<name>`, it opens, and its first import
     starts (without the news cycle). The guard (step 2) refuses a save from another game.
3. **Later starts:** the app opens the last archive.
4. **The menu:**
   - *File:* New Archive…, Open Archive Folder…, Switch To → (the archives), Show Archive in Finder,
     Show Logs in Finder
   - *Archive:* Home, Import the Newest Save, The Desk (News Cycle), Back Up Now, Archive Settings…
   - *Go:* Back, Forward, Reload

**Switching archives:** the settings are read when a server process starts (`FM_ARCHIVE_DIR`), so the
app stops one archive's server and starts the other's; the window stays. A running import or news
cycle is asked about first. A server stops by itself when the app is gone, even after a crash.

## 4. Settings

- **This archive** (in `archive.json`): name, game folder, installed game, the save, the face-pack
  folder name; "Choose…" opens folder dialogs, and a change restarts the archive.
- **AI keys** (in `keys.json`): the DeepSeek and OpenAI keys; an empty field leaves a key as it is.
- **The AI model**, as before. The app's version (the commit and the build time) shows at the bottom.
- A new archive follows no club; the first archive's followed clubs are stored in its library.

## 5. Outside services

- **AI writing** (DeepSeek, OpenAI): it works when a key is set; without one, a "Write" button says
  that no key is set.
- **Portraits** (Draw Things on `127.0.0.1:7860`): optional; when Draw Things does not answer, the
  "Generate picture" button says so.

## 6. The move (part C, done)

The web service was stopped, the archive's needs (2.6 GB: both databases, the pictures, the
library's backups, `archive.json`, the secret key) were copied into `archives/Sam Archer - Chelsea
v2`, and the copy was checked: both databases pass the integrity check, every table has the same row
count as the original, and the pictures are identical. The 7.9 GB of world-database backups were then
deleted (the saves are the world's backup).

`fm_club_archive/instance/` is the old copy, kept until the curator says to delete it.

## 7. Updates

```bash
scripts/build_mac_app.sh --install     # quit the app first
```

The script builds the app (about 15 seconds), signs it, puts it in /Applications, and keeps the
previous one zipped in `previous app/` (zipped, so Launchpad lists only the installed app; the build in
`dist/` is removed for the same reason). No archive changes: when the new app opens an archive, it runs the
library's migrations and adds the world's new tables and columns, as there is no terminal to run them
in. A change to the save reader needs the saves imported again (Archive › Import the Newest Save).

**Signing:** the build signs the whole bundle with this Mac's certificate "FM Club Archive Local
Signing" (self-signed, made once, in the login keychain), so the app keeps one identity from build to
build; without the certificate it signs ad hoc. PyInstaller itself signs ad hoc: its signing with a
certificate uses library validation, which refuses the bundled Python ("different Team IDs").

## 8. Decisions (16 October 2026)

1. A window (pywebview).
2. The current archive moves into Application Support: copied, checked, then the old folder deleted
   when the curator agrees.
3. Only for this Mac: no notarization.
4. The name: "FM Club Archive".
5. The AI keys in a plain file of the app, not the Keychain ("it's a local app"): the Keychain asked for
   access again after every build.

## 9. What the work found

- The migrations' `env.py` replaces the logging setup: the server puts its log file back after the
  upgrade at start.
- `config` made the secret key file when imported, inside the app when the window imported it: the
  key is now made when an archive's server starts.
- The window can drop its first page load while WebKit makes its storage: the app checks the page and
  loads it again.
- A spawned server is not a daemon process, because a daemon may not start the import's reader
  processes.
