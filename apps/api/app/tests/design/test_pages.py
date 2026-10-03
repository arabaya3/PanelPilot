"""Tests for `app/design/pages.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import designations, distribution, motors, pages, profile
from app.design.sheet import Text
from app.models.schemas.design import (
    DesignProject,
    Device,
    DeviceKind,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    PageKind,
    ProjectInfo,
    Revision,
)


def _project(lights: int = 14) -> DesignProject:
    loads = [
        LoadInput(description=f"Sockets {i}", load=LoadKind.SOCKET, power_kw=Decimal("1.5"))
        for i in range(10)
    ]
    loads += [
        LoadInput(description=f"Lighting {i}", load=LoadKind.LIGHTING, power_kw=Decimal("0.6"))
        for i in range(lights)
    ]
    loads.append(LoadInput(description="Data rack", load=LoadKind.DATA, power_kw=Decimal(1)))
    board = distribution.design_distribution_board(
        DistributionBoardRequest(name="DBG-HALL", loads=loads), profile.default_profile()
    )
    project = DesignProject(
        info=ProjectInfo(
            name="Pocket", number="J-1", revisions=[Revision(index="01", date="2026-10-02")]
        ),
        boards=[board],
        parts=motors.parts_for([board]),
    )
    return designations.designate_project(project, profile.default_profile())


def _texts(sheet: object) -> list[str]:
    return [i.text for i in sheet.items if isinstance(i, Text)]  # type: ignore[attr-defined]


def test_the_set_follows_the_profiles_page_order() -> None:
    sheets = pages.build_drawing_set(_project(), profile.default_profile())
    titles = [s.title for s in sheets]
    assert titles[0] == "Title page"
    assert titles[1] == "Table of contents"
    assert titles[2] == "Main power"
    assert titles[-2:] == ["Cable list", "Parts list"]
    assert [s.number for s in sheets] == list(range(1, len(sheets) + 1))
    # Every page carries its own number and the total in the title block.
    for sheet in sheets:
        assert f"{sheet.number} of {len(sheets)}" in _texts(sheet)


def test_a_company_can_drop_and_reorder_pages() -> None:
    company = profile.load_profile(
        {"key": "acme", "page_order": ["parts", "single_line"], "name": "Acme"}
    )
    sheets = pages.build_drawing_set(_project(), company)
    assert [s.title for s in sheets] == ["Parts list", "Main power"]
    assert "Acme" in _texts(sheets[0])


def test_cross_references_point_at_the_right_pages() -> None:
    sheets = pages.build_drawing_set(_project(), profile.default_profile())
    main = sheets[2]
    targets = [t for t in _texts(main) if t.startswith("to /")]
    assert targets
    for target in targets:
        page = int(target.removeprefix("to /").split(".")[0])
        assert sheets[page - 1].title == "Distribution loads"
    # And each distribution page names where it is fed from, with its column.
    dist = next(s for s in sheets if s.title == "Distribution loads")
    assert any(t.startswith("from -F1") and t.endswith("/3.1") for t in _texts(dist))


def test_every_circuit_is_drawn_once() -> None:
    project = _project()
    sheets = pages.build_drawing_set(project, profile.default_profile())
    drawn = [t for s in sheets if s.title == "Distribution loads" for t in _texts(s)]
    for circuit in project.boards[0].circuits:
        breaker = project.boards[0].device(circuit.device_ids[0])
        assert drawn.count(f"-{breaker.designation.product}") == 1  # type: ignore[union-attr]


def test_a_contactor_is_drawn_under_its_breaker() -> None:
    board = distribution.design_distribution_board(
        DistributionBoardRequest(
            name="DBG-HALL",
            loads=[
                LoadInput(
                    description="Lights",
                    load=LoadKind.LIGHTING,
                    power_kw=Decimal("0.6"),
                    controlled=True,
                )
            ],
        ),
        profile.default_profile(),
    )
    project = designations.designate_project(
        DesignProject(info=ProjectInfo(name="Pocket"), boards=[board]), profile.default_profile()
    )
    sheets = pages.build_drawing_set(project, profile.default_profile())
    drawn = [t for s in sheets if s.title == "Distribution loads" for t in _texts(s)]
    assert "20 A AC-1 2P" in drawn


def test_many_feeders_continue_onto_another_main_page() -> None:
    project = _project(lights=60)
    sheets = pages.build_drawing_set(project, profile.default_profile())
    mains = [s for s in sheets if s.title == "Main power"]
    assert len(mains) == 2
    assert "Busbar, continued" in _texts(mains[1])


def test_long_contents_take_more_pages() -> None:
    project = _project(lights=400)
    sheets = pages.build_drawing_set(project, profile.default_profile())
    contents = [s for s in sheets if s.title == "Table of contents"]
    assert len(contents) >= 2


def test_rating_text() -> None:
    assert (
        pages.rating_text(
            Device(
                id="a",
                kind=DeviceKind.CIRCUIT_BREAKER,
                poles=1,
                rated_current_a=Decimal(16),
                curve="C",
            )
        )
        == "C16 1P"
    )
    assert (
        pages.rating_text(
            Device(
                id="b",
                kind=DeviceKind.RESIDUAL_CURRENT_DEVICE,
                poles=4,
                rated_current_a=Decimal(40),
                residual_current_ma=Decimal(30),
            )
        )
        == "40 A 30 mA 4P"
    )
    assert (
        pages.rating_text(
            Device(id="k", kind=DeviceKind.CONTACTOR, poles=4, rated_current_a=Decimal(25))
        )
        == "25 A AC-1 4P"
    )
    assert (
        pages.rating_text(Device(id="c", kind=DeviceKind.CIRCUIT_BREAKER, poles=4))
        == "rating not selected 4P"
    )


def test_cable_text() -> None:
    board = _project().boards[0]
    assert pages.cable_text(board, board.circuits[0]) == "3G2.5 Cu PVC"
    no_cable = board.circuits[0].model_copy(update={"cable_id": None})
    assert pages.cable_text(board, no_cable) == ""


def test_unconfirmed_rules_are_printed_on_the_title_page() -> None:
    sheets = pages.build_drawing_set(_project(), profile.default_profile())
    assert any("not confirmed" in t for t in _texts(sheets[0]))
    assert PageKind.NOTES in profile.default_profile().page_order


def test_a_feeder_and_its_sub_board_point_at_each_other() -> None:
    from app.design import project as project_design

    def schedule(name: str, fed_from: str | None = None) -> DistributionBoardRequest:
        return DistributionBoardRequest(
            name=name,
            fed_from=fed_from,
            loads=[LoadInput(description="Lights", load=LoadKind.LIGHTING, power_kw=Decimal(1))],
        )

    boards = project_design.design_boards(
        [schedule("MDB"), schedule("DB-1", "MDB")], profile.default_profile()
    )
    designed = designations.designate_project(
        DesignProject(info=ProjectInfo(name="Tower"), boards=boards), profile.default_profile()
    )
    sheets = pages.build_drawing_set(designed, profile.default_profile())
    sub_main = next(s for s in sheets if s.title == "Main power" and s.board == "DB-1")
    feeder_page = next(
        s for s in sheets if s.board == "MDB" and any(t.startswith("to DB-1") for t in _texts(s))
    )
    assert f"to DB-1 /{sub_main.number}.0" in _texts(feeder_page)
    fed = next(t for t in _texts(sub_main) if t.startswith("from MDB"))
    assert fed.startswith("from MDB -Q")
    assert fed.endswith(f"/{feeder_page.number}.0")


def test_motor_circuits_are_drawn_with_their_own_symbols() -> None:
    from app.design import motors

    loads = [
        LoadInput(
            description="Fan",
            load=LoadKind.MOTOR,
            power_kw=Decimal(30),
            phases=3,
            starter="star_delta",
        ),
        LoadInput(
            description="Conveyor",
            load=LoadKind.MOTOR,
            power_kw=Decimal(15),
            phases=3,
            starter="drive",
        ),
    ]
    board = distribution.design_distribution_board(
        DistributionBoardRequest(name="MCC", loads=loads), profile.default_profile()
    )
    designed = designations.designate_project(
        DesignProject(
            info=ProjectInfo(name="Plant"), boards=[board], parts=motors.parts_for([board])
        ),
        profile.default_profile(),
    )
    sheets = pages.build_drawing_set(designed, profile.default_profile())
    drawn = [t for s in sheets if s.title == "Distribution loads" for t in _texts(s)]
    assert "Y/D" in drawn
    assert "A63/A30" in drawn
    assert "set 32.33 A" in drawn
    assert "ACS880-01-032A-3" in drawn
    assert "63 A aR 3P" in drawn


def test_an_arabic_company_gets_its_drawings_in_arabic() -> None:
    company = profile.default_profile().model_copy(update={"language": "ar"})
    project = _project()
    sheets = pages.build_drawing_set(project, company)
    assert sheets[0].title == "صفحة العنوان"
    assert sheets[-1].title == "قائمة المواد"
    assert f"1 من {len(sheets)}" in _texts(sheets[0])
    notes_page = next(s for s in sheets if s.title == "ملاحظات التصميم")
    assert any("مخارج" in text or "مخرج" in text for text in _texts(notes_page))
    # What the engineer typed is drawn as typed.
    assert any("Sockets 0" in text for sheet in sheets for text in _texts(sheet))


def test_each_board_has_its_terminal_strip_and_its_terminals_are_parts() -> None:
    sheets = pages.build_drawing_set(_project(), profile.default_profile())
    strip_pages = [s for s in sheets if s.title == "Terminals"]
    assert strip_pages
    assert "-X1:1" in _texts(strip_pages[0])
    parts = next(s for s in sheets if s.title == "Parts list")
    assert any("8WH1" in text for text in _texts(parts))
