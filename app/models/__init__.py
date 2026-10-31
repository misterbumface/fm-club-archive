"""Import every model so their tables and relationships register with
SQLAlchemy's declarative metadata, and re-export them for convenience:

    from app.models import ArchiveImage, WorldSoccerIssue, ...

The facts live in the world database (``app.models.world``), rebuilt from the saves; the
main database keeps what a save cannot give: written texts, pictures and settings.
"""

from __future__ import annotations

from app.models.app_setting import AppSetting
from app.models.archive_image import ArchiveImage, ArchiveImageLink
from app.models.match_text import MatchText
from app.models.outlet import Outlet, Writer
from app.models.person_bio import PersonBio, PersonOldText
from app.models.social import SocialAccount, SocialPost
from app.models.world_soccer_article import WorldSoccerArticle
from app.models.world_soccer_entity_link import WorldSoccerEntityLink
from app.models.world_soccer_image_use import WorldSoccerImageUse
from app.models.world_soccer_issue import WorldSoccerIssue
from app.models.world_soccer_thread import WorldSoccerStoryThread
from app.models.world_story import ClubSeasonChapter, ClubSeasonStory, ClubText, EditionChapter, EditionStory
from app.models import world  # noqa: F401  (the archive rebuilt from saves; own database)

__all__ = [
    "AppSetting",
    "ArchiveImage",
    "ArchiveImageLink",
    "ClubSeasonChapter",
    "ClubSeasonStory",
    "ClubText",
    "EditionChapter",
    "EditionStory",
    "MatchText",
    "Outlet",
    "Writer",
    "PersonBio",
    "PersonOldText",
    "SocialAccount",
    "SocialPost",
    "WorldSoccerArticle",
    "WorldSoccerEntityLink",
    "WorldSoccerImageUse",
    "WorldSoccerIssue",
    "WorldSoccerStoryThread",
]
