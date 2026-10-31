"""The app's version: the commit it was built from (written by scripts/build_mac_app.sh), or "source"."""

try:
    from macapp._version import APP_VERSION
except ImportError:  # run from the source code
    APP_VERSION = "source"
