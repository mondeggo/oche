# Architecture

```text
Stable launcher
└── Oche application / one FastAPI lifecycle
    ├── Sidebar, Home, Play, Camera Enhancer and System pages, and Settings
    ├── app/core/
    │   ├── AutoDarts cloud login and event processing
    │   ├── WLED lighting and visual layouts
    │   ├── Caller and voice library
    │   ├── Profiles and scoreboard
    │   └── REST and WebSocket routes
    ├── HTTPS, persistent settings, panels and updates
    └── AutoDarts detection supervisor
        └── External detection binary
```

`app/main.py` owns the shared HTTP client, runtime startup and shutdown. All darts
routes access the in-process runtime directly. The application's health check
does not require a cloud login; cloud readiness is reported separately.

Every page renders the same sidebar (`app/templates/sidebar.html`). Camera Enhancer
appears under Integrations; the local Autodarts supervisor appears under System.
Home is one document, so switching between its sections preserves caller playback and draft
form state. Its pages use direct routes such as `/integrations/caller`, `/profiles`
and `/events`; old hash links are normalized on entry. The retained navigation
shell maps those routes to one Home document and updates browser history without
reloading audio or drafts. Camera Enhancer uses `/integrations/cameras`, with a
redirect from the old supervisor query URL. Home, Camera Enhancer and System pages are
retained during application navigation to keep browser audio, camera previews and
setup sessions attached; when a retained page is shown again, only the sidebar's
visibility and panel list are refreshed. Design tokens live in
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

`app/core/local.py` owns local X01 state. `app/core/autodarts/local.py` reads local
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
source; Scoreboard checks the same selection before displaying a game. The sidebar
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
