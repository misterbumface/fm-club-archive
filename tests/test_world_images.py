# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Step 6: the archive's pictures by FM id (docs/images-plan.md)."""

import io

import pytest

from app.extensions import db
from app.models.archive_image import ArchiveImage
from app.services import world_images, world_portraits
from tests.test_world_pages import CHELSEA, ENGLAND, FA_CUP, MYERS, app, pages, small_world  # noqa: F401


@pytest.fixture()
def pictures(app, tmp_path, monkeypatch):
    """Old portraits and uploads go to a temporary folder."""
    portraits = tmp_path / "app" / "static" / "generated" / "portraits"
    portraits.mkdir(parents=True)
    monkeypatch.setattr(world_portraits, "OLD_PORTRAITS", portraits)
    monkeypatch.setattr(world_portraits, "FACES_DIR", tmp_path / "no-faces")
    monkeypatch.setattr(app, "static_folder", str(tmp_path / "app" / "static"))
    monkeypatch.setitem(app.config, "GENERATED_DIR", tmp_path / "generated")
    return portraits




def test_an_upload_shows_on_the_page_and_can_be_hidden(pages, small_world, pictures):
    upload = {"kind": "stadium", "caption": "Stamford Bridge", "image": (io.BytesIO(b"jpeg"), "ground.jpg")}
    page = pages.post(f"/club/{CHELSEA}/images", data=upload, content_type="multipart/form-data", follow_redirects=True)
    text = page.get_data(as_text=True)
    assert "Picture added." in text and 'class="stadium"' in text and "Stamford Bridge" in text
    image = db.session.query(ArchiveImage).one()
    assert image.url.startswith(f"/static/generated/world/stadiums/club-{CHELSEA}-")
    assert world_images.find_images(entity_type="club", entity_id=CHELSEA)["images"][0]["caption"] == "Stamford Bridge"

    pages.post(f"/images/{image.id}/hide", data={"back": f"/clubs/{CHELSEA}"})
    assert 'class="stadium"' not in pages.get(f"/clubs/{CHELSEA}").get_data(as_text=True)


def test_a_wrong_upload_is_refused(pages, small_world, pictures):
    bad = {"kind": "trophy", "image": (io.BytesIO(b"x"), "cup.png")}
    text = pages.post(f"/person/{MYERS}/images", data=bad, content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "No picture added: A person cannot have a trophy picture." in text
    exe = {"kind": "photograph", "image": (io.BytesIO(b"x"), "run.exe")}
    text = pages.post(f"/person/{MYERS}/images", data=exe, content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "must be a PNG, JPG or WebP" in text


def test_an_uploaded_trophy_comes_before_the_shipped_one(pages, small_world, pictures):
    upload = {"kind": "trophy", "image": (io.BytesIO(b"png"), "cup.png")}
    pages.post(f"/competition/{FA_CUP}/images", data=upload, content_type="multipart/form-data")
    page = pages.get(f"/competitions/{FA_CUP}").get_data(as_text=True)
    assert f"/static/generated/world/trophies/competition-{FA_CUP}-" in page
    assert ENGLAND
