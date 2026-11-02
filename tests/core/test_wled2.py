import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import ConnectionProblem
from app.core.integrations.wled.service import Matrix, WLEDConfig
from app.core.integrations.wled.simulator import DEMO_URL
from app.core.integrations.wled2.service import Connection, Controller, Layout, Light, Rules
from app.main import create_app


def scene(**updates):
    return Layout(
        controllers=[Controller(id="controller", name="Controller", url=DEMO_URL)],
        lights=[
            Light(
                id="ring",
                name="Ring",
                kind="ring",
                connection=Connection(controller_id="controller"),
            ),
            Light(
                id="mine",
                name="My score",
                kind="matrix",
                connection=Connection(controller_id="controller", segment=1),
                matrix=Matrix(width=16, height=16, source="local_remaining"),
            ),
            Light(
                id="opponent",
                name="Opponent score",
                kind="matrix",
                connection=Connection(controller_id="controller", segment=1),
                matrix=Matrix(width=16, height=16, x=16, source="opponent_remaining"),
            ),
            Light(id="unwired", name="Unwired strip", kind="strip"),
        ],
        **updates,
    )


@pytest.fixture
def app(settings):
    def reject(request):
        pytest.fail(f"Unexpected external request: {request.url}")

    return create_app(settings, transport=httpx.MockTransport(reject))


def test_compile_shared_matrix_panels_with_per_light_rules():
    layout = scene()
    layout.lights[0].rules = Rules()
    layout.lights[0].rules.effects["bull"].color = "#123456"
    config = layout.compile()
    targets = config.devices[0].targets
    assert [t.id for t in targets] == ["ring", "mine", "opponent"]
    assert targets[0].effects["bull"].color == "#123456"
    # A controller drives strips and panels at once, so rules stay with each light.
    assert targets[1].effects["bull"].color == layout.lights[1].rules.effects["bull"].color
    assert targets[1].effects["bull"].color != targets[0].effects["bull"].color
    assert targets[0].effects["bull"].effect != 0
    assert targets[1].effects["bull"].effect == 0
    assert layout.lights[1].rules.effects["bull"].effect != 0
    assert targets[1].matrix.x == 0 and targets[2].matrix.x == 16
    assert targets[1].matrix.source == "local_remaining"
    assert targets[2].matrix.source == "opponent_remaining"


def test_controller_rules_are_rejected_in_favor_of_per_light_rules():
    with pytest.raises(ValueError, match="Extra inputs"):
        Layout.model_validate({"controllers": [{"id": "c1", "name": "Controller",
                                                "url": "http://wled.local", "rules": {}}]})


def test_matrix_lights_start_with_their_event_effects_off():
    panel = Light(id="panel", name="Panel", kind="matrix")
    assert panel.rules.effects and not any(
        effect.enabled for effect in panel.rules.effects.values()
    )
    strip = Light(id="strip", name="Strip", kind="strip")
    assert all(effect.enabled for effect in strip.rules.effects.values())
    # A layout that carries its own rules keeps exactly what it was saved with.
    kept = Light(id="other", name="Other", kind="matrix", rules=Rules())
    assert all(effect.enabled for effect in kept.rules.effects.values())


def test_chain_preserves_address_ranges_and_matrix_panels():
    layout = scene()
    layout.lights[2].connection.after_light_id = "mine"
    layout = Layout.model_validate(layout.model_dump())
    targets = layout.compile().devices[0].targets
    assert targets[1].matrix.x == 0 and targets[2].matrix.x == 16
    strips = [
        Light(
            id=f"strip{i}",
            name=f"Strip {i}",
            kind="strip",
            connection=Connection(
                controller_id="controller",
                after_light_id=f"strip{i - 1}" if i else None,
                whole_segment=False,
                start=i * 30,
                count=30,
            ),
        )
        for i in range(3)
    ]
    layout = Layout(controllers=layout.controllers, lights=strips)
    assert [(t.mode, t.start, t.count) for t in layout.compile().devices[0].targets] == [
        ("pixels", 0, 30),
        ("pixels", 30, 30),
        ("pixels", 60, 30),
    ]


