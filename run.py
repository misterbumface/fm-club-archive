"""Development entry point for FM Club Archive.

Debug mode is opt-in rather than the default. Werkzeug's debugger keeps
every 500's traceback -- including all of its frame locals -- in memory
for the life of the process, so its interactive console can still inspect
them. Nothing ever expires them. On a server left running for days that
is an open-ended hold on whatever those frames touched, and this archive
has request paths that touch a great deal.

Set FM_DEBUG=1 when you actually want the interactive traceback page.
Without it the server still logs every unhandled exception with its full
traceback; it simply stops keeping the frames alive afterwards.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app

app = create_app()


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    app.run(
        debug=_flag("FM_DEBUG"),
        port=int(os.environ.get("PORT", 5050)),
        # No debugger means no reloader either, so this is one process
        # rather than two. Threaded so a slow import or an AI call doesn't
        # block the rest of the archive behind it.
        threaded=True,
    )
