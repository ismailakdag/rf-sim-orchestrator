"""Pure-Python CST history generator for an ex-vivo tooth-sensor study.

The module only returns labelled VBA history strings.  It never imports CST,
opens a project, starts a solver, or writes a file.  Dimensions are in mm,
frequency is in GHz, and conductivity is in S/m.

The tooth is a smooth analytic phantom assembled from scaled spheres and
truncated cones.  It is not derived from a scan and is not anatomically or
dielectrically validated.  Tissue names and constants are synthetic engineering
surrogates intended for numerical sensitivity experiments only.
"""

from __future__ import annotations

from math import log, pi, sqrt
from typing import Any


# Flat on purpose: callers can make DOE cases with ``DEFAULTS | overrides``.
DEFAULTS: dict[str, Any] = {
    "topology": "single_split_ring",
    "include_resonators": True,
    "include_tooth": True,
    "include_lesion": True,
    "ring_scale": 1.0,
    "coupling_gap_mm": 0.35,
    "sensing_gap_mm": 0.80,
    "ring_trace_mm": 1.20,
    "secondary_scale": 0.62,
    "secondary_offset_x_mm": 0.60,
    "tooth_scale": 1.0,
    # Dimensionless morphology factors.  They let the synthetic study vary
    # crown proportions, cusps and root independently without changing the
    # sensor or the nominal crown height.
    "crown_scale_x": 1.0,
    "crown_scale_y": 1.0,
    "cusp_scale": 1.0,
    "root_width_scale": 1.0,
    "root_length_scale": 1.0,
    "tooth_offset_x_mm": 0.0,
    "tooth_offset_y_mm": 0.0,
    "airgap_mm": 0.20,
    # Lesion depth is the penetration of a spherical cap from the lowest crown
    # surface.  The sphere is clipped to the tooth envelope before use.
    "lesion_radius_mm": 0.80,
    "lesion_depth_mm": 0.45,
    # Local crown coordinates before any whole-tooth tilt is applied.
    "lesion_offset_x_mm": 0.0,
    "lesion_offset_y_mm": 0.0,
    "lesion_epsilon_r": 16.0,
    "lesion_sigma_S_per_m": 0.18,
    # Synthetic common-mode wetness nuisance: -1 (drier) ... +1 (wetter).
    "hydration": 0.0,
    "board_x_mm": 54.0,
    "board_y_mm": 42.0,
    "substrate_h_mm": 0.80,
    "substrate_epsilon_r": 3.55,
    "substrate_tand": 0.0027,
    "copper_t_mm": 0.035,
    # None invokes a quasi-static 50-ohm microstrip width calculation.
    "feed_width_mm": None,
}


PARAMETER_SCHEMA: dict[str, dict[str, Any]] = {
    "topology": {"choices": ("single_split_ring", "dual_asymmetric_ring")},
    "include_resonators": {"type": "bool"},
    "include_tooth": {"type": "bool"},
    "include_lesion": {"type": "bool"},
    "ring_scale": {"unit": "1", "range": (0.75, 1.30)},
    "coupling_gap_mm": {"unit": "mm", "range": (0.15, 1.20)},
    "sensing_gap_mm": {"unit": "mm", "range": (0.30, 2.00)},
    "ring_trace_mm": {"unit": "mm", "range": (0.70, 2.00)},
    "secondary_scale": {"unit": "1", "range": (0.50, 0.70)},
    "secondary_offset_x_mm": {"unit": "mm", "range": (-1.20, 1.20)},
    "tooth_scale": {"unit": "1", "range": (0.80, 1.20)},
    "crown_scale_x": {"unit": "1", "range": (0.85, 1.15)},
    "crown_scale_y": {"unit": "1", "range": (0.85, 1.15)},
    "cusp_scale": {"unit": "1", "range": (0.85, 1.15)},
    "root_width_scale": {"unit": "1", "range": (0.80, 1.20)},
    "root_length_scale": {"unit": "1", "range": (0.85, 1.15)},
    "tooth_offset_x_mm": {"unit": "mm", "range": (-2.00, 2.00)},
    "tooth_offset_y_mm": {"unit": "mm", "range": (-2.00, 2.00)},
    "airgap_mm": {"unit": "mm", "range": (0.05, 1.00)},
    "lesion_radius_mm": {"unit": "mm", "range": (0.30, 1.40)},
    "lesion_depth_mm": {"unit": "mm", "range": (0.15, 2.50)},
    "lesion_offset_x_mm": {"unit": "mm", "range": (-4.00, 4.00)},
    "lesion_offset_y_mm": {"unit": "mm", "range": (-3.50, 3.50)},
    "lesion_epsilon_r": {"unit": "1", "range": (2.0, 40.0)},
    "lesion_sigma_S_per_m": {"unit": "S/m", "range": (0.0, 2.0)},
    "hydration": {"unit": "1", "range": (-1.0, 1.0)},
    "board_x_mm": {"unit": "mm", "range": (48.0, 70.0)},
    "board_y_mm": {"unit": "mm", "range": (38.0, 60.0)},
    "substrate_h_mm": {"unit": "mm", "range": (0.50, 1.60)},
    "substrate_epsilon_r": {"unit": "1", "range": (2.2, 6.2)},
    "substrate_tand": {"unit": "1", "range": (0.0, 0.03)},
    "copper_t_mm": {"unit": "mm", "range": (0.017, 0.070)},
    "feed_width_mm": {"unit": "mm or None", "range": (0.50, 4.50)},
}