@pytest.mark.parametrize("kind", ["strip", "matrix"])
def test_unassigned_chains_persist_without_device_output(app, settings, kind):
    data = Layout(
        lights=[
            Light(id="first", name="First", kind=kind, connection=Connection()),
            Light(
                id="second",
                name="Second",
                kind=kind,
                connection=Connection(after_light_id="first", start=256),
            ),
        ]
    ).model_dump(mode="json")
    assert Layout.model_validate(data).compile().devices == []
    with TestClient(app) as client:
        assert client.put("/api/wled2", json=data).status_code == 200
        assert client.post("/api/wled2/preview", json={"target_id": "second"}).status_code == 409
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/wled2").json() == data
        assert client.get("/api/wled2/status").json()["devices"] == []


def test_unassigned_chain_cannot_cycle_or_reference_assigned_parent():
    data = Layout(
        lights=[
            Light(id="a", name="A", kind="strip", connection=Connection()),
            Light(id="b", name="B", kind="strip", connection=Connection(after_light_id="a")),
        ]
    ).model_dump(mode="json")
    data["lights"][0]["connection"]["after_light_id"] = "b"
    with pytest.raises(ValidationError, match="cycle"):
        Layout.model_validate(data)
    data["lights"][0]["connection"]["after_light_id"] = None
    data["controllers"] = [{"id": "c", "name": "C", "url": "http://controller.test"}]
    data["lights"][0]["connection"]["controller_id"] = "c"
    with pytest.raises(ValidationError, match="same controller"):
        Layout.model_validate(data)


@pytest.mark.parametrize("invalid", ["cycle", "missing", "disconnected", "controller", "segment"])
def test_invalid_chains_are_rejected(invalid):
    data = scene().model_dump(mode="json")
    mine, opponent = data["lights"][1:3]
    opponent["connection"]["after_light_id"] = "mine"
    if invalid == "cycle":
        mine["connection"]["after_light_id"] = "opponent"
    elif invalid == "missing":
        opponent["connection"]["after_light_id"] = "missing"
    elif invalid == "disconnected":
        opponent["connection"]["after_light_id"] = "unwired"
    elif invalid == "controller":
        data["controllers"].append({"id": "second", "name": "Second", "url": "http://second.test"})
        opponent["connection"]["controller_id"] = "second"
    else:
        opponent["connection"]["segment"] = 2
    with pytest.raises(ValidationError):
        Layout.model_validate(data)


def test_names_sizes_and_chains_survive_restart(app, settings):
    data = scene().model_dump(mode="json")
    data["controllers"][0].update(name="Top controller", width=210, height=90)
    data["lights"][1].update(name="Home score", width=180, height=160)
    data["lights"][2]["connection"]["after_light_id"] = "mine"
    with TestClient(app) as client:
        assert client.put("/api/wled2", json=data).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/wled2").json() == data


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "overlap", "url", "reserved"])
def test_invalid_layout_rejected(invalid):
    data = scene().model_dump(mode="json")
    if invalid == "duplicate":
        data["lights"][0]["id"] = "controller"
    elif invalid == "missing":
        data["lights"][0]["connection"]["controller_id"] = "missing"
    elif invalid == "overlap":
        data["lights"][2]["matrix"]["x"] = 15
    elif invalid == "url":
        data["controllers"][0]["url"] = "file:///etc/passwd"
    else:
        data["lights"][0]["id"] = "board"
    with pytest.raises(ValidationError):
        Layout.model_validate(data)


def test_layout_api_is_headless_persistent_and_revision_guarded(app, settings):

    data = scene().model_dump(mode="json")
    with TestClient(app) as client:
        before = client.get("/api/wled2")
        response = client.put("/api/wled2", json=data, headers={"If-Match": before.headers["etag"]})
        assert response.status_code == 200
        assert response.json() == data
        assert response.headers["etag"] != before.headers["etag"]
        assert (
            client.put(
                "/api/wled2", json={}, headers={"If-Match": before.headers["etag"]}
            ).status_code
            == 412
        )
        assert (
            client.put("/api/wled2", json={}, headers={"Origin": "https://other.test"}).status_code
            == 403
        )
        assert client.put("/api/wled2", content="{}").status_code == 415
        invalid = deepcopy(data)
        invalid["lights"][0]["connection"]["controller_id"] = "missing"
        assert client.put("/api/wled2", json=invalid).status_code == 422
        assert client.get("/api/wled2").json() == data
        assert client.get("/api/wled2/defaults").json()["matrix"]["width"] == 16
    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/wled2").json() == data
        assert len(client.get("/api/wled2/status").json()["devices"]) == 1


