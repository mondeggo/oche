import asyncio
import json
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from conftest import BOARD_ID, MATCH_ID
from fastapi.testclient import TestClient
from pydantic import ValidationError
from zeroconf import IPVersion, ServiceStateChange

from app.core.errors import ConnectionProblem
from app.core.events import Event, EventBus, EventNormalizer
from app.core.integrations.autodarts.account.auth import DeviceAuth
from app.core.integrations.autodarts.account.cloud import CloudConnection
from app.core.integrations.wled import service as wled
from app.core.integrations.wled.service import (
    WLED,
    Appearance,
    Device,
    DeviceWorker,
    DraftPreview,
    Matrix,
    Preview,
    Target,
    WLEDConfig,
    corner_pixels,
    discover_devices,
    effect_colors,
    inspect_device,
    matrix_face,
    matrix_pixels,
    parse_outputs,
    payload,
    request,
    validate_capabilities,
)
from app.core.integrations.wled.simulator import DEMO_URL
from app.core.integrations.wled.simulator import create_app as create_demo_app
from app.main import create_app


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ("!;!;;", ["Colour", None, None]),
        ("!,!;;!;1", [None, None, None]),
        ("!;,!;", [None, "Background", None]),
        ("", ["Colour", "Background", "Accent"]),
        (None, ["Colour", "Background", "Accent"]),
    ],
)
def test_effect_color_controls_follow_device_metadata(metadata, expected):
    assert effect_colors(metadata) == expected


@pytest.mark.parametrize("combined_effects", [None, [], {}, "Solid", ["Solid"]])
async def test_probe_loads_full_effect_catalogue_when_combined_response_is_incomplete(
    combined_effects,
):
    snapshot = capabilities()
    snapshot.pop("effects")
    snapshot["info"]["fxcount"] = 5
    if combined_effects is not None:
        snapshot["effects"] = combined_effects
    names = ["Solid", "Blink", "RSVD", "-", "Rainbow"]
    definitions = ["!;!;;", "!,!;!,!;;", "", "", "!;;"]
    requested = []

    def handler(request):
        requested.append(request)
        if request.url.path == "/json":
            return httpx.Response(200, json=snapshot)
        if request.url.path == "/json/eff":
            return httpx.Response(200, json=names)
        if request.url.path == "/json/fxdata":
            return httpx.Response(200, json=definitions)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        info = await inspect_device(http, device(), metadata=True)
    assert info["effects"] == names
    assert info["effect_colors"][1] == ["Colour", "Background", None]
    assert info["effect_colors"][4] == [None, None, None]
    assert sum(request.url.path == "/json/eff" for request in requested) == 1
    assert all(request.method == "GET" for request in requested)
    board = device()
    board.targets[0].phases.ready.effect = 4
    board.targets[0].phases.idle.effect = 0
    board.targets[0].effects.clear()
    validate_capabilities(board, info)
    for reserved in (2, 3):
        board.targets[0].phases.ready.effect = reserved
        with pytest.raises(ConnectionProblem, match="native effect is unavailable"):
            validate_capabilities(board, info)


async def test_worker_keeps_native_effects_valid_when_every_snapshot_omits_names():
    snapshot = capabilities()
    snapshot.pop("effects")
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/json":
            return httpx.Response(200, json=snapshot)
        if request.url.path == "/json/eff":
            return httpx.Response(200, json=["Solid", "Blink", "Breathe"])
        return httpx.Response(404)

    board = device()
    board.targets[0].phases.ready.effect = 2
    board.targets[0].effects.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        for refresh in (True, False):
            info = await worker.probe(refresh=refresh)
            assert info["effects"] == ["Solid", "Blink", "Breathe"]
    assert calls.count("/json/eff") == 2


@pytest.mark.parametrize("names", [["Solid"], ["Solid", "Blink", "RSVD"]])
async def test_complete_combined_effect_catalogue_does_not_need_an_extra_request(names):
    snapshot = capabilities()
    snapshot["effects"] = names
    snapshot["info"]["fxcount"] = len(names)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=snapshot)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        info = await inspect_device(http, device())
    assert info["effects"] == names and calls == ["/json"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, json={}),
        httpx.Response(200, json=[]),
        httpx.Response(200, json=["Solid", 1]),
        httpx.Response(200, json=["Solid", ""]),
        httpx.Response(200, json=["Solid"]),
    ],
)
async def test_missing_effect_enumeration_is_an_error_instead_of_solid_only(response):
    snapshot = capabilities()
    snapshot.pop("effects")
    snapshot["info"]["fxcount"] = 2
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, json=snapshot) if request.url.path == "/json" else response
            )
        )
    ) as http:
        with pytest.raises(ConnectionProblem, match="effect"):
            await inspect_device(http, device())


@pytest.mark.parametrize("mode", ["pixels", "matrix"])
def test_enumerating_native_effects_does_not_enable_them_for_individual_pixel_targets(mode):
    with pytest.raises(ValidationError, match="whole-segment target"):
        device(
            targets=[
                {"id": "ring", "name": "Ring", "mode": mode, "phases": {"ready": {"effect": 1}}}
            ]
        )


def controller_outputs():
    return [
        {"start": 0, "len": 60, "pin": [16], "type": 22},
        {"start": 60, "len": 30, "pin": [2], "type": 22},
    ]


@pytest.mark.parametrize("config_path", ["/json/cfg", "/cfg.json"])
async def test_probe_detects_two_gpio_outputs_with_configuration_fallback(config_path):
    requested = []

    def handler(request):
        requested.append(request)
        if request.url.path == "/json":
            return httpx.Response(200, json=capabilities())
        if request.url.path == "/json/fxdata":
            return httpx.Response(200, json=["!;!;;"])
        if request.url.path == config_path:
            return httpx.Response(
                200,
                json={
                    "hw": {"led": {"ins": controller_outputs()}},
                    "wifi": {"password": "not-for-the-public-api"},
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        info = await inspect_device(http, device(), metadata=True)
    assert [(o["pins"], o["start"], o["stop"]) for o in info["outputs"]] == [
        ([16], 0, 60),
        ([2], 60, 90),
    ]
    assert all(o["type_name"] == "WS281x RGB" for o in info["outputs"])
    assert all(r.method == "GET" for r in requested)
    assert "not-for-the-public-api" not in json.dumps(info)


async def test_output_detection_is_optional_and_never_invents_pins():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: (
                httpx.Response(200, json=capabilities())
                if r.url.path == "/json"
                else httpx.Response(403)
            )
        )
    ) as http:
        info = await inspect_device(http, device(), metadata=True)
    assert info["outputs"] == [] and len(info["segments"]) == 2
    assert parse_outputs([None, {"start": "0", "len": 1}, {"start": 0, "len": 0}]) == []
    outputs = parse_outputs([{"start": 0, "len": 10, "pin": [0, 1, 255, -1, "2"], "type": 51}])
    assert len(outputs) == 1 and outputs[0]["pins"] == [0, 1]  # A two-pin SPI bus is one output.


@pytest.mark.parametrize(
    "kind, name, mode",
    [
        (20, "WS2812 CCT", "white"),
        (22, "WS281x RGB", "color"),
        (24, "WS2811 400 kHz", "color"),
        (30, "SK6812 RGBW", "color_white"),
        (41, "PWM white", "white"),
        (42, "PWM CCT", "white"),
        (43, "PWM RGB", "color"),
        (45, "PWM RGB+CCT", "color_white"),
        (51, "APA102", "color"),
        (40, "On/off", "on_off"),
        (127, "LED type 127", "unknown"),
        (None, "LED output", "unknown"),
    ],
)
def test_output_color_modes_follow_official_bus_types(kind, name, mode):
    output = parse_outputs([{"start": 0, "len": 1, "pin": [16], "type": kind}])[0]
    assert (output["type_name"], output["color_mode"]) == (name, mode)
    network = parse_outputs([{"start": 0, "len": 30, "pin": [192, 168, 1, 20], "type": 80}])[0]
    assert network["pins"] == []  # An IP address is not four GPIO outputs.


async def test_segments_use_hardware_modes_before_virtual_color_controls():
    state = capabilities()
    # Auto-white exposes RGB controls even for a physical white output.
    state["info"]["leds"] = {"seglc": [1, 1]}
    outputs = [
        {"start": 0, "len": 16, "type": 22},
        {"start": 16, "len": 128, "type": 41},
    ]

    def handler(request):
        if request.url.path == "/json/cfg":
            return httpx.Response(200, json={"hw": {"led": {"ins": outputs}}})
        return httpx.Response(200, json=state)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        worker = DeviceWorker(device(), http, EventBus(), lambda: {})
        for refresh in (True, False):
            info = await worker.probe(refresh=refresh)
            assert [s["color_mode"] for s in info["segments"]] == ["color", "white"]


@pytest.mark.parametrize(
    "values, expected",
    [
        ([1, 6], ["color", "white"]),
        ([3, 0], ["color_white", "on_off"]),
        ([1], ["unknown", "unknown"]),
        ([True, "2"], ["unknown", "unknown"]),
    ],
)
async def test_segment_capabilities_follow_active_segment_order(values, expected):
    state = capabilities()
    state["state"]["seg"][1]["id"] = 5
    state["info"]["leds"] = {"seglc": values}
    async with httpx.AsyncClient() as http:
        info = await inspect_device(http, device(), data=state)
    assert [s["color_mode"] for s in info["segments"]] == expected