SYNTHETIC_BASE_MATERIALS = {
    "SHELL_SYNTHETIC": {"epsilon_r": 6.0, "sigma_S_per_m": 0.03},
    "CORE_SYNTHETIC": {"epsilon_r": 10.0, "sigma_S_per_m": 0.10},
    "PULP_SYNTHETIC": {"epsilon_r": 20.0, "sigma_S_per_m": 0.30},
}


def _f(value: float) -> str:
    """Stable compact decimal representation for CST history strings."""
    if abs(value) < 5e-13:
        value = 0.0
    return format(float(value), ".12g")


def _material(
    name: str,
    epsilon_r: float,
    sigma: float,
    colour: tuple[float, float, float],
    tand: float | None = None,
) -> str:
    loss = ""
    if tand is not None:
        loss = f'''\n .TanD "{_f(tand)}"
 .TanDFreq "3.5"
 .TanDGiven "True"
 .TanDModel "ConstTanD"'''
    return f'''With Material
 .Reset
 .Name "{name}"
 .Type "Normal"
 .SetMaterialUnit "GHz", "mm"
 .Epsilon "{_f(epsilon_r)}"
 .Mu "1"
 .Kappa "{_f(sigma)}"{loss}
 .Colour "{_f(colour[0])}", "{_f(colour[1])}", "{_f(colour[2])}"
 .Create
End With'''


def _brick(
    name: str,
    component: str,
    material: str,
    x: tuple[float, float],
    y: tuple[float, float],
    z: tuple[float, float],
) -> str:
    return f'''With Brick
 .Reset
 .Name "{name}"
 .Component "{component}"
 .Material "{material}"
 .Xrange "{_f(x[0])}", "{_f(x[1])}"
 .Yrange "{_f(y[0])}", "{_f(y[1])}"
 .Zrange "{_f(z[0])}", "{_f(z[1])}"
 .Create
End With'''


def _sphere(
    name: str,
    component: str,
    material: str,
    center: tuple[float, float, float],
    radius: float,
) -> str:
    return f'''With Sphere
 .Reset
 .Name "{name}"
 .Component "{component}"
 .Material "{material}"
 .Axis "z"
 .CenterRadius "{_f(radius)}"
 .TopRadius "0"
 .BottomRadius "0"
 .Center "{_f(center[0])}", "{_f(center[1])}", "{_f(center[2])}"
 .Segments "0"
 .Create
End With'''


def _scale_shape(component: str, name: str, xyz: tuple[float, float, float]) -> str:
    return f'''With Transform
 .Reset
 .Name "{component}:{name}"
 .Origin "ShapeCenter"
 .ScaleFactor "{_f(xyz[0])}", "{_f(xyz[1])}", "{_f(xyz[2])}"
 .MultipleObjects "False"
 .GroupObjects "False"
 .Repetitions "1"
 .MultipleSelection "False"
 .Transform "Shape", "Scale"
End With'''