def test_switches_are_mutually_exclusive_and_preserve_settings_across_restart(app, settings):
    data = scene().model_dump(mode="json")
    # Disabled controllers prove exclusion applies globally, even without active hardware.
    data["controllers"][0]["enabled"] = False
    classic = WLEDConfig(
        devices=[
            {
                "id": "old",
                "name": "Old controller",
                "url": "http://other.test",
                "enabled": False,
            }
        ]
    ).model_dump(mode="json")
    with TestClient(app) as client:
        assert client.put("/api/wled", json=classic).status_code == 200
        assert client.patch("/api/wled", json={"enabled": True}).status_code == 200
        data["enabled"] = True
        assert client.put("/api/wled2", json=data).status_code == 200
        assert client.get("/api/wled").json()["enabled"] is False
        assert client.get("/api/wled2/status").json()["enabled"] is True
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/wled").json()["enabled"] is False
        assert client.get("/api/wled2").json() == data
        etag = client.get("/api/wled2").headers["etag"]
        assert client.patch("/api/wled", json={"enabled": True}).status_code == 200
        assert client.get("/api/wled2/status").json()["enabled"] is False
        data["enabled"] = False
        assert client.get("/api/wled2").json() == data
        assert client.get("/api/wled2").headers["etag"] != etag
        assert client.get("/api/wled").json()["devices"] == classic["devices"]
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/wled").json()["enabled"] is True
        assert client.get("/api/wled2").json()["enabled"] is False


@pytest.mark.parametrize("destination", ["wled", "wled2"])
@pytest.mark.parametrize("failure", ["write", "apply", "cancel"])
def test_failed_switch_restores_previous_integration(
    app, settings, monkeypatch, destination, failure
):
    with TestClient(app) as client:
        client.put("/api/wled2", json={"enabled": destination == "wled"})
        if destination == "wled2":
            client.patch("/api/wled", json={"enabled": True})
        runtime = app.state.runtime
        before = {name: client.get(f"/api/{name}").json() for name in ("wled", "wled2")}
        if failure == "write":
            module = (
                "app.core.profiles"
                if destination == "wled"
                else "app.core.integrations.wled2.service"
            )

            def fail_write(*args):
                raise ConnectionProblem("Disk unavailable")

            monkeypatch.setattr(f"{module}.write_private_json", fail_write)
        else:
            service = runtime.wled if destination == "wled" else runtime.wled2.engine
            configure = service.configure

            async def fail_enable(config, **kwargs):
                if config.enabled:
                    if failure == "cancel":
                        raise asyncio.CancelledError()
                    raise ConnectionProblem("Cannot apply layout")
                await configure(config, **kwargs)

            monkeypatch.setattr(service, "configure", fail_enable)
        if failure == "cancel":

            async def switch():
                with pytest.raises(asyncio.CancelledError):
                    if destination == "wled":
                        await runtime.profiles.configure_wled(WLEDConfig(enabled=True))
                    else:
                        await runtime.configure_wled2(Layout(enabled=True))

            client.portal.call(switch)
        else:
            response = client.request(
                "PATCH" if destination == "wled" else "PUT",
                f"/api/{destination}",
                json={"enabled": True},
            )
            assert response.status_code == 409
        for name in before:
            assert client.get(f"/api/{name}").json() == before[name]
        assert (
            json.loads((settings.data_dir / "wled2.json").read_text())["enabled"]
            == before["wled2"]["enabled"]
        )


def test_conflicting_restored_files_start_only_one_integration(app, settings):
    with TestClient(app) as client:
        client.patch("/api/wled", json={"enabled": True})
    (settings.data_dir / "wled2.json").write_text('{"enabled": true}')
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/wled").json()["enabled"] is True
        assert client.get("/api/wled2").json()["enabled"] is False
        assert client.get("/api/wled2/status").json()["enabled"] is False
        assert json.loads((settings.data_dir / "wled2.json").read_text())["enabled"] is False


