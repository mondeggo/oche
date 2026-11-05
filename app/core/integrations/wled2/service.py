"""Visual editing for the shared WLED configuration."""

import asyncio
import hashlib
from typing import Literal

from pydantic import Field, model_validator

from app.core.errors import ConnectionProblem
from app.core.integrations.sources import PlaySources
from app.core.integrations.wled.service import (
    WLED,
    Device,
    DeviceAddress,
    DraftPreview,
    Effect,
    EffectName,
    Identifier,
    Matrix,
    Model,
    Phase,
    PhaseColours,
    Placement,
    PlayerAppearance,
    PlayerSlot,
    Position,
    Preview,
    Target,
    WLEDConfig,
    default_effects,
)


class Rules(Model):
    players: dict[PlayerSlot, PlayerAppearance] = Field(default_factory=dict)
    phases: PhaseColours = Field(default_factory=PhaseColours)
    effects: dict[EffectName, Effect] = Field(default_factory=default_effects)


class SimulationFrame(Model):
    phase: Phase = "ready"
    event: EffectName | None = None
    local_remaining: int = Field(default=501, ge=0, le=501)
    opponent_remaining: int = Field(default=501, ge=0, le=501)
    turn_score: int = Field(default=0, ge=0, le=180)
    last_dart: int = Field(default=0, ge=0, le=60)
    player: Literal[0, 1] = 0
    duration: float = Field(default=1.5, ge=0.5, le=10)


class Controller(DeviceAddress):
    device_id: Identifier | None = None
    idle_timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    id: Identifier
    name: str = Field(min_length=1, max_length=80)
    position: Position = Field(default_factory=lambda: Position(x=150, y=570))
    enabled: bool = True
    width: float = Field(default=160, ge=80, le=800, allow_inf_nan=False)
    height: float = Field(default=60, ge=40, le=600, allow_inf_nan=False)


class Connection(Model):
    controller_id: Identifier | None = None
    after_light_id: Identifier | None = None
    segment: int = Field(default=0, ge=0, le=255, strict=True)
    whole_segment: bool = True
    start: int = Field(default=0, ge=0, le=65535, strict=True)
    count: int = Field(default=60, ge=1, le=1024, strict=True)


class Corners(Model):
    target_id: Identifier
    on: bool = Field(strict=True)


class Light(Model):
    target_id: Identifier | None = None
    id: Identifier
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["strip", "ring", "matrix"]
    position: Position = Field(default_factory=Position)
    width: float = Field(default=240, ge=20, le=800, allow_inf_nan=False)
    height: float = Field(default=24, ge=12, le=600, allow_inf_nan=False)
    rotation: int = Field(default=0, ge=-180, le=180, strict=True)
    enabled: bool = True
    connection: Connection | None = None
    matrix: Matrix = Field(default_factory=lambda: Matrix(width=16, height=16))
    # One controller can drive strips and matrix panels at the same time, so game rules
    # belong to the light they light up, not to the controller.
    rules: Rules = Field(default_factory=Rules)

    @model_validator(mode="after")
    def silent_matrix_effects(self):
        """A matrix panel starts with its event effects off; they are opted into one by one.

        Only a light saved without rules is affected: a layout that carries its own effects
        keeps whatever it was saved with.
        """
        if self.kind == "matrix" and "rules" not in self.model_fields_set:
            for effect in self.rules.effects.values():
                effect.enabled = False
        return self