def _cone(
    name: str,
    component: str,
    material: str,
    xcenter: float,
    ycenter: float,
    zrange: tuple[float, float],
    bottom_radius: float,
    top_radius: float,
) -> str:
    return f'''With Cone
 .Reset
 .Name "{name}"
 .Component "{component}"
 .Material "{material}"
 .Axis "z"
 .BottomRadius "{_f(bottom_radius)}"
 .TopRadius "{_f(top_radius)}"
 .Xcenter "{_f(xcenter)}"
 .Ycenter "{_f(ycenter)}"
 .Zcenter "0"
 .Zrange "{_f(zrange[0])}", "{_f(zrange[1])}"
 .Segments "0"
 .Create
End With'''


def _discrete_port(number: int, x: float, y: float, z_strip: float) -> str:
    # Vertical integration line: top strip down to top face of ground at z=0.
    return f'''With DiscretePort
 .Reset
 .PortNumber "{number}"
 .Type "Sparameter"
 .Impedance "50"
 .SetP1 "False", "{_f(x)}", "{_f(y)}", "{_f(z_strip)}"
 .SetP2 "False", "{_f(x)}", "{_f(y)}", "0"
 .InvertDirection "False"
 .LocalCoordinates "False"
 .Monitor "True"
 .Radius "0.01"
 .Create
End With'''


def _microstrip_impedance(width: float, height: float, epsilon_r: float) -> float:
    """Quasi-static Hammerstad-style zero-thickness microstrip estimate."""
    u = width / height
    effective_epsilon = (epsilon_r + 1.0) / 2.0 + (
        (epsilon_r - 1.0) / 2.0 / sqrt(1.0 + 12.0 / u)
    )
    if u < 1.0:
        return 60.0 / sqrt(effective_epsilon) * log(8.0 / u + 0.25 * u)
    return 120.0 * pi / (
        sqrt(effective_epsilon) * (u + 1.393 + 0.667 * log(u + 1.444))
    )


def _width_for_50_ohm(height: float, epsilon_r: float) -> float:
    lo, hi = 0.05 * height, 12.0 * height
    for _ in range(80):
        mid = (lo + hi) / 2.0
        # Impedance decreases monotonically as width increases.
        if _microstrip_impedance(mid, height, epsilon_r) > 50.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _lesion_surface_metrics(p: dict[str, Any]) -> dict[str, float]:
    """Return analytic lower-crown quantities in tooth-local coordinates."""
    scale = p["tooth_scale"]
    rx, ry, rz = 4.5 * scale, 4.0 * scale, 4.2 * scale
    dx = p["lesion_offset_x_mm"]
    dy = p["lesion_offset_y_mm"]
    radial_sq = (dx / rx) ** 2 + (dy / ry) ** 2
    if radial_sq >= 1.0:
        raise ValueError("lesion x/y offset lies outside the crown ellipsoid footprint")

    half_chord = rz * sqrt(max(0.0, 1.0 - radial_sq))
    chord = 2.0 * half_chord
    if p["lesion_depth_mm"] > chord:
        raise ValueError(
            "lesion_depth_mm exceeds the local crown ellipsoid vertical chord"
        )
    cap_proxy = (
        pi
        * p["lesion_depth_mm"] ** 2
        * (p["lesion_radius_mm"] - p["lesion_depth_mm"] / 3.0)
    )
    if cap_proxy <= 0.0:
        raise ValueError("analytic planar spherical-cap proxy must be positive")
    return {
        "crown_radius_x_mm": rx,
        "crown_radius_y_mm": ry,
        "crown_radius_z_mm": rz,
        "normalized_radial_coordinate_squared": radial_sq,
        "lower_surface_z_from_tooth_bottom_mm": rz - half_chord,
        "local_vertical_chord_mm": chord,
        "planar_spherical_cap_volume_proxy_mm3": cap_proxy,
    }


