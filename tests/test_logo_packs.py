"""Logo and flag packs found through their config.xml, whatever their folders are called. No real pack needed."""

def test_a_pack_with_other_folder_names_is_found_through_its_config(tmp_path, monkeypatch):
    from app.services import logo_assets

    clubs = tmp_path / "Logos" / "Clubs" / "Normal"
    clubs.mkdir(parents=True)
    (clubs / "chelsea.png").write_bytes(b"png")
    (clubs / "config.xml").write_text('<record from="chelsea" to="graphics/pictures/club/630/logo"/>')
    small = tmp_path / "Logos" / "Clubs" / "Small"
    small.mkdir(parents=True)
    (small / "tiny.png").write_bytes(b"png")
    (small / "config.xml").write_text('<record from="tiny" to="graphics/pictures/club/631/logo"/>')
    flags = tmp_path / "Flags" / "Countries"
    flags.mkdir(parents=True)
    (flags / "england.png").write_bytes(b"png")
    (flags / "config.xml").write_text('<record from="england" to="graphics/pictures/nation/765/flag"/>')
    monkeypatch.setattr(logo_assets, "LOGO_ROOT", tmp_path / "Logos")
    monkeypatch.setattr(logo_assets, "FLAG_ROOT", tmp_path / "Flags")
    logo_assets.pack_index.cache_clear()
    logo_assets.resolve_logo_path.cache_clear()
    try:
        assert logo_assets.resolve_logo_path("club", 630, "small") == clubs / "chelsea.png"
        assert logo_assets.resolve_logo_path("nation", 765, "normal") == flags / "england.png"
        assert logo_assets.resolve_logo_path("club", 631, "normal") is None  # the small folders are left out
    finally:
        logo_assets.pack_index.cache_clear()
        logo_assets.resolve_logo_path.cache_clear()


def test_a_trophy_comes_from_the_users_trophy_pack(tmp_path, monkeypatch):
    from app.public import create_public_app
    from app.services import logo_assets

    pack = tmp_path / "graphics" / "Trophies Pack"
    pack.mkdir(parents=True)
    (pack / "cup.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (pack / "config.xml").write_text('<record from="cup" to="graphics/pictures/comp/11/trophy"/>')
    monkeypatch.setattr(logo_assets, "trophy_roots", lambda: [pack])
    monkeypatch.setattr(logo_assets, "_SHIPPED_TROPHIES", tmp_path / "none")
    logo_assets.trophy_index.cache_clear()
    try:
        assert logo_assets.trophy_path(11) == pack / "cup.png" and logo_assets.trophy_path(12) is None
        site = create_public_app("testing")
        with site.app_context():
            pages = site.test_client()
            assert pages.get("/trophies/11.png").status_code == 200 and pages.get("/trophies/12.png").status_code == 404
    finally:
        logo_assets.trophy_index.cache_clear()
