"""Screenshot HA pages (panel, dashboards) from a throwaway test instance.

Boots the harness with the demo home and its scenes (tests/scenario.py),
onboards a throwaway user, logs headless Chromium in via the frontend's
auth callback and drives it over the DevTools protocol. The default
dashboard carries Magic Lights cards at /lovelace/home.

    python tests/screenshot.py OUT_DIR [NAME=]/magic_lights [NAME=]/lovelace/home ...

Each page is saved as OUT_DIR/NAME-WIDTH[-dark].png. Environment: WIDTH and
HEIGHT (comma-separated lists, paired), DARK (0, 1 or "0,1"), WAIT (seconds
per page). Browser console errors and uncaught exceptions go to stderr.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Harness, free_port  # noqa: E402
from scenario import (  # noqa: E402
    DEMO_AREAS,
    DEMO_LIGHTS,
    DEMO_SETTINGS,
    demo_scenes,
    demo_setup,
)

CHROMIUM = os.environ.get(
    "CHROMIUM",
    "/nix/store/7d79gfynv59x9lv4dd3n6haqw5kl4jcq-chromium-154.0.8037.57/bin/chromium",
)
FONT_DIRS = [
    "/nix/store/7ljx2329k06g9qw25m21zp4dzwwm91zi-noto-fonts-2026.05.01/share/fonts",
    "/nix/store/jqkd5vajcdlsg93nnnx4rwhy3jqbq230-dejavu-fonts-2.37/share/fonts/truetype",
]


CARD = "custom:magic-lights-card"
VIEWS = [
    {
        "title": "Home",
        "path": "home",
        "type": "sections",
        "max_columns": 3,
        "sections": [
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Living room"},
                {"type": CARD, "entity": "select.living_room_scene", "show_lights": True},
            ]},
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Downstairs"},
                {"type": CARD, "entity": "select.downstairs_scene"},
                {"type": CARD, "entity": "select.kitchen_scene", "show_scenes": False},
            ]},
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Elsewhere"},
                {"type": CARD, "entity": "select.bedroom_scene", "scenes": ["Relax", "Gedimmt", "Nachtlicht", "Hell"],
                 "columns": 4},
                {"type": CARD, "entity": "select.office_scene", "show_scenes": False},
            ]},
        ],
    }
]  # fmt: skip


class Browser:
    """Minimal DevTools-protocol client for one page."""

    def __init__(
        self, width: int = 1280, height: int = 900, dark: bool = False
    ) -> None:
        self.width, self.height, self.dark = width, height, dark
        self._id = 0
        self._tmp = Path(tempfile.mkdtemp(prefix="ml-chrome-"))

    async def __aenter__(self) -> Browser:
        fonts = self._tmp / "fonts.conf"
        dirs = "".join(f"<dir>{d}</dir>" for d in FONT_DIRS if Path(d).exists())
        await asyncio.to_thread(
            fonts.write_text,
            f'<?xml version="1.0"?><fontconfig>{dirs}<cachedir>{self._tmp}/fc</cachedir></fontconfig>',
        )
        port = free_port()
        self._proc = subprocess.Popen(
            [
                CHROMIUM,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self._tmp / 'profile'}",
                f"--window-size={self.width},{self.height}",
                "about:blank",
            ],
            # The dev shell's library path can carry libraries built for another glibc.
            env={
                **{k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"},
                "FONTCONFIG_FILE": str(fonts),
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._session = aiohttp.ClientSession()
        for _ in range(100):
            try:
                async with self._session.get(f"http://127.0.0.1:{port}/json") as resp:
                    pages = [p for p in await resp.json() if p["type"] == "page"]
                    if pages:
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
        self._ws = await self._session.ws_connect(
            pages[0]["webSocketDebuggerUrl"], max_msg_size=0
        )
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.view(self.width, self.height, self.dark)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._ws.close()
        await self._session.close()
        self._proc.terminate()
        await asyncio.to_thread(self._proc.wait)
        # /tmp is RAM-backed here; a profile is ~15 MB.
        await asyncio.to_thread(shutil.rmtree, self._tmp, True)

    async def view(self, width: int, height: int, dark: bool) -> None:
        """Set the viewport and colour scheme; reload pages to apply HA's theme."""
        self.width, self.height, self.dark = width, height, dark
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        await self.send(
            "Emulation.setEmulatedMedia",
            {
                "features": [
                    {
                        "name": "prefers-color-scheme",
                        "value": "dark" if dark else "light",
                    }
                ]
            },
        )

    async def login(self, base: str, tokens: dict[str, Any]) -> None:
        """Seed the frontend's token store on the instance's origin."""
        await self.goto(f"{base}/manifest.json", 0.5)
        await self.eval(
            f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))})"
        )

    async def send(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._id += 1
        msg_id = self._id
        await self._ws.send_json(
            {"id": msg_id, "method": method, "params": params or {}}
        )
        while True:
            msg = json.loads((await self._ws.receive()).data)
            if msg.get("method") == "Runtime.consoleAPICalled" and msg["params"][
                "type"
            ] in ("error", "warning"):
                args = " ".join(
                    str(a.get("value", a.get("description", "")))
                    for a in msg["params"]["args"]
                )
                sys.stderr.write(f"[console.{msg['params']['type']}] {args[:400]}\n")
            if msg.get("method") == "Runtime.exceptionThrown":
                details = msg["params"]["exceptionDetails"]
                exc = details.get("exception", {})
                # Rejections with plain objects (HA's websocket errors) only
                # describe themselves as "Object"; their preview has the fields.
                fields = {
                    p["name"]: p.get("value")
                    for p in exc.get("preview", {}).get("properties", [])
                }
                text = json.dumps(fields) if fields else exc.get("description", "")
                sys.stderr.write(f"[exception] {details['text']} {text}"[:600] + "\n")
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def goto(self, url: str, wait: float) -> None:
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait)

    async def eval(self, expression: str) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        return result.get("result", {}).get("value")

    async def screenshot(self, path: Path) -> None:
        shot = await self.send("Page.captureScreenshot", {"format": "png"})
        # HA's blocking-I/O guard watches this loop too.
        await asyncio.to_thread(path.write_bytes, base64.b64decode(shot["data"]))