@pytest.mark.parametrize("mode", ["segment", "pixels", "matrix"])
def test_white_commands_use_neutral_rgb_and_white_without_changing_configuration(mode):
    board = device(targets=[{"id": "white", "name": "White", "segment": 1, "mode": mode}])
    before = board.model_dump()
    info = {"segments": [{"id": 1, "color_mode": "white"}]}
    appearance = Appearance(color="#208040", brightness=128)
    command = payload(board, {"phase": "ready", "remaining": 20}, {"white": appearance}, info=info)
    segment = command["seg"][0]
    assert segment["id"] == 1
    if mode == "segment":
        assert segment["col"] == [[128, 128, 128, 128]]
        assert segment["bri"] == 128
    elif mode == "pixels":
        assert segment["i"][-1] == "40404040"
    else:
        assert "40404040" in segment["i"]
    assert board.model_dump() == before
    assert payload(board, {"phase": "idle"}, {}, info=info)["seg"][0]["id"] == 1


def test_on_off_output_uses_segment_power_for_black():
    info = {"segments": [{"id": 0, "color_mode": "on_off"}]}
    command = payload(
        device(), {"phase": "ready"}, {"ring": Appearance(color="#000000")}, info=info
    )
    assert command["seg"][0]["on"] is False


@pytest.mark.parametrize("output_length, expected", [(16, "white"), (8, "color")])
async def test_partial_output_geometry_does_not_claim_white_segment(output_length, expected):
    state = capabilities()
    state["info"]["leds"] = {
        "seglc": [1, 1],
        "ins": [{"start": 0, "len": output_length, "type": 41}],
    }
    async with httpx.AsyncClient() as http:
        info = await inspect_device(http, device(), data=state)
    assert info["segments"][0]["color_mode"] == expected
    assert info["segments"][1]["color_mode"] == "color"


async def test_unsaved_white_preview_uses_hardware_metadata_and_restores(tmp_path):
    state = preview_state()
    state["info"]["leds"] = {"seglc": [1, 1]}
    sent = []

    def handler(request):
        if request.method == "POST":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/json/cfg":
            return httpx.Response(
                200, json={"hw": {"led": {"ins": [{"start": 0, "len": 16, "type": 41}]}}}
            )
        return httpx.Response(200, json=state)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        await service.preview_draft(DraftPreview(device=device(), target_id="ring", duration=0.5))
    assert sent[0]["seg"][0]["col"] == [[255, 255, 255, 255]]
    assert sent[-1]["seg"][0]["col"] == [[4, 5, 6]]
    assert not (tmp_path / "wled.json").exists()


async def test_worker_keeps_output_metadata_between_probes_and_refreshes_on_request():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return (
            httpx.Response(200, json={"hw": {"led": {"ins": controller_outputs()}}})
            if request.url.path == "/json/cfg"
            else httpx.Response(200, json=capabilities())
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        worker = DeviceWorker(device(), http, EventBus(), lambda: {})
        await worker.probe()
        paths.clear()
        assert len((await worker.probe())["outputs"]) == 2
        assert paths == ["/json"]
        await worker.probe(refresh=True)
        assert "/json/cfg" in paths


def test_second_output_commands_only_address_its_segment():
    configured = device(targets=[{"id": "strip2", "name": "GPIO 2", "segment": 1}])
    command = payload(configured, {"phase": "ready"}, {})
    assert len(command["seg"]) == 1 and command["seg"][0]["id"] == 1
    assert "start" not in command["seg"][0] and "stop" not in command["seg"][0]




def test_status_directly_checks_saved_devices_without_automation(settings):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/json/fxdata":
            return httpx.Response(200, json=["!;!;;"])
        return httpx.Response(200, json=capabilities())

    app = create_app(settings, transport=httpx.MockTransport(handler))
    values = WLEDConfig(devices=[device(url="http://board.test", targets=[])]).model_dump(
        mode="json"
    )
    with TestClient(app) as client:
        assert client.put("/api/wled", json=values).status_code == 200
        assert requests == []  # Saving a disabled configuration never contacts hardware.

        status = client.get("/api/wled/status").json()
        assert status["enabled"] is False
        board = status["devices"][0]
        # Reading the status is what asks the controller directly, without automation.
        assert board["connected"] is True and board["error"] is None
        assert board["info"]["version"] == "0.15.3"


def preview_state():
    state = capabilities()
    state["state"]["seg"][0].update(on=True, bri=77, fx=1, col=[[4, 5, 6]], frz=False)
    return state


@pytest.mark.parametrize("mode", ["segment", "pixels", "matrix"])
async def test_draft_preview_restores_and_does_not_save(tmp_path, mode):
    sent = []
    snapshot = preview_state()
    snapshot["state"]["on"] = False
    snapshot["state"]["seg"][0]["stop"] = snapshot["state"]["seg"][0]["len"] = 128

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=snapshot)
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        draft_device = device(
            enabled=False, targets=[{"id": "ring", "name": "Ring", "mode": mode, "enabled": False}]
        )
        draft_device.targets[0].phases.ready.color = "#123456"
        draft_device.targets[0].matrix.appearance.color = "#abcdef"
        result = await service.preview_draft(
            DraftPreview(device=draft_device, target_id="ring", value=180, duration=0.5)
        )
        assert result["restored"]
        assert sent[0]["on"] is True
        assert sent[-1]["on"] is False and sent[-1]["seg"][0]["col"] == [[4, 5, 6]]
        if mode == "segment":
            assert sent[1]["seg"][0]["col"] == [[18, 52, 86]]
        else:
            assert "i" in sent[1]["seg"][0]
        assert not service.path.exists() and service.config.devices == []


async def test_draft_preview_restores_on_cancellation(tmp_path):
    sent = []
    applied = asyncio.Event()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=preview_state())
        sent.append(json.loads(request.content))
        applied.set()
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        task = asyncio.create_task(
            service.preview_draft(DraftPreview(device=device(), target_id="ring"))
        )
        await asyncio.wait_for(applied.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(sent) == 2 and sent[-1]["seg"][0]["bri"] == 77


@pytest.mark.parametrize("mode", ["segment", "pixels", "matrix"])
async def test_saved_preview_wakes_and_restores_disabled_device_without_saving(tmp_path, mode):
    snapshot = preview_state()
    snapshot["state"].update(on=False, bri=0)
    snapshot["state"]["seg"][0].update(stop=128, len=128)
    sent = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=snapshot)
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        await service.configure(
            WLEDConfig(
                devices=[
                    device(
                        enabled=False,
                        targets=[
                            {
                                "id": "ring",
                                "name": "Ring",
                                "enabled": False,
                                "mode": mode,
                            }
                        ],
                    )
                ]
            )
        )
        before = service.path.read_bytes()
        result = await service.test("board", Preview(target_id="ring", duration=0.5))
        assert result == {"applied": True, "restored": True, "duration": 0.5}
        assert sent[0] == {"on": True, "bri": 128, "tt": 0}
        assert sent[-1]["on"] is False and sent[-1]["bri"] == 0
        assert sent[-1]["seg"][0]["col"] == [[4, 5, 6]]
        assert ("i" in sent[1]["seg"][0]) is (mode != "segment")
        assert service.path.read_bytes() == before
        assert not service.tasks and service.status()["devices"][0]["connected"]
        await service.close()


async def test_saved_preview_restores_power_on_cancellation(tmp_path):
    snapshot = preview_state()
    snapshot["state"]["on"] = False
    sent, applied = [], asyncio.Event()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=snapshot)
        sent.append(json.loads(request.content))
        if "seg" in sent[-1]:
            applied.set()
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        await service.configure(WLEDConfig(devices=[device()]))
        task = asyncio.create_task(service.test("board", Preview(target_id="ring")))
        await asyncio.wait_for(applied.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(sent) == 3
        assert sent[0]["on"] is True and sent[-1]["on"] is False
        await service.close()


async def test_demo_controller_reflects_real_automation(tmp_path):
    """The whole point of the demo controller: real WLED code, zero hardware."""
    async with httpx.AsyncClient(
        mounts={DEMO_URL: httpx.ASGITransport(app=create_demo_app())}
    ) as http:
        service = WLED(
            tmp_path / "wled.json",
            http,
            EventBus(),
            lambda: {"phase": "ready", "available": True},
        )
        await service.configure(WLEDConfig(enabled=True, devices=[device(url=DEMO_URL)]))
        try:
            ring = None
            async with asyncio.timeout(3):
                while True:
                    demo = (await http.get(f"{DEMO_URL}/json")).json()
                    ring = demo["state"]["seg"][0]
                    if ring["col"] == [[0, 255, 0]]:
                        break
                    await asyncio.sleep(0.01)
            assert ring["bri"] == 128  # Default "ready" phase appearance.
            assert ring["fx"] == 0
        finally:
            await service.close()


async def test_lights_off_survives_game_events_and_configuration_updates(tmp_path):
    state = preview_state()
    sent = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=state)
        body = json.loads(request.content)
        sent.append(body)
        if "on" in body:
            state["state"]["on"] = body["on"]
        return httpx.Response(200, json={"success": True})

    bus = EventBus()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(
            tmp_path / "wled.json", http, bus, lambda: {"phase": "ready", "available": True}
        )
        await service.configure(WLEDConfig(enabled=True, devices=[device()]))
        try:
            await until(lambda: bool(sent))
            assert (await service.power(False))["applied"]
            stored = service.path.read_bytes()
            bus.publish("core", "match_win", {"player": {"is_local": True}}, kind="normalized")
            await asyncio.sleep(0.35)
            assert sent[-1] == {"on": False}
            assert service.path.read_bytes() == stored
            await service.configure(service.config)
            await asyncio.sleep(0.35)
            assert sent[-1] == {"on": False}
            assert (await service.power(True, "board"))["applied"]
            await until(lambda: "seg" in sent[-1])
            assert sent[-1]["seg"][0]["col"] == [[0, 255, 0]]
        finally:
            await service.close()