def _lesion_placement(p: dict[str, Any], z_metal_top: float) -> dict[str, Any]:
    """Return pre-tilt lesion placement while keeping the tooth envelope fixed."""
    metrics = _lesion_surface_metrics(p)
    tooth_x = p["tooth_offset_x_mm"]
    tooth_y = p["_ring_top"] + p["tooth_offset_y_mm"]
    tooth_bottom_z = z_metal_top + p["airgap_mm"]
    surface_z = tooth_bottom_z + metrics["lower_surface_z_from_tooth_bottom_mm"]
    center_z = surface_z + p["lesion_depth_mm"] - p["lesion_radius_mm"]
    return {
        "local_offset_x_mm": p["lesion_offset_x_mm"],
        "local_offset_y_mm": p["lesion_offset_y_mm"],
        "tooth_center_x_mm": tooth_x,
        "tooth_center_y_mm": tooth_y,
        "tooth_bottom_z_mm": tooth_bottom_z,
        "crown_lower_surface_z_mm": surface_z,
        "lesion_center_pre_tilt_mm": [
            tooth_x + p["lesion_offset_x_mm"],
            tooth_y + p["lesion_offset_y_mm"],
            center_z,
        ],
        "nominal_penetration_depth_mm": p["lesion_depth_mm"],
        **metrics,
        "analytic_checks": {
            "centerline_inside_crown_footprint": True,
            "positive_planar_cap_proxy": True,
            "depth_within_local_vertical_chord": True,
            "actual_cst_boolean_volume_validated": False,
        },
    }


def _validated(params: dict[str, Any]) -> dict[str, Any]:
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise KeyError(f"Unknown geometry parameter(s): {sorted(unknown)}")
    p = dict(DEFAULTS)
    p.update(params)

    if p["topology"] not in PARAMETER_SCHEMA["topology"]["choices"]:
        raise ValueError(
            "topology must be 'single_split_ring' or 'dual_asymmetric_ring'"
        )
    for key in ("include_resonators", "include_tooth", "include_lesion"):
        if not isinstance(p[key], bool):
            raise TypeError(f"{key} must be bool")
    for key, spec in PARAMETER_SCHEMA.items():
        if "range" not in spec or (key == "feed_width_mm" and p[key] is None):
            continue
        try:
            value = float(p[key])
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{key} must be numeric") from exc
        lower, upper = spec["range"]
        if not lower <= value <= upper:
            raise ValueError(f"{key}={value} outside supported range [{lower}, {upper}]")
        p[key] = value

    if p["feed_width_mm"] is None:
        p["feed_width_mm"] = _width_for_50_ohm(
            p["substrate_h_mm"], p["substrate_epsilon_r"]
        )
    if p["lesion_depth_mm"] > 2.0 * p["lesion_radius_mm"]:
        raise ValueError(
            "lesion_depth_mm must not exceed the lesion sphere diameter"
        )
    _lesion_surface_metrics(p)

    # Fixed nominal resonator is scaled, which keeps the DOE deliberately small.
    outer_x = 18.0 * p["ring_scale"]
    outer_y = 16.0 * p["ring_scale"]
    trace = p["ring_trace_mm"]
    if 2.0 * trace + p["sensing_gap_mm"] >= outer_x:
        raise ValueError("sensing gap and trace consume the full primary ring width")
    if 2.0 * trace >= outer_y:
        raise ValueError("ring_trace_mm consumes the full primary ring height")

    # Concentric secondary ring must remain inside the primary opening with a
    # positive fabrication clearance.  This is checked before any VBA is made.
    if p["topology"] == "dual_asymmetric_ring":
        secondary_x = outer_x * p["secondary_scale"]
        secondary_y = outer_y * p["secondary_scale"]
        clearance_x = (
            outer_x / 2.0
            - trace
            - (abs(p["secondary_offset_x_mm"]) + secondary_x / 2.0)
        )
        clearance_y = outer_y / 2.0 - trace - secondary_y / 2.0
        if min(clearance_x, clearance_y) < 0.20:
            raise ValueError(
                "secondary ring does not fit in primary opening with 0.20 mm clearance"
            )

    ring_center_y = 3.8
    ring_top = ring_center_y + outer_y / 2.0
    ring_bottom = ring_center_y - outer_y / 2.0
    feed_y = ring_bottom - p["coupling_gap_mm"] - p["feed_width_mm"] / 2.0
    if abs(feed_y) + p["feed_width_mm"] / 2.0 >= p["board_y_mm"] / 2.0 - 1.0:
        raise ValueError("feed line does not fit on board with 1 mm edge clearance")
    tooth_ry = 4.0 * p["tooth_scale"] * p["crown_scale_y"]
    tooth_y = ring_top + p["tooth_offset_y_mm"]
    if abs(tooth_y) + tooth_ry >= p["board_y_mm"] / 2.0 - 0.5:
        raise ValueError("tooth crown footprint does not fit on board")
    p["_outer_x"] = outer_x
    p["_outer_y"] = outer_y
    p["_ring_center_y"] = ring_center_y
    p["_ring_top"] = ring_top
    p["_feed_y"] = feed_y
    return p


