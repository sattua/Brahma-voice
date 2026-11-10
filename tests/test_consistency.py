import json

from brahma.consistency import (
    CHARACTER_WEIGHT,
    character_clause,
    character_sheet_prompt,
    panel_prompt,
    panel_references,
    stable_seed,
    style_clause,
)
from brahma.models import CharacterSpec, LocationSpec, PanelSpec, StyleSpec
from brahma.script import MockScript, parse_plan


def _style() -> StyleSpec:
    return StyleSpec(
        medium="inked graphic novel",
        line="clear ink line",
        rendering="flat color with soft cell shading",
        palette="muted teal and amber",
        lighting="overcast daylight",
    )


def _person() -> CharacterSpec:
    return CharacterSpec(
        id="ana",
        name="Ana",
        role="protagonist",
        appearance="woman in her 30s, short black hair, brown eyes, light brown skin",
        wardrobe="ochre trench coat and dark turtleneck",
        distinguishing="thin scar through the left eyebrow",
    )


def _place() -> LocationSpec:
    return LocationSpec(
        id="station",
        name="estación",
        appearance="old train station hall with high iron arches and a wet stone floor",
        lighting="cold overhead lamps",
    )


def test_character_clause_is_identical_on_the_sheet_and_the_panel():
    style, person, place = _style(), _person(), _place()
    panel = PanelSpec(
        shot="close-up",
        angle="eye-level",
        location_id="station",
        character_ids=["ana"],
        action="she looks back over her shoulder",
    )
    clause = character_clause(person)
    assert clause in character_sheet_prompt(style, person)
    assert clause in panel_prompt(style, place, [person], panel)
    assert style_clause(style) in character_sheet_prompt(style, person)
    assert style_clause(style) in panel_prompt(style, place, [person], panel)
    assert panel.shot == "close"


def test_a_panel_conditions_on_one_reference_and_never_the_previous_panel(tmp_path):
    sheet = tmp_path / "ana.png"
    plate = tmp_path / "station.png"
    previous = tmp_path / "p01_01.png"
    sheet.write_bytes(b"a")
    plate.write_bytes(b"b")
    previous.write_bytes(b"c")
    with_person = PanelSpec(
        shot="medium",
        angle="low",
        location_id="station",
        character_ids=["ana", "other"],
        action="she steps into the hall",
    )
    empty = PanelSpec(shot="establishing", angle="high", location_id="station", action="rain in the empty hall")
    person_refs = panel_references(with_person, {"ana": sheet}, {"station": plate})
    place_refs = panel_references(empty, {"ana": sheet}, {"station": plate})
    assert len(person_refs) == 1
    assert person_refs[0].path == sheet
    assert person_refs[0].weight == CHARACTER_WEIGHT
    assert person_refs[0].role == "character"
    assert place_refs[0].path == plate
    assert previous not in {item.path for item in person_refs + place_refs}


def test_seed_depends_on_revision_and_nothing_random():
    assert stable_seed("story", "p01_01", "1") == stable_seed("story", "p01_01", "1")
    assert stable_seed("story", "p01_01", "1") != stable_seed("story", "p01_01", "2")


def test_plan_rejects_a_character_the_panel_invented():
    raw = json.dumps(
        {
            "title": "Lluvia",
            "logline": "Alguien llega tarde.",
            "language": "es",
            "style": {
                "medium": "inked graphic novel",
                "line": "clear ink line",
                "rendering": "flat color",
                "palette": "teal and amber",
                "lighting": "overcast",
            },
            "characters": [],
            "locations": [
                {
                    "id": "calle",
                    "name": "calle",
                    "appearance": "a wet street with shuttered shops",
                    "lighting": "sodium lamps",
                }
            ],
            "pages": [
                {
                    "number": 1,
                    "panels": [
                        {
                            "shot": "wide",
                            "angle": "eye-level",
                            "location_id": "calle",
                            "character_ids": ["fantasma"],
                            "action": "a figure stands under a lamp",
                            "dialogue": [],
                        }
                    ],
                }
            ],
        }
    )
    try:
        parse_plan(raw)
    except Exception as exc:
        assert "fantasma" in str(exc)
    else:
        raise AssertionError("invented character was accepted")


def test_mock_script_keeps_the_author_language_in_dialogue():
    plan = MockScript().plan("El detective cruzó la calle. La lluvia no paraba.")
    assert plan.language == "es"
    texts = [line.text for page in plan.pages for panel in page.panels for line in panel.dialogue]
    assert any("detective" in text for text in texts)
    assert "ink-blue coat" in plan.characters[0].wardrobe