async def onboard(base: str) -> dict[str, Any]:
    """Onboard a fresh instance with a throwaway owner; return frontend tokens."""
    client_id = f"{base}/"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base}/api/onboarding/users",
            json={
                "client_id": client_id,
                "name": "Test",
                "username": "test",
                "password": "test-password",
                "language": "en",
            },
        ) as resp:
            resp.raise_for_status()
            code = (await resp.json())["auth_code"]
        async with session.post(
            f"{base}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
            },
        ) as resp:
            resp.raise_for_status()
            tokens = await resp.json()
    return {
        **tokens,
        "hassUrl": base,
        "clientId": client_id,
        "expires": int((time.time() + tokens["expires_in"]) * 1000),
    }


@asynccontextmanager
async def demo_instance() -> AsyncIterator[tuple[Harness, str, dict[str, Any]]]:
    """Boot the demo home with a dashboard; yield it, its URL and login tokens."""
    # Only the user step is left open: the others add integrations that
    # fail offline (core_config) or are irrelevant here.
    onboarding = {
        "version": 4,
        "minor_version": 1,
        "key": "onboarding",
        "data": {"done": ["core_config", "analytics", "integration"]},
    }
    lovelace = {
        "version": 1,
        "minor_version": 1,
        "key": "lovelace",
        "data": {"config": {"views": VIEWS}},
    }
    async with Harness(
        lights=DEMO_LIGHTS,
        areas=DEMO_AREAS,
        extra_storage={"onboarding": onboarding, "lovelace": lovelace},
        # The frontend asks the recorder for its status on every page.
        yaml={"recorder": {}},
    ) as h:
        await demo_setup(h)
        await h.ws({"type": "magic_lights/settings/save", "settings": DEMO_SETTINGS})
        await demo_scenes(h)
        base = f"http://127.0.0.1:{h.port}"
        yield h, base, await onboard(base)


def _variants() -> list[tuple[int, int, bool]]:
    widths = [int(w) for w in os.environ.get("WIDTH", "1280").split(",")]
    heights = [int(v) for v in os.environ.get("HEIGHT", "900").split(",")]
    heights += heights[-1:] * (len(widths) - len(heights))
    darks = [d == "1" for d in os.environ.get("DARK", "0").split(",")]
    return [(w, ht, d) for d in darks for w, ht in zip(widths, heights, strict=False)]


async def main(out_dir: str, pages: list[str]) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    wait = float(os.environ.get("WAIT", "6"))
    named = [
        page.split("=", 1) if "=" in page else [page.strip("/").replace("/", "_"), page]
        for page in pages
    ]
    async with demo_instance() as (_, base, tokens), Browser() as browser:
        await browser.login(base, tokens)
        for width, height, dark in _variants():
            await browser.view(width, height, dark)
            for name, path in named:
                sys.stderr.write(f"[page] {name} {width}{' dark' if dark else ''}\n")
                await browser.goto(f"{base}{path}", wait)
                target = out / f"{name or 'root'}-{width}{'-dark' if dark else ''}.png"
                await browser.screenshot(target)
                sys.stdout.write(f"{target}\n")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