def _rectangular_split_ring(
    prefix: str,
    cx: float,
    cy: float,
    outer_x: float,
    outer_y: float,
    trace: float,
    gap: float,
    z: tuple[float, float],
) -> list[tuple[str, str]]:
    """Four-sided top-split rectangular ring, returned as one united solid."""
    component = "sensor"
    xmin, xmax = cx - outer_x / 2.0, cx + outer_x / 2.0
    ymin, ymax = cy - outer_y / 2.0, cy + outer_y / 2.0
    pieces = [
        (f"{prefix}_left", (xmin, xmin + trace), (ymin, ymax)),
        (f"{prefix}_right", (xmax - trace, xmax), (ymin, ymax)),
        (f"{prefix}_bottom", (xmin + trace, xmax - trace), (ymin, ymin + trace)),
        (f"{prefix}_top_left", (xmin + trace, cx - gap / 2.0), (ymax - trace, ymax)),
        (f"{prefix}_top_right", (cx + gap / 2.0, xmax - trace), (ymax - trace, ymax)),
    ]
    blocks: list[tuple[str, str]] = []
    for name, xr, yr in pieces:
        blocks.append((f"Create {name}", _brick(name, component, "PEC", xr, yr, z)))
    for name, _, _ in pieces[1:]:
        blocks.append(
            (f"Unite {name}", f'Solid.Add "{component}:{pieces[0][0]}", "{component}:{name}"')
        )
    return blocks


def _ellipsoid_blocks(
    name: str,
    material: str,
    center: tuple[float, float, float],
    radii: tuple[float, float, float],
) -> list[tuple[str, str]]:
    # Unit sphere + documented Transform.Scale is less version-sensitive than
    # an imported/free-form surface and produces a smooth analytic solid.
    return [
        (f"Create sphere for {name}", _sphere(name, "phantom", material, center, 1.0)),
        (f"Scale {name} to ellipsoid", _scale_shape("phantom", name, radii)),
    ]


def _tooth_envelope_blocks(
    name: str,
    material: str,
    x: float,
    y: float,
    z0: float,
    scale: float,
    shrink: float = 0.0,
    crown_scale_x: float = 1.0,
    crown_scale_y: float = 1.0,
    cusp_scale: float = 1.0,
    root_width_scale: float = 1.0,
    root_length_scale: float = 1.0,
) -> tuple[list[tuple[str, str]], dict[str, float]]:
    """Build one nested crown/root envelope; ``shrink`` is radial clearance."""
    rx = 4.5 * scale * crown_scale_x - shrink
    ry = 4.0 * scale * crown_scale_y - shrink
    rz = 4.2 * scale - shrink
    if min(rx, ry, rz) <= 0.2:
        raise ValueError("tooth envelope shrink leaves no valid crown")
    crown_center_z = z0 + 4.2 * scale
    root_start = z0 + 1.45 * (4.2 * scale) + 0.30 * shrink
    root_end = z0 + 2.0 * (4.2 * scale) + 7.5 * scale * root_length_scale - shrink
    root_bottom = 2.40 * scale * root_width_scale - 0.85 * shrink
    root_top = 0.90 * scale * root_width_scale - 0.45 * shrink
    if min(root_bottom, root_top) <= 0.15 or root_end <= root_start:
        raise ValueError("tooth envelope shrink leaves no valid root")

    root_name = f"{name}_root"
    blocks = _ellipsoid_blocks(name, material, (x, y, crown_center_z), (rx, ry, rz))
    # Four rounded cusp lobes: an assumed molar-like crown, not scan anatomy.
    # Apply the same erosion to nested envelopes to retain a material shell.
    for index, (dx, dy) in enumerate(((-1.5,-1.1),(-1.5,1.1),(1.5,-1.1),(1.5,1.1))):
        cusp_name = f'{name}_cusp{index}'
        cusp_radii = (
            1.65 * scale * cusp_scale * crown_scale_x - shrink,
            1.45 * scale * cusp_scale * crown_scale_y - shrink,
            1.65 * scale * cusp_scale - shrink,
        )
        if min(cusp_radii) > 0.15:
            blocks.extend(_ellipsoid_blocks(cusp_name, material,
                (x+dx*scale*crown_scale_x,y+dy*scale*crown_scale_y,z0+1.65*scale),cusp_radii))
            blocks.append((f'Unite rounded cusp {index} for {name}',
                f'Solid.Add "phantom:{name}", "phantom:{cusp_name}"'))
    blocks.extend(
        [
            (
                f"Create cone for {name}",
                _cone(root_name, "phantom", material, x, y, (root_start, root_end), root_bottom, root_top),
            ),
            (
                f"Flatten root depth for {name}",
                _scale_shape("phantom", root_name, (1.0, 0.78, 1.0)),
            ),
            (
                f"Unite crown and root for {name}",
                f'Solid.Add "phantom:{name}", "phantom:{root_name}"',
            ),
        ]
    )
    return blocks, {
        "crown_center_z": crown_center_z,
        "crown_bottom": crown_center_z - rz,
        "crown_top": crown_center_z + rz,
        "root_end": root_end,
    }


