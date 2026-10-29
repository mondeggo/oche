import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.integrations.wled.service import WLEDConfig
from app.core.integrations.wled.simulator import (
    DEMO_URL,
    EFFECT_COUNT,
    RING_LENGTH,
    Simulator,
    apply_indices,
    create_app,
    effect_metadata,
    effect_names,
)
from app.main import create_app as create_oche_app


def test_effect_names_cover_default_effects_and_fill_gaps():
    names = effect_names()
    assert len(names) == EFFECT_COUNT
    assert names[0] == "Solid" and names[2] == "Breathe" and names[33] == "Rainbow Runner"
    assert names[4] == "FX 4"  # Unused indices still get a valid, non-empty name.


def test_effect_metadata_marks_colorless_effects():
    metadata = effect_metadata()
    assert metadata[9] == "" and metadata[33] == ""  # Rainbow, Rainbow Runner.
    assert metadata[0] == ";Colour,Background,Accent"


@pytest.mark.parametrize(
    "indices, length, expected",
    [
        ([0, 1, "FF0000"], 4, ["FF0000", "000000", "000000", "000000"]),
        ([1, "00FF00", "0000FF"], 4, ["000000", "00FF00", "0000FF", "000000"]),
        # A range chunk followed by a run chunk, as two targets sharing one segment.
        ([0, 1, "FF0000", 2, "00FF00", "0000FF"], 4, ["FF0000", "000000", "00FF00", "0000FF"]),
    ],
)
def test_apply_indices_decodes_both_ocheore_shapes(indices, length, expected):
    pixels = ["000000"] * length
    apply_indices(pixels, indices)
    assert pixels == expected


def test_apply_indices_ignores_out_of_range_positions():
    pixels = ["000000"] * 2
    apply_indices(pixels, [0, 5, "FF0000"])  # Range extends past the buffer.
    assert pixels == ["FF0000", "FF0000"]


def test_segment_renders_solid_color_scaled_by_brightness_until_pixels_are_addressed():
    simulator = Simulator()
    ring = simulator.segments[0]
    ring.apply({"on": True, "bri": 128, "col": [[0, 255, 0]]})
    pixels = ring.rendered_pixels()
    assert len(pixels) == RING_LENGTH
    assert pixels[0] == pixels[-1] == "008000"  # 255 scaled by 128/255, rounded.

    ring.apply({"on": False})
    assert ring.rendered_pixels() == ["000000"] * RING_LENGTH

    ring.on = True
    ring.apply({"i": [0, "123456"]})
    assert ring.rendered_pixels()[0] == "123456"
    assert ring.rendered_pixels()[1] == "000000"  # Addressing one pixel leaves others dark.


async def test_json_endpoint_matches_what_inspect_device_expects():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://demo"
    ) as http:
        data = (await http.get("/json")).json()
        assert isinstance(data["info"]["ver"], str)
        segments = data["state"]["seg"]
        assert [segment["id"] for segment in segments] == [0, 1]
        assert segments[0]["stop"] - segments[0]["start"] == segments[0]["len"]
        assert len(data["effects"]) == EFFECT_COUNT


async def test_state_round_trip_and_auxiliary_endpoints():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://demo"
    ) as http:
        response = await http.post(
            "/json/state", json={"seg": [{"id": 0, "bri": 200, "col": [[10, 20, 30]]}]}
        )
        assert response.status_code == 200 and response.json() == {"success": True}

        state = (await http.get("/json/state")).json()
        assert state["seg"][0]["bri"] == 200 and state["seg"][0]["col"] == [[10, 20, 30]]

        assert len((await http.get("/json/eff")).json()) == EFFECT_COUNT
        assert (await http.get("/json/fxdata")).json()[9] == ""

        pixels = (await http.get("/pixels")).json()
        assert len(pixels["0"]) == RING_LENGTH

        page = await http.get("/")
        assert page.status_code == 200 and "demo WLED controller" in page.text


def test_demo_wled_app_is_reachable_from_a_real_browser_and_shares_state(settings):
    def unexpected_request(request):
        pytest.fail(f"Unconfigured app attempted {request.method} {request.url.path}")

    app = create_oche_app(settings, transport=httpx.MockTransport(unexpected_request))
    with TestClient(app) as client:
        page = client.get("/demo-wled/")
        assert page.status_code == 200 and "demo WLED controller" in page.text

        values = WLEDConfig(
            enabled=True,
            devices=[
                {
                    "id": "demo",
                    "name": "Demo",
                    "url": DEMO_URL,
                    "targets": [{"id": "ring", "name": "Ring"}],
                }
            ],
        ).model_dump(mode="json")
        assert client.put("/api/wled", json=values).status_code == 200

        # The worker reaches the simulator via the httpx mount; this request reaches
        # the same instance via the real /demo-wled mount - proving they share state.
        deadline = time.monotonic() + 3
        ring = {"col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]]}
        while time.monotonic() < deadline and len(ring["col"]) != 1:
            ring = client.get("/demo-wled/json").json()["state"]["seg"][0]
            time.sleep(0.01)
        assert len(ring["col"]) == 1  # Segment-mode payloads only ever send one color.
