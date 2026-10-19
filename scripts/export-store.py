#!/usr/bin/env python3
"""Print a home's Magic Lights store, runtime and lights per area as one JSON object.

Usage: scripts/export-store.py <ha-config-dir> > home.json

Reads <ha-config-dir>/.storage and writes nothing. Every area lists the
lights Magic Lights manages there, placed and filtered by the registries as
its light index does, if any. The output holds the two Magic Lights files, light
entity ids and area names: no tokens, users or credentials. It feeds
StoredHomeTest: MAGIC_LIGHTS_V3_STORE=home.json.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

STORE = "magic_lights"
RUNTIME = "magic_lights.runtime"


def read(storage: Path, key: str) -> Any:
    path = storage / key
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def is_group(entry: dict[str, Any], devices: dict[str, dict[str, Any]]) -> bool:
    """Mirror lights.py _is_group_entry: groups are never managed lights."""
    if (
        entry.get("platform") == "group"
        or (
            entry.get("platform") == "hue"
            and entry.get("translation_key") == "hue_grouped_light"
        )
        or (
            entry.get("platform") == "deconz"
            and "-/groups/" in (entry.get("unique_id") or "")
        )
    ):
        return True
    device = devices.get(entry.get("device_id") or "")
    return (
        entry.get("platform") == "mqtt"
        and device is not None
        and device.get("manufacturer") == "Zigbee2MQTT"
        and device.get("model") == "Group"
    )


def areas(storage: Path) -> dict[str, dict[str, Any]]:
    """Return area id -> {name, lights} for every area of the registry."""
    out: dict[str, dict[str, Any]] = {
        a["id"]: {"name": a["name"], "lights": []}
        for a in read(storage, "core.area_registry")["data"]["areas"]
    }
    devices = {
        d["id"]: d for d in read(storage, "core.device_registry")["data"]["devices"]
    }
    for entry in read(storage, "core.entity_registry")["data"]["entities"]:
        if (
            not entry["entity_id"].startswith("light.")
            or entry.get("disabled_by") is not None
            or entry.get("entity_category") is not None
            or is_group(entry, devices)
        ):
            continue
        device = devices.get(entry.get("device_id") or "") or {}
        area_id = entry.get("area_id") or device.get("area_id")
        if area_id in out:
            out[area_id]["lights"].append(entry["entity_id"])
    for area in out.values():
        area["lights"].sort()
    return dict(sorted(out.items()))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(__doc__ or "")
        return 2
    storage = Path(argv[1], ".storage")
    if (store := read(storage, STORE)) is None:
        sys.stderr.write(f"no {storage / STORE}\n")
        return 1
    home = {"store": store, "runtime": read(storage, RUNTIME), "areas": areas(storage)}
    count = sum(len(a["lights"]) for a in home["areas"].values())
    sys.stderr.write(
        f"store v{store.get('version')}.{store.get('minor_version')},"
        f" {count} lights in {len(home['areas'])} areas\n"
    )
    json.dump(home, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
