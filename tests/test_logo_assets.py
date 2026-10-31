import pytest

from app import create_app
from app.extensions import db
from app.services.logo_assets import LOGO_ROOT, known_club_logo_uid, resolve_logo_path

# The Logos pack is too large for git, so a fresh clone (CI) does not have it.
pytestmark = pytest.mark.skipif(not LOGO_ROOT.is_dir(), reason="Logos pack not installed")


@pytest.fixture()
def client():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_known_competition_logo_resolves_from_fm_uid():
    path = resolve_logo_path("competition", 1301394, "small")
    assert path is not None
    assert path.name == "1301394.png"


def test_small_club_logo_falls_back_to_available_normal_asset():
    path = resolve_logo_path("club", 1736, "small")
    assert path is not None
    assert path.name == "1736.png"
    assert path.parent.name == "Normal"


@pytest.mark.parametrize(
    ("club_name", "fm_uid"),
    [
        ("Chelsea", "630"),
        ("FC Bayern München", "915"),
        ("FC Internazionale Milano", "1135"),
        ("Boreham Wood", "5103712"),
    ],
)
def test_archive_club_name_resolves_to_curated_logo_uid(club_name, fm_uid):
    assert known_club_logo_uid(club_name) == fm_uid
    assert resolve_logo_path("club", fm_uid, "normal") is not None


def test_logo_resolver_rejects_unknown_shapes_and_path_traversal():
    assert resolve_logo_path("mystery", 1301394, "small") is None
    assert resolve_logo_path("competition", "../../config", "small") is None
    assert resolve_logo_path("competition", 1301394, "enormous") is None




def test_logo_route_serves_known_asset_and_404s_unknown(client):
    known = client.get("/logos/competition/small/1301394.png")
    assert known.status_code == 200
    assert known.mimetype == "image/png"

    missing = client.get("/logos/competition/small/999999999999.png")
    assert missing.status_code == 404