@pytest.mark.parametrize("master_on", [True, False])
async def test_output_power_isolated_during_events_reconfigure_and_shutdown(tmp_path, master_on):
    state = capabilities()
    state["state"]["on"] = master_on
    state["info"]["leds"] = {
        "ins": [
            {"start": 0, "len": 16, "pin": [16], "type": 22},
            {"start": 16, "len": 128, "pin": [2], "type": 22},
        ]
    }
    sent = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=state)
        body = json.loads(request.content)
        sent.append(body)
        if "on" in body:
            state["state"]["on"] = body["on"]
        for update in body.get("seg", []):
            next(s for s in state["state"]["seg"] if s["id"] == update["id"]).update(update)
        return httpx.Response(200, json={"success": True})

    bus = EventBus()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(
            tmp_path / "wled.json", http, bus, lambda: {"phase": "ready", "available": True}
        )
        await service.configure(
            WLEDConfig(
                enabled=True,
                devices=[
                    device(
                        targets=[
                            {"id": "ring", "name": "Ring", "segment": 0},
                            {"id": "strip", "name": "Strip", "segment": 1},
                        ]
                    )
                ],
            )
        )
        try:
            if master_on:
                assert (await service.output_power("board", 1, False))["applied"]
            else:
                assert (await service.output_power("board", 0, True))["applied"]
                assert sent[-1]["on"] is True
            assert service.workers["board"].off_segments == {1}
            start = len(sent)
            bus.publish("core", "match_win", {"player": {"is_local": True}}, kind="normalized")
            await until(lambda: len(sent) > start)
            assert state["state"]["seg"][0]["on"] is True
            assert state["state"]["seg"][1]["on"] is False
            with pytest.raises(ConnectionProblem, match="Turn this output on"):
                await service.test("board", Preview(target_id="strip"))
            await service.configure(service.config)
            await asyncio.sleep(0.35)
            assert service.status()["devices"][0]["off_segments"] == [1]
            assert all(
                s.get("on") is False
                for body in sent[start:]
                for s in body.get("seg", [])
                if s["id"] == 1
            )
            assert (await service.output_power("board", 1, True))["applied"]
            assert state["state"]["seg"][1]["on"] is True
            await service.output_power("board", 0, False)
        finally:
            await service.close()
        assert state["state"]["seg"][0]["on"] is False


def test_output_power_api_rejects_shared_segments_and_failed_commands(settings):
    state = capabilities()
    state["info"]["leds"] = {
        "ins": [
            {"start": 0, "len": 8, "pin": [16], "type": 22},
            {"start": 8, "len": 136, "pin": [2], "type": 22},
        ]
    }
    posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=state)
        posts.append(request)
        return httpx.Response(200, json={"success": False})

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        client.put("/api/wled", json=WLEDConfig(devices=[device()]).model_dump(mode="json"))
        path = "/api/wled/board/outputs/0/power"
        result = client.post(path, json={"on": False})
        assert result.status_code == 409
        assert "separate" in result.text
        assert not posts
        state["info"]["leds"]["ins"][0]["len"] = 16
        state["info"]["leds"]["ins"][1].update(start=16, len=128)
        assert client.post(path, json={"on": False}).status_code == 409
        assert client.get("/api/wled/status").json()["devices"][0]["off_segments"] == []
        assert (
            client.post(
                path, json={"on": False}, headers={"Origin": "https://other.test"}
            ).status_code
            == 403
        )


async def test_removing_a_target_clears_its_ready_color(tmp_path):
    sent = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=capabilities())
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(
            tmp_path / "wled.json", http, EventBus(), lambda: {"phase": "ready", "available": True}
        )
        config = WLEDConfig(
            enabled=True,
            devices=[
                device(
                    targets=[
                        {"id": "ring", "name": "Ring"},
                        {"id": "other", "name": "Other", "segment": 1},
                    ]
                )
            ],
        )
        await service.configure(config)
        try:
            await until(lambda: bool(sent))
            config.devices[0].targets.pop()
            await service.configure(config)
            assert any(
                s.get("id") == 1 and s.get("col") == [[255, 0, 0]]
                for body in sent
                for s in body["seg"]
            )
            await until(lambda: len(sent[-1]["seg"]) == 1)
        finally:
            await service.close()






def test_blank_profile_can_be_created_before_any_devices(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/profiles", json={"name": "Fresh", "source": "blank"})
        assert response.status_code == 200
        config = client.get("/api/wled").json()
        assert config["devices"] == []
        assert config["profiles"][1]["targets"] == {}




def test_partial_update_cannot_persist_a_missing_active_profile(settings):
    with TestClient(create_app(settings)) as client:
        client.post("/api/profiles", json={"name": "Practice"})
        client.delete("/api/profiles/default")
        before = client.get("/api/wled").json()
        assert client.patch("/api/wled", json={"active_profile": "default"}).status_code == 422
        assert client.get("/api/wled").json() == before


def device(**updates):
    return Device.model_validate(
        {
            "id": "board",
            "name": "Board",
            "url": "http://wled.test",
            "targets": [{"id": "ring", "name": "Ring"}],
            **updates,
        }
    )


def capabilities():
    return {
        "info": {"name": "Test WLED", "ver": "0.15.3"},
        "state": {
            "on": True,
            "bri": 255,
            "seg": [
                {"id": 0, "start": 0, "stop": 16, "len": 16},
                {"id": 1, "start": 16, "stop": 144, "len": 128},
            ],
        },
        "effects": [
            "Solid",
            "Blink",
            "Breathe",
            "Wipe",
            "Wipe Random",
            "Random Colors",
            "Sweep",
            "Dynamic",
            "Colorloop",
            "Rainbow",
        ]
        + ["Test effect"] * 24,
    }


def event(name, **data):
    return Event(
        sequence=1,
        source="core",
        kind="normalized",
        event=name,
        data={"player": {"is_local": True}, **data},
    )


async def test_disabled_effects_never_reach_the_device():
    board = device(
        targets=[
            {
                "id": "ring",
                "name": "Ring",
                "effects": {"bull": {"color": "#ff0080", "enabled": False}},
            }
        ]
    )
    async with httpx.AsyncClient() as http:
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        worker.accept(event("throw", dart={"segment": {"number": 25, "multiplier": 2}}))
        assert not worker.overlays
        # The same rule flashes once it is switched back on.
        board.targets[0].effects["bull"].enabled = True
        worker.accept(event("throw", dart={"segment": {"number": 25, "multiplier": 2}}))
        assert worker.overlays


@pytest.mark.parametrize(
    "number, multiplier, name",
    [
        (20, 1, "single"),
        (20, 2, "double"),
        (20, 3, "triple"),
        (25, 1, "outer_bull"),
        (25, 2, "bull"),
        (0, 0, "miss"),
    ],
)
async def test_event_matrix_hit_rules_and_fallback_are_isolated(number, multiplier, name):
    board = device(
        targets=[
            {"id": "ring", "name": "Ring", "effects": {name: {"color": "#123456"}}},
            {
                "id": "ambient",
                "name": "Ambient",
                "segment": 1,
                "effects": {"throw": {"color": "#abcdef"}},
            },
            {"id": "white", "name": "White", "segment": 2, "effects": {}},
        ]
    )
    async with httpx.AsyncClient() as http:
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        hit = event("throw", dart={"segment": {"number": number, "multiplier": multiplier}})
        worker.accept(hit)
        result = worker.desired({"phase": "ready", "available": True})["seg"]
        assert result[0]["col"] == [[18, 52, 86]]
        assert result[1]["col"] == [[171, 205, 239]]
        assert result[2]["col"] == [[0, 255, 0]]
        worker.overlays.clear()
        hit.data["player"]["is_local"] = False
        worker.accept(hit)
        assert not worker.overlays
        hit.data["player"]["is_local"] = True
        hit.snapshot = True
        worker.accept(hit)
        assert not worker.overlays


@pytest.mark.parametrize(
    "number,multiplier,name,effect,duration",
    [
        (20, 1, "single", 3, 0.6),
        (20, 2, "double", 1, 0.8),
        (20, 3, "triple", 33, 1),
        (25, 1, "outer_bull", 6, 1),
        (25, 2, "bull", 9, 1.5),
        (0, 0, "miss", 12, 0.5),
        (20, None, "throw", 12, 0.6),
    ],
)
async def test_default_hit_animations_yield_to_match_celebration(
    number, multiplier, name, effect, duration
):
    board = device()
    async with httpx.AsyncClient() as http:
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        hit = event("throw", dart={"segment": {"number": number, "multiplier": multiplier}})
        worker.accept(hit)
        view = {"phase": "ready", "available": True}
        assert worker.desired(view)["seg"][0]["fx"] == effect
        assert board.targets[0].effects[name].duration == duration
        worker.accept(event("match_win"))
        worker.accept(hit)
        assert worker.desired(view)["seg"][0]["fx"] == 9
        assert worker.overlays["ring"][3] == "match_win"
    for mode in ("pixels", "matrix"):
        target = wled.Target(id="pixels", name="Pixels", mode=mode)
        assert all(item.effect == 0 for item in target.effects.values())
        assert target.effects["match_win"].duration == 10
        assert wled.Target.model_validate(target.model_dump()) == target


def test_player_colors_follow_ready_local_player_and_restore_after_takeout():
    board = device(
        targets=[
            {
                "id": "ring",
                "name": "Ring",
                "effects": {},
                "players": {
                    1: {"color": "#123456"},
                    2: {"color": "#abcdef", "name_filter": "Alice"},
                },
            }
        ]
    )
    view = {"phase": "ready", "player": {"index": 0, "name": "Bob", "is_local": True}}
    assert payload(board, view, {})["seg"][0]["col"] == [[18, 52, 86]]
    assert payload(board, {**view, "phase": "takeout"}, {})["seg"][0]["col"] == [[225, 113, 0]]
    assert payload(board, {**view, "phase": "takeout"}, {})["seg"][0]["bri"] == 191
    view["player"].update(index=3, name="ALICE Smith")
    assert payload(board, view, {})["seg"][0]["col"] == [[171, 205, 239]]
    view["player"]["is_local"] = False
    assert payload(board, view, {})["seg"][0]["col"] == [[0, 255, 0]]
    assert (
        wled.preview_appearance(board.targets[0], Preview(target_id="ring", player=2)).color
        == "#abcdef"
    )
    with pytest.raises(ValidationError):
        Preview(target_id="ring", player=11)


@pytest.mark.parametrize(
    "name",
    ["match_started", "match_ended", "manual_reset", "calibration_started", "calibration_finished"],
)
async def test_board_and_match_flow_events_do_not_require_a_player(name):
    async with httpx.AsyncClient() as http:
        board = device(
            targets=[{"id": "ring", "name": "Ring", "effects": {name: {"color": "#123456"}}}]
        )
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        worker.accept(event(name, player=None))
        assert worker.overlays["ring"][2].color == "#123456"


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/lights",
        "http://user:secret@wled.test",
        "http://wled.test/api",
        "http://wled.test?x=1",
        "http://bad host",
        "http://wled.test:99999",
    ],
)
def test_rejects_non_origin_wled_addresses(url):
    with pytest.raises(ValidationError):
        device(url=url)


