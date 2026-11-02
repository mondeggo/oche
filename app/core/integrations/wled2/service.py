"""Persist a visual lighting layout and compile its connections into WLED targets."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, ValidationError, model_validator

from app.core.errors import ConnectionProblem
from app.core.events import EventBus
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
    Preview,
    Target,
    WLEDConfig,
    default_effects,
)
from app.core.storage import write_private_json


class Position(Model):
    x: float = Field(default=500, ge=0, le=1000, allow_inf_nan=False)
    y: float = Field(default=350, ge=0, le=700, allow_inf_nan=False)


class Rules(Model):
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

    def compile(self) -> WLEDConfig:
        devices = []
        for controller in self.controllers:
            targets = []
            for light in self.lights:
                wire = light.connection
                if not wire or wire.controller_id != controller.id:
                    continue
                rules = light.rules.model_copy(deep=True)
                mode = (
                    "matrix"
                    if light.kind == "matrix"
                    else ("segment" if wire.whole_segment else "pixels")
                )
                if mode != "segment":
                    # Individual pixels support solid colours, not native segment effects.
                    for phase in ("ready", "takeout", "waiting", "idle"):
                        getattr(rules.phases, phase).effect = 0
                    for effect in rules.effects.values():
                        effect.effect = 0
                targets.append(
                    Target(
                        id=light.id,
                        name=light.name,
                        enabled=light.enabled,
                        segment=wire.segment,
                        mode=mode,
                        start=wire.start,
                        count=wire.count,
                        phases=rules.phases,
                        effects=rules.effects,
                        matrix=light.matrix,
                    )
                )
            devices.append(
                Device(
                    id=controller.id,
                    name=controller.name,
                    url=controller.url,
                    enabled=controller.enabled,
                    targets=targets,
                )
            )
        return WLEDConfig(
            enabled=self.enabled, devices=devices,
            run_on_cloud=self.run_on_cloud, run_on_local=self.run_on_local,
        )


class WLED2:
    def __init__(
        self,
        path: Path,
        http: httpx.AsyncClient,
        bus: EventBus,
        view: Callable,
        classic: WLED,
    ):
        self.path = path
        self.config = Layout()
        self.error: str | None = None
        try:
            if path.exists():
                self.config = Layout.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError):
            self.error = "Cannot load wled2.json. Restore a backup or save a new layout."
        if self.config.enabled and classic.config.enabled:
            # Recover safely from independently restored files or an interrupted switch.
            self.config.enabled = False
            write_private_json(path, self.config.model_dump(mode="json"))
            self.error = "WLED2 was turned off because WLED was already enabled."
        self.engine = WLED(path, http, bus, view, initial=self.config.compile(), name="WLED2")
        self.engine.peer = classic
        classic.peer = self.engine

    def start(self) -> None:
        self.engine.start()

    async def close(self) -> None:
        await self.engine.close()

    async def configure(self, layout: Layout) -> None:
        layout = Layout.model_validate(layout.model_dump())
        compiled = layout.compile()
        self.engine.check_ownership(compiled)
        # Moving objects changes only the diagram, never interrupts device automation.
        previous = self.config.compile()
        previous_engine = self.engine.config.model_copy(deep=True)
        existed = self.path.exists()
        write_private_json(self.path, layout.model_dump(mode="json"))
        try:
            if compiled != previous or self.error:
                await self.engine.configure(compiled, persist=False)
        except (Exception, asyncio.CancelledError):

            async def restore():
                if existed:
                    write_private_json(self.path, self.config.model_dump(mode="json"))
                else:
                    self.path.unlink(missing_ok=True)
                await self.engine.configure(previous_engine, persist=False)

            task = asyncio.create_task(restore())
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
            task.result()
            raise
        self.config, self.error = layout, None

    async def preview(self, preview: Preview) -> dict:
        light = next((item for item in self.config.lights if item.id == preview.target_id), None)
        if light is None or light.connection is None or light.connection.controller_id is None:
            raise ConnectionProblem("Connect this light to a controller before testing it.")
        if preview.corners and light.kind != "matrix":
            raise ConnectionProblem("Only matrix panels show corner colours.")
        return await self.engine.test(light.connection.controller_id, preview)

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
        return {**self.engine.status(), "error": self.error or self.engine.error}

    async def check_connections(self) -> None:
        """Let the status route report real controller reachability, not a cached guess."""
        await self.engine.check_connections()

    async def show_corners(self, controller_id: str, target_id: str, on: bool) -> dict:
        """Toggle a matrix panel's corner colours; the service holds them until switched off."""
        return await self.engine.show_corners(controller_id, target_id, on)
