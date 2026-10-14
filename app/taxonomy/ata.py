"""ATA iSpec-2200-style chapter/section taxonomy for the component kinds and v2 fault modes we model.

ATA-style, simplified, fictional part numbers: chapter numbers follow the public ATA chapter numbering loosely; section
and subsection numbers are a simplified illustration, NOT a reproduction of any manufacturer's manual or of iSpec 2200.
Everything here is SIMULATED demo reference data and must never be used to plan real maintenance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PROVENANCE = "SIMULATED"
LABEL = "ATA-style, simplified, fictional part numbers"

CHAPTERS: dict[str, str] = {
    "05": "Time Limits / Maintenance Checks",
    "21": "Air Conditioning",
    "22": "Auto Flight",
    "24": "Electrical Power",
    "29": "Hydraulic Power",
    "31": "Indicating / Recording Systems",
    "32": "Landing Gear",
    "34": "Navigation",
    "36": "Pneumatic",
    "49": "Airborne Auxiliary Power",
    "72": "Engine",
}

# section code "CC-SS" -> title (only the sections we reference)
SECTIONS: dict[str, str] = {
    "05-20": "Scheduled Maintenance Checks",
    "21-50": "Cooling (packs, heat exchangers)",
    "21-51": "Refrigeration (pack valves)",
    "22-10": "Autopilot",
    "24-20": "AC Generation (IDG)",
    "24-30": "DC Generation (batteries)",
    "29-10": "Main Hydraulic Power (pumps, lines)",
    "31-50": "Central Warning / Maintenance Computers",
    "32-11": "Main Landing Gear (shock strut)",
    "32-40": "Wheels and Brakes",
    "32-41": "Main Wheels and Tires",
    "32-42": "Brakes",
    "34-00": "Navigation / Avionics General",
    "36-10": "Pneumatic Distribution",
    "49-10": "APU Power Plant",
    "49-40": "APU Starting",
    "49-60": "APU Controls / EGT Indicating",
    "72-00": "Engine General",
    "72-30": "Compressor Section",
    "72-50": "Turbine Section",
}

# component kind -> default section
KIND_SECTION: dict[str, str] = {
    "engine": "72-00",
    "apu": "49-10",
    "ecs": "21-50",
    "electrical": "24-20",
    "hydraulics": "29-10",
    "avionics": "34-00",
    "brakes": "32-42",
    "tires": "32-41",
    "landing_gear": "32-11",
}

# v2 fault mode id -> section
MODE_SECTION: dict[str, str] = {
    "hpt_blade_erosion": "72-50",
    "compressor_fouling": "72-30",
    "stuck_brake": "32-42",
    "rejected_takeoff_overheat": "32-42",
    "slow_leak": "32-41",
    "fod_cut": "32-41",
    "strut_seal_leak": "32-11",
    "hard_landing_damage": "32-11",
    "pump_cavitation": "29-10",
    "line_leak": "29-10",
    "cell_short": "24-30",
    "idg_bearing": "24-20",
    "intermittent_connector": "34-00",
    "overheating": "34-00",
    "starter_failure": "49-40",
    "egt_overtemp": "49-60",
    "valve_stuck": "21-51",
    "hx_fouling": "21-50",
}


@dataclass(frozen=True)
class AtaRef:
    chapter: str
    chapter_title: str
    section: str
    section_title: str

    @property
    def code(self) -> str:
        return self.section

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "code": self.code}


def _ref(section: str) -> AtaRef:
    ch = section.split("-")[0]
    return AtaRef(ch, CHAPTERS[ch], section, SECTIONS[section])


def ata_for(kind: str, mode: str | None = None) -> AtaRef:
    """ATA reference for a component kind, refined by fault mode when given. Raises KeyError for unknown kind/mode."""
    if mode is not None:
        if mode not in MODE_SECTION:
            raise KeyError(f"unknown fault mode: {mode}")
        return _ref(MODE_SECTION[mode])
    if kind not in KIND_SECTION:
        raise KeyError(f"unknown component kind: {kind}")
    return _ref(KIND_SECTION[kind])


def chapter_for(kind: str, mode: str | None = None) -> str:
    return ata_for(kind, mode).chapter


def kinds_in_chapter(chapter: str) -> list[str]:
    return sorted(k for k, s in KIND_SECTION.items() if s.split("-")[0] == chapter)


def table() -> dict[str, Any]:
    """The whole taxonomy as JSON-ready data."""
    chapters = []
    for ch in sorted(CHAPTERS):
        secs = [{"section": s, "title": t, "kinds": sorted(k for k, v in KIND_SECTION.items() if v == s),
                 "fault_modes": sorted(m for m, v in MODE_SECTION.items() if v == s)}
                for s, t in sorted(SECTIONS.items()) if s.startswith(ch + "-")]
        chapters.append({"chapter": ch, "title": CHAPTERS[ch], "kinds": kinds_in_chapter(ch), "sections": secs})
    return {"simulated": True, "provenance": PROVENANCE, "label": LABEL, "chapters": chapters,
            "kind_to_section": dict(sorted(KIND_SECTION.items())), "mode_to_section": dict(sorted(MODE_SECTION.items()))}
