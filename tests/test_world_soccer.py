"""World Soccer: issues, articles, threads and links to the world (FM ids)."""

import pytest

from app.models import WorldSoccerIssue, WorldSoccerStoryThread
from app.services import world_soccer


def test_world_soccer_issue_is_editable_then_frozen_as_memory(session):
    issue_result = world_soccer.create_issue(
        publication_date="2062-10-10",
        headline="Malcolm makes the capital blue",
    )

    story = world_soccer.write_article(
        issue_result["id"],
        headline="Malcolm turns the derby",
        standfirst="Chelsea's talisman provided the last word.",
        body="Paul Malcolm carried Chelsea through the afternoon.",
        prominence="lead",
        entity_links=[
            {
                "entity_type": "person",
                "entity_id": 2002312311,
                "label": "Paul Malcolm",
            }
        ],
        story_thread_title="The London title race",
        story_thread_summary="Chelsea and Arsenal trade blows.",
        story_thread_status="concluded",
    )

    assert story["created"] is True
    assert story["article"]["entity_links"][0]["path"] == "/people/2002312311"
    assert story["article"]["entity_links"][0]["world_ref"] == "person:2002312311"
    assert WorldSoccerStoryThread.query.count() == 1
    assert WorldSoccerStoryThread.query.one().status == "concluded"

    published = world_soccer.publish_issue(issue_result["id"])

    assert published["status"] == "published"
    assert published["published_at"]
    assert WorldSoccerIssue.query.one().headline == "Malcolm makes the capital blue"
    with pytest.raises(ValueError, match="historical memory"):
        world_soccer.write_article(
            issue_result["id"],
            article_id=story["article"]["id"],
            headline="Rewritten history",
            body="This must not replace yesterday's paper.",
        )


def test_same_dated_draft_is_updated_instead_of_duplicated(session):
    first = world_soccer.create_issue(publication_date="2062-10-10")
    second = world_soccer.create_issue(
        publication_date="2062-10-10",
        headline="Late news",
    )

    assert first["id"] == second["id"]
    assert second["created"] is False
    assert WorldSoccerIssue.query.count() == 1



def test_names_of_linked_people_link_to_their_pages():
    targets = {"Paul Malcolm": "/people/2002312311", "Malcolm": "/people/2002312311"}
    linked = world_soccer.link_person_mentions("Paul Malcolm scored; <b>Malcolm</b> again. Malcolmson did not.", targets)
    assert str(linked) == ('<a href="/people/2002312311">Paul Malcolm</a> scored; &lt;b&gt;<a href="/people/2002312311">Malcolm</a>'
                           '&lt;/b&gt; again. Malcolmson did not.')