def test_targets_validate_overlap_and_matrix_limits():
    a = {"id": "first", "name": "First", "mode": "pixels", "start": 0, "count": 5}
    b = {"id": "second", "name": "Second", "mode": "pixels", "start": 4, "count": 2}
    with pytest.raises(ValidationError, match="overlap"):
        device(targets=[a, b])
    valid = device(targets=[a, {**b, "start": 5}])
    assert len(valid.targets) == 2
    with pytest.raises(ValidationError, match="overlap"):
        device(targets=[{**a, "mode": "segment"}, {**b, "start": 5}])
    with pytest.raises(ValidationError):
        Matrix(width=64, height=64)
    with pytest.raises(ValidationError):
        Matrix(width=8, height=8)
    assert Matrix(width=8, height=16, rotation=90).width == 8
    with pytest.raises(ValidationError, match="once"):
        WLEDConfig(devices=[valid, valid.model_copy(update={"id": "other"})])


def test_segment_and_pixel_targets_are_scoped_and_composed():
    board = device(
        targets=[
            {"id": "first", "name": "First", "mode": "pixels", "start": 0, "count": 1},
            {"id": "second", "name": "Second", "mode": "pixels", "start": 2, "count": 2},
            {"id": "matrix", "name": "Score", "mode": "matrix", "segment": 1},
        ]
    )
    body = payload(board, {"phase": "takeout", "remaining": 180}, {})
    assert set(body) == {"seg"}  # No master power/brightness or segment geometry changes.
    assert all(segment["tt"] == 0 for segment in body["seg"])  # Pixels update crisply.
    assert body["seg"][0]["i"] == [0, 1, "A95500", 2, 4, "A95500"]
    assert len(body["seg"]) == 2 and len(body["seg"][1]["i"]) == 129
    changed = payload(board, {"phase": "takeout", "remaining": 40}, {})
    assert changed["seg"][0] == body["seg"][0]
    assert changed["seg"][1] != body["seg"][1]
    native = payload(device(), {"phase": "ready"}, {})["seg"][0]
    assert native["frz"] is False and native["col"] == [[0, 255, 0]]
    assert native["tt"] == wled.DEFAULT_TRANSITION  # Whole-segment changes may crossfade.
    assert "i" not in native


def test_corner_pixels_and_preview_mark_only_the_panel_corners():
    matrix = Matrix(width=16, height=8, serpentine=True)
    pixels = corner_pixels(matrix)
    assert len(pixels) == 128
    # Odd serpentine rows are mirrored so the colours land on the physical panel corners.
    assert pixels[0] == "00FF00"
    assert pixels[15] == "FF0000"
    assert pixels[7 * 16 + 15] == "0000FF"
    assert pixels[7 * 16] == "FFFFFF"
    assert set(pixels) == {"000000", "00FF00", "FF0000", "0000FF", "FFFFFF"}
    straight = corner_pixels(Matrix(width=16, height=8, serpentine=False))
    assert straight[7 * 16] == "0000FF" and straight[7 * 16 + 15] == "FFFFFF"

    target = Target(id="panel", name="Panel", mode="matrix", matrix=matrix)
    device = Device(id="board", name="Board", url="http://wled.test", targets=[target])
    body = payload(
        device, {"phase": "ready"}, {}, Preview(target_id="panel", corners=True, duration=1)
    )
    segment = body["seg"][0]
    assert segment["bri"] == 255 and segment["tt"] == 0
    assert segment["i"] == [0, *pixels]
    # A held corner toggle sends exactly the same frame as a corner preview.
    held = payload(device, {"phase": "ready"}, {}, corners={"panel"})["seg"][0]
    assert held == segment


