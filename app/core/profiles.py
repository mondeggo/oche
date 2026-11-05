"""Application profiles with lighting, Caller, theme and language preferences."""

import asyncio
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.api.ui import UISettings
from app.core.config import UIConfig
from app.core.errors import ConnectionProblem
from app.core.integrations.caller.service import Caller, CallerConfig
from app.core.integrations.wled.service import WLED, Identifier, TargetStyle, WLEDConfig
from app.core.storage import write_private_json


class ProfileZone(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: Identifier
    device_name: str = Field(min_length=1, max_length=80)
    target_id: Identifier
    target_name: str = Field(min_length=1, max_length=80)
    mode: Literal["segment", "pixels", "matrix"]
    style: TargetStyle


class ProfileFile(BaseModel):
    """Portable appearances and Caller settings, without addresses or credentials."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    format: Literal["oche-profile"]
    version: Literal[1]
    name: str = Field(min_length=1, max_length=60)
    zones: list[ProfileZone] = Field(max_length=128)
    caller: CallerConfig
    appearance: UIConfig | None = None


class ProfileImport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: ProfileFile
    name: str | None = Field(default=None, min_length=1, max_length=60)


class ProfileData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    revision: str = Field(default_factory=lambda: uuid4().hex)
    wled: WLEDConfig
    callers: dict[str, CallerConfig]
    appearances: dict[str, UIConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def matching_profiles(self):
        if set(self.callers) != {profile.id for profile in self.wled.profiles}:
            raise ValueError("Every profile must have matching lighting and Caller settings")
        if self.appearances and set(self.appearances) != set(self.callers):
            raise ValueError("Every profile must have matching appearance settings")
        return self


class Profiles:
    def __init__(self, path: Path, wled: WLED, caller: Caller, ui: UISettings | None = None):
        self.path, self.wled, self.caller = path, wled, caller
        self.ui = ui
        if path.exists():
            try:
                self.data = ProfileData.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValueError, OSError, ValidationError) as exc:
                # Never fall back to old backups and overwrite newer settings.
                raise ConnectionProblem(
                    "Cannot read profiles.json. Restore a valid backup before starting Oche."
                ) from exc
            self.wled.config = self.data.wled.model_copy(deep=True)
            self.caller.config = self.data.callers[self.data.wled.active_profile].model_copy(deep=True)
            self.wled.error = self.caller.error = None
        else:
            # Initialize the authoritative profile store from the current defaults.
            self.data = ProfileData(
                wled=wled.config.model_copy(deep=True),
                callers={profile.id: caller.config.model_copy(deep=True) for profile in wled.config.profiles},
            )
        if not self.data.appearances:
            current_ui = ui.config if ui is not None else UIConfig()
            self.data.appearances = {key: current_ui.model_copy(deep=True) for key in self.data.callers}
        if ui is not None:
            ui.config = self.data.appearances[self.data.wled.active_profile].model_copy(deep=True)

    @property
    def revision(self) -> str:
        return f'"{self.data.revision}"'

    def summary(self) -> dict:
        return {
            "active_profile": self.data.wled.active_profile,
            "profiles": [{"id": p.id, "name": p.name} for p in self.data.wled.profiles],
            "appearance": self.data.appearances[self.data.wled.active_profile].model_dump(),
        }

    async def _commit(self, data: ProfileData, *, update_caller: bool = False) -> None:
        data = ProfileData.model_validate(data.model_dump())
        WLED.sync_profiles(data.wled)
        caller = data.callers[data.wled.active_profile]
        if update_caller:
            # A blocked download switch must leave both lighting and persistence unchanged.
            self.caller.library.check_selection(caller.voice)
        data.revision = uuid4().hex
        previous = self.data
        previous_ui = self.ui.config.model_copy(deep=True) if self.ui is not None else None
        appearance = data.appearances[data.wled.active_profile]
        update_wled = data.wled != previous.wled or self.wled.error is not None
        written = False
        try:
            write_private_json(self.path, data.model_dump(mode="json"))
            written = True
            if update_wled:
                await self.wled.configure(data.wled, persist=False)
            if update_caller:
                await self.caller.configure(caller.model_copy(deep=True), persist=False)
            if self.ui is not None and appearance != previous_ui:
                await self.ui.configure(appearance)
        except (Exception, asyncio.CancelledError):

            async def restore_previous() -> None:
                if written:
                    write_private_json(self.path, previous.model_dump(mode="json"))
                if update_wled:
                    await self.wled.configure(previous.wled, persist=False)
                if update_caller:
                    await self.caller.configure(
                        previous.callers[previous.wled.active_profile].model_copy(deep=True),
                        persist=False,
                    )
                if self.ui is not None and self.ui.config != previous_ui:
                    await self.ui.configure(previous_ui)

            # Finish rollback before releasing the caller's lock, even if cancelled again.
            restore = asyncio.create_task(restore_previous(), name="profiles-rollback")
            while not restore.done():
                try:
                    await asyncio.shield(restore)
                except asyncio.CancelledError:
                    pass
            restore.result()
            raise
        self.data = data

    async def configure_wled(self, config: WLEDConfig) -> None:
        data = self.data.model_copy(deep=True)
        data.wled = config.model_copy(deep=True)
        data.callers = {
            profile.id: data.callers.get(profile.id, self.caller.config).model_copy(deep=True)
            for profile in config.profiles
        }
        data.appearances = {
            profile.id: data.appearances.get(
                profile.id, self.data.appearances[self.data.wled.active_profile]
            ).model_copy(deep=True)
            for profile in config.profiles
        }
        await self._commit(data, update_caller=config.active_profile != self.data.wled.active_profile)

    async def configure_caller(self, config: CallerConfig) -> None:
        data = self.data.model_copy(deep=True)
        data.callers[data.wled.active_profile] = config.model_copy(deep=True)
        await self._commit(data, update_caller=True)

    async def configure_ui(self, update: UIConfig) -> None:
        data = self.data.model_copy(deep=True)
        current = data.appearances[data.wled.active_profile]
        data.appearances[data.wled.active_profile] = UIConfig.model_validate(
            {
                **current.model_dump(),
                **update.model_dump(exclude_unset=True),
            }
        )
        await self._commit(data)

    async def create(self, name: str, source: Literal["current", "blank"] = "current") -> dict:
        data = self.data.model_copy(deep=True)
        data.wled = self.wled.create_profile_config(name, source)
        data.callers[data.wled.active_profile] = (
            CallerConfig() if source == "blank" else self.caller.config.model_copy(deep=True)
        )
        data.appearances[data.wled.active_profile] = (
            UIConfig()
            if source == "blank"
            else self.data.appearances[self.data.wled.active_profile].model_copy(deep=True)
        )
        await self._commit(data, update_caller=True)
        return self.summary()

    async def select(self, profile_id: str) -> dict:
        data = self.data.model_copy(deep=True)
        data.wled = self.wled.select_profile_config(profile_id)
        await self._commit(data, update_caller=True)
        return self.summary()

    def export(self, profile_id: str) -> ProfileFile:
        config = self.data.wled
        profile = next((p for p in config.profiles if p.id == profile_id), None)
        if profile is None:
            raise ConnectionProblem("Unknown profile.")
        return ProfileFile(
            format="oche-profile",
            version=1,
            name=profile.name,
            zones=[
                ProfileZone(
                    device_id=device.id,
                    device_name=device.name,
                    target_id=target.id,
                    target_name=target.name,
                    mode=target.mode,
                    style=profile.targets[device.id][target.id].model_copy(deep=True),
                )
                for device in config.devices
                for target in device.targets
            ],
            caller=self.data.callers[profile_id].model_copy(deep=True),
            appearance=self.data.appearances[profile_id].model_copy(deep=True),
        )

    async def import_profile(self, imported: ProfileImport) -> dict:
        source = imported.profile
        name = imported.name or source.name
        names = {p.name.casefold() for p in self.data.wled.profiles}
        original, number = name, 2
        while name.casefold() in names:
            suffix = f" ({number})"
            name = original[: 60 - len(suffix)] + suffix
            number += 1
        data = self.data.model_copy(deep=True)
        data.wled = self.wled.create_profile_config(name, "blank")
        profile = next(p for p in data.wled.profiles if p.id == data.wled.active_profile)
        local = [(d, t) for d in data.wled.devices for t in d.targets]
        used = set()
        for zone in source.zones:
            candidates = [(d, t) for d, t in local if d.id == zone.device_id and t.id == zone.target_id]
            if not candidates:
                candidates = [
                    (d, t)
                    for d, t in local
                    if d.name.casefold() == zone.device_name.casefold()
                    and t.name.casefold() == zone.target_name.casefold()
                ]
            if not candidates:
                candidates = [(d, t) for d, t in local if t.name.casefold() == zone.target_name.casefold()]
            if len(candidates) != 1 or candidates[0][1].mode != zone.mode:
                raise ConnectionProblem(
                    f'Cannot match zone "{zone.target_name}" on "{zone.device_name}". '
                    "Create a zone with the same name and mode, then import again."
                )
            device, target = candidates[0]
            key = (device.id, target.id)
            if key in used:
                raise ConnectionProblem("Multiple imported zones match the same local zone.")
            used.add(key)
            # Validate native effects against the local target mode before writing anything.
            values = target.model_dump()
            values.update(zone.style.model_dump(exclude={"matrix"}))
            values["matrix"]["appearance"] = zone.style.matrix.model_dump()
            type(target).model_validate(values)
            profile.targets[device.id][target.id] = zone.style.model_copy(deep=True)
        WLED.apply_profile(data.wled, profile)
        data.callers[profile.id] = source.caller.model_copy(deep=True)
        data.appearances[profile.id] = (
            source.appearance or self.data.appearances[self.data.wled.active_profile]
        ).model_copy(deep=True)
        await self._commit(data, update_caller=True)
        return self.summary()

    async def delete(self, profile_id: str) -> dict:
        data = self.data.model_copy(deep=True)
        data.wled = self.wled.delete_profile_config(profile_id)
        del data.callers[profile_id]
        del data.appearances[profile_id]
        await self._commit(data, update_caller=data.wled.active_profile != self.data.wled.active_profile)
        return self.summary()