def test_moving_objects_does_not_restart_workers(app, monkeypatch):
    with TestClient(app) as client:
        data = scene().model_dump(mode="json")
        client.put("/api/wled2", json=data)
        worker = app.state.runtime.wled2.engine.workers["controller"]
        data["board"]["x"] = 400
        data["lights"][0]["position"]["x"] = 600
        data["controllers"][0]["width"] = 240
        data["lights"][0]["width"] = 350
        assert client.put("/api/wled2", json=data).status_code == 200
        assert app.state.runtime.wled2.engine.workers["controller"] is worker


def test_preview_uses_connected_controller_and_restores_simulator(app):
    with TestClient(app) as client:
        data = scene().model_dump(mode="json")
        data["lights"] = data["lights"][:1]
        assert client.put("/api/wled2", json=data).status_code == 200
        assert client.post("/api/wled2/probe", json={"url": DEMO_URL}).status_code == 200
        before = client.get("/demo-wled/json").json()["state"]
        result = client.post("/api/wled2/preview", json={"target_id": "ring", "duration": 0.5})
        assert result.status_code == 200 and result.json()["restored"] is True
        after = client.get("/demo-wled/json").json()["state"]
        for key in ("on", "bri", "col", "fx"):
            assert after["seg"][0][key] == before["seg"][0][key]
        assert client.post("/api/wled2/preview", json={"target_id": "missing"}).status_code == 409


