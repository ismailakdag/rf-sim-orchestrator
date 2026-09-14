"""Geometry-only CST history generator for the fixtured two-port G5 study.

The unchanged ``single_split_ring`` route is retained as the reference.  The
``hilbert_split_ring`` route replaces the two sensor-facing top branches with
mirrored, recursively generated Hilbert paths.  ``dual_asymmetric_ring`` is
passed through to the immutable base, while ``interdigital_split_ring`` places
two interleaved combs in an open U-shaped ring.  This module does not connect
to CST, start a solver, configure a mesh, or write output files.

The selected G5 board remains the only driven two-port device.  An optional
unfed thin copper loop on a 1.6 mm FR-4 frame sits above the crown and surrounds
the root.  Closed, single-split and double-split modes can redistribute the
field without adding a measured or driven port.

All geometry dimensions are millimetres, frequency is GHz, rotation is degrees,
and conductivity is S/m.  The tooth remains the analytic layered synthetic
phantom defined by ``base_geometry.py``; it is not scan-derived or validated as
real dental tissue.
"""

from __future__ import annotations

import importlib.util
from math import cos, radians, sin, sqrt
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_base() -> ModuleType:
    """Load the immutable sibling without requiring ``fractal-v1`` as a package."""
    path = Path(__file__).with_name("base_geometry.py")
    spec = importlib.util.spec_from_file_location("tooth_fractal_v1_base_geometry", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load base geometry from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base()


DEFAULTS: dict[str, Any] = dict(base.DEFAULTS)
DEFAULTS['include_fixture'] = False
DEFAULTS['inclusion_site'] = 'near'
DEFAULTS.update(
    {
        "topology": "hilbert_split_ring",
        "fractal_order": 2,
        "fractal_span_mm": 4.80,
        "fractal_trace_mm": 0.45,
        "tilt_x_deg": 0.0,
        "tilt_y_deg": 0.0,
        "include_counterface": False,
        "counterface_gap_mm": 0.2,
        "counterface_outer_x_mm": 14.0,
        "counterface_outer_y_mm": 12.0,
        "counterface_aperture_x_mm": 6.4,
        "counterface_aperture_y_mm": 5.6,
        "passive_loop_mode": "closed",
        "passive_loop_split_gap_mm": 1.0,
        "fixture_clearance_mm": 0.30,
        "fixture_post_width_mm": 1.50,
        "fixture_bridge_thickness_mm": 1.20,
        "fixture_reference_airgap_mm": 0.20,
        "idc_fingers_per_side": 4,
        "idc_finger_length_mm": 10.0,
        "idc_finger_width_mm": 0.35,
        "idc_finger_gap_mm": 0.35,
        "idc_tip_to_bus_gap_mm": 0.35,
    }
)

PARAMETER_SCHEMA: dict[str, dict[str, Any]] = {
    key: dict(value) for key, value in base.PARAMETER_SCHEMA.items()
}
PARAMETER_SCHEMA.update(
    {
        "topology": {"choices": (
            "single_split_ring",
            "dual_asymmetric_ring",
            "hilbert_split_ring",
            "interdigital_split_ring",
        )},
        "fractal_order": {"type": "int", "choices": (1, 2)},
        "fractal_span_mm": {"unit": "mm", "range": (3.00, 6.00)},
        "fractal_trace_mm": {"unit": "mm", "range": (0.25, 0.90)},
        "tilt_x_deg": {"unit": "deg", "range": (-10.0, 10.0)},
        "tilt_y_deg": {"unit": "deg", "range": (-20.0, 20.0)},
        "include_counterface": {"type": "bool"},
        "counterface_gap_mm": {"unit": "mm", "range": (0.1, 2.0)},
        "counterface_outer_x_mm": {"unit": "mm", "range": (10.0, 18.0)},
        "counterface_outer_y_mm": {"unit": "mm", "range": (9.0, 15.0)},
        "counterface_aperture_x_mm": {"unit": "mm", "range": (5.0, 16.0)},
        "counterface_aperture_y_mm": {"unit": "mm", "range": (4.5, 13.0)},
        "passive_loop_mode": {"choices": ("closed", "single_split", "double_split")},
        "passive_loop_split_gap_mm": {"unit": "mm", "range": (0.5, 2.0)},
        "fixture_clearance_mm": {"unit": "mm", "range": (0.20, 0.60)},
        "fixture_post_width_mm": {"unit": "mm", "range": (1.20, 2.50)},
        "fixture_bridge_thickness_mm": {"unit": "mm", "range": (0.80, 2.00)},
        "fixture_reference_airgap_mm": {"unit": "mm", "range": (0.10, 0.50)},
        "idc_fingers_per_side": {"type": "int", "range": (2, 6)},
        "idc_finger_length_mm": {"unit": "mm", "range": (4.00, 14.00)},
        "idc_finger_width_mm": {"unit": "mm", "range": (0.25, 0.80)},
        "idc_finger_gap_mm": {"unit": "mm", "range": (0.25, 1.00)},
        "idc_tip_to_bus_gap_mm": {"unit": "mm", "range": (0.25, 1.00)},
    }
)

SYNTHETIC_BASE_MATERIALS = base.SYNTHETIC_BASE_MATERIALS
_EXTRA_KEYS = {
    "fractal_order",
    "fractal_span_mm",
    "fractal_trace_mm",
    "tilt_x_deg",
    "tilt_y_deg",
    "include_counterface",
    "counterface_gap_mm",
    "counterface_outer_x_mm",
    "counterface_outer_y_mm",
    "counterface_aperture_x_mm",
    "counterface_aperture_y_mm",
    "passive_loop_mode",
    "passive_loop_split_gap_mm",
    "fixture_clearance_mm",
    "fixture_post_width_mm",
    "fixture_bridge_thickness_mm",
    "fixture_reference_airgap_mm",
    "idc_fingers_per_side",
    "idc_finger_length_mm",
    "idc_finger_width_mm",
    "idc_finger_gap_mm",
    "idc_tip_to_bus_gap_mm",
}
_MIN_FEATURE_MM = 0.25


def _base_params(p: dict[str, Any], *, include_resonators: bool | None = None,
                 include_tooth: bool | None = None) -> dict[str, Any]:
    result = {key: value for key, value in p.items() if key in base.DEFAULTS}
    # The immutable base does not know the fractal topology.  Its validation is
    # still reused for every shared board, material, feed, ring, and tooth value.
    if result["topology"] in ("hilbert_split_ring", "interdigital_split_ring"):
        result["topology"] = "single_split_ring"
    if include_resonators is not None:
        result["include_resonators"] = include_resonators
    if include_tooth is not None:
        result["include_tooth"] = include_tooth
    return result


def _validated(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(params, dict):
        raise TypeError("params must be a dict")
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise KeyError(f"Unknown geometry parameter(s): {sorted(unknown)}")

    p = dict(DEFAULTS)
    p.update(params)
    if p["topology"] not in PARAMETER_SCHEMA["topology"]["choices"]:
        raise ValueError(
            "topology must be single_split_ring, dual_asymmetric_ring, "
            "hilbert_split_ring, or interdigital_split_ring"
        )

    order = p["fractal_order"]
    if isinstance(order, bool) or not isinstance(order, int) or order not in (1, 2):
        raise ValueError("fractal_order must be integer 1 or 2")
    fingers = p["idc_fingers_per_side"]
    if isinstance(fingers, bool) or not isinstance(fingers, int):
        raise TypeError("idc_fingers_per_side must be an integer")
    lo_fingers, hi_fingers = PARAMETER_SCHEMA["idc_fingers_per_side"]["range"]
    if not lo_fingers <= fingers <= hi_fingers:
        raise ValueError(
            f"idc_fingers_per_side={fingers} outside supported range "
            f"[{lo_fingers}, {hi_fingers}]"
        )
    if not isinstance(p["include_counterface"], bool):
        raise TypeError("include_counterface must be bool")
    if p["passive_loop_mode"] not in PARAMETER_SCHEMA["passive_loop_mode"]["choices"]:
        raise ValueError("passive_loop_mode must be closed, single_split or double_split")
    for key in (
        "fractal_span_mm", "fractal_trace_mm", "tilt_x_deg", "tilt_y_deg",
        "counterface_gap_mm", "counterface_outer_x_mm", "counterface_outer_y_mm",
        "counterface_aperture_x_mm", "counterface_aperture_y_mm",
        "passive_loop_split_gap_mm",
        "fixture_clearance_mm", "fixture_post_width_mm",
        "fixture_bridge_thickness_mm", "fixture_reference_airgap_mm",
        "idc_finger_length_mm", "idc_finger_width_mm", "idc_finger_gap_mm",
        "idc_tip_to_bus_gap_mm",
    ):
        try:
            value = float(p[key])
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{key} must be numeric") from exc
        lo, hi = PARAMETER_SCHEMA[key]["range"]
        if not lo <= value <= hi:
            raise ValueError(f"{key}={value} outside supported range [{lo}, {hi}]")
        p[key] = value

    if p["counterface_aperture_x_mm"] >= p["counterface_outer_x_mm"]:
        raise ValueError("counterface x aperture must be smaller than its outer width")
    if p["counterface_aperture_y_mm"] >= p["counterface_outer_y_mm"]:
        raise ValueError("counterface y aperture must be smaller than its outer width")
    if p["include_counterface"] and (p["tilt_x_deg"] or p["tilt_y_deg"]):
        raise ValueError("passive-loop-v1 is preflighted only for the upright tooth")

    bp = base._validated(_base_params(p))
    # Freeze the shared effective value (notably the calculated 50-ohm width).
    for key in base.DEFAULTS:
        if key != "topology":
            p[key] = bp[key]

    if p["topology"] == "hilbert_split_ring":
        pitch = p["fractal_span_mm"] / (2**order - 1)
        clearance = pitch - p["fractal_trace_mm"]
        if clearance < _MIN_FEATURE_MM:
            raise ValueError(
                "fractal grid leaves less than 0.25 mm clearance between parallel traces"
            )
        if p["sensing_gap_mm"] < _MIN_FEATURE_MM:
            raise ValueError("sensing_gap_mm must be at least 0.25 mm")

        half_used = (
            p["sensing_gap_mm"] / 2.0
            + p["fractal_trace_mm"]
            + p["fractal_span_mm"]
        )
        ring_inner_edge = bp["_outer_x"] / 2.0 - p["ring_trace_mm"]
        if ring_inner_edge - half_used < _MIN_FEATURE_MM:
            raise ValueError(
                "Hilbert path does not fit between the ring side and sensing gap "
                "with 0.25 mm clearance"
            )
        y_extent = (
            bp["_ring_top"]
            + p["fractal_span_mm"] / 2.0
            + p["fractal_trace_mm"] / 2.0
        )
        if y_extent >= p["board_y_mm"] / 2.0 - 0.5:
            raise ValueError("Hilbert path does not fit on board with 0.5 mm edge clearance")

    if p["topology"] == "interdigital_split_ring":
        width = p["idc_finger_width_mm"]
        gap = p["idc_finger_gap_mm"]
        tip_gap = p["idc_tip_to_bus_gap_mm"]
        if min(width, gap, tip_gap) < _MIN_FEATURE_MM:
            raise ValueError("IDC width and clearances must be at least 0.25 mm")
        rows = 2 * fingers
        window_height = rows * width + (rows - 1) * gap
        inner_height = bp["_outer_y"] - 2.0 * p["ring_trace_mm"]
        if inner_height - window_height < _MIN_FEATURE_MM:
            raise ValueError("IDC finger window does not fit above the ring bottom")

        # Finger length plus its clear tip-to-opposite-bus distance determines
        # the separation of the two bus inner faces.  Each bus uses the robust
        # ring-trace width, not the smaller finger width.
        bus_inner_separation = p["idc_finger_length_mm"] + tip_gap
        inner_width = bp["_outer_x"] - 2.0 * p["ring_trace_mm"]
        connector_length = (
            inner_width - bus_inner_separation - 2.0 * p["ring_trace_mm"]
        ) / 2.0
        if connector_length < _MIN_FEATURE_MM:
            raise ValueError(
                "IDC buses and finger length do not fit with 0.25 mm side connectors"
            )
        if p["idc_finger_length_mm"] <= tip_gap:
            raise ValueError("IDC opposing fingers must have positive x overlap")
        _interdigital_layout(p, bp)

    # A conservative projected-envelope check for the upright analytic phantom.
    # At the allowed small tilts this also bounds the narrow root displacement.
    if p["include_tooth"]:
        height = 15.9 * p["tooth_scale"]
        projected_y = (
            4.0 * p["tooth_scale"]
            + height * abs(sin(radians(p["tilt_x_deg"])))
        )
        tooth_y = bp["_ring_top"] + p["tooth_offset_y_mm"]
        if abs(tooth_y) + projected_y >= p["board_y_mm"] / 2.0 - 0.5:
            raise ValueError("tilted tooth envelope does not fit on board")
        if 4.5 * p["tooth_scale"] * p["crown_scale_x"] + abs(p["tooth_offset_x_mm"]) >= 7.7:
            raise ValueError("tooth crown does not fit the fixed passive-loop x aperture")
        if 4.0 * p["tooth_scale"] * p["crown_scale_y"] + abs(p["tooth_offset_y_mm"]) >= 6.2:
            raise ValueError("tooth crown does not fit the fixed passive-loop y aperture")
    return p, bp


def _hilbert_unit_points(order: int) -> list[tuple[float, float]]:
    """Return a recursively generated, normalized order-1/2 Hilbert polyline."""
    points: list[tuple[float, float]] = []

    def visit(
        level: int,
        x0: float,
        y0: float,
        xi: float,
        xj: float,
        yi: float,
        yj: float,
    ) -> None:
        if level == 0:
            points.append((x0 + (xi + yi) / 2.0, y0 + (xj + yj) / 2.0))
            return
        visit(level - 1, x0, y0, yi / 2, yj / 2, xi / 2, xj / 2)
        visit(level - 1, x0 + xi / 2, y0 + xj / 2, xi / 2, xj / 2, yi / 2, yj / 2)
        visit(
            level - 1,
            x0 + xi / 2 + yi / 2,
            y0 + xj / 2 + yj / 2,
            xi / 2,
            xj / 2,
            yi / 2,
            yj / 2,
        )
        visit(
            level - 1,
            x0 + xi / 2 + yi,
            y0 + xj / 2 + yj,
            -yi / 2,
            -yj / 2,
            -xi / 2,
            -xj / 2,
        )

    visit(order, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0)
    xmin = min(x for x, _ in points)
    xmax = max(x for x, _ in points)
    ymin = min(y for _, y in points)
    ymax = max(y for _, y in points)
    return [((x - xmin) / (xmax - xmin), (y - ymin) / (ymax - ymin)) for x, y in points]


def _trace_brick(
    name: str,
    a: tuple[float, float],
    b: tuple[float, float],
    width: float,
    z: tuple[float, float],
) -> str:
    half = width / 2.0
    if abs(a[1] - b[1]) < 1e-12:
        xr = (min(a[0], b[0]) - half, max(a[0], b[0]) + half)
        yr = (a[1] - half, a[1] + half)
    elif abs(a[0] - b[0]) < 1e-12:
        xr = (a[0] - half, a[0] + half)
        yr = (min(a[1], b[1]) - half, max(a[1], b[1]) + half)
    else:
        raise ValueError("Hilbert trace segment is not axis-aligned")
    return base._brick(name, "sensor", "PEC", xr, yr, z)


def _hilbert_split_ring(p: dict[str, Any], bp: dict[str, Any],
                        z: tuple[float, float]) -> list[tuple[str, str]]:
    cx = 0.0
    cy = bp["_ring_center_y"]
    outer_x, outer_y = bp["_outer_x"], bp["_outer_y"]
    ring_trace = p["ring_trace_mm"]
    fractal_trace = p["fractal_trace_mm"]
    span = p["fractal_span_mm"]
    gap = p["sensing_gap_mm"]
    xmin, xmax = cx - outer_x / 2.0, cx + outer_x / 2.0
    ymin, ymax = cy - outer_y / 2.0, cy + outer_y / 2.0

    pieces: list[tuple[str, str]] = [
        ("fractal_primary_left", base._brick(
            "fractal_primary_left", "sensor", "PEC",
            (xmin, xmin + ring_trace), (ymin, ymax), z)),
        ("fractal_primary_right", base._brick(
            "fractal_primary_right", "sensor", "PEC",
            (xmax - ring_trace, xmax), (ymin, ymax), z)),
        ("fractal_primary_bottom", base._brick(
            "fractal_primary_bottom", "sensor", "PEC",
            (xmin + ring_trace, xmax - ring_trace), (ymin, ymin + ring_trace), z)),
    ]

    unit = _hilbert_unit_points(p["fractal_order"])
    inner_left_x = -(gap / 2.0 + fractal_trace / 2.0)
    outer_left_x = inner_left_x - span
    center_y0 = ymax - span / 2.0
    # Rotate the canonical curve by 90 degrees so its first endpoint anchors to
    # the ring side and its other endpoint terminates beside the central gap.
    left_points = [
        (outer_left_x + v * span, center_y0 + u * span) for u, v in unit
    ]
    right_points = [(-x, y) for x, y in left_points]

    for side, points, side_x in (
        ("left", left_points, xmin + ring_trace / 2.0),
        ("right", right_points, xmax - ring_trace / 2.0),
    ):
        connector_name = f"fractal_{side}_anchor"
        connector_start = (side_x, points[0][1])
        pieces.append(
            (
                connector_name,
                _trace_brick(connector_name, connector_start, points[0], fractal_trace, z),
            )
        )
        for index, (a, b) in enumerate(zip(points, points[1:]), start=1):
            name = f"fractal_{side}_seg_{index:02d}"
            pieces.append((name, _trace_brick(name, a, b, fractal_trace, z)))

    blocks = [(f"Create {name}", vba) for name, vba in pieces]
    root = pieces[0][0]
    blocks.extend(
        (f"Unite {name}", f'Solid.Add "sensor:{root}", "sensor:{name}"')
        for name, _ in pieces[1:]
    )
    return blocks


def _interdigital_layout(p: dict[str, Any], bp: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic IDC AABBs without producing CST history.

    ``network`` labels describe the two ends of the capacitive discontinuity.
    They are connected only by the long U-shaped ring path; no left/right IDC
    AABBs may touch or overlap across the open sensing window.
    """
    trace = p["ring_trace_mm"]
    width = p["idc_finger_width_mm"]
    gap = p["idc_finger_gap_mm"]
    tip_gap = p["idc_tip_to_bus_gap_mm"]
    length = p["idc_finger_length_mm"]
    rows = 2 * p["idc_fingers_per_side"]

    xmin = -bp["_outer_x"] / 2.0
    xmax = bp["_outer_x"] / 2.0
    ymax = bp["_ring_center_y"] + bp["_outer_y"] / 2.0
    inner_left = xmin + trace
    inner_right = xmax - trace
    bus_inner_separation = length + tip_gap
    left_bus_inner = -bus_inner_separation / 2.0
    right_bus_inner = bus_inner_separation / 2.0
    left_bus_outer = left_bus_inner - trace
    right_bus_outer = right_bus_inner + trace
    window_height = rows * width + (rows - 1) * gap
    window_bottom = ymax - window_height

    pieces: list[dict[str, Any]] = [
        {
            "name": "idc_left_bus",
            "network": "left",
            "kind": "bus",
            "x_range_mm": (left_bus_outer, left_bus_inner),
            "y_range_mm": (window_bottom, ymax),
        },
        {
            "name": "idc_right_bus",
            "network": "right",
            "kind": "bus",
            "x_range_mm": (right_bus_inner, right_bus_outer),
            "y_range_mm": (window_bottom, ymax),
        },
        {
            "name": "idc_left_connector",
            "network": "left",
            "kind": "connector",
            "x_range_mm": (inner_left, left_bus_outer),
            "y_range_mm": (ymax - width, ymax),
        },
        {
            "name": "idc_right_connector",
            "network": "right",
            "kind": "connector",
            "x_range_mm": (right_bus_outer, inner_right),
            "y_range_mm": (ymax - width, ymax),
        },
    ]

    side_counts = {"left": 0, "right": 0}
    for row in range(rows):
        side = "left" if row % 2 == 0 else "right"
        side_counts[side] += 1
        y_high = ymax - row * (width + gap)
        y_range = (y_high - width, y_high)
        if side == "left":
            x_range = (left_bus_inner, right_bus_inner - tip_gap)
        else:
            x_range = (left_bus_inner + tip_gap, right_bus_inner)
        pieces.append(
            {
                "name": f"idc_{side}_finger_{side_counts[side]:02d}",
                "network": side,
                "kind": "finger",
                "x_range_mm": x_range,
                "y_range_mm": y_range,
            }
        )

    opposing_clearances: list[float] = []
    left_pieces = [piece for piece in pieces if piece["network"] == "left"]
    right_pieces = [piece for piece in pieces if piece["network"] == "right"]
    for left in left_pieces:
        for right in right_pieces:
            lx0, lx1 = left["x_range_mm"]
            ly0, ly1 = left["y_range_mm"]
            rx0, rx1 = right["x_range_mm"]
            ry0, ry1 = right["y_range_mm"]
            dx = max(rx0 - lx1, lx0 - rx1, 0.0)
            dy = max(ry0 - ly1, ly0 - ry1, 0.0)
            if dx == 0.0 and dy == 0.0:
                raise ValueError(
                    f"IDC opposing conductors overlap or touch: {left['name']} / {right['name']}"
                )
            opposing_clearances.append(sqrt(dx * dx + dy * dy))

    minimum_opposing_clearance = min(opposing_clearances)
    if minimum_opposing_clearance + 1e-12 < _MIN_FEATURE_MM:
        raise ValueError("IDC opposing-conductor clearance is below 0.25 mm")

    return {
        "pieces": pieces,
        "finger_count_total": rows,
        "finger_count_per_side": p["idc_fingers_per_side"],
        "window_height_mm": window_height,
        "window_y_range_mm": (window_bottom, ymax),
        "bus_inner_separation_mm": bus_inner_separation,
        "finger_x_overlap_mm": length - tip_gap,
        "side_connector_length_mm": left_bus_outer - inner_left,
        "minimum_opposing_clearance_mm": minimum_opposing_clearance,
    }


def _interdigital_split_ring(p: dict[str, Any], bp: dict[str, Any],
                             z: tuple[float, float]) -> list[tuple[str, str]]:
    """Build an open U-ring with two non-touching interleaved IDC combs."""
    trace = p["ring_trace_mm"]
    xmin = -bp["_outer_x"] / 2.0
    xmax = bp["_outer_x"] / 2.0
    ymin = bp["_ring_center_y"] - bp["_outer_y"] / 2.0
    ymax = bp["_ring_center_y"] + bp["_outer_y"] / 2.0
    pieces: list[tuple[str, str]] = [
        ("idc_primary_left", base._brick(
            "idc_primary_left", "sensor", "PEC",
            (xmin, xmin + trace), (ymin, ymax), z)),
        ("idc_primary_right", base._brick(
            "idc_primary_right", "sensor", "PEC",
            (xmax - trace, xmax), (ymin, ymax), z)),
        ("idc_primary_bottom", base._brick(
            "idc_primary_bottom", "sensor", "PEC",
            (xmin + trace, xmax - trace), (ymin, ymin + trace), z)),
    ]
    for piece in _interdigital_layout(p, bp)["pieces"]:
        name = piece["name"]
        pieces.append((
            name,
            base._brick(
                name,
                "sensor",
                "PEC",
                piece["x_range_mm"],
                piece["y_range_mm"],
                z,
            ),
        ))

    blocks = [(f"Create {name}", vba) for name, vba in pieces]
    root = pieces[0][0]
    blocks.extend(
        (f"Unite {name}", f'Solid.Add "sensor:{root}", "sensor:{name}"')
        for name, _ in pieces[1:]
    )
    return blocks


def _tilt_geometry(p: dict[str, Any]) -> tuple[float, float]:
    """Return exact crown lift and a conservative root clearance above pivot.

    The applied order is first ``Rx`` and then global ``Ry`` (R = Ry @ Rx).
    For the outer crown ellipsoid, transformed centre and vertical support
    radius give its exact lowest z.  The root value is a lower bound using the
    largest conical radius at its lowest z, after its 0.78 y flattening.
    """
    scale = p["tooth_scale"]
    tx = radians(p["tilt_x_deg"])
    ty = radians(p["tilt_y_deg"])
    cx, sx = cos(tx), sin(tx)
    cy, sy = cos(ty), sin(ty)

    rx, ry, rz = 4.5 * scale, 4.0 * scale, 4.2 * scale
    transformed_centre_z = 4.2 * scale * cx * cy
    vertical_extent = sqrt(
        (rx * sy) ** 2
        + (ry * cy * sx) ** 2
        + (rz * cy * cx) ** 2
    )
    lift = max(0.0, vertical_extent - transformed_centre_z)

    root_start = 1.45 * 4.2 * scale
    root_rx = 2.40 * scale
    root_ry = 0.78 * 2.40 * scale
    root_clearance = (
        root_start * cx * cy
        - root_rx * abs(sy)
        - root_ry * abs(cy * sx)
    )
    if root_clearance < 0.0:
        raise ValueError("tilted root can pass below the crown-bottom pivot")
    return lift, root_clearance


def _rotate_shape(name: str, axis: str, angle_deg: float, p: dict[str, Any],
                  bp: dict[str, Any], pivot_z: float) -> str:
    # CST Studio Suite 2026 local VBA help, Transform Object: Origin("Free"),
    # Center(u,v,w), Angle(u,v,w), and Transform("Shape","Rotate").
    pivot_x = p["tooth_offset_x_mm"]
    pivot_y = bp["_ring_top"] + p["tooth_offset_y_mm"]
    angles = {
        "x": (angle_deg, 0.0, 0.0),
        "y": (0.0, angle_deg, 0.0),
    }
    if axis not in angles:
        raise ValueError("rotation axis must be x or y")
    ax, ay, az = angles[axis]
    return f'''With Transform
 .Reset
 .Name "phantom:{name}"
 .Origin "Free"
 .Center "{base._f(pivot_x)}", "{base._f(pivot_y)}", "{base._f(pivot_z)}"
 .Angle "{base._f(ax)}", "{base._f(ay)}", "{base._f(az)}"
 .MultipleObjects "False"
 .GroupObjects "False"
 .Repetitions "1"
 .MultipleSelection "False"
 .Transform "Shape", "Rotate"
End With'''


def _tooth_with_tilt(p: dict[str, Any], bp: dict[str, Any],
                     metal_top: float) -> list[tuple[str, str]]:
    lift, _ = _tilt_geometry(p)
    build_metal_top = metal_top + lift
    centers = inclusion_centers(p, bp, build_metal_top)
    blocks = base._tooth_blocks(dict(bp, include_lesion=False), build_metal_top)
    # All cases contain the same four sphere boundaries and object names.  Only
    # the selected site's material properties change between control/contrast.
    # This lets us test cross-site mesh identity instead of moving a single
    # topology-changing solid around the tooth.
    blocks.append(('Host-equivalent inclusion material',base._material('DENTIN_INSERT',10.0,0.1,(0.72,0.56,0.32))))
    blocks.append(('Selected inclusion material',base._material('LESION_SYNTHETIC',p['lesion_epsilon_r'],p['lesion_sigma_S_per_m'],(0.3,0.6,0.9))))
    for site, center in centers.items():
        tool=f'inclusion_tool_{site}'; solid=f'inclusion_{site}'
        material='LESION_SYNTHETIC' if site == p['inclusion_site'] else 'DENTIN_INSERT'
        blocks.append((f'Core inclusion tool {site}',base._sphere(tool,'phantom','DENTIN_INSERT',center,p['lesion_radius_mm'])))
        blocks.append((f'Subtract inclusion {site} from core',f'Solid.Subtract "phantom:core", "phantom:{tool}"'))
        blocks.append((f'Fixed-volume inclusion {site}',base._sphere(solid,'phantom',material,center,p['lesion_radius_mm'])))
    if p['tilt_x_deg'] or p['tilt_y_deg']:
        pivot_z = metal_top + p['airgap_mm'] + lift
        names = ['shell', 'core', 'pulp'] + [f'inclusion_{site}' for site in centers]
        for axis, angle in (('x', p['tilt_x_deg']), ('y', p['tilt_y_deg'])):
            if angle == 0.0:
                continue
            blocks.extend(
                (
                    f'Tilt common-topology phantom solid {name} about {axis}',
                    _rotate_shape(name, axis, angle, p, bp, pivot_z),
                )
                for name in names
            )
    return blocks


def inclusion_center_for_site(p, bp, metal_top, site):
    sites={'near':(0,0,1.5),'left':(-2.3,0,3.8),'right':(2.3,0,3.8),'far':(2.3,0,6.0)}
    dx,dy,dz=sites[site];s=p['tooth_scale'];r=p['lesion_radius_mm']
    sx=p['crown_scale_x'];sy=p['crown_scale_y']
    # The sphere's bounding box is inside the dentin envelope ellipsoid.
    outer_bound=sum(((abs(v)+r)/radius)**2 for v,radius in zip(
        (dx*s*sx,dy*s*sy,(dz-4.2)*s),(3.85*s*sx,3.35*s*sy,3.55*s)))
    # Bounding box stays outside the pulp crown; all sites precede its root start.
    pulp_lower=sum((max(abs(v)-r,0)/radius)**2 for v,radius in zip(
        (dx*s*sx,dy*s*sy,(dz-4.75)*s),(1.35*s*sx,1.15*s*sy,2.25*s)))
    if outer_bound>=1 or pulp_lower<=1 or (dz*s+r>=6.25*s and abs(dx*s)-r<=.68*s):
        raise ValueError('Inclusion not conservatively contained in dentin alone')
    return (p['tooth_offset_x_mm']+dx*s*sx,bp['_ring_top']+p['tooth_offset_y_mm']+dy*s*sy,
            metal_top+p['airgap_mm']+dz*s)


def inclusion_centers(p, bp, metal_top):
    return {site: inclusion_center_for_site(p,bp,metal_top,site)
            for site in ('near','left','right','far')}


def inclusion_center(p, bp, metal_top):
    return inclusion_center_for_site(p,bp,metal_top,p['inclusion_site'])


def _rotated_inclusion_centers(p, bp, metal_top):
    """Return all four final centres after the same Rx then Ry history rotations."""
    lift, _ = _tilt_geometry(p)
    pivot = (
        p['tooth_offset_x_mm'],
        bp['_ring_top'] + p['tooth_offset_y_mm'],
        metal_top + p['airgap_mm'] + lift,
    )
    tx = radians(p['tilt_x_deg']); ty = radians(p['tilt_y_deg'])
    cx, sx = cos(tx), sin(tx); cy, sy = cos(ty), sin(ty)
    result = {}
    for site, point in inclusion_centers(p, bp, metal_top + lift).items():
        dx, dy, dz = (point[index] - pivot[index] for index in range(3))
        x1 = dx
        y1 = cx * dy - sx * dz
        z1 = sx * dy + cx * dz
        x2 = cy * x1 + sy * z1
        y2 = y1
        z2 = -sy * x1 + cy * z1
        result[site] = (pivot[0] + x2, pivot[1] + y2, pivot[2] + z2)
    return result


def _legacy_tooth_with_tilt(p: dict[str, Any], bp: dict[str, Any],
                     metal_top: float) -> list[tuple[str, str]]:
    lift, _ = _tilt_geometry(p)
    blocks = base._tooth_blocks(bp, metal_top + lift)
    if p["tilt_x_deg"] == 0.0 and p["tilt_y_deg"] == 0.0:
        return blocks
    pivot_z = metal_top + p["airgap_mm"] + lift
    names = ["shell", "core", "pulp"]
    if p["include_lesion"]:
        names.append("lesion")
    # Sequential blocks make the documented order unambiguous: Rx, then global Ry.
    for axis, angle in (("x", p["tilt_x_deg"]), ("y", p["tilt_y_deg"])):
        if angle == 0.0:
            continue
        blocks.extend(
            (
                f"Tilt analytic phantom solid {name} about {axis}",
                _rotate_shape(name, axis, angle, p, bp, pivot_z),
            )
            for name in names
        )
    return blocks


def _original_history(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ordered ``(label, VBA)`` geometry blocks; never run CST/solver."""
    p, bp = _validated(params)
    no_tilt = p["tilt_x_deg"] == 0.0 and p["tilt_y_deg"] == 0.0

    # These paths are byte-for-byte the immutable base histories at zero tilt.
    if p["topology"] in ("single_split_ring", "dual_asymmetric_ring") and no_tilt:
        return base.history(_base_params(p))

    blocks = base.history(
        _base_params(p, include_resonators=False, include_tooth=False)
    )
    h = p["substrate_h_mm"]
    copper = p["copper_t_mm"]
    z_trace = (h, h + copper)
    if p["include_resonators"]:
        if p["topology"] in ("single_split_ring", "dual_asymmetric_ring"):
            blocks.extend(base._rectangular_split_ring(
                "primary", 0.0, bp["_ring_center_y"], bp["_outer_x"], bp["_outer_y"],
                p["ring_trace_mm"], p["sensing_gap_mm"], z_trace,
            ))
            if p["topology"] == "dual_asymmetric_ring":
                blocks.extend(base._rectangular_split_ring(
                    "secondary", p["secondary_offset_x_mm"], bp["_ring_center_y"],
                    bp["_outer_x"] * p["secondary_scale"],
                    bp["_outer_y"] * p["secondary_scale"],
                    p["ring_trace_mm"], 0.75 * p["sensing_gap_mm"], z_trace,
                ))
        elif p["topology"] == "hilbert_split_ring":
            blocks.extend(_hilbert_split_ring(p, bp, z_trace))
        else:
            blocks.extend(_interdigital_split_ring(p, bp, z_trace))
    if p["include_tooth"]:
        blocks.extend(_tooth_with_tilt(p, bp, h + copper))
    return blocks


def _lesion_position_manifest(p: dict[str, Any], bp: dict[str, Any],
                              tilt_lift: float) -> dict[str, Any]:
    """Return analytic pre/post-tilt lesion coordinates for traceability."""
    metal_top = p["substrate_h_mm"] + p["copper_t_mm"]
    placement = base._lesion_placement(bp, metal_top + tilt_lift)
    pivot = (
        p["tooth_offset_x_mm"],
        bp["_ring_top"] + p["tooth_offset_y_mm"],
        metal_top + p["airgap_mm"] + tilt_lift,
    )
    point = placement["lesion_center_pre_tilt_mm"]
    dx, dy, dz = (point[index] - pivot[index] for index in range(3))

    tx = radians(p["tilt_x_deg"])
    ty = radians(p["tilt_y_deg"])
    cx, sx = cos(tx), sin(tx)
    cy, sy = cos(ty), sin(ty)
    # Active rotations in the same documented order as the VBA history:
    # first global x, then global y (R = Ry @ Rx).
    x_after_rx = dx
    y_after_rx = cx * dy - sx * dz
    z_after_rx = sx * dy + cx * dz
    x_after_ry = cy * x_after_rx + sy * z_after_rx
    y_after_ry = y_after_rx
    z_after_ry = -sy * x_after_rx + cy * z_after_rx
    placement["tilt_pivot_mm"] = list(pivot)
    placement["lesion_center_post_tilt_mm"] = [
        pivot[0] + x_after_ry,
        pivot[1] + y_after_ry,
        pivot[2] + z_after_ry,
    ]
    placement["coordinate_definition"] = (
        "lesion offsets are tooth-local x/y before Rx then global Ry tilt"
    )
    return placement


def geometry_manifest(params: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-safe effective parameters, units, and derived geometry."""
    p, bp = _validated(params)
    effective = {key: p[key] for key in DEFAULTS}
    tilt_lift, root_clearance = _tilt_geometry(p)
    derived: dict[str, Any] = {
        "outer_ring_x_mm": bp["_outer_x"],
        "outer_ring_y_mm": bp["_outer_y"],
        "ring_center_y_mm": bp["_ring_center_y"],
        "feed_center_y_mm": bp["_feed_y"],
        "tilt_clearance_lift_mm": tilt_lift,
        "root_min_z_above_pivot_bound_mm": root_clearance,
        "minimum_feature_rule_mm": _MIN_FEATURE_MM,
    }
    derived["lesion_placement"] = _lesion_position_manifest(p, bp, tilt_lift)
    centers=_rotated_inclusion_centers(p,bp,p['substrate_h_mm']+p['copper_t_mm'])
    center=centers[p['inclusion_site']]
    derived['lesion_placement']={'lesion_center_post_tilt_mm':list(center),
       'site':p['inclusion_site'],'scope':'fully internal fixed-volume dentin inclusion; not a surface lesion',
       'target_volume_mm3':4*3.141592653589793*p['lesion_radius_mm']**3/3,
       'selected_solid':'inclusion_'+p['inclusion_site'],
       'all_site_centers_mm':{site:list(value) for site,value in centers.items()},
       'common_topology':'All four equal-volume sphere boundaries exist in every case; only the selected material changes.'}
    z0 = p["substrate_h_mm"] + p["copper_t_mm"] + p["airgap_mm"]
    counterface_z = (
        p["substrate_h_mm"] + p["copper_t_mm"]
        + p["fixture_reference_airgap_mm"] + 8.4 * p["tooth_scale"]
        + p["counterface_gap_mm"]
    )
    derived["passive_loop"] = {
        "included": p["include_counterface"],
        "centre_x_mm": 0.0,
        "centre_y_mm": bp["_ring_top"],
        "copper_lower_z_mm": counterface_z,
        "copper_upper_z_mm": counterface_z + p["copper_t_mm"],
        "fr4_upper_z_mm": counterface_z + p["copper_t_mm"] + p["substrate_h_mm"],
        "outer_x_mm": p["counterface_outer_x_mm"],
        "outer_y_mm": p["counterface_outer_y_mm"],
        "aperture_x_mm": p["counterface_aperture_x_mm"],
        "aperture_y_mm": p["counterface_aperture_y_mm"],
        "mode": p["passive_loop_mode"],
        "split_gap_mm": p["passive_loop_split_gap_mm"],
        "ports": [1, 2],
        "unfed": True,
        "scope": "board-fixed floating copper frame on FR-4 with a central root aperture; passive field-return candidate",
    }
    derived["fixture"] = {
        "included": p["include_fixture"],
        "material": "PLA_ASSUMED",
        "relative_permittivity": 2.7,
        "loss_tangent": 0.01,
        "crown_cradle_clearance_mm": p["fixture_clearance_mm"],
        "post_width_mm": p["fixture_post_width_mm"],
        "bridge_thickness_mm": p["fixture_bridge_thickness_mm"],
        "reference_airgap_mm": p["fixture_reference_airgap_mm"],
        "board_referenced": True,
        "sample_retention": (
            "open-bottom replaceable crown cradle plus upper root collar; "
            "print fit and real extracted-tooth retention remain experimental"
        ),
    }
    if p["topology"] == "dual_asymmetric_ring":
        derived.update({
            "secondary_outer_x_mm": bp["_outer_x"] * p["secondary_scale"],
            "secondary_outer_y_mm": bp["_outer_y"] * p["secondary_scale"],
            "secondary_offset_x_mm": p["secondary_offset_x_mm"],
            "secondary_sensing_gap_mm": 0.75 * p["sensing_gap_mm"],
        })
    elif p["topology"] == "hilbert_split_ring":
        order = p["fractal_order"]
        pitch = p["fractal_span_mm"] / (2**order - 1)
        connector = (
            bp["_outer_x"] / 2.0
            - p["ring_trace_mm"] / 2.0
            - (
                p["sensing_gap_mm"] / 2.0
                + p["fractal_trace_mm"] / 2.0
                + p["fractal_span_mm"]
            )
        )
        derived.update({
            "fractal_grid_pitch_mm": pitch,
            "fractal_parallel_clearance_mm": pitch - p["fractal_trace_mm"],
            "hilbert_segments_per_arm": 4**order - 1,
            "hilbert_curve_length_per_arm_mm": (4**order - 1) * pitch,
            "anchor_length_per_arm_mm": connector,
        })
    elif p["topology"] == "interdigital_split_ring":
        layout = _interdigital_layout(p, bp)
        derived.update({
            key: value for key, value in layout.items() if key != "pieces"
        })
        derived["idc_bus_width_mm"] = p["ring_trace_mm"]
        derived["idc_piece_aabbs"] = [
            {
                "name": piece["name"],
                "network": piece["network"],
                "kind": piece["kind"],
                "x_range_mm": list(piece["x_range_mm"]),
                "y_range_mm": list(piece["y_range_mm"]),
            }
            for piece in layout["pieces"]
        ]
    return {
        "geometry_version": "fr4-g5-fixtured-loop-v1",
        "evidence_domain": "phantom",
        "phantom_designation": "assumed four-cusp molar-like crown, tapered root, enamel/dentin/pulp surrogate layers; not scan-derived or biological material validation",
        "units": {
            "length": "mm",
            "frequency": "GHz",
            "rotation": "deg",
            "conductivity": "S/m",
        },
        "effective_parameters": effective,
        "derived_geometry": derived,
        "frequency_tuning_note": (
            "ring_scale, fractal dimensions, and IDC dimensions are geometric tuning variables; "
            "they do not guarantee a 3 GHz resonance"
        ),
        "rotation_api_evidence": (
            "CST Studio Suite 2026 local help: Transform Object methods "
            "Origin, Center, Angle, Transform(Shape, Rotate)"
        ),
        "rotation_order": "first global x, then global y (R = Ry @ Rx)",
    }


__all__ = [
    "DEFAULTS",
    "PARAMETER_SCHEMA",
    "SYNTHETIC_BASE_MATERIALS",
    "geometry_manifest",
    "history",
]


def history(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Two-port FR-4/copper G5 sensor with an optional unfed passive loop."""
    p, bp = _validated(params)
    if not .9 <= p['tooth_scale'] <= 1.1:
        raise ValueError('Multiview screening supports only tooth scales from 0.9 to 1.1.')
    if abs(p['tooth_offset_x_mm']) > 0.5 or abs(p['tooth_offset_y_mm']) > 0.5:
        raise ValueError('Holder translation exceeds the preflighted +/-0.5 mm envelope.')
    if p['include_fixture'] and (p['tilt_x_deg'] or p['tilt_y_deg']):
        raise ValueError('The nominal PLA socket is not valid for a tilted tooth; run the angle screen without a fixture.')
    blocks = _original_history(params)
    copper = '''With Material
 .Reset
 .Name "COPPER_ASSUMED"
 .FrqType "all"
 .Type "Lossy metal"
 .SetMaterialUnit "GHz", "mm"
 .Mu "1"
 .Kappa "5.8e7"
 .Create
End With'''
    blocks.insert(1, ("Copper from CST 2026 annealed library definition", copper))
    blocks = [(label.replace("Assumed low-loss", "Assumed FR-4"),
               vba.replace('"SUBSTRATE_ASSUMED_ER3P55"', '"FR4_ASSUMED"')
                  .replace('.Material "PEC"', '.Material "COPPER_ASSUMED"'))
              for label, vba in blocks]
    if p['include_counterface']:
        # The passive carrier is fixed to the sensor board.  Tooth offsets are
        # therefore real placement tolerances relative to the RF structure.
        cx = 0.0
        cy = bp['_ring_top']
        outer_x = p['counterface_outer_x_mm']
        outer_y = p['counterface_outer_y_mm']
        aperture_x = p['counterface_aperture_x_mm']
        aperture_y = p['counterface_aperture_y_mm']
        x0, x1 = cx - outer_x / 2.0, cx + outer_x / 2.0
        y0, y1 = cy - outer_y / 2.0, cy + outer_y / 2.0
        ax0, ax1 = cx - aperture_x / 2.0, cx + aperture_x / 2.0
        ay0, ay1 = cy - aperture_y / 2.0, cy + aperture_y / 2.0
        zc0 = (p['substrate_h_mm'] + p['copper_t_mm']
               + p['fixture_reference_airgap_mm'] + 8.4 * p['tooth_scale']
               + p['counterface_gap_mm'])
        zc1 = zc0 + p['copper_t_mm']
        zf1 = zc1 + p['substrate_h_mm']
        carrier_segments = (
            ('left', (x0, ax0), (y0, y1)),
            ('right', (ax1, x1), (y0, y1)),
            ('front', (ax0, ax1), (y0, ay0)),
            ('back', (ax0, ax1), (ay1, y1)),
        )
        for name, xr, yr in carrier_segments:
            blocks.append((
                f'Passive-loop FR-4 carrier {name}',
                base._brick(f'fr4_{name}', 'counterface', 'FR4_ASSUMED', xr, yr, (zc1, zf1)),
            ))
        gap = p['passive_loop_split_gap_mm']
        copper_segments = list(carrier_segments)
        if p['passive_loop_mode'] in ('single_split', 'double_split'):
            copper_segments = [item for item in copper_segments if item[0] != 'back']
            copper_segments.extend((
                ('back_left', (ax0, cx-gap/2), (ay1, y1)),
                ('back_right', (cx+gap/2, ax1), (ay1, y1)),
            ))
        if p['passive_loop_mode'] == 'double_split':
            copper_segments = [item for item in copper_segments if item[0] != 'front']
            copper_segments.extend((
                ('front_left', (ax0, cx-gap/2), (y0, ay0)),
                ('front_right', (cx+gap/2, ax1), (y0, ay0)),
            ))
        for name, xr, yr in copper_segments:
            blocks.append((
                f'Unfed passive-loop copper {name}',
                base._brick(f'copper_{name}', 'counterface', 'COPPER_ASSUMED', xr, yr, (zc0, zc1)),
            ))
    if not isinstance(p['include_fixture'], bool):
        raise TypeError('include_fixture must be bool')
    if not p['include_fixture']:
        return blocks
    blocks.append(("Assumed PLA material", base._material("PLA_ASSUMED", 2.7, 0.0, (0.7, 0.7, 0.8), 0.01)))
    # A board-referenced bridge fixes the passive carrier height.  A replaceable
    # crown cradle has explicit radial clearance; its open lower aperture leaves
    # the sensor-facing crown exposed while the tapered shoulder prevents the
    # specimen falling through.  The upper collar centres the root.
    cx = p['tooth_offset_x_mm']
    cy = bp['_ring_top'] + p['tooth_offset_y_mm']
    fixture_cx = 0.0
    fixture_cy = bp['_ring_top']
    zboard = p['substrate_h_mm']
    z0 = zboard + p['copper_t_mm'] + p['airgap_mm']
    s=p['tooth_scale']
    clearance=p['fixture_clearance_mm']
    blocks.append(('PLA replaceable crown cradle', base._brick(
        'socket','fixture','PLA_ASSUMED',(fixture_cx-6.5,fixture_cx+6.5),
        (fixture_cy-6,fixture_cy+6),(z0+1.8*s,z0+4.2*s))))
    cavity, _ = base._tooth_envelope_blocks(
        'holder_cavity','PLA_ASSUMED',cx,cy,z0,s,-clearance,
        crown_scale_x=p['crown_scale_x'],crown_scale_y=p['crown_scale_y'],
        cusp_scale=p['cusp_scale'],root_width_scale=p['root_width_scale'],
        root_length_scale=p['root_length_scale'])
    blocks.extend((label, code.replace('"phantom"','"fixture"').replace('phantom:','fixture:')) for label,code in cavity)
    blocks.append(('Open lower-crown cavity','Solid.Subtract "fixture:socket", "fixture:holder_cavity"'))
    post=p['fixture_post_width_mm']
    bridge=p['fixture_bridge_thickness_mm']
    carrier_top=(p['substrate_h_mm']+p['copper_t_mm']+
                 p['fixture_reference_airgap_mm']+8.4*s+
                 p['counterface_gap_mm']+p['copper_t_mm']+p['substrate_h_mm'])
    for side, xr in [('left', (-11,-11+post)), ('right',(11-post,11))]:
        for end, yr in [('front',(fixture_cy-9,fixture_cy-9+post)),
                        ('back',(fixture_cy+9-post,fixture_cy+9))]:
            name=side+'_'+end+'_post'
            blocks.append(('PLA bridge post '+name,base._brick(
                name,'fixture','PLA_ASSUMED',xr,yr,(zboard,carrier_top+bridge))))
    for name,xr,yr in (
        ('top_left',(-11,-9),(fixture_cy-9,fixture_cy+9)),
        ('top_right',(9,11),(fixture_cy-9,fixture_cy+9)),
        ('top_front',(-9,9),(fixture_cy-9,fixture_cy-7.5)),
        ('top_back',(-9,9),(fixture_cy+7.5,fixture_cy+9)),
    ):
        blocks.append(('PLA carrier clamp '+name,base._brick(
            name,'fixture','PLA_ASSUMED',xr,yr,(carrier_top,carrier_top+bridge))))
    collar_z0=carrier_top+bridge
    blocks.append(('PLA root centring collar',base._brick(
        'root_collar','fixture','PLA_ASSUMED',(-4.2,4.2),
        (fixture_cy-3.7,fixture_cy+3.7),(collar_z0,collar_z0+1.5))))
    blocks.append(('PLA root collar aperture',base._brick(
        'root_collar_aperture','fixture','PLA_ASSUMED',(-3.1,3.1),
        (fixture_cy-2.7,fixture_cy+2.7),(collar_z0-0.1,collar_z0+1.6))))
    blocks.append(('Open root collar aperture',
        'Solid.Subtract "fixture:root_collar", "fixture:root_collar_aperture"'))
    return blocks
