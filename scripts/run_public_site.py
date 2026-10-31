"""Serve the public site (app/public) on its own, for a look at it while developing:

    FM_ARCHIVE_DIR=<archive folder> python scripts/run_public_site.py [PORT]

The Mac app serves it on port 8080 by itself (macapp/server.py).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from waitress import serve  # noqa: E402

from app.public import PUBLIC_PORT, create_public_app  # noqa: E402

if __name__ == "__main__":
    serve(create_public_app(), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else PUBLIC_PORT, threads=4)