def test_automation_dispatches_light_rules_and_cleans_up_on_switch(app, monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setattr(
            app.state.runtime.sources["autodarts"].cloud, "game_state", lambda: {"phase": "ready", "available": True}
        )
        data = scene(enabled=True).model_dump(mode="json")
        data["lights"] = data["lights"][:1]
        data["lights"][0]["rules"]["effects"]["match_win"]["color"] = "#123456"
        data["lights"][0]["rules"]["effects"]["match_win"]["effect"] = 0
        assert client.put("/api/wled2", json=data).status_code == 200

        async def dispatch():
            runtime = app.state.runtime
            worker = runtime.wled2.engine.workers["controller"]
            async with asyncio.timeout(3):
                while not worker.sent:  # noqa: ASYNC110 - observe the real worker's output
                    await asyncio.sleep(0.01)
                runtime.bus.publish(
                    "core", "match_win", {"player": {"is_local": True}}, kind="normalized"
                )
                while worker.last_payload["seg"][0]["col"] != [[18, 52, 86]]:  # noqa: ASYNC110
                    await asyncio.sleep(0.01)

        client.portal.call(dispatch)
        assert client.patch("/api/wled", json={"enabled": True}).status_code == 200
        assert not app.state.runtime.wled2.engine.tasks
        assert client.get("/api/wled2/status").json()["enabled"] is False


def test_status_checks_saved_controllers_while_automation_is_off(app):
    """The service answers with a direct controller result, not an assumption."""
    with TestClient(app) as client:
        data = scene().model_dump(mode="json")
        data["lights"] = data["lights"][:1]
        assert client.put("/api/wled2", json=data).status_code == 200

        status = client.get("/api/wled2/status").json()
        assert status["enabled"] is False  # Automation stays off until the layout is enabled.
        controller = status["devices"][0]
        # A direct check, not the automation loop, already reported the controller as reachable.
        assert controller["connected"] is True and controller["error"] is None
        assert [segment["id"] for segment in controller["info"]["segments"]] == [0, 1]


def test_status_reports_unreachable_controller_and_caches_the_check(settings):
    requests = []

    def transport(request):
        requests.append(request)
        raise httpx.ConnectError("refused", request=request)

    app = create_app(settings, transport=httpx.MockTransport(transport))
    data = Layout(
        controllers=[Controller(id="controller", name="Controller", url="http://offline.test")],
        lights=[
            Light(
                id="ring",
                name="Ring",
                kind="ring",
                connection=Connection(controller_id="controller"),
            )
        ],
    ).model_dump(mode="json")
    with TestClient(app) as client:
        assert client.put("/api/wled2", json=data).status_code == 200

        controller = client.get("/api/wled2/status").json()["devices"][0]
        assert controller["connected"] is False
        assert "Cannot connect to WLED" in controller["error"]
        checked = len(requests)
        assert checked == 1  # One direct check, not one per status poll.

        assert client.get("/api/wled2/status").json()["devices"][0]["connected"] is False
        assert len(requests) == checked  # The recent result is reused.


def test_corner_preview_only_applies_to_matrix_panels(app):
    with TestClient(app) as client:
        data = scene().model_dump(mode="json")
        data["lights"] = [
            data["lights"][0],  # Ring on segment 0.
            {
                "id": "panel",
                "name": "Panel",
                "kind": "matrix",
                "connection": {"controller_id": "controller", "segment": 1},
                "matrix": {"width": 16, "height": 8},
            },
        ]
        assert client.put("/api/wled2", json=data).status_code == 200
        body = {"target_id": "panel", "corners": True, "duration": 0.5}
        assert client.post("/api/wled2/preview", json=body).status_code == 200
        assert (
            client.post("/api/wled2/preview", json={**body, "target_id": "ring"}).status_code == 409
        )


def test_corner_toggle_holds_colours_until_it_is_switched_off(app):
    with TestClient(app) as client:
        data = scene().model_dump(mode="json")
        data["lights"] = [
            {
                "id": "panel",
                "name": "My score",
                "kind": "matrix",
                "connection": {"controller_id": "controller", "segment": 1},
                "matrix": {"width": 16, "height": 8, "source": "local_remaining"},
            }
        ]
        assert client.put("/api/wled2", json=data).status_code == 200
        corners = "/api/wled2/controllers/controller/corners"
        assert client.post(corners, json={"target_id": "panel", "on": True}).status_code == 200
        assert client.get("/api/wled2/status").json()["devices"][0]["corners"] == ["panel"]
        # The status carries the frame the panel shows, for the editor canvas.
        face = client.get("/api/wled2/status").json()["devices"][0]["faces"]["panel"]
        assert len(face) == 8 and all(len(row) == 16 for row in face)
        assert set("".join(face)) == {".", "2", "3", "4", "5"}

        # The demo controller reports the panel's four corners, mirrored for serpentine rows.
        pixels = client.get("/demo-wled/pixels").json()["1"]
        assert (pixels[0], pixels[15]) == ("00FF00", "FF0000")
        assert (pixels[7 * 16], pixels[7 * 16 + 15]) == ("FFFFFF", "0000FF")

        assert client.post(corners, json={"target_id": "panel", "on": False}).status_code == 200
        assert client.get("/api/wled2/status").json()["devices"][0]["corners"] == []
        # Switching it off draws the score again instead of the corner markers.
        assert (
            set(client.get("/demo-wled/pixels").json()["1"]) & {"00FF00", "FF0000", "0000FF"}
            == set()
        )


@pytest.mark.parametrize("source, value", [("local_remaining", 321), ("opponent_remaining", 441)])
def test_simulated_frame_updates_all_targets_and_restores_settings(app, monkeypatch, source, value):
    import app.core.integrations.wled.service as lighting

    sent = []
    original = lighting.request

    async def capture(http, device, path, body=None, **kwargs):
        if body and path == "/json/state":
            sent.append(deepcopy(body))
        return await original(http, device, path, body, **kwargs)

    with TestClient(app) as client:
        data = scene().model_dump(mode="json")
        data["lights"] = data["lights"][:2]
        data["lights"][1]["matrix"].update(width=16, height=8, source=source)
        assert client.put("/api/wled2", json=data).status_code == 200
        before = client.get("/demo-wled/json").json()["state"]
        monkeypatch.setattr(lighting, "request", capture)
        response = client.post(
            "/api/wled2/simulate",
            json={
                "event": "triple",
                "local_remaining": 321,
                "opponent_remaining": 441,
                "player": 1,
                "duration": 0.5,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["restored"] is True
        frame = next(body for body in sent if any("i" in seg for seg in body.get("seg", [])))
        assert {seg["id"] for seg in frame["seg"]} == {0, 1}
        panel = next(seg for seg in frame["seg"] if seg["id"] == 1)
        from app.core.integrations.wled.service import matrix_pixels

        matrix = Matrix.model_validate(data["lights"][1]["matrix"])
        expected = matrix_pixels(matrix, value, matrix.appearance)
        assert panel["i"] == [0, *expected]
        after = client.get("/demo-wled/json").json()["state"]
        for old, restored in zip(before["seg"], after["seg"], strict=True):
            for key in ("on", "bri", "col", "fx", "frz"):
                assert restored.get(key) == old.get(key)
        assert client.post("/api/wled2/simulate", json={"player": 2}).status_code == 422