class Layout(Model, PlaySources):
    version: Literal[1] = 1
    enabled: bool = False
    board: Position = Field(default_factory=Position)
    controllers: list[Controller] = Field(default_factory=list, max_length=8)
    lights: list[Light] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def valid_connections(self):
        ids = [item.id for item in [*self.controllers, *self.lights]]
        if len(ids) != len(set(ids)) or "board" in ids:
            raise ValueError("Layout objects must have unique IDs; 'board' is reserved")
        controllers = {c.id for c in self.controllers}
        lights = {light.id: light for light in self.lights}
        for light in self.lights:
            if (
                light.connection
                and light.connection.controller_id is not None
                and light.connection.controller_id not in controllers
            ):
                raise ValueError(f"{light.name}: the connected controller does not exist")
            seen = {light.id}
            wire = light.connection
            while wire and wire.after_light_id:
                if wire.after_light_id in seen:
                    raise ValueError("Light wiring must not contain a cycle")
                seen.add(wire.after_light_id)
                parent = lights.get(wire.after_light_id)
                if parent is None or parent.connection is None:
                    raise ValueError("Wire lights after an existing connected light")
                if (parent.connection.controller_id, parent.connection.segment) != (
                    wire.controller_id,
                    wire.segment,
                ):
                    raise ValueError("Chained lights must use the same controller and segment")
                wire = parent.connection
        self.compile()
        return self

    def compile(self, base: WLEDConfig | None = None) -> WLEDConfig:
        """Project the editor into the one saved lighting configuration."""
        def target(light):
            wire = light.connection or Connection()
            rules = light.rules.model_copy(deep=True)
            mode = "matrix" if light.kind == "matrix" else "segment" if wire.whole_segment else "pixels"
            if mode != "segment":
                for style in [*(getattr(rules.phases, p) for p in Phase.__args__),
                              *rules.effects.values(), *rules.players.values()]:
                    style.effect = 0
            return Target(
                id=light.target_id or light.id, name=light.name, enabled=light.enabled,
                segment=wire.segment, mode=mode, start=wire.start, count=wire.count,
                phases=rules.phases, effects=rules.effects, players=rules.players, matrix=light.matrix,
                shape=light.kind,
                placement=Placement(id=light.id, position=light.position, width=light.width,
                                    height=light.height, rotation=light.rotation,
                                    after_light_id=wire.after_light_id, wired=light.connection is not None),
            )

        config = base.model_copy(deep=True) if base is not None else WLEDConfig()
        config.enabled = self.enabled
        config.run_on_cloud, config.run_on_local = self.run_on_cloud, self.run_on_local
        config.board = self.board.model_copy(deep=True)
        config.devices = [Device(
            id=c.device_id or c.id, name=c.name, url=c.url, enabled=c.enabled,
            idle_timeout_minutes=c.idle_timeout_minutes,
            placement=Placement(id=c.id, position=c.position, width=c.width, height=c.height),
            targets=[target(light) for light in self.lights
                     if light.connection and light.connection.controller_id == c.id],
        ) for c in self.controllers]
        config.unconnected_lights = [target(light) for light in self.lights
                                    if not light.connection or light.connection.controller_id is None]
        return WLEDConfig.model_validate(config.model_dump())

    @classmethod
    def from_config(cls, config: WLEDConfig):
        used = {"board"}

        def identity(preferred, key):
            result = preferred
            if result in used:
                result = "item-" + hashlib.sha256(key.encode()).hexdigest()[:24]
            used.add(result)
            return result

        controllers, lights = [], []
        for index, device in enumerate(config.devices):
            place = device.placement or Placement(position=Position(x=150 + index * 80, y=570),
                                                   width=160, height=60)
            cid = identity(place.id or device.id, "controller:" + device.id)
            controllers.append(Controller(
                id=cid, device_id=device.id if cid != device.id else None,
                name=device.name, url=device.url, enabled=device.enabled,
                idle_timeout_minutes=device.idle_timeout_minutes,
                position=place.position, width=max(80, place.width), height=max(40, place.height),
            ))
            for target in device.targets:
                lights.append(cls._light(target, cid, identity, len(lights), config.board))
        for target in config.unconnected_lights:
            lights.append(cls._light(target, None, identity, len(lights), config.board))
        # List edits may remove/reassign a light that was the upstream canvas connection.
        by_id = {light.id: light for light in lights}
        for light in lights:
            wire = light.connection
            if wire and wire.after_light_id:
                parent = by_id.get(wire.after_light_id)
                if not parent or not parent.connection or (
                    parent.connection.controller_id, parent.connection.segment
                ) != (wire.controller_id, wire.segment):
                    wire.after_light_id = None
        return cls(enabled=config.enabled, run_on_cloud=config.run_on_cloud,
                   run_on_local=config.run_on_local, board=config.board,
                   controllers=controllers, lights=lights)

    @staticmethod
    def _light(target, controller_id, identity, index, board):
        kind = "matrix" if target.mode == "matrix" else target.shape if target.shape != "matrix" else "strip"
        place = target.placement or Placement(
            position=board if kind == "ring" else Position(x=220 + index % 5 * 150, y=120 + (index // 5) % 5 * 100),
            width=310 if kind == "ring" else 160 if kind == "matrix" else 240,
            height=310 if kind == "ring" else 160 if kind == "matrix" else 24,
        )
        lid = identity(place.id or target.id, f"light:{controller_id}:{target.id}")
        return Light(
            id=lid, target_id=target.id if lid != target.id else None,
            name=target.name, kind=kind, enabled=target.enabled,
            position=place.position, width=place.width, height=place.height, rotation=place.rotation,
            connection=Connection(controller_id=controller_id, segment=target.segment,
                                  whole_segment=target.mode != "pixels", start=target.start,
                                  count=target.count, after_light_id=place.after_light_id)
                       if controller_id is not None or place.wired else None,
            matrix=target.matrix,
            rules=Rules(phases=target.phases, effects=target.effects, players=target.players),
        )


class WLED2:
    """Visual editor adapter for WLED. It owns no workers or independent configuration."""

    def __init__(self, classic: WLED, profiles):
        self.engine = classic
        self.profiles = profiles
        self.error: str | None = None

    @property
    def config(self) -> Layout:
        return Layout.from_config(self.engine.config)

    async def configure(self, layout: Layout) -> None:
        layout = Layout.model_validate(layout.model_dump())
        await self.profiles.configure_wled(layout.compile(self.engine.config))

    async def preview(self, preview: Preview) -> dict:
        light = next((item for item in self.config.lights if item.id == preview.target_id), None)
        if light is None or light.connection is None or light.connection.controller_id is None:
            raise ConnectionProblem("Connect this light to a controller before testing it.")
        if preview.corners and light.kind != "matrix":
            raise ConnectionProblem("Only matrix panels show corner colours.")
        controller = next(c for c in self.config.controllers if c.id == light.connection.controller_id)
        return await self.engine.test(controller.device_id or controller.id,
                                      preview.model_copy(update={"target_id": light.target_id or light.id}))

    async def simulate(self, frame: SimulationFrame) -> dict:
        """Preview a complete game frame without publishing synthetic game events."""
        players = [
            {"id": "local", "index": 0, "is_local": True, "remaining": frame.local_remaining},
            {
                "id": "opponent",
                "index": 1,
                "is_local": False,
                "remaining": frame.opponent_remaining,
            },
        ]
        view = {
            **frame.model_dump(),
            "players": players,
            "remaining": players[frame.player]["remaining"],
            "player": players[frame.player],
        }

        async def show(device):
            targets = [target for target in device.targets if target.enabled]
            if not device.enabled or not targets:
                return
            await self.engine.preview_draft(
                DraftPreview(
                    device=device,
                    target_id=targets[0].id,
                    phase=frame.phase,
                    event=frame.event,
                    duration=frame.duration,
                ),
                scene_view=view,
            )

        results = await asyncio.gather(
            *(show(device) for device in self.config.compile().devices), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return {"applied": True, "restored": True}

    def status(self) -> dict:
        status = self.engine.status()
        layout = self.config
        for device in status["devices"]:
            controller = next(c for c in layout.controllers if (c.device_id or c.id) == device["id"])
            device["id"] = controller.id
            mapping = {light.target_id or light.id: light.id for light in layout.lights
                       if light.connection and light.connection.controller_id == controller.id}
            device["faces"] = {mapping.get(key, key): value for key, value in device.get("faces", {}).items()}
            device["corners"] = [mapping.get(key, key) for key in device.get("corners", [])]
        return {**status, "error": self.error or self.engine.error}

    async def check_connections(self) -> None:
        """Let the status route report real controller reachability, not a cached guess."""
        await self.engine.check_connections()

    async def show_corners(self, controller_id: str, target_id: str, on: bool) -> dict:
        """Toggle a matrix panel's corner colours; the service holds them until switched off."""
        controller = next((c for c in self.config.controllers if c.id == controller_id), None)
        light = next((light for light in self.config.lights if light.id == target_id), None)
        if not controller or not light or not light.connection or light.connection.controller_id != controller_id:
            raise ConnectionProblem("Connect this light to a controller before testing it.")
        return await self.engine.show_corners(controller.device_id or controller.id,
                                              light.target_id or light.id, on)