def _pulp_blocks(
    name: str,
    material: str,
    x: float,
    y: float,
    z0: float,
    scale: float,
    crown_scale_x: float = 1.0,
    crown_scale_y: float = 1.0,
    root_width_scale: float = 1.0,
    root_length_scale: float = 1.0,
) -> tuple[list[tuple[str, str]], dict[str, float]]:
    crown_center_z = z0 + 4.75 * scale
    radii = (1.35 * scale * crown_scale_x, 1.15 * scale * crown_scale_y, 2.25 * scale)
    root_start = z0 + 6.25 * scale
    root_end = root_start + (14.1 - 6.25) * scale * root_length_scale
    root_name = f"{name}_root"
    blocks = _ellipsoid_blocks(name, material, (x, y, crown_center_z), radii)
    blocks.extend(
        [
            (
                f"Create cone for {name}",
                _cone(root_name, "phantom", material, x, y, (root_start, root_end),
                      0.68 * scale * root_width_scale, 0.25 * scale * root_width_scale),
            ),
            (f"Flatten root depth for {name}", _scale_shape("phantom", root_name, (1.0, 0.72, 1.0))),
            (f"Unite crown and root for {name}", f'Solid.Add "phantom:{name}", "phantom:{root_name}"'),
        ]
    )
    return blocks, {"crown_bottom": crown_center_z - radii[2]}


def _clipped_lesion_blocks(
    name: str,
    p: dict[str, Any],
    tooth_x: float,
    tooth_y: float,
    z0: float,
    lesion_x: float,
    lesion_y: float,
    lesion_z: float,
) -> list[tuple[str, str]]:
    clip_name = f"{name}_clip"
    blocks, _ = _tooth_envelope_blocks(
        clip_name, "LESION_SYNTHETIC", tooth_x, tooth_y, z0, p["tooth_scale"],
        crown_scale_x=p["crown_scale_x"], crown_scale_y=p["crown_scale_y"],
        cusp_scale=p["cusp_scale"], root_width_scale=p["root_width_scale"],
        root_length_scale=p["root_length_scale"],
    )
    blocks.append(
        (
            f"Create raw {name}",
            _sphere(
                name,
                "phantom",
                "LESION_SYNTHETIC",
                (lesion_x, lesion_y, lesion_z),
                p["lesion_radius_mm"],
            ),
        )
    )
    blocks.append(
        (
            f"Clip {name} to tooth envelope",
            f'Solid.Intersect "phantom:{name}", "phantom:{clip_name}"',
        )
    )
    return blocks


