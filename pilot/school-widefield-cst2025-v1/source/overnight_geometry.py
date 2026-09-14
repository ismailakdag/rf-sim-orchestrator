"""Geometry-only generator for the wide-field opposed two-port preflight.

Two balanced stepped bow-tie elements face one another across the analytic
tooth.  Each element occupies one 1.6 mm FR-4 board and has one discrete port;
there is no third feed.  A simple PLA frame fixes the board separation and a
lower shelf supports the specimen.  The module only returns CST VBA history
and JSON-safe metadata.  It never opens CST or starts a solver.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"widefield_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load("base_geometry.py")
legacy = _load("legacy_geometry.py")

DEFAULTS: dict[str, Any] = dict(legacy.DEFAULTS)
DEFAULTS.update(
    {
        "widefield_gap_mm": 0.5,
        "widefield_board_x_mm": 20.0,
        "widefield_board_z_mm": 15.0,
        "widefield_span_x_mm": 14.0,
        "widefield_outer_z_mm": 10.0,
        "widefield_throat_z_mm": 1.5,
        "widefield_port_gap_mm": 1.0,
        "widefield_steps": 6,
        "include_widefield_frame": True,
        "frame_bar_mm": 1.4,
        "support_clearance_mm": 0.2,
    }
)

PARAMETER_SCHEMA = dict(legacy.PARAMETER_SCHEMA)
PARAMETER_SCHEMA.update(
    {
        "widefield_gap_mm": {"unit": "mm", "range": (0.3, 1.5)},
        "widefield_board_x_mm": {"unit": "mm", "range": (18.0, 26.0)},
        "widefield_board_z_mm": {"unit": "mm", "range": (14.0, 20.0)},
        "widefield_span_x_mm": {"unit": "mm", "range": (12.0, 20.0)},
        "widefield_outer_z_mm": {"unit": "mm", "range": (8.0, 16.0)},
        "widefield_throat_z_mm": {"unit": "mm", "range": (1.0, 3.0)},
        "widefield_port_gap_mm": {"unit": "mm", "range": (0.6, 2.0)},
        "widefield_steps": {"type": "int", "range": (4, 8)},
        "include_widefield_frame": {"type": "bool"},
        "frame_bar_mm": {"unit": "mm", "range": (1.0, 2.5)},
        "support_clearance_mm": {"unit": "mm", "range": (0.1, 0.8)},
    }
)
SYNTHETIC_BASE_MATERIALS = legacy.SYNTHETIC_BASE_MATERIALS
_NEW_NUMERIC_KEYS = {
    "widefield_gap_mm",
    "widefield_board_x_mm",
    "widefield_board_z_mm",
    "widefield_span_x_mm",
    "widefield_outer_z_mm",
    "widefield_throat_z_mm",
    "widefield_port_gap_mm",
    "frame_bar_mm",
    "support_clearance_mm",
}


def _validated(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(params, dict):
        raise TypeError("params must be a dict")
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise KeyError(f"Unknown geometry parameter(s): {sorted(unknown)}")
    p = dict(DEFAULTS)
    p.update(params)
    if p["include_fixture"] or p["include_counterface"]:
        raise ValueError("widefield-v1 uses only its explicit PLA frame")
    if p["tilt_x_deg"] or p["tilt_y_deg"]:
        raise ValueError("widefield-v1 is preflighted only for an upright tooth")
    if p["tooth_offset_x_mm"] or p["tooth_offset_y_mm"]:
        raise ValueError("widefield-v1 freezes the tooth at the fixture datum")
    if p["topology"] != "hilbert_split_ring":
        raise ValueError("legacy topology tag must remain hilbert_split_ring")
    if not isinstance(p["include_widefield_frame"], bool):
        raise TypeError("include_widefield_frame must be bool")
    steps = p["widefield_steps"]
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise TypeError("widefield_steps must be an integer")
    lo_steps, hi_steps = PARAMETER_SCHEMA["widefield_steps"]["range"]
    if not lo_steps <= steps <= hi_steps:
        raise ValueError("widefield_steps outside supported range")
    for key in _NEW_NUMERIC_KEYS:
        value = float(p[key])
        lo, hi = PARAMETER_SCHEMA[key]["range"]
        if not lo <= value <= hi:
            raise ValueError(f"{key}={value} outside supported range [{lo}, {hi}]")
        p[key] = value
    if p["widefield_span_x_mm"] >= p["widefield_board_x_mm"] - 1.0:
        raise ValueError("bow-tie needs at least 0.5 mm x edge clearance")
    if p["widefield_outer_z_mm"] >= p["widefield_board_z_mm"] - 1.0:
        raise ValueError("bow-tie needs at least 0.5 mm z edge clearance")
    if p["widefield_port_gap_mm"] >= p["widefield_span_x_mm"] / 3.0:
        raise ValueError("feed gap is too large for the bow-tie span")
    if p["widefield_throat_z_mm"] >= p["widefield_outer_z_mm"]:
        raise ValueError("bow-tie throat must be narrower than its outer edge")
    legacy_params = {key: p[key] for key in legacy.DEFAULTS}
    legacy_params.update(
        include_resonators=False,
        include_fixture=False,
        include_counterface=False,
        tilt_x_deg=0.0,
        tilt_y_deg=0.0,
    )
    _, bp = legacy._validated(legacy_params)
    return p, bp


def _port(number: int, p1: tuple[float, float, float], p2: tuple[float, float, float]) -> str:
    return f'''With DiscretePort
 .Reset
 .PortNumber "{number}"
 .Type "Sparameter"
 .Impedance "50"
 .SetP1 "False", "{base._f(p1[0])}", "{base._f(p1[1])}", "{base._f(p1[2])}"
 .SetP2 "False", "{base._f(p2[0])}", "{base._f(p2[1])}", "{base._f(p2[2])}"
 .InvertDirection "False"
 .LocalCoordinates "False"
 .Monitor "True"
 .Radius "0.01"
 .Create
End With'''


def _bowtie_blocks(
    component: str,
    y_range: tuple[float, float],
    p: dict[str, Any],
    cx: float,
    cz: float,
    port_number: int,
) -> list[tuple[str, str]]:
    span = p["widefield_span_x_mm"]
    gap = p["widefield_port_gap_mm"]
    outer_h = p["widefield_outer_z_mm"]
    throat_h = p["widefield_throat_z_mm"]
    steps = p["widefield_steps"]
    arm_length = (span - gap) / 2.0
    step_length = arm_length / steps
    blocks: list[tuple[str, str]] = []
    for side, sign in (("left", -1.0), ("right", 1.0)):
        for index in range(steps):
            inner = gap / 2.0 + index * step_length
            outer = gap / 2.0 + (index + 1) * step_length
            fraction = (index + 1) / steps
            height = throat_h + (outer_h - throat_h) * fraction
            if sign < 0:
                xr = (cx - outer, cx - inner)
            else:
                xr = (cx + inner, cx + outer)
            zr = (cz - height / 2.0, cz + height / 2.0)
            name = f"{side}_{index + 1:02d}"
            blocks.append(
                (
                    f"{component} stepped bow-tie {name}",
                    base._brick(name, component, "COPPER_ASSUMED", xr, y_range, zr),
                )
            )
    y = sum(y_range) / 2.0
    p1 = (cx - gap / 2.0, y, cz)
    p2 = (cx + gap / 2.0, y, cz)
    if port_number == 2:
        p1, p2 = p2, p1
    blocks.append((f"Port {port_number} across {component} feed gap", _port(port_number, p1, p2)))
    return blocks


def _placement(p: dict[str, Any], bp: dict[str, Any]) -> dict[str, Any]:
    cx = p["tooth_offset_x_mm"]
    cy = bp["_ring_top"] + p["tooth_offset_y_mm"]
    z_bottom = p["substrate_h_mm"] + p["copper_t_mm"] + p["airgap_mm"]
    cz = z_bottom + 4.2 * p["tooth_scale"]
    crown_ry = 4.0 * p["tooth_scale"] * p["crown_scale_y"]
    tx_face = cy - crown_ry - p["widefield_gap_mm"]
    rx_face = cy + crown_ry + p["widefield_gap_mm"]
    copper = p["copper_t_mm"]
    fr4 = p["substrate_h_mm"]
    return {
        "centre_x_mm": cx,
        "centre_y_mm": cy,
        "centre_z_mm": cz,
        "tooth_bottom_z_mm": z_bottom,
        "tx_copper_y_range_mm": [tx_face - copper, tx_face],
        "tx_fr4_y_range_mm": [tx_face - copper - fr4, tx_face - copper],
        "rx_copper_y_range_mm": [rx_face, rx_face + copper],
        "rx_fr4_y_range_mm": [rx_face + copper, rx_face + copper + fr4],
        "copper_face_separation_mm": rx_face - tx_face,
        "crown_clearance_each_side_mm": p["widefield_gap_mm"],
    }


def _fixture_blocks(p: dict[str, Any], place: dict[str, Any]) -> list[tuple[str, str]]:
    if not p["include_widefield_frame"]:
        return []
    cx, cy, cz = place["centre_x_mm"], place["centre_y_mm"], place["centre_z_mm"]
    half_x = p["widefield_board_x_mm"] / 2.0
    half_z = p["widefield_board_z_mm"] / 2.0
    bar = p["frame_bar_mm"]
    y0 = place["tx_fr4_y_range_mm"][0]
    y1 = place["rx_fr4_y_range_mm"][1]
    z_bottom = place["tooth_bottom_z_mm"]
    blocks: list[tuple[str, str]] = [
        ("Assumed PLA material", base._material("PLA_ASSUMED", 2.7, 0.0, (0.7, 0.7, 0.8), 0.01))
    ]
    # Four longitudinal corner rails define board separation without crossing
    # the sensing aperture around the tooth.
    for x_side, x in (("left", cx - half_x + bar / 2.0), ("right", cx + half_x - bar / 2.0)):
        for z_side, z in (("bottom", cz - half_z + bar / 2.0), ("top", cz + half_z - bar / 2.0)):
            name = f"rail_{x_side}_{z_side}"
            blocks.append(
                (
                    f"PLA {name}",
                    base._brick(name, "fixture", "PLA_ASSUMED", (x - bar / 2.0, x + bar / 2.0), (y0, y1), (z - bar / 2.0, z + bar / 2.0)),
                )
            )
    # A thin plate below the analytic tooth prevents an extracted specimen from
    # falling through.  Its upper face remains below the tooth by a controlled
    # clearance, so the CST solids do not overlap.
    shelf_top = z_bottom - p["support_clearance_mm"]
    shelf_bottom = shelf_top - 0.7
    blocks.append(
        (
            "PLA tooth support shelf",
            base._brick("tooth_shelf", "fixture", "PLA_ASSUMED", (cx - 4.5, cx + 4.5), (cy - 4.5, cy + 4.5), (shelf_bottom, shelf_top)),
        )
    )
    # Two wings connect the shelf to the corner frame in the physical concept.
    for side, xr in (
        ("left", (cx - half_x + bar, cx - 4.5)),
        ("right", (cx + 4.5, cx + half_x - bar)),
    ):
        blocks.append(
            (
                f"PLA shelf wing {side}",
                base._brick(f"shelf_wing_{side}", "fixture", "PLA_ASSUMED", xr, (cy - bar / 2.0, cy + bar / 2.0), (shelf_bottom, shelf_top)),
            )
        )
    return blocks


def history(params: dict[str, Any]) -> list[tuple[str, str]]:
    p, bp = _validated(params)
    legacy_params = {key: p[key] for key in legacy.DEFAULTS}
    legacy_params.update(
        include_resonators=False,
        include_fixture=False,
        include_counterface=False,
        tilt_x_deg=0.0,
        tilt_y_deg=0.0,
    )
    skip = {
        "Substrate",
        "Continuous ground plane",
        "Nominal 50 ohm through microstrip",
        "Port 1 strip to ground",
        "Port 2 strip to ground",
    }
    blocks = [(label, code) for label, code in legacy.history(legacy_params) if label not in skip]
    place = _placement(p, bp)
    cx, cz = place["centre_x_mm"], place["centre_z_mm"]
    board_x = p["widefield_board_x_mm"]
    board_z = p["widefield_board_z_mm"]
    xr = (cx - board_x / 2.0, cx + board_x / 2.0)
    zr = (cz - board_z / 2.0, cz + board_z / 2.0)
    blocks.append(("Tx FR-4 board", base._brick("substrate", "tx", "FR4_ASSUMED", xr, tuple(place["tx_fr4_y_range_mm"]), zr)))
    blocks.append(("Rx FR-4 board", base._brick("substrate", "rx", "FR4_ASSUMED", xr, tuple(place["rx_fr4_y_range_mm"]), zr)))
    blocks.extend(_bowtie_blocks("tx", tuple(place["tx_copper_y_range_mm"]), p, cx, cz, 1))
    blocks.extend(_bowtie_blocks("rx", tuple(place["rx_copper_y_range_mm"]), p, cx, cz, 2))
    blocks.extend(_fixture_blocks(p, place))
    return blocks


def geometry_manifest(params: dict[str, Any]) -> dict[str, Any]:
    p, bp = _validated(params)
    legacy_params = {key: p[key] for key in legacy.DEFAULTS}
    legacy_params.update(
        include_resonators=False,
        include_fixture=False,
        include_counterface=False,
        tilt_x_deg=0.0,
        tilt_y_deg=0.0,
    )
    manifest = legacy.geometry_manifest(legacy_params)
    place = _placement(p, bp)
    manifest["geometry_version"] = "fr4-widefield-txrx-v1"
    manifest["effective_parameters"] = {key: p[key] for key in DEFAULTS}
    manifest["derived_geometry"]["widefield_txrx"] = {
        **place,
        "axis": "y",
        "ports": {"tx": 1, "rx": 2},
        "board_size_xz_mm": [p["widefield_board_x_mm"], p["widefield_board_z_mm"]],
        "bowtie_span_x_mm": p["widefield_span_x_mm"],
        "bowtie_outer_z_mm": p["widefield_outer_z_mm"],
        "bowtie_throat_z_mm": p["widefield_throat_z_mm"],
        "feed_gap_mm": p["widefield_port_gap_mm"],
        "steps_per_arm": p["widefield_steps"],
        "scope": "Two facing balanced stepped bow-ties on 1.6 mm FR-4; exploratory through-tooth transmission topology.",
    }
    manifest["derived_geometry"]["widefield_frame"] = {
        "included": p["include_widefield_frame"],
        "material": "PLA_ASSUMED",
        "bar_width_mm": p["frame_bar_mm"],
        "tooth_support_clearance_mm": p["support_clearance_mm"],
        "mechanical_status": "Electromagnetic concept geometry only; STL and print-fit remain unverified.",
    }
    manifest["frequency_tuning_note"] = (
        "The stepped bow-tie dimensions are a coarse 1-4.5 GHz topology screen and are not an impedance-optimized final sensor."
    )
    return manifest


__all__ = ["DEFAULTS", "PARAMETER_SCHEMA", "SYNTHETIC_BASE_MATERIALS", "geometry_manifest", "history"]


