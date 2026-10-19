"""Store v3 as its code ran, over the raw store: what the migrations are proven against.

A copy of v3's available_scenes, scenes_for, find_scene and resolve_scene,
checked against the v3 coordinator while that code existed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from custom_components.magic_lights.effects import BUILTIN, Registry
from custom_components.magic_lights.models import Layer, LightIndex, Part, Target
from custom_components.magic_lights.presets import load_parts

REGISTRY = Registry(BUILTIN)
BUILTINS = load_parts(REGISTRY)  # the catalogue v3 knew as presets

type Raw = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class V3Scene:
    """A stored v3 scene, or a preset; params validated as the engine ran them."""

    id: str
    name: str
    icon: str | None
    layers: tuple[Layer, ...]
    preset: str | None = None
    others: Literal["off", "keep"] = "off"
    brightness: int = 100
    transition: float | None = None

    @property
    def public_id(self) -> str:
        """The id targets know it by: an edited preset's is the preset's."""
        return self.preset or self.id

    @classmethod
    def from_raw(cls, raw: Raw) -> V3Scene:
        layers = tuple(
            Layer(
                "all" if lay["lights"] == "all" else tuple(lay["lights"]),
                lay["effect"],
                REGISTRY.get(lay["effect"]).validate(lay.get("params") or {}),
            )
            for lay in raw["layers"]
        )
        return cls(
            raw["id"],
            raw["name"],
            raw.get("icon"),
            layers,
            raw.get("preset"),
            raw.get("others", "off"),
            raw.get("brightness", 100),
            raw.get("transition"),
        )


def preset_scene(part: Part) -> V3Scene:
    return V3Scene(
        part.id, part.name, part.icon, (Layer("all", part.effect, part.params),)
    )


def fits(index: LightIndex, scene: Any, target: Target) -> bool:
    return any(index.expand(lay.lights, target) for lay in scene.layers)


def menu_of(data: Raw, target_id: str) -> list[str] | None:
    """Return a target's hand-made menu, or None for its default one."""
    return ((data.get("targets") or {}).get(target_id) or {}).get("visible_scenes")


def available(data: Raw) -> dict[str, V3Scene]:
    """Every scene by public id: presets, an edit in its place, then user scenes."""
    scenes = [V3Scene.from_raw(raw) for raw in (data.get("scenes") or {}).values()]
    edits = {s.preset: s for s in scenes if s.preset in BUILTINS}
    out = {pid: edits.get(pid) or preset_scene(p) for pid, p in BUILTINS.items()}
    out.update((s.id, s) for s in scenes if s.preset not in BUILTINS)
    return out


def fitting(data: Raw, target: Target, index: LightIndex) -> dict[str, V3Scene]:
    """Return the scenes that fit by public id; the first of a name hides others."""
    out: dict[str, V3Scene] = {}
    names: set[str] = set()
    for pid, found in available(data).items():
        if fits(index, found, target) and found.name.casefold() not in names:
            out[pid] = found
            names.add(found.name.casefold())
    return out


def scenes_for(data: Raw, target: Target, index: LightIndex) -> list[V3Scene]:
    """Return the target's menu."""
    shown = fitting(data, target, index)
    if (order := menu_of(data, target.id)) is None:
        return [s for pid, s in shown.items() if pid not in BUILTINS]
    return [shown[pid] for pid in order if pid in shown]


def find(
    data: Raw, target: Target, query: str, index: LightIndex
) -> tuple[V3Scene | None, str | None]:
    """Return the scene a call finds (also one that does not fit) and the error."""
    candidates = list(available(data).values())
    found = next((c for c in candidates if query in (c.id, c.preset)), None)
    if found is None:
        named = [c for c in candidates if c.name.casefold() == query.casefold()]
        found = next(
            (c for c in named if fits(index, c, target)), named[0] if named else None
        )
    if found is None:
        return None, "unknown_scene"
    if not fits(index, found, target):
        return found, "scene_not_adapted"
    return found, None


def resolve(data: Raw, public_id: str) -> V3Scene | None:
    return available(data).get(public_id)


def default(data: Raw, target: Target, index: LightIndex) -> V3Scene | None:
    """Return the scene a light switched on in the idle target starts."""
    chosen = ((data.get("targets") or {}).get(target.id) or {}).get("default_scene")
    if chosen is None and target.kind == "room":
        chosen = (data.get("settings") or {}).get("default_scene")
    if chosen in (None, "none"):
        return None
    found = resolve(data, chosen)
    return found if found is not None and fits(index, found, target) else None