def test_matrix_face_mirrors_what_the_panel_shows():
    matrix = Matrix(width=16, height=8, serpentine=True)
    target = Target(id="panel", name="Panel", mode="matrix", matrix=matrix)
    view = {"phase": "ready", "available": True, "remaining": 180, "players": []}
    painted = matrix_pixels(matrix, 180, matrix.appearance)
    rows = [painted[row * 16 : (row + 1) * 16] for row in range(8)]
    assert matrix_face(target, view, False) == [
        "".join("1" if pixel != "000000" else "." for pixel in row) for row in rows
    ]
    # While the corner toggle is on, the panel shows markers instead of the score.
    corners_only = matrix_face(target, view, True)
    assert set("".join(corners_only)) == {".", "2", "3", "4", "5"}


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_matrix_rotation_wiring_and_unknown_scores(rotation):
    matrix = Matrix(width=16, height=16, rotation=rotation, serpentine=False)
    color = Appearance(color="#ffffff", brightness=255)
    normal = matrix_pixels(matrix, 180, color)
    assert len(normal) == 256 and any(p != "000000" for p in normal)
    reference = matrix_pixels(matrix.model_copy(update={"rotation": 0}), 180, color)
    grid = [reference[row * 16 : (row + 1) * 16] for row in range(16)]
    for _ in range(rotation // 90):
        grid = list(zip(*grid[::-1], strict=True))
    assert normal == [pixel for row in grid for pixel in row]
    snake = matrix_pixels(matrix.model_copy(update={"serpentine": True}), 180, color)
    for row in range(16):
        expected = normal[row * 16 : (row + 1) * 16]
        assert snake[row * 16 : (row + 1) * 16] == (expected[::-1] if row % 2 else expected)
    assert matrix_pixels(matrix, None, color) == matrix_pixels(matrix, 10000, color)
    assert matrix_pixels(matrix, 0, color) != matrix_pixels(matrix, None, color)


@pytest.mark.parametrize("height", [8, 16])
def test_matrix_uses_centered_four_by_eight_digits(height):
    matrix = Matrix(width=16, height=height, serpentine=False)
    pixels = matrix_pixels(matrix, 501, Appearance(brightness=255))
    rows = [
        "".join("#" if p != "000000" else "." for p in pixels[y * 16 : (y + 1) * 16])
        for y in range(height)
    ]
    expected = [
        ".####..##....#..",
        ".#....#..#..##..",
        ".#....#..#...#..",
        ".###..#..#...#..",
        "....#.#..#...#..",
        "....#.#..#...#..",
        ".#..#.#..#...#..",
        "..##...##...###.",
    ]
    margin = ["." * 16] * ((height - 8) // 2)
    assert rows == margin + expected + margin


@pytest.mark.parametrize("width,height,value", [(11, 8, 501), (16, 5, 501), (16, 16, 9999)])
def test_matrix_retains_compact_font_when_large_digits_do_not_fit(width, height, value):
    pixels = matrix_pixels(
        Matrix(width=width, height=height, serpentine=False), value, Appearance(brightness=255)
    )
    lit = [(i % width, i // width) for i, pixel in enumerate(pixels) if pixel != "000000"]
    assert max(y for _, y in lit) - min(y for _, y in lit) == 4
    assert max(x for x, _ in lit) - min(x for x, _ in lit) == len(str(value)) * 4 - 2


def native_matrix_state():
    # WLED 0.15.1 reports len as X width, not the number of matrix pixels.
    return {
        "info": {"ver": "0.15.1", "leds": {"matrix": {"w": 16, "h": 16}, "seglc": [1, 1, 2]}},
        "state": {
            "seg": [
                {"id": 0, "start": 0, "stop": 16, "startY": 0, "stopY": 8, "len": 16},
                {"id": 1, "start": 0, "stop": 16, "startY": 8, "stopY": 16, "len": 16},
                {"id": 2, "start": 256, "stop": 272, "startY": 0, "stopY": 1, "len": 16},
            ]
        },
        "effects": capabilities()["effects"],
    }


async def test_native_matrix_geometry_and_separate_rows_accept_scores():
    state = native_matrix_state()
    state["info"]["leds"]["ins"] = [
        {"start": 0, "len": 16, "type": 41, "pin": [16]},
        {"start": 256, "len": 16, "type": 41, "pin": [2]},
    ]
    board = device(
        targets=[
            {"id": "top", "name": "Top score", "mode": "matrix", "segment": 0},
            {"id": "bottom", "name": "Bottom score", "mode": "matrix", "segment": 1},
            {"id": "strip", "name": "Strip", "segment": 2},
        ]
    )
    async with httpx.AsyncClient() as http:
        info = await inspect_device(http, board, data=state)
    assert info["matrix"] == {"width": 16, "height": 16}
    assert [segment["length"] for segment in info["segments"]] == [128, 128, 16]
    assert info["segments"][1]["matrix"] == {"width": 16, "height": 8, "start_y": 8, "stop_y": 16}
    assert "matrix" not in info["segments"][2]
    # Logical matrix columns must not be mistaken for the white bus on GPIO 16.
    assert [segment["color_mode"] for segment in info["segments"]] == ["color", "color", "white"]
    validate_capabilities(board, info)
    info["segments"][1]["matrix"]["start_y"] = 7
    with pytest.raises(ConnectionProblem, match="overlap"):
        validate_capabilities(board, info)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
async def test_native_matrix_uses_logical_rows_and_keeps_segment_geometry(rotation):
    state = native_matrix_state()
    state["state"]["seg"][0]["stopY"] = 16
    board = device(
        targets=[
            {
                "id": "score",
                "name": "Score",
                "mode": "matrix",
                "matrix": {"width": 16, "height": 16, "rotation": rotation, "serpentine": True},
            }
        ]
    )
    async with httpx.AsyncClient() as http:
        info = await inspect_device(http, board, data=state)
    validate_capabilities(board, info)
    before = board.model_dump()
    command = payload(board, {"phase": "ready", "remaining": 180}, {}, info=info)["seg"][0]
    matrix = board.targets[0].matrix
    logical = matrix_pixels(matrix.model_copy(update={"serpentine": False}), 180, matrix.appearance)
    assert command["i"] == [0, *logical]
    assert set(command) == {"id", "on", "bri", "i", "tt"}
    assert command["tt"] == 0  # Pixels update crisply.
    assert board.model_dump() == before
    # A matrix connected as a linear strip still needs the serpentine mapping.
    linear = payload(board, {"phase": "ready", "remaining": 180}, {})["seg"][0]
    assert linear["i"] != command["i"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("grp", 2),
        ("spc", 1),
        ("of", 1),
        ("mi", True),
        ("mY", True),
        ("rev", True),
        ("rY", True),
        ("tp", True),
    ],
)
async def test_native_matrix_pixel_transforms_are_rejected_without_changing_wled(field, value):
    state = native_matrix_state()
    state["state"]["seg"][0][field] = value
    board = device(targets=[{"id": "score", "name": "Score", "mode": "matrix"}])
    async with httpx.AsyncClient() as http:
        info = await inspect_device(http, board, data=state)
    with pytest.raises(ConnectionProblem, match="In WLED, set segment grouping"):
        validate_capabilities(board, info)
    validate_capabilities(device(), info)  # Whole-segment native effects keep their settings.


@pytest.mark.parametrize(
    "field,value", [("grp", 2), ("spc", 1), ("of", 1), ("rev", True), ("mi", True)]
)
@pytest.mark.parametrize("mode", ["pixels", "matrix"])
async def test_linear_pixel_transforms_are_rejected_before_preview(tmp_path, field, value, mode):
    snapshot = preview_state()
    snapshot["state"]["seg"][0].update({"stop": 128, "len": 128, field: value})
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=snapshot)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        target = {"id": "ring", "name": "Ring", "mode": mode}
        if mode == "pixels":
            target.update(start=64, count=32)
        with pytest.raises(ConnectionProblem, match="In WLED, set segment grouping"):
            await service.preview_draft(
                DraftPreview(device=device(targets=[target]), target_id="ring")
            )
        assert all(request.method == "GET" for request in requests)
        info = await inspect_device(http, device(), data=snapshot)
        validate_capabilities(device(), info)  # Native effects can retain their transforms.


@pytest.mark.parametrize("width,height,start", [(17, 8, 0), (16, 9, 0), (16, 8, 1)])
async def test_native_matrix_scores_must_fit_segment_geometry(width, height, start):
    board = device(
        targets=[
            {
                "id": "score",
                "name": "Score",
                "mode": "matrix",
                "start": start,
                "matrix": {"width": width, "height": height},
            }
        ]
    )
    async with httpx.AsyncClient() as http:
        info = await inspect_device(http, board, data=native_matrix_state())
    with pytest.raises(
        ConnectionProblem, match="First LED 0 and place the panel within the 16 × 8"
    ):
        validate_capabilities(board, info)


@pytest.mark.parametrize(
    "source,color",
    [
        ("remaining", "#ff0000"),
        ("turn_score", "#ff0000"),
        ("local_remaining", "#00ff00"),
        ("opponent_remaining", "#ff0000"),
    ],
)
def test_score_digits_default_to_red_and_my_score_to_green(source, color):
    assert Matrix(source=source).appearance.color == color
    # A colour the user chose, white included, is kept.
    assert Matrix(source=source, appearance={"color": "#ffffff"}).appearance.color == "#ffffff"


def score_panels():
    return device(
        targets=[
            {
                "id": "mine",
                "name": "My score",
                "mode": "matrix",
                "matrix": {"width": 16, "height": 16, "source": "local_remaining"},
            },
            {
                "id": "opponent",
                "name": "Opponent score",
                "mode": "matrix",
                "matrix": {"width": 16, "height": 16, "x": 16, "source": "opponent_remaining"},
            },
        ]
    )


async def panel_capabilities(board):
    state = native_matrix_state()
    state["info"]["leds"]["matrix"] = {"w": 48, "h": 24}
    # Segment-local coordinates must work even with a non-zero canvas origin.
    state["state"]["seg"] = [{"id": 0, "start": 8, "stop": 40, "startY": 4, "stopY": 20, "len": 32}]
    async with httpx.AsyncClient() as http:
        return await inspect_device(http, board, data=state)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
async def test_two_score_panels_share_segment_without_overwriting_each_other(rotation):
    from app.core.integrations.wled.simulator import apply_indices

    board = score_panels()
    for target in board.targets:
        target.matrix.rotation = rotation
    info = await panel_capabilities(board)
    validate_capabilities(board, info)
    view = {
        "phase": "ready",
        "players": [
            {"id": "local", "index": 0, "is_local": True, "remaining": 301},
            {"id": "remote", "index": 1, "is_local": False, "remaining": 180},
        ],
    }
    command = payload(board, view, {}, info=info)["seg"]
    assert len(command) == 1
    assert set(command[0]) == {"id", "on", "bri", "tt", "i"}
    pixels = ["unchanged"] * 512
    apply_indices(pixels, command[0]["i"])
    expected = []
    for target, score in zip(board.targets, [301, 180], strict=True):
        matrix = target.matrix.model_copy(update={"serpentine": False})
        expected.append(matrix_pixels(matrix, score, matrix.appearance))
    for row in range(16):
        assert pixels[row * 32 : (row + 1) * 32] == (
            expected[0][row * 16 : (row + 1) * 16] + expected[1][row * 16 : (row + 1) * 16]
        )
    preview = payload(board, view, {}, Preview(target_id="mine", value=40), info=info)
    preview_pixels = pixels.copy()
    apply_indices(preview_pixels, preview["seg"][0]["i"])
    assert preview_pixels != pixels
    for row in range(16):
        assert (
            preview_pixels[row * 32 + 16 : (row + 1) * 32] == pixels[row * 32 + 16 : (row + 1) * 32]
        )


async def test_panel_bounds_overlap_and_linear_addressing_are_checked():
    board = score_panels()
    info = await panel_capabilities(board)
    invalid = board.model_dump()
    invalid["targets"][1]["matrix"]["x"] = 15
    with pytest.raises(ValidationError, match="overlap"):
        Device.model_validate(invalid)
    board.targets[1].matrix.x = 17
    with pytest.raises(ConnectionProblem, match="place the panel within"):
        validate_capabilities(board, info)
    board.targets[1].matrix.x = 16
    board.targets[1].matrix.y = 1
    with pytest.raises(ConnectionProblem, match="place the panel within"):
        validate_capabilities(board, info)
    board.targets[1].matrix.y = 0
    linear = deepcopy(info)
    linear["segments"][0].pop("matrix")
    with pytest.raises(ConnectionProblem, match="panel X/Y positions require"):
        validate_capabilities(board, linear)
    # Pixel 32 is on the second row of the left panel, despite the panel's width of 16.
    board.targets[1] = wled.Target(id="pixel", name="Pixel", mode="pixels", start=32)
    with pytest.raises(ConnectionProblem, match="overlap"):
        validate_capabilities(board, info)
    board.targets[1].start = 16
    validate_capabilities(board, info)


async def test_stacked_panels_update_only_their_rectangles():
    from app.core.integrations.wled.simulator import apply_indices

    board = score_panels()
    board.targets[0].matrix.height = 8
    board.targets[1].matrix.height = 8
    board.targets[1].matrix.x = 0
    board.targets[1].matrix.y = 8
    board = Device.model_validate(board.model_dump())
    info = await panel_capabilities(board)
    validate_capabilities(board, info)
    pixels = ["untouched"] * 512
    body = payload(board, {"phase": "waiting"}, {}, info=info)
    apply_indices(pixels, body["seg"][0]["i"])
    for row in range(16):
        assert "untouched" not in pixels[row * 32 : row * 32 + 16]
        assert pixels[row * 32 + 16 : (row + 1) * 32] == ["untouched"] * 16


async def test_panel_preview_restores_segment_without_saving_or_changing_geometry(tmp_path):
    board = score_panels()
    state = native_matrix_state()
    state["info"]["leds"]["matrix"] = {"w": 32, "h": 16}
    state["state"].update(on=True, bri=128)
    state["state"]["seg"] = [
        {
            "id": 0,
            "start": 0,
            "stop": 32,
            "startY": 0,
            "stopY": 16,
            "len": 32,
            "on": True,
            "bri": 77,
            "fx": 1,
            "col": [[4, 5, 6]],
            "frz": False,
        }
    ]
    sent = []

    def handler(request):
        if request.method == "POST":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json=state)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        result = await service.preview_draft(
            DraftPreview(device=board, target_id="opponent", value=180, duration=0.5)
        )
        assert result["restored"]
        indices = sent[0]["seg"][0]["i"]
        assert [i for i in indices if isinstance(i, int)] == [16 + row * 32 for row in range(16)]
        assert sent[-1]["seg"][0]["col"] == [[4, 5, 6]]
        assert sent[-1]["seg"][0]["bri"] == 77
        assert all(not {"start", "stop", "startY", "stopY"} & b["seg"][0].keys() for b in sent)
        assert not service.path.exists()


def test_matrix_player_sources_stay_assigned_across_turns_corrections_and_disconnects():
    bus = EventBus()
    normalizer = EventNormalizer(BOARD_ID, bus)
    normalizer.select(MATCH_ID)
    frame = json.loads((Path(__file__).parent / "fixtures/x01-state.json").read_text())
    mine, opponent = (t.matrix for t in score_panels().targets)
    for active, scores in [(0, [441, 501]), (1, [441, 401]), (1, [461, 401])]:
        frame["player"], frame["gameScores"] = active, scores
        normalizer.consume(bus.publish("cloud", "match.state", deepcopy(frame), snapshot=True))
        view = normalizer.current_state(True)
        assert [wled.matrix_value(m, view) for m in (mine, opponent)] == scores
    assert wled.matrix_value(mine, normalizer.current_state(False)) is None
    normalizer.resync()
    assert wled.matrix_value(opponent, normalizer.current_state(True)) is None
    players = [
        {"id": "a", "index": 0, "is_local": True, "remaining": 0},
        {"id": "b", "index": 1, "is_local": True, "remaining": 301},
    ]
    assert wled.matrix_value(mine, {"players": players}) is None
    assert wled.matrix_value(opponent, {"players": players}) is None
    assert wled.matrix_value(Matrix(source="player_remaining"), {"players": players}) == 0
    assert (
        wled.matrix_value(Matrix(source="player_remaining", player_slot=2), {"players": players})
        == 301
    )
    players[1]["remaining"] = None
    assert (
        wled.matrix_value(Matrix(source="player_remaining", player_slot=2), {"players": players})
        is None
    )


def test_panel_settings_persist_through_api_and_profile_switch(settings):
    board = score_panels()
    with TestClient(create_app(settings)) as client:
        assert client.patch("/api/wled", json={"devices": [board.model_dump()]}).status_code == 200
        assert client.post("/api/profiles", json={"name": "Panels"}).status_code == 200
        assert client.put("/api/profile", json={"id": "default"}).status_code == 200
        assert client.get("/api/wled").json()["devices"] == [board.model_dump(mode="json")]
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/wled").json()["devices"] == [board.model_dump(mode="json")]


@pytest.mark.parametrize("bounds", [{"startY": -1}, {"stopY": 17}, {"stopY": None}])
async def test_native_matrix_rejects_invalid_row_bounds(bounds):
    state = native_matrix_state()
    state["state"]["seg"][0].update(bounds)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectionProblem, match="valid WLED capabilities"):
            await inspect_device(http, device(), data=state)


