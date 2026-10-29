"""In-process fake WLED controller for exploring and demoing the WLED integration
without real hardware.

Mounted into the shared httpx client under DEMO_URL via `mounts` (see main.py), so it
never binds a real socket or port: a request to that one fake origin is routed in-process
to the FastAPI app below, and every other origin (real WLED devices, the AutoDarts cloud)
is unaffected. Add it in the UI with "Try a demo controller", or manually with DEMO_URL.

Implements enough of WLED's JSON API for Oche's own inspect_device()/request() to
treat it like a real controller: one 40-LED ring (segment 0, GPIO 2) and one 16x8 matrix
wired as a plain 128-LED strip (segment 1, GPIO 4), matching config/wled.example.json.
Native WLED effects are not actually animated - a non-Solid effect renders as its base
color with the effect name labelled, rather than reimplementing WLED's effect engine.
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

DEMO_URL = "http://oche-demo.local"
RING_LENGTH = 40
MATRIX_WIDTH, MATRIX_HEIGHT = 16, 8
MATRIX_LENGTH = MATRIX_WIDTH * MATRIX_HEIGHT
TOTAL_LENGTH = RING_LENGTH + MATRIX_LENGTH

# Real WLED effect IDs that Oche's own defaults reference (PhaseColours, default_effects),
# so a fresh profile shows correct names instead of "Effect 33" placeholders.
EFFECT_NAMES = {
    0: "Solid",
    1: "Blink",
    2: "Breathe",
    3: "Wipe",
    6: "Sweep",
    9: "Rainbow",
    12: "Fade",
    33: "Rainbow Runner",
}
EFFECT_COUNT = 34
# Effects whose animation does not use the primary color slot (WLED's /json/fxdata format).
COLORLESS_EFFECTS = {9, 33}


def effect_names() -> list[str]:
    return [EFFECT_NAMES.get(index, f"FX {index}") for index in range(EFFECT_COUNT)]


def effect_metadata() -> list[str]:
    return [
        "" if index in COLORLESS_EFFECTS else ";Colour,Background,Accent"
        for index in range(EFFECT_COUNT)
    ]


def apply_indices(pixels: list[str], indices: list) -> None:
    """Decode Oche's own two "i" shapes, distinguished by the second element's type:
    [start, stop, color] for a solid range, or [start, color, color, ...] for a run of
    individually colored pixels (as payload() produces for pixel and matrix targets).
    Concatenated chunks from multiple targets sharing one segment are handled in sequence.
    """
    position = 0
    while position < len(indices):
        start = indices[position]
        following = indices[position + 1]
        if isinstance(following, int):
            stop, color = following, indices[position + 2]
            for index in range(start, stop):
                if 0 <= index < len(pixels):
                    pixels[index] = color
            position += 3
        else:
            position += 1
            while position < len(indices) and isinstance(indices[position], str):
                if 0 <= start < len(pixels):
                    pixels[start] = indices[position]
                start += 1
                position += 1


class Segment:
    def __init__(self, segment_id: int, name: str, start: int, length: int):
        self.id, self.name, self.start, self.length = segment_id, name, start, length
        self.on = True
        self.brightness = 128
        self.color: list[list[int]] = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        self.effect = 0
        self.speed = self.intensity = 128
        self.palette = 0
        self.pixels: list[str] | None = None  # Set once individual pixels are addressed.

    def to_state(self) -> dict:
        return {
            "id": self.id,
            "n": self.name,
            "start": self.start,
            "stop": self.start + self.length,
            "len": self.length,
            "on": self.on,
            "bri": self.brightness,
            "col": self.color,
            "fx": self.effect,
            "sx": self.speed,
            "ix": self.intensity,
            "pal": self.palette,
        }

    def apply(self, body: dict) -> None:
        if "on" in body:
            self.on = bool(body["on"])
        for attribute, key in (
            ("brightness", "bri"),
            ("effect", "fx"),
            ("palette", "pal"),
            ("speed", "sx"),
            ("intensity", "ix"),
        ):
            if key in body:
                setattr(self, attribute, body[key])
        if "col" in body:
            self.color = body["col"]
        if "i" in body:
            if self.pixels is None:
                self.pixels = ["000000"] * self.length
            apply_indices(self.pixels, body["i"])

    def rendered_pixels(self) -> list[str]:
        if self.pixels is not None:
            return self.pixels
        if not self.on:
            return ["000000"] * self.length
        scale = self.brightness / 255
        rgb = self.color[0] if self.color else [0, 0, 0]
        hex_color = "".join(f"{round(value * scale):02X}" for value in rgb)
        return [hex_color] * self.length


class Simulator:
    def __init__(self) -> None:
        self.on = True
        self.brightness = 128
        self.segments = {
            0: Segment(0, "Ring", 0, RING_LENGTH),
            1: Segment(1, "Score matrix", RING_LENGTH, MATRIX_LENGTH),
        }

    def info(self) -> dict:
        return {
            "ver": "0.15.0-sim",
            "name": "Oche demo controller",
            "leds": {
                "count": TOTAL_LENGTH,
                "rgbw": False,
                "wv": False,
                "maxseg": 16,
                "ins": [
                    {"start": 0, "len": RING_LENGTH, "pin": [2], "type": 22, "order": 0},
                    {
                        "start": RING_LENGTH,
                        "len": MATRIX_LENGTH,
                        "pin": [4],
                        "type": 22,
                        "order": 0,
                    },
                ],
            },
            "fxcount": EFFECT_COUNT,
        }

    def state(self) -> dict:
        return {
            "on": self.on,
            "bri": self.brightness,
            "seg": [segment.to_state() for segment in self.segments.values()],
        }

    def pixels(self) -> dict:
        return {segment.id: segment.rendered_pixels() for segment in self.segments.values()}

    def apply(self, body: dict) -> None:
        if "on" in body:
            self.on = bool(body["on"])
        if "bri" in body:
            self.brightness = body["bri"]
        for entry in body.get("seg", []):
            segment = self.segments.get(entry.get("id"))
            if segment is not None:
                segment.apply(entry)


def create_app() -> FastAPI:
    simulator = Simulator()
    app = FastAPI(title="Oche demo WLED controller", docs_url=None, redoc_url=None)

    @app.get("/json")
    def read_json() -> dict:
        return {
            "info": simulator.info(),
            "state": simulator.state(),
            "effects": effect_names(),
        }

    @app.get("/json/state")
    def read_state() -> dict:
        return simulator.state()

    @app.post("/json/state")
    def write_state(body: dict) -> dict:
        simulator.apply(body)
        return {"success": True}

    @app.get("/json/eff")
    def read_effects() -> list[str]:
        return effect_names()

    @app.get("/json/fxdata")
    def read_effect_metadata() -> list[str]:
        return effect_metadata()

    @app.get("/pixels")
    def read_pixels() -> dict:
        return simulator.pixels()

    @app.get("/")
    def view() -> HTMLResponse:
        return HTMLResponse(_PAGE)

    return app


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Oche demo WLED controller</title>
<style>
  body { background: #15151a; color: #e7e7ea; font: 14px system-ui, sans-serif; padding: 24px; }
  h1 { font-size: 16px; font-weight: 650; margin: 0 0 4px; }
  p.hint { color: #9a9aa5; margin: 0 0 20px; }
  .segment { margin-bottom: 20px; }
  .segment h2 { font-size: 13px; color: #9a9aa5; margin: 0 0 8px; font-weight: 600; }
  .ring { display: flex; flex-wrap: wrap; gap: 3px; max-width: 480px; }
  .ring span {
    width: 12px; height: 12px; border-radius: 50%;
    display: inline-block; border: 1px solid #ffffff22;
  }
  .matrix { display: grid; grid-template-columns: repeat(16, 14px); gap: 2px; }
  .matrix span { width: 14px; height: 14px; border: 1px solid #ffffff22; }
  .meta { color: #9a9aa5; font-size: 12px; margin-top: 4px; }
</style>
</head>
<body>
<h1>Oche demo WLED controller</h1>
<p class="hint">
  No hardware here - this page just shows what Oche is sending.
  Refreshes automatically.
</p>
<div id="segments"></div>
<script>
async function tick() {
  try {
    const [jsonResponse, pixelsResponse] = await Promise.all([
      fetch('/json', {cache: 'no-store'}),
      fetch('/pixels', {cache: 'no-store'}),
    ]);
    render(await jsonResponse.json(), await pixelsResponse.json());
  } catch (error) {}
  setTimeout(tick, 200);
}
function render(data, pixels) {
  const root = document.getElementById('segments');
  root.replaceChildren();
  for (const segment of data.state.seg) {
    const section = document.createElement('div');
    section.className = 'segment';
    const heading = document.createElement('h2');
    heading.textContent = `${segment.n} (segment ${segment.id})`;
    section.append(heading);
    const grid = document.createElement('div');
    grid.className = segment.len === 128 ? 'matrix' : 'ring';
    for (const color of pixels[segment.id] || []) {
      const pixel = document.createElement('span');
      pixel.style.background = '#' + color;
      grid.append(pixel);
    }
    section.append(grid);
    const meta = document.createElement('p');
    meta.className = 'meta';
    const effectName = (data.effects || [])[segment.fx] || `FX ${segment.fx}`;
    const state = segment.on ? 'on' : 'off';
    meta.textContent = `${state} - brightness ${segment.bri} - effect ${effectName}`;
    section.append(meta);
  }
}
tick();
</script>
</body>
</html>"""
