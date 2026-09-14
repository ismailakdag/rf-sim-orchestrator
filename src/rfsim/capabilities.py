from __future__ import annotations

import os
import re
from pathlib import Path


def detect_cst_installations(explicit_roots: list[str] | None = None) -> list[dict]:
    """Inspect only configured and conventional CST roots; never launch CST."""
    candidates: list[Path] = []
    for value in explicit_roots or []:
        if value:
            candidates.append(Path(value))
    if value := os.environ.get("CST_STUDIO_ROOT"):
        candidates.append(Path(value))
    for drive in ("C:", "D:", "E:"):
        for year in range(2024, 2028):
            candidates.extend(
                [
                    Path(f"{drive}/Program Files/CST Studio Suite {year}"),
                    Path(f"{drive}/CST Studio Suite {year}"),
                ]
            )
    found: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            root = candidate.expanduser().resolve(strict=True)
        except OSError:
            continue
        key = os.path.normcase(str(root))
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        match = re.search(r"(?:Suite\s+)?(20\d{2})", root.name)
        libraries = root / "AMD64" / "python_cst_libraries"
        design_environment = root / "CST DESIGN ENVIRONMENT.exe"
        found.append(
            {
                "root": str(root),
                "major": int(match.group(1)) if match else None,
                "python_libraries": str(libraries) if libraries.is_dir() else None,
                "design_environment": str(design_environment) if design_environment.is_file() else None,
                "python_api_available": libraries.is_dir(),
            }
        )
    return sorted(found, key=lambda item: (item["major"] or 0, item["root"]))