async def test_native_matrix_preview_checks_dimensions_and_restores_existing_effect(tmp_path):
    state = native_matrix_state()
    state["state"].update(on=True, bri=128)
    state["state"]["seg"][0].update(on=True, bri=72, fx=0, col=[[1, 2, 3]], frz=False)
    sent = []

    def handler(request):
        if request.method == "POST":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json=state)

    board = device(targets=[{"id": "score", "name": "Score", "mode": "matrix"}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        result = await service.preview_draft(
            DraftPreview(device=board, target_id="score", value=180, duration=0.5)
        )
    assert result["restored"]
    assert len(sent[0]["seg"][0]["i"]) == 129
    assert sent[-1]["seg"][0]["col"] == [[1, 2, 3]]
    assert sent[-1]["seg"][0]["bri"] == 72
    assert all(not {"start", "stop", "startY", "stopY"} & body["seg"][0].keys() for body in sent)


@pytest.mark.parametrize("canvas", [{"w": True, "h": 16}, {"w": 16, "h": 0}, [], {}])
async def test_invalid_native_matrix_metadata_is_rejected(canvas):
    state = native_matrix_state()
    state["info"]["leds"]["matrix"] = canvas
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectionProblem, match="valid WLED capabilities"):
            await inspect_device(http, device(), data=state)


async def test_capabilities_bounds_reserved_effects_and_malformed_device():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=capabilities()))
    ) as http:
        info = await inspect_device(http, device())
        validate_capabilities(device(), info)
        with pytest.raises(ConnectionProblem, match="exceed"):
            validate_capabilities(
                device(targets=[{"id": "x", "name": "x", "mode": "pixels", "count": 17}]), info
            )
        with pytest.raises(ConnectionProblem, match="does not exist"):
            validate_capabilities(device(targets=[{"id": "x", "name": "x", "segment": 5}]), info)
        invalid = device()
        info["effects"][32] = "RSVD"
        invalid.targets[0].phases.ready.effect = 32
        with pytest.raises(ConnectionProblem, match="unavailable"):
            validate_capabilities(invalid, info)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    ) as http:
        with pytest.raises(ConnectionProblem, match="invalid response"):
            await inspect_device(http, device())


@pytest.mark.parametrize("delay", [0.0033, 0.0115, 0.0393])
@pytest.mark.parametrize("is_bot", [False, True])
async def test_takeout_finished_effect_survives_remote_turn_until_original_expiry(
    monkeypatch, delay, is_bot
):
    clock = [100.0]
    monkeypatch.setattr(wled.time, "monotonic", lambda: clock[0])
    board = device(
        targets=[
            {
                "id": "ring",
                "name": "Ring",
                "effects": {"takeout_finished": {"color": "#123456", "duration": 2}},
            }
        ]
    )
    async with httpx.AsyncClient() as http:
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        worker.accept(event("takeout_finished"))
        clock[0] += delay
        player = {"is_local": False, "is_bot": is_bot}
        worker.accept(event("turn_started", player=player))
        waiting = {"phase": "waiting", "available": True}
        assert worker.desired(waiting)["seg"][0]["col"] == [[18, 52, 86]]

        clock[0] = 101.9
        worker.accept(
            event("throw", player=player, dart={"segment": {"number": 20, "multiplier": 1}})
        )
        stale = event("turn_started")
        stale.received_at -= timedelta(seconds=3)
        worker.accept(stale)
        assert worker.desired(waiting)["seg"][0]["col"] == [[18, 52, 86]]

        clock[0] = 102.0
        assert worker.desired(waiting)["seg"][0]["col"] == [[255, 0, 0]]
        stale = event("takeout_finished")
        stale.received_at -= timedelta(seconds=3)
        worker.accept(stale)
        assert not worker.overlays