def _tooth_blocks(p: dict[str, Any], z_metal_top: float) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    hydration = p["hydration"]
    eps_factor = 1.0 + 0.12 * hydration
    sigma_factor = 1.0 + 0.35 * hydration
    colours = {
        "SHELL_SYNTHETIC": (0.92, 0.88, 0.68),
        "CORE_SYNTHETIC": (0.88, 0.60, 0.28),
        "PULP_SYNTHETIC": (0.75, 0.18, 0.18),
    }
    for name, base in SYNTHETIC_BASE_MATERIALS.items():
        blocks.append(
            (
                f"Synthetic material {name}",
                _material(
                    name,
                    base["epsilon_r"] * eps_factor,
                    base["sigma_S_per_m"] * sigma_factor,
                    colours[name],
                ),
            )
        )
    blocks.append(
        (
            "Synthetic lesion material",
            _material(
                "LESION_SYNTHETIC",
                p["lesion_epsilon_r"] * eps_factor,
                p["lesion_sigma_S_per_m"] * sigma_factor,
                (0.25, 0.35, 0.85),
            ),
        )
    )

    x = p["tooth_offset_x_mm"]
    y = p["_ring_top"] + p["tooth_offset_y_mm"]
    z0 = z_metal_top + p["airgap_mm"]
    scale = p["tooth_scale"]
    shell_thickness = 0.65 * scale

    outer, outer_info = _tooth_envelope_blocks(
        "shell", "SHELL_SYNTHETIC", x, y, z0, scale,
        crown_scale_x=p["crown_scale_x"], crown_scale_y=p["crown_scale_y"],
        cusp_scale=p["cusp_scale"], root_width_scale=p["root_width_scale"],
        root_length_scale=p["root_length_scale"],
    )
    blocks.extend(outer)
    inner, inner_info = _tooth_envelope_blocks(
        "shell_cavity", "CORE_SYNTHETIC", x, y, z0, scale, shell_thickness,
        crown_scale_x=p["crown_scale_x"], crown_scale_y=p["crown_scale_y"],
        cusp_scale=p["cusp_scale"], root_width_scale=p["root_width_scale"],
        root_length_scale=p["root_length_scale"],
    )
    blocks.extend(inner)
    blocks.append(
        (
            "Subtract core cavity from shell",
            'Solid.Subtract "phantom:shell", "phantom:shell_cavity"',
        )
    )

    core, _ = _tooth_envelope_blocks(
        "core", "CORE_SYNTHETIC", x, y, z0, scale, shell_thickness,
        crown_scale_x=p["crown_scale_x"], crown_scale_y=p["crown_scale_y"],
        cusp_scale=p["cusp_scale"], root_width_scale=p["root_width_scale"],
        root_length_scale=p["root_length_scale"],
    )
    blocks.extend(core)
    pulp_cavity, pulp_info = _pulp_blocks(
        "pulp_cavity", "PULP_SYNTHETIC", x, y, z0, scale,
        p["crown_scale_x"], p["crown_scale_y"], p["root_width_scale"],
        p["root_length_scale"],
    )
    blocks.extend(pulp_cavity)
    blocks.append(
        (
            "Subtract pulp cavity from core",
            'Solid.Subtract "phantom:core", "phantom:pulp_cavity"',
        )
    )
    pulp, _ = _pulp_blocks(
        "pulp", "PULP_SYNTHETIC", x, y, z0, scale,
        p["crown_scale_x"], p["crown_scale_y"], p["root_width_scale"],
        p["root_length_scale"],
    )
    blocks.extend(pulp)

    if p["include_lesion"]:
        # Put a spherical cap into the sensor-facing crown surface.  Its upper
        # extent is exactly ``lesion_depth_mm`` inside the local lower surface.
        # Preserve the frozen source's arithmetic and emitted VBA at zero offset.
        if p["lesion_offset_x_mm"] == 0.0 and p["lesion_offset_y_mm"] == 0.0:
            lesion_x = x
            lesion_y = y
            lesion_z = (
                outer_info["crown_bottom"]
                + p["lesion_depth_mm"]
                - p["lesion_radius_mm"]
            )
            lesion_top = outer_info["crown_bottom"] + p["lesion_depth_mm"]
        else:
            placement = _lesion_placement(p, z_metal_top)
            lesion_x, lesion_y, lesion_z = placement["lesion_center_pre_tilt_mm"]
            lesion_top = (
                placement["crown_lower_surface_z_mm"] + p["lesion_depth_mm"]
            )
        # One independently clipped tool per subtraction avoids relying on CST
        # retaining Boolean tools.  The clip stays at the tooth centre; only the
        # spherical lesion moves.  The last clipped copy becomes the lesion.
        affected_layers = ["shell"]
        if lesion_top > inner_info["crown_bottom"]:
            affected_layers.append("core")
        for layer_name in affected_layers:
            tool_name = f"lesion_tool_{layer_name}"
            blocks.extend(_clipped_lesion_blocks(
                tool_name, p, x, y, z0, lesion_x, lesion_y, lesion_z
            ))
            blocks.append(
                (
                    f"Subtract lesion from {layer_name}",
                    f'Solid.Subtract "phantom:{layer_name}", "phantom:{tool_name}"',
                )
            )
        if lesion_top > pulp_info["crown_bottom"]:
            blocks.extend(
                _clipped_lesion_blocks(
                    "lesion_tool_pulp", p, x, y, z0, lesion_x, lesion_y, lesion_z
                )
            )
            blocks.append(
                (
                    "Subtract lesion from pulp",
                    'Solid.Subtract "phantom:pulp", "phantom:lesion_tool_pulp"',
                )
            )
        blocks.extend(_clipped_lesion_blocks(
            "lesion", p, x, y, z0, lesion_x, lesion_y, lesion_z
        ))

    return blocks


