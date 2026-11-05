# Architecture

```text
Stable launcher
└── Oche application / one FastAPI lifecycle
    ├── Sidebar, Home, Play, Camera Enhancer and System pages, and Settings
    ├── app/core/
    │   ├── Event processing and the board API client (app/core/board/)
    │   ├── WLED lighting and visual layouts
    │   ├── Caller and voice library
    │   ├── Profiles
    │   └── REST and WebSocket routes
    ├── HTTPS, persistent settings, panels and updates
    └── app/core/integrations/registry.py
        ├── autodarts/  board software, setup terminal, Camera Enhancer, cloud account, Play on Autodarts
        └── ochecore/   offline board service, setup terminal and management page
```

Integrations that install and run a program live in their own package under
`app/core/integrations/<id>/`, with their routes, templates (`<id>/<name>.html`)
and static files (`/static/integrations/<id>/`). Each one registers an
`Integration` in `registry.py`: buttons below Play, pages under Integrations,
its Updates entry (how to find, check and unpack a release), its process and port,
a vision engine, and shutdown hooks. The rest of Oche reads only the registry:
`main.py` mounts what is registered, the sidebar and Settings list it, Updates
checks it, and Play offers its engines. A switch on the Integrations page turns an
integration on or off (`integrations.<id>.enabled` in `data/settings.json`);
switching on a missing vision engine submits a verified background installation
through the existing updater. Integration cards show progress, failures and a
retry button. Already installed engines are reused. Installation requires the
Docker launcher and does not start an engine or change Play's camera owner.
Switching it off hides what it adds and stops its engine, and keeps its files and
settings. Registration order is fixed by `order`, then id.

Integrations also extend Home and the runtime. Home has slots for their templates
(`notices` above every view, the Overview `board` heading and `tiles`, and the
`account` area at the top of Integrations) and their own views with routes,
titles and scripts; Home sends `/api/status` to those scripts as an `oche:status`
event. Only enabled integrations contribute sidebar status indicators. Each
engine combines process, detection and event readiness in one dot; its registered
module (`health(id, status, tr)`) supplies event health. Autodarts uses cloud
events outside local games; local play uses the local event connection. An
unselected engine is grey, and unsupported scoring stays amber rather than
claiming readiness. A grey Manual indicator appears when Play selects manual
scoring (also the fallback without enabled engines), and disappears when an
engine is selected. `/api/status` supplies the engine snapshot and registered
indicators so open pages can refresh them without reloading.
A `source` factory gives the runtime a game source:
the runtime starts it, pauses it during local games, merges its status into
`/api/status` and drops it when the integration is switched off. The Autodarts
integration uses this for the cloud account (`account/`): sign-in, board choice and
Autodarts Play events, with its API under `/api/autodarts/`.

Vision engines share the cameras, so `app/services/engines.py` keeps one chosen
(`detection_engine`, or `manual`): choosing one stops the others, and boot starts
it when **Start on boot** is on. An engine that scores speaks the
Autodarts-compatible board API (`GET /api/state`, `WS /api/events?type=state`),
which core reads through `app/core/board/`. Engine releases are
installed into `data/modules/<id>`. OcheCore keeps its settings in
`data/ochecore/board.toml` and listens on the LAN, like Autodarts.

Both engine pages embed the shared setup terminal and link to the engine's HTTP
UI on the Oche host (Autodarts port 3180, OcheCore port 5180). Each terminal runs
the engine's `remote` client in its own PTY. The shared transport checks browser
origins, limits concurrent sessions to four, validates input and resize messages,
and reaps the client on disconnect; closing setup leaves the daemon running.

`app/main.py` owns the shared HTTP client, runtime startup and shutdown. All darts
routes access the in-process runtime directly. The application's health check
does not require a cloud login; cloud readiness is reported separately.

Every page renders the same sidebar (`app/templates/sidebar.html`). Camera Enhancer
appears under Integrations; the local Autodarts supervisor appears under System.
Home pages use direct routes such as `/integrations/caller`, `/profiles` and
`/events`; old hash links are normalized on entry. Navigation is a normal browser
page load, so each route, history entry and reload has one URL owner. Embedded engine
terminals remain iframes inside their own integration pages, but Oche never embeds
another complete Oche application. WLED effects continue in the server-side runtime
when the browser navigates or reloads. Camera Enhancer uses `/integrations/cameras`,
with a redirect from the old supervisor query URL. Design tokens live in
`app/static/css/theme.css`; Home's styles are scoped to `.core-workspace` and map
those tokens. Modal select menus remain inside the Home scope. The theme manager
uses `/api/ui` on the same origin.

Theme and language belong to each application profile. The profile store is
authoritative and mirrors the active appearance to the UI settings service.
Templates and browser code share bundled catalogues in `app/static/locales`.
Language changes save pending edits before reloading; normal navigation retains
the existing views. See [interface languages](I18N.md) for migration and message
editing conventions.

The detector, terminal and camera tools retain their own responsibilities. The
detector is an external executable, while account authentication, profiles,
lighting and caller processing belong to Oche. Use one application worker to avoid
duplicating board connections and device output.

`app/core/local.py` owns local X01 state. `app/core/board/local.py` reads local
detector WebSocket state events, with HTTP snapshots and a polling fallback.
The detector defaults to loopback; `OCHE_CORE_LOCAL_AUTODARTS_URL` selects a
separate LAN detector. Local frames use the existing event normalizer and integrations. Runtime source
selection pauses cloud processing during a local game and restores a silent
cloud baseline afterward. Local data lives in `data/core/local-game.json`.
See [local games](LOCAL_GAMES.md) for the detector contract and the unresolved
Autodarts v2 offline-scoring limitation.

Normalized events identify their play source (`cloud` or `local`). Each integration
persists independent `run_on_cloud` and `run_on_local` settings. Caller and lighting
filter both events and current snapshots, including queued events from the previous
source. The sidebar
reports the Autodarts process, local detector readiness and cloud status in that
order on a single row. Local readiness reads the configured detector state separately
from game scoring, whether or not a local game is active. It does not depend on
cloud connectivity or on the availability of scored throws.
For a remote detector, readiness does not depend on the bundled local process.

The Events page renews a ten-second local feed monitoring lease through the events
API. Raw WebSocket subscriptions and debug recording also keep that same read-only
connection alive. Raw local states identify `source: autodarts-local`; monitoring
without a local game never generates normalized game events or changes scores.
The UI labels and filters both event streams by cloud/local source.

`app/services/metrics_history.py` samples device metrics on the application's
lifecycle, independently of browser pages. The default is 120 samples over 60
minutes, at 30-second intervals. Retention is configurable from 5 to 1,440 minutes,
sampling from 5 to 300 seconds, with a 5,000-sample limit. Samples stay in RAM;
settings are saved in `data/settings.json`. Disabling history clears samples and
stops collection, while live dashboard values continue updating. The system
metrics reader briefly caches snapshots so simultaneous dashboard/history reads
do not reset CPU and I/O measurement intervals against each other.

The data root is independent of application releases. Credentials are not returned
in status responses. Mutations enforce browser origin checks, JSON API writes
require JSON, and WebSocket streams validate their browser origin. Persistent
profile updates and release activation use atomic replacement.