def test_idle_timeout_tracks_a_continuous_idle_streak(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(wled.time, "monotonic", lambda: clock[0])
    worker = DeviceWorker(device(idle_timeout_minutes=5), None, EventBus(), lambda: {})
    idle, ready = {"phase": "idle"}, {"phase": "ready"}

    assert worker.idle_timeout_elapsed(idle) is False  # Streak just started.
    clock[0] += 5 * 60 - 1
    assert worker.idle_timeout_elapsed(idle) is False  # Not quite there yet.
    clock[0] += 1
    assert worker.idle_timeout_elapsed(idle) is True  # Timeout reached.

    assert worker.idle_timeout_elapsed(ready) is False  # Leaving idle resets the streak.
    assert worker.idle_since is None
    clock[0] += 5 * 60
    assert worker.idle_timeout_elapsed(idle) is False  # Streak restarts from scratch.


def test_idle_timeout_disabled_by_default(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(wled.time, "monotonic", lambda: clock[0])
    worker = DeviceWorker(device(), None, EventBus(), lambda: {})
    clock[0] += 10_000
    assert worker.idle_timeout_elapsed({"phase": "idle"}) is False


@pytest.mark.parametrize("next_event", ["turn_started", "throw"])
async def test_local_play_interrupts_takeout_finished_without_a_hit_rule(next_event):
    board = device(
        targets=[
            {
                "id": "ring",
                "name": "Ring",
                "effects": {"takeout_finished": {"color": "#123456"}},
            }
        ]
    )
    async with httpx.AsyncClient() as http:
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        worker.accept(event("takeout_finished"))
        worker.accept(event(next_event, dart={"segment": {"number": 20, "multiplier": 1}}))
        assert not worker.overlays
        assert worker.desired({"phase": "ready", "available": True})["seg"][0]["col"] == [
            [0, 255, 0]
        ]


async def test_takeout_transition_policy_keeps_win_priority_and_clears_on_disconnect():
    board = device()
    board.targets[0].effects["takeout_finished"] = wled.Effect(color="#123456")
    async with httpx.AsyncClient() as http:
        worker = DeviceWorker(board, http, EventBus(), lambda: {})
        worker.accept(event("match_win"))
        worker.accept(event("takeout_finished"))
        worker.accept(event("throw", dart={"segment": {"number": 20, "multiplier": 1}}))
        assert worker.desired({"phase": "takeout", "available": True})["seg"][0]["col"] == [
            [160, 0, 255]
        ]
        worker.accept(event("turn_started", player={"is_local": False, "is_bot": True}))
        assert not worker.overlays
        worker.accept(event("takeout_finished"))
        assert worker.desired({"phase": "waiting", "available": False})["seg"][0]["col"] == [
            [255, 0, 0]
        ]
        assert not worker.overlays


async def test_priority_expiry_and_corrections(monkeypatch):
    async with httpx.AsyncClient() as http:
        worker = DeviceWorker(device(), http, EventBus(), lambda: {})
        view = {"phase": "ready", "available": True}
        worker.accept(event("turn_end", score=180, busted=False))
        worker.accept(event("leg_win"))
        worker.accept(event("match_win"))
        worker.accept(event("turn_end", score=180, busted=False))
        assert worker.desired(view)["seg"][0]["col"] == [[160, 0, 255]]
        assert worker.desired(view)["seg"][0]["fx"] == 9
        assert worker.device.targets[0].effects["match_win"].duration == 10
        future = time.monotonic() + 11
        monkeypatch.setattr("app.core.integrations.wled.service.time.monotonic", lambda: future)
        assert worker.desired({**view, "phase": "takeout"})["seg"][0]["col"] == [[225, 113, 0]]
        worker.accept(event("bust", player={"is_local": False}))
        assert not worker.overlays
        worker.accept(event("bust"))
        worker.accept(event("throw_corrected"))
        assert not worker.overlays
        old = event("match_win")
        old.received_at = datetime.now(UTC) - timedelta(seconds=10)
        worker.accept(old)
        assert not worker.overlays
        assert worker.desired({"phase": "waiting", "available": False})["seg"][0]["col"] == [
            [255, 0, 0]
        ]


async def test_worker_isolation_preview_restoration_and_shutdown(tmp_path):
    sent = []
    blocked = asyncio.Event()

    async def transport(request):
        assert "authorization" not in request.headers
        if request.url.host == "offline.test":
            await blocked.wait()
        if request.method == "GET":
            return httpx.Response(200, json=capabilities())
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    view = {"phase": "waiting", "available": True}
    bus = EventBus()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        service = WLED(tmp_path / "wled.json", http, bus, lambda: view)
        await service.configure(
            WLEDConfig(
                enabled=True, devices=[device(), device(id="offline", url="http://offline.test")]
            )
        )
        try:
            await until(lambda: bool(sent))
            assert sent[-1]["seg"][0]["col"] == [[255, 0, 0]]
            result = await service.test("board", Preview(target_id="ring", duration=0.5))
            assert result["applied"] and result["restored"]
            assert sent[-2]["seg"][0]["frz"] is False
            assert any(body["seg"][0].get("col") == [[0, 255, 0]] for body in sent)
            assert sent[-1]["seg"][0]["col"] == [[255, 0, 0]]
            view["phase"] = "takeout"
            await until(lambda: sent[-1]["seg"][0]["col"] == [[225, 113, 0]])
            bus.publish("core", "match_win", {"player": {"is_local": True}}, kind="normalized")
            await until(lambda: sent[-1]["seg"][0]["col"] == [[160, 0, 255]])
        finally:
            await service.close()
        assert sent[-1]["seg"][0]["col"] == [[255, 0, 0]]
        assert not bus.normalized_queues and not service.tasks






def test_wled_partial_updates_validate_and_keep_browser_guards(settings):
    config = WLEDConfig(devices=[device(enabled=False)])
    WLED.sync_profiles(config)
    values = config.model_dump(mode="json")
    with TestClient(create_app(settings)) as client:
        assert client.put("/api/wled", json=values).status_code == 200
        assert client.patch("/api/wled", json={}).status_code == 200
        for invalid in (
            {"enabled": None},
            {"enabled": "invalid"},
            {"devices": None},
            {"devices": values["devices"] * 2},
            {"unknown": True},
        ):
            assert client.patch("/api/wled", json=invalid).status_code == 422
        assert client.patch("/api/wled", content='{"enabled":true}').status_code == 415
        assert (
            client.patch(
                "/api/wled", json={"enabled": True}, headers={"Origin": "https://other.test"}
            ).status_code
            == 403
        )
        assert client.get("/api/wled").json() == values
        assert client.patch("/api/wled", json={"devices": []}).status_code == 200
        assert client.get("/api/wled").json() == WLEDConfig().model_dump(mode="json")


def test_retired_visual_import_metadata_is_discarded():
    config = WLEDConfig.model_validate({
        "visual_migrated": True,
        "migration_note": "Retired migration notice",
    })
    assert "visual_migrated" not in config.model_dump()
    assert "migration_note" not in config.model_dump()


def test_bad_saved_wled_configuration_does_not_stop_core(settings):
    (settings.data_dir / "wled.json").write_text("broken", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/wled/status").json()["error"]
        assert client.put("/api/wled", json={}).status_code == 200
        assert client.get("/api/wled/status").json()["error"] is None


def test_game_phases_require_readiness_and_corrected_scores():
    bus = EventBus()
    normalizer = EventNormalizer(BOARD_ID, bus)
    normalizer.select(MATCH_ID)
    frame = json.loads((Path(__file__).parent / "fixtures/x01-state.json").read_text())

    def feed(data, name="match.state", **kwargs):
        normalizer.consume(bus.publish("cloud", name, deepcopy(data), **kwargs))

    feed(frame, snapshot=True)
    assert not bus.normalized_history
    assert normalizer.current_state(True)["phase"] == "waiting"
    feed({"status": "Ready for throw"}, "board.state")
    assert normalizer.current_state(True)["phase"] == "ready"
    frame["turns"][0]["throws"] = [
        {"id": "one", "segment": {"number": 20, "multiplier": 3, "name": "T20"}}
    ]
    frame["turns"][0]["points"] = 60
    frame["gameScores"][0] = 441
    feed(frame)
    assert normalizer.current_state(True)["remaining"] == 441
    frame["turns"][0]["throws"][0]["segment"] = {"number": 20, "multiplier": 1, "name": "S20"}
    frame["gameScores"][0] = 481
    frame["turns"][0]["points"] = 20
    feed(frame)
    assert normalizer.current_state(True)["last_dart"] == 20
    assert normalizer.current_state(True)["remaining"] == 481
    players = normalizer.current_state(True)["players"]
    assert [p["remaining"] for p in players] == [481, 501]
    assert [p["active"] for p in players] == [True, False]
    assert [p["name"] for p in players] == ["Local player", "Remote player"]
    feed({"event": "Takeout started"}, "board.events")
    assert normalizer.current_state(True)["phase"] == "takeout"
    feed({"event": "Takeout finished"}, "board.events")
    assert normalizer.current_state(True)["phase"] == "waiting"
    feed({"status": "Ready"}, "board.state")
    frame["player"] = 1
    feed(frame)
    assert normalizer.current_state(True)["phase"] == "waiting"
    assert normalizer.current_state(True)["turn_score"] == 0
    assert normalizer.current_state(False)["remaining"] is None
    assert normalizer.current_state(False)["players"] == []
    normalizer.resync()
    assert normalizer.current_state(True)["remaining"] is None
    assert normalizer.current_state(True)["players"] == []


async def test_game_view_rejects_offline_or_stale_board(tmp_path):
    async with httpx.AsyncClient() as http:
        auth = DeviceAuth(http, "https://api.autodarts.com", "test", tmp_path / "tokens.json")
        cloud = CloudConnection(http, auth, BOARD_ID, EventBus())
        cloud.state = "connected"
        cloud.board = {"state": {"connected": True}}
        cloud.board_checked_at = time.monotonic()
        assert cloud.game_state()["phase"] == "idle"
        cloud.board_checked_at -= 100
        assert cloud.game_state()["available"] is False
        cloud.board_checked_at = time.monotonic()
        cloud.state = "reconnecting"
        assert cloud.game_state()["phase"] == "waiting"


def test_remote_takeout_and_invalid_state_do_not_show_local_readiness():
    bus = EventBus()
    normalizer = EventNormalizer(BOARD_ID, bus)
    normalizer.select(MATCH_ID)
    frame = json.loads((Path(__file__).parent / "fixtures/x01-state.json").read_text())
    frame["player"] = 1
    frame["turns"][0]["playerId"] = "remote-player"
    normalizer.consume(bus.publish("cloud", "match.state", frame, snapshot=True))
    normalizer.board_status({"status": "Ready"})
    normalizer.consume(bus.publish("cloud", "match.events", {"event": "Takeout started"}))
    assert normalizer.current_state(True)["phase"] == "waiting"
    normalizer.consume(bus.publish("cloud", "match.events", {"event": "Takeout finished"}))
    frame["player"] = 0
    frame["turns"][0]["playerId"] = "local-player"
    normalizer.consume(bus.publish("cloud", "match.state", frame))
    assert normalizer.current_state(True)["phase"] == "ready"
    invalid = {**frame, "players": []}
    normalizer.consume(bus.publish("cloud", "match.state", invalid))
    assert normalizer.current_state(True)["phase"] == "waiting"
    assert normalizer.current_state(True)["remaining"] is None
    normalizer.consume(bus.publish("cloud", "match.state", frame))
    assert normalizer.current_state(True)["remaining"] == 501
    assert normalizer.current_state(True)["phase"] == "waiting"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="invalid"),
        httpx.Response(200, json={"error": 9}),
        httpx.Response(200, json={"success": False}),
        httpx.Response(200, text="null"),
        httpx.Response(200, json=[]),
    ],
)
async def test_rejected_device_commands_are_not_successful(response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(ConnectionProblem):
            await request(http, device(), "/json/state", {"seg": []})


async def test_connection_errors_distinguish_timeout_from_unreachable():
    def raises(exc):
        def handler(_request):
            raise exc

        return handler

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(raises(httpx.ConnectError("refused")))
    ) as http:
        with pytest.raises(ConnectionProblem, match="Cannot connect to WLED"):
            await request(http, device(), "/json")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(raises(httpx.ConnectTimeout("slow")))
    ) as http:
        with pytest.raises(ConnectionProblem, match="Timed out waiting for WLED"):
            await request(http, device(), "/json")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(raises(httpx.ProtocolError("bad")))
    ) as http:
        with pytest.raises(ConnectionProblem, match="Cannot reach WLED"):
            await request(http, device(), "/json")


