"""End-to-end scenarios on a real Home Assistant booted by tests/harness.py."""

from __future__ import annotations

from typing import Any
from unittest import IsolatedAsyncioTestCase

from tests.harness import Harness
from tests.scenario import DEMO_AREAS, DEMO_LIGHTS

LIVING = "select.living_room_scene"
KITCHEN = "select.kitchen_scene"
BEDROOM = "select.bedroom_scene"
LIVING_LIGHTS = [
    "light.living_ceiling",
    "light.living_floor_lamp",
    "light.living_sofa_lamp",
    "light.living_tv_strip",
]


class HarnessCase(IsolatedAsyncioTestCase):
    """The demo home with Magic Lights set up, fresh for every test."""

    storage: dict[str, Any] | None = None
    settle: float | None = None

    async def asyncSetUp(self) -> None:
        self.h = Harness(
            lights=DEMO_LIGHTS,
            areas=DEMO_AREAS,
            storage=self.storage,
            settle=self.settle,
        )
        # Unlike asyncTearDown, this also runs when the setup fails.
        self.addAsyncCleanup(self.h.stop)
        await self.h.start()
        await self.h.setup_integration()
        await self.h.settle()
        self.h.clear_calls()
        self.h.events.clear()

    def state(self, entity_id: str) -> str:
        state = self.h.hass.states.get(entity_id)
        assert state is not None, entity_id
        return state.state

    def status(self, entity_id: str) -> dict[str, Any]:
        return self.h.attributes(entity_id)

    def data_of(self, entity_id: str) -> list[dict[str, Any]]:
        """Service data of every call a light received since the last clear."""
        return [call["data"] for call in self.h.calls(entity_id)]

    def ours(self, entity_id: str, since: float = 0.0) -> list[dict[str, Any]]:
        """Return the calls Magic Lights made to a light (marked contexts)."""
        return [
            call
            for call in self.h.calls(entity_id)
            if call["t"] >= since and ":mgl:" in (call["context"] or "")
        ]

    async def light_call(self, service: str, entity_id: str, **data: Any) -> None:
        """Call a light service as someone else would (an automation, the UI)."""
        await self.h.hass.services.async_call(
            "light", service, {"entity_id": entity_id, **data}, blocking=True
        )
        await self.h.idle()


def part(part_id: str, name: str, effect: str = "white", **params: Any) -> dict:
    """Return a stored part."""
    return {"id": part_id, "name": name, "effect": effect, "params": params}


def scene(scene_id: str, name: str, *layers: tuple[Any, str], **kw: Any) -> dict:
    """Return a stored scene; each layer is (lights, part id)."""
    return {
        "id": scene_id,
        "name": name,
        "icon": None,
        "layers": [{"lights": lights, "part": part_id} for lights, part_id in layers],
        "others": "off",
        "transition": None,
        **kw,
    }


async def save(
    h: Harness, target_id: str | None = None, *scenes: dict, parts: tuple = ()
) -> Any:
    """Save parts and a target's scenes in one library/save."""
    return await h.ws(
        {
            "type": "magic_lights/library/save",
            "parts": list(parts),
            "scenes": [{"target": target_id, "scene": s} for s in scenes],
        }
    )
