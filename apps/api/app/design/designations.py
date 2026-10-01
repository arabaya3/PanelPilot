"""Reference designations, assigned from a company profile.

A design never carries typed-in designations: they are assigned here, in
drawing order, so the same design issued under two companies' profiles gets
each company's letters and numbering, and a circuit added later renumbers
cleanly rather than leaving gaps or duplicates.
"""

from __future__ import annotations

from collections import defaultdict

from app.models.schemas.design import (
    Board,
    CompanyProfile,
    Designation,
    DesignProject,
    DeviceKind,
)


def _drawing_order(board: Board) -> list[str]:
    """Device ids in the order they are drawn.

    Incomers, then each circuit's upstream devices (what feeds a device
    first) and its own devices, then anything not yet reached.
    """
    order: list[str] = []
    seen: set[str] = set()

    upstream = {d.id: d.upstream_id for d in board.devices}

    def visit(device_id: str | None) -> None:
        if device_id is not None and device_id not in seen:
            seen.add(device_id)
            # What feeds a device is drawn before it.
            visit(upstream.get(device_id))
            order.append(device_id)

    for device_id in board.incomer_ids:
        visit(device_id)
    for circuit in board.circuits:
        visit(circuit.upstream_id)
        for device_id in circuit.device_ids:
            visit(device_id)
    for device in board.devices:
        visit(device.id)
    return order


def _cable_order(board: Board) -> list[str]:
    order = [c.cable_id for c in board.circuits if c.cable_id is not None]
    order += [c.id for c in board.cables if c.id not in order]
    return order


def designate_board(board: Board, profile: CompanyProfile) -> Board:
    """Assign every device and cable in a board its designation.

    Numbering runs per letter, in drawing order, from the profile's start
    number: with the default profile a board's breakers and contactors are
    Q1, Q2, ... in the order they are drawn, its residual current devices F1,
    F2, ..., and its cables W1, W2, ....

    Args:
        board: The board; not modified.
        profile: The company profile whose letters and numbering apply.

    Returns:
        A copy of the board with designations set.
    """
    designated = board.model_copy(deep=True)
    function = designated.function or designated.name
    location = designated.location
    counters: dict[str, int] = defaultdict(lambda: profile.start_number)

    def next_designation(kind: DeviceKind) -> Designation:
        letter = profile.letters[kind]
        number = counters[letter]
        counters[letter] += 1
        return Designation(function=function, location=location, product=f"{letter}{number}")

    by_id = {d.id: d for d in designated.devices}
    for device_id in _drawing_order(designated):
        device = by_id[device_id]
        device.designation = next_designation(device.kind)

    cables = {c.id: c for c in designated.cables}
    for cable_id in _cable_order(designated):
        cables[cable_id].designation = next_designation(DeviceKind.CABLE)
    return designated


def designate_project(project: DesignProject, profile: CompanyProfile) -> DesignProject:
    """Assign designations throughout a project.

    Each board is numbered on its own: the function aspect already tells two
    boards' Q1s apart, which is how EPLAN and E3.series number by default.

    Args:
        project: The project; not modified.
        profile: The company profile whose letters and numbering apply.

    Returns:
        A copy of the project, issued under the profile.
    """
    return project.model_copy(
        update={
            "profile": profile.key,
            "boards": [designate_board(board, profile) for board in project.boards],
        },
        deep=True,
    )