async def test_disable_after_preview_clears_lights_and_workers(tmp_path):
    sent = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(200, json=capabilities())
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        bus = EventBus()
        service = WLED(
            tmp_path / "wled.json", http, bus, lambda: {"phase": "idle", "available": True}
        )
        await service.configure(WLEDConfig(enabled=True, devices=[device()]))
        try:
            await until(lambda: bool(sent))
            await service.test("board", Preview(target_id="ring", duration=0.5))
            assert any(body["seg"][0].get("col") == [[0, 255, 0]] for body in sent)
            assert sent[-1]["seg"][0]["col"] == [[111, 0, 221]]
            assert sent[-1]["seg"][0]["fx"] == 2
            assert sent[-1]["seg"][0]["bri"] == 64
            await service.configure(WLEDConfig(enabled=False, devices=[device()]))
            assert sent[-1]["seg"][0]["col"] == [[255, 0, 0]]
            assert not service.tasks and not bus.normalized_queues
        finally:
            await service.close()


def mock_discovery(monkeypatch, records):
    """Advertise services without opening multicast sockets in the test suite."""
    lifecycle = {"closed": False, "cancelled": False}

    class Zeroconf:
        def __init__(self, *, ip_version):
            assert ip_version is IPVersion.V4Only
            self.zeroconf = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            lifecycle["closed"] = True

    class Browser:
        def __init__(self, zeroconf, service_type, *, handlers):
            assert service_type == wled.WLED_SERVICE
            for name, record in records.items():
                args = dict(zeroconf=zeroconf, service_type=service_type, name=name)
                handlers[0](**args, state_change=ServiceStateChange.Added)
                handlers[0](**args, state_change=ServiceStateChange.Updated)
                if record.get("removed"):
                    handlers[0](**args, state_change=ServiceStateChange.Removed)

        async def async_cancel(self):
            lifecycle["cancelled"] = True

    class Info:
        def __init__(self, service_type, name):
            self.record = records[name]
            self.port = self.record.get("port", 80)
            self.server = self.record.get("hostname", f"{name}.local.")

        async def async_request(self, zeroconf, timeout_ms):
            if "pending" in self.record:
                self.record["pending"].set()
                await asyncio.Future()
            return self.record.get("resolved", True)

        def parsed_addresses(self, version):
            assert version is IPVersion.V4Only
            return self.record.get("addresses", [])

    monkeypatch.setattr(wled, "AsyncZeroconf", Zeroconf)
    monkeypatch.setattr(wled, "AsyncServiceBrowser", Browser)
    monkeypatch.setattr(wled, "AsyncServiceInfo", Info)
    monkeypatch.setattr(wled, "DISCOVERY_SECONDS", 0.01)
    return lifecycle


async def test_discovery_verifies_deduplicates_and_filters_advertisements(monkeypatch):
    records = {
        "ring": {"addresses": ["192.168.1.20"]},
        "alias": {"addresses": ["192.168.1.20"], "hostname": "ring.local."},
        "matrix": {"addresses": ["192.168.1.21"], "port": 8080},
        "offline": {"addresses": ["192.168.1.22"]},
        "wrong-service": {"addresses": ["192.168.1.23"]},
        "removed": {"addresses": ["192.168.1.24"], "removed": True},
        "unresolved": {"resolved": False},
        "invalid": {"addresses": ["not-an-address"]},
        "loopback": {"addresses": ["127.0.0.1", "0.0.0.0"]},
    }
    lifecycle = mock_discovery(monkeypatch, records)
    requests = []

    def transport(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/json"
        assert "authorization" not in request.headers
        if request.url.host == "192.168.1.22":
            raise httpx.ConnectError("offline")
        if request.url.host == "192.168.1.23":
            return httpx.Response(200, json={"product": "not WLED"})
        data = capabilities()
        data["info"]["name"] = "Matrix" if request.url.port == 8080 else "Ring"
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        found = await discover_devices(http)
    assert [item["name"] for item in found] == ["Matrix", "Ring"]
    assert [item["url"] for item in found] == ["http://192.168.1.21:8080", "http://192.168.1.20"]
    assert found[1]["hostname"] == "ring.local"
    assert len(requests) == 6  # Updated announcements do not repeat a probe.
    assert lifecycle == {"closed": True, "cancelled": True}


async def test_discovery_limits_work_and_closes_pending_probes(monkeypatch):
    pending = asyncio.Event()
    records = {str(index): {"pending": pending} for index in range(80)}
    lifecycle = mock_discovery(monkeypatch, records)
    async with httpx.AsyncClient() as http:
        assert await asyncio.wait_for(discover_devices(http), timeout=1) == []
    assert pending.is_set()
    assert lifecycle == {"closed": True, "cancelled": True}


async def test_discovery_cancellation_and_concurrent_scan(tmp_path, monkeypatch):
    pending = asyncio.Event()
    lifecycle = mock_discovery(monkeypatch, {"ring": {"pending": pending}})
    monkeypatch.setattr(wled, "DISCOVERY_SECONDS", 10)
    async with httpx.AsyncClient() as http:
        service = WLED(tmp_path / "wled.json", http, EventBus(), lambda: {})
        scan = asyncio.create_task(service.discover())
        try:
            await asyncio.wait_for(pending.wait(), timeout=1)
            with pytest.raises(ConnectionProblem, match="already running"):
                await service.discover()
            assert service.status()["enabled"] is False
            assert not service.path.exists()
        finally:
            scan.cancel()
            with pytest.raises(asyncio.CancelledError):
                await scan
        assert not service.discovery_lock.locked()
    assert lifecycle == {"closed": True, "cancelled": True}


async def test_discovery_network_failure_is_actionable(monkeypatch):
    def fail(**kwargs):
        raise OSError("network interface unavailable")

    monkeypatch.setattr(wled, "AsyncZeroconf", fail)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectionProblem, match="add an address manually"):
            await discover_devices(http)
