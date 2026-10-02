"""Company profiles: how one company issues a design.

A design is made once, under no company's conventions; a profile then names
its devices, orders its pages, fills its title block and sets the design
rules a company decides for itself (which breaker a lighting circuit gets,
how many circuits share a residual current device).

The default profile is this software's, not any company's. Its letters are
the single-letter classes of IEC 81346-2 as commonly applied (Q switching
power, F protecting, K processing signals, X connecting, W guiding, T
converting, P presenting, M providing mechanical energy). Its circuit rules
are defaults, and a profile that leaves ``rules_confirmed_by`` empty is
printed on every drawing as unconfirmed: a company's engineers replace them,
the software does not settle them.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.models.schemas.design import (
    CircuitRule,
    CompanyProfile,
    DeviceKind,
    LoadKind,
    PageKind,
    TitleField,
)

DEFAULT_PROFILE_KEY = "iec-default"

_DEFAULT_LETTERS: dict[DeviceKind, str] = {
    DeviceKind.CIRCUIT_BREAKER: "Q",
    DeviceKind.RESIDUAL_CURRENT_DEVICE: "F",
    DeviceKind.SWITCH_DISCONNECTOR: "Q",
    DeviceKind.CONTACTOR: "Q",
    DeviceKind.OVERLOAD_RELAY: "F",
    DeviceKind.FUSE: "F",
    DeviceKind.SURGE_PROTECTOR: "F",
    DeviceKind.DRIVE: "T",
    DeviceKind.MOTOR: "M",
    DeviceKind.RELAY: "K",
    DeviceKind.BUS_ACTUATOR: "K",
    DeviceKind.POWER_SUPPLY: "T",
    DeviceKind.METER: "P",
    DeviceKind.INDICATOR_LAMP: "P",
    DeviceKind.TERMINAL_STRIP: "X",
    DeviceKind.CABLE: "W",
    DeviceKind.BUSBAR: "W",
    DeviceKind.OTHER: "A",
}

_DEFAULT_RULES: dict[LoadKind, CircuitRule] = {
    LoadKind.LIGHTING: CircuitRule(
        breaker_a=Decimal(10), residual_current_ma=Decimal(30), cable_mm2=Decimal("1.5")
    ),
    LoadKind.SOCKET: CircuitRule(
        breaker_a=Decimal(16), residual_current_ma=Decimal(30), cable_mm2=Decimal("2.5")
    ),
    # At least 16 A for a split unit: below it the compressor's start trips a
    # breaker sized to the running current.
    LoadKind.AIR_CONDITIONING: CircuitRule(
        breaker_a=Decimal(16), residual_current_ma=Decimal(30), cable_mm2=Decimal("2.5")
    ),
    LoadKind.WATER_HEATER: CircuitRule(residual_current_ma=Decimal(30)),
    LoadKind.KITCHEN: CircuitRule(residual_current_ma=Decimal(30)),
    LoadKind.FAN: CircuitRule(residual_current_ma=Decimal(30)),
    LoadKind.MOTOR: CircuitRule(curve="D"),
    LoadKind.LIFT: CircuitRule(curve="D"),
    LoadKind.SUB_BOARD: CircuitRule(),
    LoadKind.CONTROL: CircuitRule(breaker_a=Decimal(6), cable_mm2=Decimal("1.5")),
    LoadKind.DATA: CircuitRule(breaker_a=Decimal(16), cable_mm2=Decimal("2.5")),
    LoadKind.SPARE: CircuitRule(),
    LoadKind.OTHER: CircuitRule(),
}

DEFAULT_PROFILE = CompanyProfile(
    key=DEFAULT_PROFILE_KEY,
    name="",
    language="en",
    letters=_DEFAULT_LETTERS,
    title_fields=[
        TitleField.COMPANY,
        TitleField.PROJECT_NAME,
        TitleField.PROJECT_NUMBER,
        TitleField.BOARD_NAME,
        TitleField.PAGE_TITLE,
        TitleField.REVISION,
        TitleField.DATE,
        TitleField.DRAWN_BY,
        TitleField.CHECKED_BY,
        TitleField.APPROVED_BY,
        TitleField.PAGE_NUMBER,
    ],
    page_order=[
        PageKind.TITLE,
        PageKind.CONTENTS,
        PageKind.SINGLE_LINE,
        PageKind.DISTRIBUTION,
        PageKind.NOTES,
        PageKind.LAYOUT,
        PageKind.TERMINALS,
        PageKind.CABLES,
        PageKind.PARTS,
    ],
    circuit_rules=_DEFAULT_RULES,
)


def default_profile() -> CompanyProfile:
    """Return this software's own profile, whose rules no company confirmed.

    Returns:
        A copy, so a caller's changes never leak into the next project.
    """
    return DEFAULT_PROFILE.model_copy(deep=True)


def load_profile(data: dict[str, object]) -> CompanyProfile:
    """Build a company's profile from its settings, over the default.

    A company states only what it does differently: a key it leaves out keeps
    the default's value, and a mapping (letters, circuit rules, preferred
    manufacturers) is merged entry by entry, so overriding the lighting rule
    does not drop the socket rule.

    Args:
        data: The company's settings, as stored (JSON-shaped).

    Returns:
        The profile.

    Raises:
        ValidationError: If a setting is unknown or malformed, naming it.
    """
    if "key" not in data or not str(data["key"]).strip():
        raise ValidationError("a company profile needs a key", code="profile_no_key")
    base = DEFAULT_PROFILE.model_dump(mode="json")
    known = set(base)
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValidationError(
            f"unknown profile settings: {unknown}",
            code="profile_unknown_settings",
            params={"settings": unknown},
        )
    merged: dict[str, object] = dict(base)
    for name, value in data.items():
        if isinstance(value, dict) and isinstance(base.get(name), dict):
            current = dict(base[name])
            for entry, setting in value.items():
                if isinstance(setting, dict) and isinstance(current.get(entry), dict):
                    current[entry] = {**current[entry], **setting}
                else:
                    current[entry] = setting
            merged[name] = current
        else:
            merged[name] = value
    try:
        return CompanyProfile.model_validate(merged)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ValidationError(
            f"profile setting {where}: {first['msg']}",
            code="profile_setting_invalid",
            params={"setting": where},
        ) from exc
