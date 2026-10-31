# Step 6: images by FM id

Status: approved and built, 15 October 2026. Step 6 of `docs/rewrite-plan.md`.

## Today

| Images | Named by | Count | On the new pages? |
|---|---|---|---|
| Club and competition logos, nation flags | FM uid | game folders | yes |
| Shipped trophies (`static/img/trophies/<uid>.png`) | FM uid | 50 | yes |
| Faces (face pack, generated) | FM uid | 3,500+ | yes |
| Old portraits and player photos | old player id | 235 portraits, 16 photos | portraits only, through `players.fm_uid` |
| Stadium photos, club gallery | old club id | 13 + 1 | no |
| Uploaded trophies (`generated/competitions/<old id>/`) | old competition id | 11 | no |
| Individual award images | award name | 9 | no |

The archive's own pictures are listed in `archive_images` (286) and tied to their subject by
`archive_image_links` (289: 263 player, 14 club, 11 competition, 1 match), all by old ids.
World Soccer finds pictures for a story through these links and old ids.

## The idea

- **Files stay where they are.** The rewrite plan said to rename portrait and stadium files to
  FM ids. But published World Soccer issues are frozen and point to these file paths, so a
  rename would break them. Instead, each image link gets a `world_ref` (the same form as the
  World Soccer links: `person:<uid>`, `club:<uid>`, `competition:<uid>`, `match:...`), and
  the new pages find pictures through it.
- **One command,** `flask images move-old`, safe to run again: it fills `world_ref` from FM
  ids, and adds links for the old portraits, player photos and stadium photos that have no
  link yet. Players without an FM id are matched by name and birth date in the world
  database, then by the names in the face pack's `config.xml` files; a name that fits more
  than one person is not used. It lists every picture it cannot place.
- **Uploaded trophies** win over the shipped ones. Seven old competitions match by exact
  name. Three by hand: UEFA U-21 → European U21 Championship (150783), AFC Asian Cup → Asian
  Nations Cup (102414), UEFA Europa Conf. League → UEFA Europa Conference League (31051584).
  Joe Haigh Cup was a custom friendly: its trophy is not moved (curator, 15 October 2026).

## Pages

- **Player:** the old portrait when there is no face-pack face (as now, but through the
  links, so the 26+ newly matched players get theirs), and a photo gallery.
- **Club:** the stadium photo in the header, and the club gallery.
- **Competition and edition:** the uploaded trophy first, then the shipped one, then the logo.
- **Award:** the award image, matched by name as today.
- **Uploads on the new pages,** because the old pages go in step 7: a portrait and photos for
  a person, a stadium photo and gallery pictures for a club, a trophy for a competition.
  They are saved as archive images with a `world_ref` link.

## World Soccer and MCP

`find_archive_images` takes world entities (person, club, competition, edition, match,
nation) and returns the linked pictures plus the pictures known by FM id: face, crest,
trophy, competition logo and flag.

## Tests

The move on a small old database (a linked portrait, a portrait matched by name and birth
date, a stadium, an uploaded trophy), the pages showing them, an upload, and the World Soccer
image search.

## Built (15 October 2026)

- `flask images move-old` ran on the real database: 280 of 289 links have a `world_ref`.
  Players without an FM id were matched by name and birth date (28), the face pack (26),
  the name alone where it fits one person (29), and the name and the club (8). Seven players'
  portraits stay unlinked (for example Musiala and Lautaro Martínez: several people share
  the name in the save). French Youth Invitational has two FM ids; the one with 90 editions
  (208926) was taken. Joe Haigh Cup was left out.
- People with an archive portrait by FM id: 222 (142 before).
- Code: `image_migration.py`, `world_images.py`; uploads go to `app/static/generated/world/`;
  tests in `tests/test_world_images.py`.

## Not in this plan

Deleting the old pages and their upload routes (step 7).
