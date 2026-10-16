"""Activations, light ownership, and the reactions to what lights and users do."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from functools import partial
import logging
import math
from typing import TYPE_CHECKING, Any

from homeassistant.core import Context, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.ulid import ulid_now

from ..color import diverges
from ..const import DOMAIN, Priority
from ..models import (
    ActivationSnapshot,
    Command,
    Config,
    EffectLookup,
    HaLight,
    Host,
    LightIndex,
    LightsChange,
    LightState,
    RuntimeStore,
    RuntimeV1,
    Scene,
    Target,
    TargetId,
    TargetOptions,
    TargetStatus,
    available_scenes,
    ha_to_state,
    normalise,
)
from .activation import (
    Activation,
    LayerRun,
    Plan,
    build_runs,
    percent,
    plan_runs,
    render,
    seeded_rng,
)
from .preview import preview
from .sender import Sender
from .status import compute_status

if TYPE_CHECKING:
    from .watcher import Watcher

_LOGGER = logging.getLogger(__name__)

# After start, lights that come back are adopted (never turned on) this long.
ADOPTION_GRACE = 300.0
# After an override, our own in-flight frame may still turn the light on.
LATE_FRAME = 2.0
# A foreign service call classifies the flips it causes for this long.
CALL_ECHO = 2.0
_OFF = "__off__"


def salt_key(scene_id: str, target_id: TargetId) -> str:
    return f"{scene_id}|{target_id}"


def _narrowed(snapshot: ActivationSnapshot, lights: set[str]) -> ActivationSnapshot:
    return replace(
        snapshot,
        lights=tuple(e for e in snapshot.lights if e in lights),
        manual=tuple(e for e in snapshot.manual if e in lights),
    )


def _merged(snapshots: Iterable[ActivationSnapshot]) -> tuple[ActivationSnapshot, ...]:
    """One snapshot per activation, with the lights of all its snapshots."""
    out: dict[str, ActivationSnapshot] = {}
    for snapshot in snapshots:
        if (seen := out.get(snapshot.id)) is not None:
            snapshot = replace(
                seen,
                lights=tuple(sorted({*seen.lights, *snapshot.lights})),
                manual=tuple(sorted({*seen.manual, *snapshot.manual})),
            )
        out[snapshot.id] = snapshot
    return tuple(out.values())


def _keep_always(run: LayerRun, plan: Plan) -> bool:
    return True


def _keep_same_members(run: LayerRun, plan: Plan) -> bool:
    return run.members == plan.members


class Coordinator:
    """All activations; every method is a synchronous event-loop callback."""

    def __init__(
        self,
        host: Host,
        lights: LightIndex,
        effects: EffectLookup,
        sender: Sender,
        config: Config,
        presets: Mapping[str, Scene],
        runtime: RuntimeStore,
    ) -> None:
        self._host = host
        self._lights = lights
        self._effects = effects
        self._scheduler = sender.scheduler
        self._sender = sender
        self._runtime = runtime
        self.config = config
        self.presets = presets
        self.activations: dict[str, Activation] = {}
        self.by_target: dict[TargetId, str] = {}
        self.owner: dict[str, str] = {}
        self.salts: dict[str, int] = {}
        self.watcher: Watcher | None = None
        self._recent_calls: dict[str, float] = {}
        self._target_lights = self._light_sets()
        self._started = False
        self._adoption_timer: Callable[[], None] | None = None
        sender.bind(self._accepts, self.on_settled)
        self._unsub_lights = lights.subscribe(self._on_lights_change)

    @property
    def targets(self) -> Mapping[TargetId, Target]:
        return self._lights.targets

    # ---- scenes

    def scenes_for(
        self, target_id: TargetId, *, include_hidden: bool = False
    ) -> list[Scene]:
        """Scenes of a target in menu order, customised copies in place."""
        available = available_scenes(target_id, self.config.scenes, self.presets)
        target = self.targets.get(target_id)
        order = target.options.visible_scenes if target is not None else None
        if order is None:
            ids = [
                public_id
                for public_id, scene in available.items()
                # A copy is shown where its preset would be.
                if self.presets.get(public_id, scene).default_visible
            ]
        else:
            ids = [public_id for public_id in order if public_id in available]
        if include_hidden:
            ids += [public_id for public_id in available if public_id not in ids]
        return [available[public_id] for public_id in ids]

    def resolve_scene(self, target_id: TargetId, scene_id: str) -> Scene | None:
        """Return the scene a target runs for a public id (copies replace presets)."""
        return available_scenes(target_id, self.config.scenes, self.presets).get(
            scene_id
        )

    def find_scene(self, target_id: TargetId, scene: str) -> Scene:
        """Find a scene by id or case-insensitive name; raise ServiceValidationError."""
        candidates = self.scenes_for(target_id, include_hidden=True)
        if (via := self._via(target_id)) is not None and not via.draft:
            candidates.append(via.scene)
        for candidate in candidates:
            if scene in (candidate.id, candidate.preset):
                return candidate
        key = scene.casefold()
        for candidate in candidates:
            if candidate.name.casefold() == key:
                return candidate
        exists = any(
            scene in (s.id, s.preset) or s.name.casefold() == key
            for s in (*self.presets.values(), *self.config.scenes.values())
        )
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scene_not_available" if exists else "unknown_scene",
            translation_placeholders={"scene": scene, "target_id": target_id},
        )

    def _resolve_for(self, target_id: TargetId, scene_id: str) -> Scene | None:
        if (scene := self.resolve_scene(target_id, scene_id)) is not None:
            return scene
        # A zone-scoped scene picked on a room through the zone (status "via").
        stored = self.config.scenes.get(scene_id)
        return stored if stored is not None and stored.preset is None else None

    # ---- actions

    @callback
    def activate(
        self,
        target_id: TargetId,
        scene: Scene,
        *,
        brightness: float | None = None,
        dynamic: bool | None = None,
        transition: float | None = None,
        only_on: Iterable[str] | None = None,
        draft: bool = False,
        manual: Collection[str] = (),
        context: Context | None = None,
    ) -> None:
        target = self._target(target_id)
        known = set(target.lights) & self._lights.lights.keys()
        wanted = known if only_on is None else known & set(only_on)
        plans, uncovered = plan_runs(scene, target, wanted, self._lights)
        claimed = wanted if scene.others == "off" else wanted - uncovered
        if not claimed:
            return  # a scene that keeps every light of the target has nothing to run
        touched = set(claimed)
        for other in list(self.activations.values()):
            if other.target_id != target_id and (taken := other.lights & claimed):
                self._remove_lights(other, taken)
                if not other.lights:
                    self._end(other, "superseded")
        if (old := self._own(target_id)) is not None:
            touched |= old.lights
            self._end(old, "replaced", silent=True)
        scene_id = scene.public_id
        activation = Activation(
            id=ulid_now(),
            target_id=target_id,
            scene_id=scene_id,
            scene=scene,
            started=self._host.now(),
            transition=self._transition(transition, scene),
            transition_override=transition,
            adjust={} if dynamic is None else {"dynamic": dynamic},
            level=None if brightness is None else percent(brightness),
            salt=self.salts.get(salt_key(scene_id, target_id), 0),
            draft=draft,
            only_on=only_on is not None,
            lights=claimed,
            manual=claimed & set(manual),
            context=context,
        )
        build_runs(activation, plans, uncovered & claimed, self._effects)
        self._register(activation)
        self._tick(activation, "start", force=True)
        self._emit_activated(activation)
        self._touch(touched, target_id)
        self._persist()

    @callback
    def turn_off(
        self,
        target_id: TargetId,
        *,
        transition: float | None = None,
        context: Context | None = None,
    ) -> None:
        target = self._target(target_id)
        lights = set(target.lights)
        for other in list(self.activations.values()):
            if taken := other.lights & lights:
                self._remove_lights(other, taken)
                if not other.lights:
                    self._end(other, "turned_off")
        self._sender.submit_off(
            sorted(
                e
                for e in lights
                if self._sender.on_ish(e) or self._host.state(e) is None
            ),
            transition if transition is not None else self.config.settings.transition,
            parent=context,
        )
        self._host.emit({"type": "off", "target_id": target_id})
        self._touch(lights, target_id)
        self._persist()

    @callback
    def release(self, target_id: TargetId, *, context: Context | None = None) -> None:
        """End the target's own activation without sending; others go manual."""
        target = self._target(target_id)
        lights = set(target.lights)
        if (own := self._own(target_id)) is not None:
            self._end(own, "released")
        for other in self.activations.values():
            if moved := other.active & lights:
                other.manual |= moved
                self._sender.drop(moved, other.id)
                self._refresh_dynamic(other)
        self._host.emit({"type": "released", "target_id": target_id})
        self._touch(lights, target_id)
        self._persist()

    @callback
    def reclaim(
        self,
        target_id: TargetId,
        entity_id: str | None = None,
        *,
        context: Context | None = None,
    ) -> None:
        target = self._target(target_id)
        scope = {entity_id} if entity_id else set(target.lights)
        for activation in list(self.activations.values()):
            if back := activation.manual & scope:
                activation.manual -= back
                self._tick(
                    activation, "reclaim", only=back, force=True, context=context
                )
                for e in sorted(back):
                    self._emit_light("reclaimed", activation, e)
        self._touch(scope, target_id)
        self._persist()

    @callback
    def adjust_brightness(
        self,
        target_id: TargetId,
        *,
        value: float | None = None,
        step: float | None = None,
        context: Context | None = None,
    ) -> None:
        """Set the level of the brightest light; the others keep their ratio."""
        activation = self._running(target_id)
        if activation is None or (value is None and step is None):
            return
        wanted = value if value is not None else activation.brightness + (step or 0)
        activation.level = percent(wanted)
        self._tick(activation, "adjust", force=True, context=context)
        self._touch(activation.lights, target_id)
        self._persist()

    @callback
    def set_dynamic(
        self, target_id: TargetId, dynamic: bool, *, context: Context | None = None
    ) -> None:
        """Switch the motion of the scene running on a target; lights stay as they are."""
        if (activation := self._running(target_id)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_scene",
                translation_placeholders={"target_id": target_id},
            )
        activation.adjust["dynamic"] = dynamic
        self._tick(activation, "config", context=context)
        self._touch(activation.lights, target_id)
        self._persist()

    @callback
    def next_scene(
        self,
        target_id: TargetId,
        step: int = 1,
        cycle: bool = True,
        *,
        context: Context | None = None,
    ) -> None:
        menu = [s.public_id for s in self.scenes_for(target_id)]
        status = self.status(target_id)
        current = status.scene_id if status.state == "scene" else None
        seq = [*menu, _OFF]
        index = (seq.index(current) if current in seq else -1) + step
        if not 0 <= index < len(seq):
            if not cycle:
                return
            index %= len(seq)
        if seq[index] == _OFF:
            self.turn_off(target_id, context=context)
        elif (scene := self.resolve_scene(target_id, seq[index])) is not None:
            self.activate(target_id, scene, context=context)

    @callback
    def reshuffle(self, target_id: TargetId, *, context: Context | None = None) -> None:
        if (activation := self._own(target_id)) is None:
            return
        activation.salt += 1
        self.salts[salt_key(activation.scene_id, target_id)] = activation.salt
        for run in activation.runs:
            run.rng = seeded_rng(
                activation.scene_id, target_id, run.index, activation.salt
            )
            run.memory.clear()
            run.prev = {}
            run.steady = {}
        self._tick(activation, "reshuffle", context=context)
        self._persist()

    @callback
    def try_scene(
        self,
        target_id: TargetId,
        scene: Scene,
        adjust: Mapping[str, Any] | None,
        *,
        context: Context | None = None,
    ) -> None:
        """Run an unsaved scene; stop_try gives back what it took over."""
        adjust = adjust or {}
        lights = set(self._target(target_id).lights)
        previous = self._own(target_id)
        if previous is None:
            replaced: list[ActivationSnapshot] = []
        elif previous.draft:
            replaced = list(previous.replaced)
        else:
            replaced = [previous.snapshot()]
        donors = [
            other.snapshot()
            for other in self.activations.values()
            if other is not previous and other.lights & lights
        ]
        self.activate(
            target_id,
            scene,
            brightness=adjust.get("brightness"),
            dynamic=adjust.get("dynamic"),
            draft=True,
            # Lights the user took over keep their state: stopping could not restore it.
            manual={e for a in self.activations.values() for e in a.manual & lights},
            context=context,
        )
        if (draft := self._own(target_id)) is None or not draft.draft:
            return
        for before in donors:
            donor = self.activations.get(before.id)
            kept = donor.lights if donor is not None else set()
            if taken := set(before.lights) - kept:
                replaced.append(_narrowed(before, taken))
        draft.replaced = _merged(replaced)
        self._persist()

    @callback
    def stop_try(self, target_id: TargetId, *, context: Context | None = None) -> None:
        draft = self._own(target_id)
        if draft is None or not draft.draft:
            return
        if not draft.replaced:
            self.release(target_id, context=context)
            return
        self._end(draft, "replaced", silent=True)
        back: set[str] = set()
        for snapshot in draft.replaced:
            if (donor := self.activations.get(snapshot.id)) is not None:
                back |= self._give_back(donor, snapshot, context)
            elif (restored := self._restore(snapshot)) is not None:
                # Diffed against the draft's commands: only what differs is sent.
                self._tick(restored, "start", context=context)
                self._emit_activated(restored)
                back |= restored.lights
        if not back:
            self.release(target_id, context=context)
            return
        self._touch(draft.lights | back, target_id)
        self._persist()

    def preview(
        self,
        target_id: TargetId,
        scene: Scene,
        *,
        start: datetime | None = None,
        duration: float | None = None,
        samples: int | None = None,
        adjust: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Render a scene on a target without sending (PreviewFrame dicts)."""
        scene_id = scene.public_id
        return preview(
            scene,
            self._target(target_id),
            self._lights,
            self._effects,
            sun=self._host.sun(),
            start=start or self._host.now(),
            salt=self.salts.get(salt_key(scene_id, target_id), 0),
            duration=duration,
            samples=samples,
            adjust=adjust,
        )

    # ---- status

    def status(self, target_id: TargetId) -> TargetStatus:
        if (target := self.targets.get(target_id)) is None:
            return TargetStatus(target_id, "off")
        return compute_status(
            target,
            self.activations,
            self.by_target,
            self.owner,
            self._host.state,
        )

    # ---- config

    @callback
    def apply_config(self, config: Config) -> None:
        """Take a new config; running activations follow their edited scenes."""
        old, self.config = self.config, config
        for activation in list(self.activations.values()):
            if activation.id not in self.activations:
                continue
            target = self.targets.get(activation.target_id)
            if target is None:
                self._end(activation, "config")
                continue
            if activation.draft:
                # Saving the scene being tried turns the draft into the real thing.
                saved = config.scenes.get(activation.scene.id)
                if saved is None or saved == old.scenes.get(activation.scene.id):
                    continue
                activation.draft = False
                activation.replaced = ()
                activation.scene_id = saved.public_id
                scene = saved
            else:
                scene = self._resolve_for(activation.target_id, activation.scene_id)
                if scene is None:
                    self._end(activation, "config")
                    continue
            activation.transition = self._transition(
                activation.transition_override, scene
            )
            if scene == activation.scene and not activation.errors:
                continue
            activation.scene = scene
            self._replan(activation, target, keep=_keep_same_members)
            self._rerender(activation)
        self._host.status_changed(self.targets)
        self._persist()

    @callback
    def rename_light(self, old: str, new: str) -> None:
        """Carry a light over to its new entity id, as owned and as manual as it was."""

        def ids(entity_ids: Iterable[str]) -> tuple[str, ...]:
            return tuple(new if e == old else e for e in entity_ids)

        for record in (self.owner, self._recent_calls):
            if old in record:
                record[new] = record.pop(old)
        # The index has not seen the rename yet: the light must not look new.
        self._target_lights = {
            target_id: frozenset(ids(lights))
            for target_id, lights in self._target_lights.items()
        }
        for activation in self.activations.values():
            for lights in (activation.lights, activation.manual, activation.uncovered):
                if old in lights:
                    lights.remove(old)
                    lights.add(new)
            for run in activation.runs:
                run.members = tuple(sorted(ids(run.members)))
                for states in (run.prev, run.steady):
                    if old in states:
                        states[new] = states.pop(old)
            activation.replaced = tuple(
                replace(s, lights=ids(s.lights), manual=ids(s.manual))
                for s in activation.replaced
            )
        self._sender.rename(old, new)
        self._persist()

    # ---- restore

    @callback
    def restore_ownership(self, runtime: RuntimeV1 | None) -> None:
        """Rebuild activations from the runtime file; sends nothing, fires nothing."""
        if runtime is None:
            return
        self.salts = dict(runtime.salts)
        for snapshot in runtime.activations:
            if (activation := self._restore(snapshot)) is None:
                _LOGGER.info(
                    "Not restoring %s on %s: scene, target or lights are gone",
                    snapshot.scene_id,
                    snapshot.target,
                )
                continue
            activation.adopting = True

    @callback
    def start(self) -> None:
        """Adopt the restored activations' lights and start watching (at start)."""
        self._started = True
        for activation in list(self.activations.values()):
            if not activation.adopting:
                continue
            frame = self._render(activation, "adopt")
            self._adopt(activation, sorted(activation.active), frame)
            self._rearm(activation)
            self._check_all_off(activation)
        self._adoption_timer = self._host.call_later(ADOPTION_GRACE, self._end_adoption)
        if self.watcher is not None:
            self.watcher.start()
        self._host.status_changed(self.targets)
        self._persist()

    @callback
    def shutdown(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
        self._unsub_lights()
        if self._adoption_timer is not None:
            self._adoption_timer()
            self._adoption_timer = None
        for activation in self.activations.values():
            self._cancel_timer(activation)
        self._sender.shutdown()
        self._scheduler.shutdown()

    def _restore(self, snapshot: ActivationSnapshot) -> Activation | None:
        target = self.targets.get(snapshot.target)
        if target is None or snapshot.target in self.by_target:
            return None
        if (scene := self._resolve_for(snapshot.target, snapshot.scene_id)) is None:
            return None
        # Never widened, never stolen: an earlier snapshot wins an overlap.
        lights = set(snapshot.lights) & set(target.lights) & self._lights.lights.keys()
        lights -= self.owner.keys()
        plans, uncovered = plan_runs(scene, target, lights, self._lights)
        if scene.others == "keep":
            lights -= uncovered
        if not lights:
            return None
        activation = Activation(
            id=snapshot.id,
            target_id=snapshot.target,
            scene_id=snapshot.scene_id,
            scene=scene,
            started=snapshot.started,
            transition=self._transition(None, scene),
            adjust=dict(snapshot.adjust),
            salt=self.salts.get(salt_key(snapshot.scene_id, snapshot.target), 0),
            only_on=snapshot.only_on,
            lights=lights,
            manual=set(snapshot.manual) & lights,
        )
        build_runs(activation, plans, uncovered & lights, self._effects)
        self._register(activation)
        return activation

    def _adopt(
        self,
        activation: Activation,
        entity_ids: Iterable[str],
        frame: Mapping[str, LightState],
    ) -> None:
        """Take over lights as they are; never turns one on."""
        send = {}
        for e in entity_ids:
            reported = self._host.state(e)
            if reported is None or not reported.available:
                continue
            self._sender.seed(e, ha_to_state(reported))
            if activation.wants_on(e) != reported.on:
                activation.manual.add(e)
            elif (
                reported.on
                and e in frame
                and self._adopt_diverges(activation, e, reported, frame[e])
            ):
                send[e] = frame[e]
        if send:
            self._sender.submit_frame(
                activation.id,
                send,
                Priority.INTERACTIVE,
                force=True,
                parent=activation.context,
            )

    def _adopt_diverges(
        self, activation: Activation, e: str, reported: HaLight, state: LightState
    ) -> bool:
        run = activation.run_of(e)
        if run is not None and run.animating:
            return False  # resumes with its next step
        caps = self._lights.lights[e].caps
        expected = normalise(state, caps)
        if expected.gradient:
            return False
        if expected.effect:
            return ha_to_state(reported).effect != expected.effect
        return diverges(ha_to_state(reported), expected, caps)

    @callback
    def _end_adoption(self) -> None:
        self._adoption_timer = None
        for activation in list(self.activations.values()):
            activation.adopting = False
            # Lights not seen yet stop keeping it alive; with none seen there is
            # nothing to judge.
            if any(self._available(e) for e in activation.lights):
                self._check_all_off(activation)

    # ---- watcher entry points

    @callback
    def on_foreign_call(self, entity_ids: set[str], service: str, bare: bool) -> None:
        """Judge a foreign light service call that targeted these lights."""
        now = self._host.monotonic()
        for e in sorted(entity_ids):
            self._recent_calls[e] = now
            reported = self._host.state(e)
            on = reported is not None and reported.on
            turn_off = service == "turn_off" or (service == "toggle" and on)
            activation = self._activation_of(e)
            if activation is None:
                if not turn_off and bare and not on:
                    self._adopt_light(e)
            elif e in activation.manual:
                if (
                    not turn_off
                    and bare
                    and activation.wants_on(e)
                    and self._reclaim_allowed(activation, e)
                ):
                    self._rejoin(activation, e)
            elif turn_off:
                self._override(activation, e, "turn_off")
            elif not bare:
                self._override(activation, e, "changed")
            elif not activation.wants_on(e):
                self._override(activation, e, "turned_on")

    @callback
    def on_light_state(
        self, entity_id: str, old: HaLight | None, new: HaLight | None
    ) -> None:
        e = entity_id
        activation = self._activation_of(e)
        sender = self._sender
        if new is None or not new.available:
            self._touch({e})
            return
        if old is None or not old.available:
            if activation is not None and e in activation.active:
                if activation.adopting:
                    self._adopt_late(activation, e)
                else:
                    sender.rebase(e, ha_to_state(new))
                    self._tick(activation, "available", only={e})
            elif (
                activation is None
                and new.on
                and self._started
                and self._adoption_timer is None
                and sender.last_cmd_on(e) is False
            ):
                # Off before it lost power: switched on at a plain wall switch.
                # Lights that were on (a bridge restart) are left alone.
                sender.observe(e, new)
                self._adopt_light(e)
            self._touch({e})
            return
        now = self._host.monotonic()
        if old.on and not new.on:
            if sender.last_cmd_on(e) is False:
                if activation is not None:
                    self._check_all_off(activation)
            # An OFF we sent just before the current ON may still echo; the
            # ON's settle end judges the light instead.
            elif not sender.recent_off(e):
                self._flip(activation, e, new)
            self._touch({e})
            return
        if not old.on and new.on:
            if self._late_frame(activation, e, now):
                # Our frame was in flight when the user switched the light off.
                del sender.overridden_at[e]
                sender.submit_off({e}, 0)
            elif sender.last_cmd_on(e):
                sender.passed_off(e)  # ours
            elif sender.in_settle(e):
                pass  # judged once when the settle window ends
            elif now - self._recent_calls.get(e, -math.inf) < CALL_ECHO:
                sender.observe(e, new)  # the call path classified it
            else:
                self._flip(activation, e, new)
            self._touch({e})
            return
        if (
            activation is not None
            and e in activation.active
            and not sender.in_settle(e)
        ):
            self._drift_check(activation, e, new)

    @callback
    def on_settled(self, entity_id: str) -> None:
        """Judge a light once against its command when its settle window ends."""
        e = entity_id
        reported = self._host.state(e)
        cur = self._sender.commanded.get(e)
        if reported is None or not reported.available or cur is None or cur.pending:
            return
        activation = self._activation_of(e)
        if reported.on != cur.state.on:
            if self._recent_calls.get(e, -math.inf) >= cur.at:
                self._sender.observe(e, reported)  # the call path classified it
                if activation is not None:
                    self._check_all_off(activation)
            else:
                self._flip(activation, e, reported)
            self._touch({e})
            return
        if activation is None:
            return
        if reported.on and e in activation.active:
            self._drift_check(activation, e, reported)
        self._check_all_off(activation)

    # ---- reactions

    def _flip(self, activation: Activation | None, e: str, reported: HaLight) -> None:
        """React to a light switching on or off that was not our doing."""
        if activation is not None and e in activation.active:
            if not reported.on:
                self._override(activation, e, "turned_off", observed=reported)
                return
            if not activation.wants_on(e):
                self._override(activation, e, "turned_on", observed=reported)
                return
        self._sender.observe(e, reported)
        if activation is None:
            if reported.on:
                self._adopt_light(e)
        elif not reported.on:
            self._check_all_off(activation)
        elif (
            e in activation.manual
            and activation.wants_on(e)
            and self._reclaim_allowed(activation, e)
        ):
            self._rejoin(activation, e)

    def _late_frame(self, activation: Activation | None, e: str, now: float) -> bool:
        overridden = self._sender.overridden_at.get(e, -math.inf)
        return (
            now - overridden < LATE_FRAME
            and not (activation is not None and e in activation.active)
            # A foreign call after the override switched it on on purpose.
            and self._recent_calls.get(e, -math.inf) <= overridden
        )

    def _drift_check(self, activation: Activation, e: str, reported: HaLight) -> None:
        cur = self._sender.commanded.get(e)
        if cur is None or not cur.state.on or cur.state.effect or cur.state.gradient:
            return
        light = self._lights.lights.get(e)
        if light is None or not all(
            options.detect_changes for options in self._options(activation, e)
        ):
            return
        if diverges(ha_to_state(reported), cur.state, light.caps):
            self._override(activation, e, "changed")

    def _override(
        self,
        activation: Activation,
        e: str,
        reason: str,
        *,
        observed: HaLight | None = None,
    ) -> None:
        now = self._host.monotonic()
        sent = self._sender.sent.get(e)
        in_flight = (
            sent is not None
            and sent.owner is not None
            and sent.state.on
            and now - sent.at < LATE_FRAME
        )
        activation.manual.add(e)
        self._sender.drop({e}, activation.id)
        if observed is not None:
            self._sender.observe(e, observed)
        if in_flight and reason in ("turn_off", "turned_off"):
            # That frame may still switch it back on.
            self._sender.overridden_at[e] = now
        self._refresh_dynamic(activation)
        self._emit_light("override", activation, e, reason=reason)
        self._touch({e}, activation.target_id)
        self._persist()
        self._check_all_off(activation)

    def _rejoin(self, activation: Activation, e: str) -> None:
        activation.manual.discard(e)
        self._tick(activation, "turn_on", only={e}, force=True)
        self._emit_light("reclaimed", activation, e)
        self._touch({e}, activation.target_id)
        self._persist()

    def _adopt_light(self, e: str) -> None:
        """Claim a switched-on unowned light, or start its default scene."""
        for target_id in sorted(
            self._lights.targets_of(e),
            key=lambda t: (self.targets[t].kind != "room", t),
        ):
            target = self.targets[target_id]
            own = self._own(target_id)
            if own is not None and own.only_on:
                self._claim(own, target, e)
                return
            if (
                own is None
                and (default := self._default_scene(target)) is not None
                and (scene := self.resolve_scene(target_id, default)) is not None
            ):
                self.activate(target_id, scene, only_on={e})
                return

    def _claim(self, activation: Activation, target: Target, e: str) -> None:
        if (
            activation.scene.others == "keep"
            and e in plan_runs(activation.scene, target, {e}, self._lights)[1]
        ):
            return
        activation.lights.add(e)
        self.owner[e] = activation.id
        self._replan(activation, target)
        self._tick(activation, "turn_on", only={e}, force=True)
        self._touch({e}, activation.target_id)
        self._persist()

    def _adopt_late(self, activation: Activation, e: str) -> None:
        manual = set(activation.manual)
        self._adopt(activation, [e], self._render(activation, "adopt", only={e}))
        self._rearm(activation)
        if activation.manual != manual:
            self._persist()
        self._check_all_off(activation)

    def _check_all_off(self, activation: Activation) -> None:
        """End the activation when none of its lights can be on any more."""
        if activation.id not in self.activations or not activation.lights:
            return
        # A restored light not seen yet may still be on; it is adopted on report.
        if any(
            self._sender.on_ish(e) or (activation.adopting and not self._available(e))
            for e in activation.lights
        ):
            return
        self._end(activation, "all_off")

    @callback
    def _on_lights_change(self, change: LightsChange) -> None:
        for target_id in change.removed_targets:
            if (own := self._own(target_id)) is not None:
                self._end(own, "config")
        sets = self._light_sets()
        known = self._lights.lights.keys()
        changed_any = False
        for activation in list(self.activations.values()):
            if activation.id not in self.activations:
                continue
            target = self.targets.get(activation.target_id)
            if target is None:
                self._end(activation, "config")
                continue
            members = sets[target.id] & known
            gone = activation.lights - members
            added: set[str] = set()
            if self._started and not activation.only_on:
                new = members - self._target_lights.get(target.id, frozenset())
                added = new - self.owner.keys()
                if added and activation.scene.others == "keep":
                    added -= plan_runs(activation.scene, target, added, self._lights)[1]
            changed = (change.changed_lights & activation.active) - added - gone
            if not (gone or added or changed):
                continue
            changed_any = True
            if gone:
                self._remove_lights(activation, gone)
            if added:
                activation.lights |= added
                for e in added:
                    self.owner[e] = activation.id
            if not activation.lights:
                self._end(activation, "config")
                continue
            self._replan(activation, target)
            if not self._started:
                continue
            if added:
                self._tick(activation, "start", only=added, force=True)
            if changed:
                self._rerender(activation, changed)
            self._rearm(activation)
            self._check_all_off(activation)
        self._target_lights = sets
        self._scheduler.update_limits()
        if change.added_targets or change.removed_targets:
            self._host.targets_changed(change.added_targets, change.removed_targets)
        touched = change.added_targets | change.removed_targets | change.changed_targets
        for e in change.changed_lights:
            touched |= self._lights.targets_of(e)
        self._host.status_changed(touched)
        if changed_any or change.removed_targets:
            self._persist()

    # ---- bookkeeping

    def _register(self, activation: Activation) -> None:
        for e in activation.lights:
            self.owner[e] = activation.id
        self.activations[activation.id] = activation
        self.by_target[activation.target_id] = activation.id

    def _end(
        self, activation: Activation, reason: str, *, silent: bool = False
    ) -> None:
        self._cancel_timer(activation)
        self._sender.drop(activation.lights, activation.id)
        self._scheduler.set_dynamic(activation.id, ())
        for e in activation.lights:
            if self.owner.get(e) == activation.id:
                del self.owner[e]
        self.activations.pop(activation.id, None)
        if self.by_target.get(activation.target_id) == activation.id:
            del self.by_target[activation.target_id]
        if silent:
            return
        self._host.emit(
            {
                "type": "ended",
                "target_id": activation.target_id,
                "scene_id": activation.scene_id,
                "reason": reason,
            }
        )
        self._touch(activation.lights, activation.target_id)
        self._persist()

    def _remove_lights(self, activation: Activation, entity_ids: set[str]) -> None:
        activation.lights -= entity_ids
        activation.manual -= entity_ids
        activation.uncovered -= entity_ids
        for e in entity_ids:
            if self.owner.get(e) == activation.id:
                del self.owner[e]
        self._sender.drop(entity_ids, activation.id)
        for run in activation.runs:
            run.members = tuple(e for e in run.members if e in activation.lights)
        self._refresh_dynamic(activation)

    def _replan(
        self,
        activation: Activation,
        target: Target,
        keep: Callable[[LayerRun, Plan], bool] = _keep_always,
    ) -> None:
        plans, uncovered = plan_runs(
            activation.scene, target, activation.lights, self._lights
        )
        build_runs(
            activation, plans, uncovered & activation.lights, self._effects, keep=keep
        )

    def _give_back(
        self,
        activation: Activation,
        snapshot: ActivationSnapshot,
        context: Context | None,
    ) -> set[str]:
        """Return the lights of `snapshot` nobody owns; send what differs."""
        target = self.targets[activation.target_id]
        back = set(snapshot.lights) & set(target.lights) & self._lights.lights.keys()
        back -= self.owner.keys()
        if not back:
            return back
        activation.lights |= back
        activation.manual |= back & set(snapshot.manual)
        for e in back:
            self.owner[e] = activation.id
        self._replan(activation, target)
        self._tick(activation, "start", only=back, context=context)
        return back

    def _render(
        self,
        activation: Activation,
        reason: str,
        *,
        only: Collection[str] | None = None,
        due_only: bool = False,
    ) -> dict[str, LightState]:
        return render(
            activation,
            reason,
            lights=self._lights.lights,
            available=self._available,
            now=self._host.now(),
            monotonic=self._host.monotonic(),
            sun=self._host.sun(),
            min_period=self._scheduler.min_period,
            only=only,
            due_only=due_only,
        )

    def _tick(
        self,
        activation: Activation,
        reason: str,
        *,
        only: Collection[str] | None = None,
        force: bool = False,
        due_only: bool = False,
        context: Context | None = None,
    ) -> None:
        frame = self._render(activation, reason, only=only, due_only=due_only)
        if frame:
            if reason != "periodic":
                priority = Priority.INTERACTIVE
            elif any(run.effect.key == "adaptive" for run in activation.runs):
                priority = Priority.BACKGROUND
            else:
                priority = Priority.DYNAMIC
            self._sender.submit_frame(
                activation.id,
                frame,
                priority,
                force=force,
                parent=activation.context if context is None else context,
            )
        self._rearm(activation)

    def _rerender(
        self, activation: Activation, only: Collection[str] | None = None
    ) -> None:
        """Send what an edit changed; a light not adopted yet waits for its report."""
        if activation.adopting:
            only = {
                e
                for e in (activation.lights if only is None else only)
                if e in self._sender.commanded
            }
        self._tick(activation, "config", only=only)

    def _rearm(self, activation: Activation) -> None:
        self._cancel_timer(activation)
        self._refresh_dynamic(activation)
        due = min((run.due for run in activation.runs), default=math.inf)
        if due < math.inf:
            activation.timer = self._host.call_later(
                max(0.0, due - self._host.monotonic()),
                partial(self._periodic, activation.id),
            )

    @callback
    def _periodic(self, activation_id: str) -> None:
        if (activation := self.activations.get(activation_id)) is not None:
            activation.timer = None
            brightness = activation.brightness
            self._tick(activation, "periodic", due_only=True)
            if activation.brightness != brightness:  # an adaptive ramp
                self._touch(activation.lights, activation.target_id)

    @staticmethod
    def _cancel_timer(activation: Activation) -> None:
        if activation.timer is not None:
            activation.timer()
            activation.timer = None

    def _refresh_dynamic(self, activation: Activation) -> None:
        active = activation.active
        self._scheduler.set_dynamic(
            activation.id,
            {
                e
                for run in activation.runs
                if run.animating
                for e in run.members
                if e in active
            },
        )

    def _accepts(self, cmd: Command) -> bool:
        """Return whether the command's owner still drives the light."""
        if cmd.owner is None:
            return True
        activation = self.activations.get(cmd.owner)
        return (
            activation is not None
            and self.owner.get(cmd.entity_id) == cmd.owner
            and cmd.entity_id not in activation.manual
        )

    def _persist(self) -> None:
        running = [a for a in self.activations.values() if not a.draft]
        owned = {e for a in running for e in a.lights}
        # A restart while trying brings back what the drafts took over.
        taken = [
            _narrowed(snapshot, set(snapshot.lights) - owned)
            for a in self.activations.values()
            if a.draft
            for snapshot in a.replaced
        ]
        snapshots = _merged(
            [*(a.snapshot() for a in running), *(s for s in taken if s.lights)]
        )
        self._runtime.schedule_save(RuntimeV1(snapshots, dict(self.salts)))

    def _emit_activated(self, activation: Activation) -> None:
        self._host.emit(
            {
                "type": "activated",
                "target_id": activation.target_id,
                "scene_id": activation.scene_id,
            }
        )

    def _emit_light(
        self, kind: str, activation: Activation, e: str, **extra: str
    ) -> None:
        self._host.emit(
            {
                "type": kind,
                "target_id": activation.target_id,
                "scene_id": activation.scene_id,
                "entity_id": e,
                **extra,
            }
        )

    def _touch(self, entity_ids: Iterable[str], *target_ids: TargetId) -> None:
        touched = set(target_ids)
        for e in entity_ids:
            touched |= self._lights.targets_of(e)
        if touched:
            self._host.status_changed(touched)

    def _transition(self, value: float | None, scene: Scene) -> float:
        if value is not None:
            return value
        if scene.transition is not None:
            return scene.transition
        return self.config.settings.transition

    def _target(self, target_id: TargetId) -> Target:
        if (target := self.targets.get(target_id)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_target",
                translation_placeholders={"target_id": target_id},
            )
        return target

    def _own(self, target_id: TargetId) -> Activation | None:
        activation_id = self.by_target.get(target_id)
        return None if activation_id is None else self.activations.get(activation_id)

    def _activation_of(self, entity_id: str) -> Activation | None:
        activation_id = self.owner.get(entity_id)
        return None if activation_id is None else self.activations.get(activation_id)

    def _via(self, target_id: TargetId) -> Activation | None:
        via = self.status(target_id).via
        return None if via is None else self._own(via)

    def _running(self, target_id: TargetId) -> Activation | None:
        """Return the target's own activation, else the one whose scene it shows."""
        own = self._own(target_id)
        return own if own is not None else self._via(target_id)

    def _available(self, entity_id: str) -> bool:
        reported = self._host.state(entity_id)
        return reported is not None and reported.available

    def _default_scene(self, target: Target) -> str | None:
        default = target.options.default_scene
        if default is None and target.kind == "room":
            default = self.config.settings.default_scene
        return None if default in (None, "none") else default

    def _options(self, activation: Activation, e: str) -> list[TargetOptions]:
        """Options that apply to a light: its activation's target's and its rooms'."""
        ids = {activation.target_id} | self._lights.targets_of(e)
        return [
            target.options
            for target_id in ids
            if (target := self.targets.get(target_id)) is not None
            and (target.kind == "room" or target_id == activation.target_id)
        ]

    def _reclaim_allowed(self, activation: Activation, e: str) -> bool:
        return all(
            options.reclaim_on_turn_on for options in self._options(activation, e)
        )

    def _light_sets(self) -> dict[TargetId, frozenset[str]]:
        return {
            target_id: frozenset(target.lights)
            for target_id, target in self.targets.items()
        }
