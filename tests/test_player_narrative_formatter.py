from app.services.player_narrative_formatter import format_player_narrative


def test_recognizes_plain_and_markdown_career_headings():
    narrative = (
        "### Career overview\n\nA short opening.\n\n"
        "**Playing career**\n\nA career paragraph.\n\n"
        "Honours and awards\n\nA concise closing paragraph."
    )

    sections = format_player_narrative(narrative)

    assert [section["heading"] for section in sections] == [
        "Career overview",
        "Playing career",
        "Honours and awards",
    ]
    assert sections[1]["paragraphs"] == ["A career paragraph."]


def test_splits_wall_of_text_without_losing_narrative_words():
    awards = [f"Award {number} recognition" for number in range(1, 31)]
    paragraph = "He was recognised with several awards: " + ", ".join(awards) + "."

    sections = format_player_narrative(f"Honours and awards\n\n{paragraph}")
    paragraphs = sections[0]["paragraphs"]

    assert len(paragraphs) > 1
    assert all(len(item.split()) <= 90 for item in paragraphs)
    for award in awards:
        assert award in " ".join(paragraphs)


def test_existing_unheaded_paragraphs_keep_their_natural_structure():
    narrative = "A concise overview.\n\nA well-sized club career paragraph."

    sections = format_player_narrative(narrative)

    assert sections == [
        {
            "heading": None,
            "paragraphs": [
                "A concise overview.",
                "A well-sized club career paragraph.",
            ],
        }
    ]


def test_heading_and_body_can_share_one_line_block():
    sections = format_player_narrative(
        "Career overview\nA compact opening paragraph.\n\n"
        "Playing career\nA second paragraph."
    )

    assert sections == [
        {"heading": "Career overview", "paragraphs": ["A compact opening paragraph."]},
        {"heading": "Playing career", "paragraphs": ["A second paragraph."]},
    ]


def test_formatter_preserves_player_specific_headings():
    narrative = (
        "The Berlin brace\n\n"
        "Two goals in the final made this the defining night.\n\n"
        "A second football life\n\n"
        "Retirement from playing opened a new chapter in management."
    )

    sections = format_player_narrative(narrative)

    assert [section["heading"] for section in sections] == [
        "The Berlin brace",
        "A second football life",
    ]