def history(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ordered ``(label, VBA)`` blocks for one CST model.

    ``params`` is an override dictionary; omitted values come from ``DEFAULTS``.
    Unsupported keys and out-of-range values fail before any VBA is returned.
    The output includes units, materials, board, ground, feed, two 50-ohm
    vertical discrete ports, optional resonators, and an optional layered tooth.
    It deliberately contains no solver, mesh, monitor, CST connection, or I/O.
    """
    if not isinstance(params, dict):
        raise TypeError("params must be a dict")
    p = _validated(params)
    blocks: list[tuple[str, str]] = [
        (
            "Geometry units",
            '''With Units
 .SetUnit "Length", "mm"
 .SetUnit "Frequency", "GHz"
 .SetUnit "Time", "ns"
End With''',
        ),
        (
            "Assumed low-loss substrate material",
            _material(
                "SUBSTRATE_ASSUMED_ER3P55",
                p["substrate_epsilon_r"],
                0.0,
                (0.20, 0.62, 0.32),
                p["substrate_tand"],
            ),
        ),
    ]

    bx, by = p["board_x_mm"], p["board_y_mm"]
    h, copper = p["substrate_h_mm"], p["copper_t_mm"]
    z_trace = (h, h + copper)
    blocks.extend(
        [
            (
                "Substrate",
                _brick(
                    "substrate",
                    "sensor",
                    "SUBSTRATE_ASSUMED_ER3P55",
                    (-bx / 2.0, bx / 2.0),
                    (-by / 2.0, by / 2.0),
                    (0.0, h),
                ),
            ),
            (
                "Continuous ground plane",
                _brick(
                    "ground",
                    "sensor",
                    "PEC",
                    (-bx / 2.0, bx / 2.0),
                    (-by / 2.0, by / 2.0),
                    (-copper, 0.0),
                ),
            ),
            (
                "Nominal 50 ohm through microstrip",
                _brick(
                    "feed",
                    "sensor",
                    "PEC",
                    (-bx / 2.0 + 0.50, bx / 2.0 - 0.50),
                    (
                        p["_feed_y"] - p["feed_width_mm"] / 2.0,
                        p["_feed_y"] + p["feed_width_mm"] / 2.0,
                    ),
                    z_trace,
                ),
            ),
        ]
    )

    port_x = bx / 2.0 - 0.75
    blocks.append(("Port 1 strip to ground", _discrete_port(1, -port_x, p["_feed_y"], h + copper)))
    blocks.append(("Port 2 strip to ground", _discrete_port(2, port_x, p["_feed_y"], h + copper)))

    if p["include_resonators"]:
        blocks.extend(
            _rectangular_split_ring(
                "primary",
                0.0,
                p["_ring_center_y"],
                p["_outer_x"],
                p["_outer_y"],
                p["ring_trace_mm"],
                p["sensing_gap_mm"],
                z_trace,
            )
        )
        if p["topology"] == "dual_asymmetric_ring":
            blocks.extend(
                _rectangular_split_ring(
                    "secondary",
                    p["secondary_offset_x_mm"],
                    p["_ring_center_y"],
                    p["_outer_x"] * p["secondary_scale"],
                    p["_outer_y"] * p["secondary_scale"],
                    p["ring_trace_mm"],
                    0.75 * p["sensing_gap_mm"],
                    z_trace,
                )
            )

    if p["include_tooth"]:
        blocks.extend(_tooth_blocks(p, h + copper))
    return blocks


__all__ = [
    "DEFAULTS",
    "PARAMETER_SCHEMA",
    "SYNTHETIC_BASE_MATERIALS",
    "history",
]

