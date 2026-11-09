**English** | [简体中文](README.md)

# LvJiang (律匠)

> A general-purpose vision RPA engine + plugin architecture

LvJiang is a desktop automation tool built on computer vision, using a "generic engine + plugins" architecture. It performs tedious operation flows automatically by capturing screenshots of a desktop window, running OCR, and simulating clicks. It works on **PC** (mainly Windows; macOS supports ADB mode) and on **Android phones** (the LvJiang APK runs standalone on the device). It **never reads process memory, never injects into the game process, and never uploads your game data**, which keeps anti-cheat risk out of the picture.

The built-in plugin targets 《燕云十六声》 (*Where Winds Meet*) gear tuning, and the plugin mechanism can be extended to other games and scenarios.

> **Current status**: LvJiang can drive a PC game window directly (including background capture), drive Android through **screen mirroring**, an **Android emulator** or **ADB**, and run standalone on a phone via the LvJiang APK. When operating a PC game window directly, start LvJiang as administrator.

<p align="left">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%E2%80%933.12-blue">
  <img alt="GUI" src="https://img.shields.io/badge/GUI-PyQt6-41CD52">
  <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Android-0078D6">
  <img alt="License" src="https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-0099cc">
</p>

> 📖 **User guide** (Chinese): [Quick start](docs/60-userguide/01-quick-start.md) | [Contents](docs/60-userguide/README.md)

---

## ✨ Features

### General

- **Two input backends**: "window mode" captures any PC window (windowed game, screen-mirroring window, emulator; foreground SendInput, background PostMessage or background Graphics Capture) and "ADB mode" uses screencap / scrcpy video streams with touch injection or the on-device proxy channel. When the video stream drops, tasks wait for recovery instead of recognizing and clicking on a frozen last frame.
- **Ready-made connection presets**: three built-in presets (mobile game / PC game / mobile screen mirroring) bundle the gallery, environment and layout — the three settings that must match — into one choice picked by how you run the game. A connection that contradicts the preset is rejected up front, so whole-screen coordinate drift surfaces as an error instead of silence.
- **Vision pipeline**: `mss` / scrcpy capture → RapidOCR (ONNX) text recognition → structured parsing → rarity and value scoring.
- **Declarative scenes and layouts**: Scene / Area / Point / Region are defined in YAML, annotated with the built-in visual editor and scaled to the actual resolution; includes gallery management (reference images / groups / template matching) and world map management (region catalog / stitched probe maps).
- **Workflow DSL engine**: a purpose-built `.wf` domain-specific language with variables, conditions, loops, sub-workflow calls and built-in functions that turns "scan → recognize → decide → click" into reusable flows. It ships a CoordRef coordinate type system with vector arithmetic, `full by` exhaustive and short-circuit matching modes, panel-range indexing, and pause/resume. Tooling includes a script workbench (visual composition, point/color picking, single-step debugging, built-in function browser), macro recording, image/color primitives and script groups.
- **Command-line runner**: `lvjiang-cli` (or `python -m lvjiang` from a source checkout) lists scripts, shows their parameters and executes a task without opening the UI, sharing the same engine, config snapshot and user locks as the GUI — suited to scheduled jobs and external orchestration.
- **Human-like input**: randomized delays before and after clicks, random coordinate offsets and region-center jitter reduce mechanical patterns.
- **Auto-plot dialogue library**: maintain a library of dialogue options and let tasks pick the best OCR match automatically, with a built-in skip-the-plot workflow ready to schedule.
- **Accounts and multi-device sync**: after signing in you can sync the character roster, documents and the profile overview ledger to the cloud, using an explicit initial sync followed by incremental reconciliation that only pulls cloud changes and only pushes local edits; the phone can either sync as a peer or keep using offline packages pushed from the PC. Without signing in and syncing, everything stays on the local machine.
- **i18n**: `tr()` covers the core modules and the UI ships 12 languages (简体中文, 繁體中文, English, 日本語, 한국어, Deutsch, Français, Español, Português, Русский, ไทย, Tiếng Việt), either following the system language or selected manually in settings.
- **Editor support**: a VS Code extension (`editors/vscode/lvjiang-wf`) provides `.wf` syntax highlighting, completion, go-to-definition and cross-file diagnostics. It is read-only: it never saves or runs a script for you.

### 《燕云十六声》 plugin

- **Gear data management**: player profiles (SQLite) and a gear pool, scanning of worn and bagged gear, OCR affix parsing, fingerprint de-duplication, and wearing, removing, copy-correcting and exporting of real or simulated gear.
- **Auto-tuning**: bag scrolling traversal with fps data-completeness checks, batch filtering and fine-tuning; tuning history and a live overview covering the affix inheritance (承音), transmutation (转律) and dual dingyin (定音) models, plus material (传律石) estimates for gear progression.
- **Graduation-rate analysis**: reverse engineering of base attributes and aggregation of combat attributes, real DPS and graduation rate; a pruned top-10 optimal combination search, a loadout simulator, the skill rotation view, affix value-per-point and development / transmutation advice.
- **Smart tuning (external agents)**: connect an external agent over MCP (for example WorkBuddy or QoderWork) to analyze development and combinations, generate a brand-new tuning configuration and launch it in one click.
- **Loadout plans**: draft editing with explicit saving and pre-filtering of invalid candidates by school rules.
- **Daily task workflows**: orchestration of daily activities such as Jianghu Command, the No-Grind Shop and daily check-in.

### Mobile app (bundled with the 《燕云十六声》 plugin)

- **Standalone execution**: the LvJiang APK ships a Material 3 four-section workbench (Workbench / Tasks / Users / Settings) that runs single and batch tasks on the phone without a PC.
- **Batch and unattended runs**: pick tasks, users and units by configuration group, with drag-ordering, round counts and unattended mode; pause, resume and stop control the whole batch.
- **PC ↔ phone sync**: push configurations (optionally keeping phone-specific task parameters and batch settings) and send task history, tuning history, logs and results back to the PC one way.
- **Floating control**: a floating ball with three states (logo / play / pause) starts and pauses tasks in one tap, snaps to the screen edge to get out of the way, and means you never have to switch back to LvJiang.

---

## 🧱 Architecture

```
PC game window / game running on a phone
    ↓ direct capture / screen mirroring (video stream + reverse control) / ADB / on-device proxy
PC game or mirroring window / ADB connection / LvJiang APK on the phone
    ↓ screenshot
LvJiang: capture → OCR → affix parsing → rule scoring → decision → simulated click
    ↓ clicks delivered via window input / mirroring protocol / ADB / accessibility gestures
Action performed on PC or phone
```

| Layer | Modules | Responsibility |
|-------|---------|----------------|
| Capture / input | `src/lvjiang/core/capture_base.py`, `core/desktop/`, `core/android/` | Unified screenshot and click abstraction; desktop windows (including background capture) plus ADB / scrcpy / on-device proxy backends |
| OCR | `src/lvjiang/core/ocr.py` | RapidOCR (ONNX Runtime) wrapper |
| Scenes / layouts | `src/lvjiang/core/scene_definition.py`, `core/layout_manager.py` | Declarative UI model loading, coordinate conversion, region alignment |
| Recognizers | `src/lvjiang/core/recognizers/` | Pluggable recognizers (OCR / template matching / color features / reference images) |
| Configuration | `src/lvjiang/core/config/` | App config, session management, user data, workflow config loading and merging, online config installation |
| Workflows | `src/lvjiang/workflows/` | `.wf` DSL parsing (grammar/), execution engine (engine/), shared built-in functions (builtins/) |
| Command line | `src/lvjiang/core/task_cli.py` | `--list` / `--exec` entry point that shares the engine, config snapshot and user locks with the GUI |
| Accounts / sync | `src/lvjiang/core/account/`, `core/sync/` | Account identity and session; initial sync and incremental reconciliation of definitions, users, documents and the overview ledger |
| Auto-plot | `src/lvjiang/core/story/` | Dialogue option library matching by OCR similarity |
| Smart tuning | `src/lvjiang/core/ai/`, `core/agent_*.py` | External agent access over MCP, document discovery and the business-operation contract |
| Anonymous telemetry | `src/lvjiang/core/telemetry/` | Anonymous tuning data collection, field allow-list validation and transport |
| i18n | `src/lvjiang/i18n/`, `config/i18n/` | Runtime `tr()` translation, 12 language tables and Qt standard-dialog translations |
| UI | `src/lvjiang/ui/` | PyQt6 main window, scene editor, recognition test, script workbench, batch, mobile device, account dialogs and run control |
| Plugins | `src/lvjiang/apps/<name>/` | Game/scenario plugins (recognizers, workflows, UI tabs) |
| 《燕云十六声》 plugin | `src/lvjiang/apps/yysls/` | Gear management, scoring, tuning, material recognition, graduation rate, loadouts, smart tuning and its UI (config/ + core/ + workflows/ + sync/ + ui/) |
| On-device | `src/lvjiang/core/ondevice/`, `android/` | Android standalone runtime: accessibility capture/gesture bridge, device-side rapidocr adapter, Kotlin host project |

See the [architecture docs](docs/30-architecture/README.md) and the [DSL grammar reference](docs/30-architecture/32-grammar/README.md) for more detail. (Both are in Chinese.)

---

## 📂 Repository layout

```
lvjiang/
├── src/lvjiang/               # Generic vision RPA engine (src layout, the only importable package)
│   ├── __main__.py            # Entry point (python -m lvjiang [-reg <plugin> | --list | --exec])
│   ├── app.py                 # QApplication entry
│   ├── _version.py            # Version number
│   ├── core/                  # Capture / input / OCR / scenes / layouts / coordinate types
│   │   ├── coord_types.py     # CoordRef coordinate type system (CoordRef/RectCoordRef/CircleCoordRef/Offset)
│   │   ├── capture_base.py    # Capture abstraction (desktop window / ADB / scrcpy / background capture)
│   │   ├── config/            # Config loading and sessions (app.yaml / session / users / workflows / online config)
│   │   ├── desktop/           # Desktop mirroring-window backend (capture + SendInput / PostMessage clicks)
│   │   ├── android/           # ADB / scrcpy backend
│   │   ├── ondevice/          # On-device (Chaquopy) accessibility capture/input and OCR adapter
│   │   ├── recognizers/       # Pluggable recognizer package (OCR / template / color / reference image)
│   │   ├── account/           # Account identity and login state
│   │   ├── sync/              # Cloud peer sync (definitions / users / documents / incremental reconciliation)
│   │   ├── story/             # Auto-plot dialogue library matching
│   │   ├── ai/                # Smart tuning: external agent access (MCP) and contracts
│   │   ├── telemetry/         # Anonymous tuning data collection and transport
│   │   ├── license/           # Feature activation and licensing
│   │   ├── profile/           # Player profiles
│   │   ├── task_cli.py        # Command-line list / exec entry point
│   │   ├── scene_definition.py      # Declarative scene model loading
│   │   ├── layout_manager.py        # Layout index and coordinate conversion
│   │   ├── reference_db.py          # Reference image library
│   │   ├── platforms.py             # Cross-platform unification point
│   │   └── crash_handler.py         # Crash log collection
│   ├── workflows/             # Workflow DSL grammar, engine and shared built-ins
│   │   ├── grammar/           # .wf grammar definition (Lark) and parser
│   │   ├── engine/            # Execution engine (control flow / data operations / panel recognition)
│   │   ├── base/              # DSL primitive base classes (recognition / coordinates / panels / actions)
│   │   ├── builtins/          # Shared built-in functions (arithmetic / strings / system)
│   │   └── implementations/   # Plugin-level workflow implementations (bag traversal / tuning)
│   ├── i18n/                  # i18n runtime
│   ├── ui/                    # PyQt6 user interface
│   │   ├── scene_editor/      # Scene editor (coordinate annotation / region drawing)
│   │   ├── reference/         # Reference gallery UI
│   │   ├── reference_manager/ # Reference gallery management
│   │   ├── batch/             # Batch run panel
│   │   ├── background/        # Background jobs: online config update and telemetry upload
│   │   ├── main/              # Main window and its mixins (run control / window ops / menus / tray)
│   │   ├── scripts/           # Script workbench (composition, point/color picking, single-step debugging)
│   │   ├── macros/            # Macro recording
│   │   ├── map_manager/       # World map management (region catalog / map stitching)
│   │   ├── mobile/            # Phone sync and device tools
│   │   ├── profile/           # User / profile UI
│   │   ├── ocr/               # Recognition dialog and canvas
│   │   └── notices/           # Announcements / updates / about / feedback
│   └── apps/                  # Plugin registry
│       ├── base.py            # AppHooks dataclass
│       └── yysls/             # 《燕云十六声》 plugin
│           ├── config/        # Config management (manager / profile / graduation sessions / playstyles)
│           ├── core/          # Core business logic
│           │   ├── agent/         # Smart tuning
│           │   ├── attr_model/    # Combat attribute model
│           │   ├── combat/        # Combat attributes and gear model
│           │   ├── damage/        # Training-dummy damage / DPS
│           │   ├── equip_parser/  # Gear affix parsing (including dingyin affixes)
│           │   ├── evaluator/     # Scoring rule engine
│           │   ├── graduation/    # Graduation rate (Excel formulas → Python)
│           │   ├── loadout/       # Loadouts
│           │   ├── profile_engine/ # Profile persistence and regeneration math
│           │   ├── recognizer/    # Material recognition
│           │   ├── role_attr_parser/ # Character panel attribute parsing
│           │   ├── tuning_history/ # Tuning history
│           │   └── tuning_rules/  # Tuning rule parsing and evaluation
│           ├── workflows/     # Plugin workflows and built-in functions
│           │   ├── builtins/      # Bag / gear / profile built-ins
│           │   └── implementations/ # Auto-tuning / bag traversal implementations
│           ├── sync/          # Plugin document sync adapters (config groups, playstyles, scripts …)
│           ├── ondevice/      # Plugin on-device adapters
│           ├── telemetry/     # Plugin telemetry schemas
│           └── ui/            # Plugin UI tabs
│               ├── game_settings/ # Game rule configuration panel
│               ├── loadout/       # Gear display and plan panel
│               ├── tune_settings/ # Tuning rule editor
│               └── tuning/        # Tuning management, live overview and history details
├── android/                   # Android standalone runtime (Kotlin + Chaquopy host project)
├── config/
│   ├── system/                # Config shipped with each release (online config and member content install here)
│   │   ├── layouts/           # Layout definitions (one directory per name)
│   │   ├── scenes/            # Scene definition YAML
│   │   ├── workflows/         # Workflow scripts (.wf)
│   │   ├── references/        # Reference galleries ({space}.yaml + an image directory of the same name)
│   │   ├── templates/         # Bundled templates
│   │   ├── yysls/             # Plugin-specific config (tuning rules / playstyles …)
│   │   ├── app.yaml           # Application config
│   │   ├── layouts.yaml       # Layout index
│   │   ├── scenes.yaml        # Scene index
│   │   └── ocr.yaml           # OCR recognition and text normalization config
│   ├── i18n/                  # UI translations (12 languages, shipped with each release)
│   ├── updates/               # Downloaded online config archives and install state (created at runtime)
│   ├── local/                 # User overrides (git-ignored)
│   └── session/               # Runtime session data
├── data/                      # Material template images, scrcpy-server, ADB / APK assets
├── docs/                      # Layered documentation (Chinese)
├── editors/vscode/lvjiang-wf/ # VS Code extension: .wf highlighting and language service
├── ops/                       # Anonymous telemetry: stats-worker (server) and stats-client (local console)
├── packaging/                 # PyInstaller packaging config and scripts
├── scripts/                   # Helper scripts and manual tests
├── tests/                     # pytest suites (core / ui / workflows / apps / yysls)
├── dev.bat                    # Windows launcher script
├── dev.sh                     # macOS launcher script
└── pyproject.toml             # Project metadata and dependencies
```

---

## 🔧 Requirements

- **OS**: Windows (window mode and background capture rely on Win32 APIs), macOS 11+ (ADB mode; window mode is not implemented) or an Android 8.0+ device running the LvJiang APK
- **Python**: 3.11–3.12 (3.11 is the first version with high-precision `time.sleep` on Windows, which precise timing such as training-dummy damage measurement depends on; RapidOCR 1.4.4 requires Python <3.13)
- **Game**: 《燕云十六声》, with any one of these setups:
  - **Mobile game**: **screen mirroring** (a PC-side window that can be captured and that forwards mouse clicks to the phone — vivo's built-in mirroring is the reference, not a brand requirement) or **ADB mode** (USB / wireless debugging enabled on the phone, optionally with the scrcpy video stream)
  - **PC game**: capture the game window directly, in the foreground or in the background (PostMessage, background capture)

---

## 🚀 Installation

```powershell
# 1. Clone the repository
git clone git@github.com:wanda1416/lvjiang.git
cd lvjiang

# 2. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies (including dev extras)
pip install -e ".[dev]"
```

[uv](https://docs.astral.sh/uv/) works too: `uv sync` installs exactly what `pyproject.toml` and `uv.lock` describe.

Key dependencies: `PyQt6`, `mss`, `pynput`, `rapidocr-onnxruntime`, `onnxruntime`, `PyYAML`, `lark` (DSL parser), `loguru`, `av` (H.264 decoding for scrcpy mode), `openpyxl` (graduation-rate Excel formulas), `mcp` (smart tuning), `keyring` (license tokens), `cryptography` (license signature verification), `httpx` (network access and sync), `windows-capture` (Windows background capture).

---

## ▶️ Usage

> 🧑‍💻 **First time here?** Start with the [user guide](docs/60-userguide/README.md) (Chinese) — it walks through connecting a phone, launching the app and running auto-tuning.

**Windows users** can download the latest release from [Releases](https://github.com/wanda1416/lvjiang/releases) — a portable zip or an installer exe; run `lvjiang.exe` afterwards, no Python needed. **Android users** can install the LvJiang APK shipped with each release and run tasks standalone on the phone.

> With the installer, choose "install for the current user only". "Install for all users" is more likely to be blocked by security software.

**From source** (macOS / Linux / latest features):

```powershell
# Option 1: generic engine only (scene editing + recognition tests)
python -m lvjiang

# Option 2: load the 《燕云十六声》 plugin (generic engine + gear tuning)
python -m lvjiang -reg yysls

# Option 3: Windows helper script (sets PYTHONPATH, loads no plugin by default)
.\dev.bat                         # generic mode
.\dev.bat -reg yysls              # generic + built-in plugin
.\dev.bat -reg yysls_premium      # generic + an external plugin under plugins/
.\dev.bat -reg foo@..\foo         # generic + an external plugin anywhere (NAME@PATH)
.\dev.bat --all                   # all built-in plugins plus everything under plugins/
.\dev.bat --all-plugins           # same as --all
.\dev.bat --admin                 # private admin console (needs an admin/ checkout)
.\dev.bat --stats                 # local telemetry console from ops/stats-client
```

> With the src layout, the `lvjiang` package lives under `src/`: `pip install -e .` makes it importable, otherwise add `src` to `PYTHONPATH` (which `dev.bat` already does).

**From the command line** (no UI; suited to scheduled jobs and external orchestration):

```powershell
# List the scripts that can run
python -m lvjiang --list

# Show the parameter definition of one script
python -m lvjiang --list --id haoyun_lianji

# Run once (the release ships lvjiang-cli.exe with the same arguments)
python -m lvjiang --exec --id haoyun_lianji --user 张三 --target adb --yes --json
```

> Full parameters, exit codes and JSON output: [Appendix D: command line](docs/60-userguide/appendix-d-command-line.md) (Chinese).

Once the PyQt6 main window is up, the basic flow is:

1. **Connect**: refresh and pick a mirroring window, a PC game window, or connect an ADB device.
2. **Pick a preset**: choose "Mobile game", "PC game" or "Mobile screen mirroring" in the top "Preset" dropdown; gallery, environment and layout are configured accordingly.
3. **Calibrate**: annotate or verify each UI region in the scene editor (on first use, or after the game UI changes).
4. **Manage gear**: scan worn and bagged gear, then manage profiles, scores, simulated gear and plans.
5. **Analyze and run**: compute graduation-rate combinations and loadouts, or run batch auto-tuning / smart tuning.
6. **Phone (optional)**: push configurations to the LvJiang APK and run or collect results on the phone.

> Detailed workflows are in the [user guide](docs/60-userguide/README.md) and the [auto-tuning flow](docs/20-requirements/10-tuning/01-auto-tuning.md) (both Chinese).

---

## ⚙️ Configuration

Configuration is split into three layers — system defaults → user overrides → runtime session — and is always read as a merged view. With "receive online config updates" enabled, official fixes and member content are installed cumulatively into `system` by revision index, while `local` still wins.

| Layer | Path | Contents | Committed to the repo |
|---|---|---|---|
| System defaults | `config/system/` | Scenes / workflows (`.wf`) / layouts / input parameters / connection presets / tuning rules / reference images | ✅ |
| User overrides | `config/local/` | Key-level config differences, user scripts and other local entities; development builds also keep resources for external plugins there | ❌ |
| Runtime session | `config/session/` | `session.json` (run state), `interface.json` (current user / layout / preset and other UI selections), user data and lock files | ❌ |
| Update archive | `config/updates/` | Downloaded online config packages and install state, used to install into `system` incrementally and to roll back | ❌ |

Task results and logs are not under `config/`: result JSON lives in `logs/output/`, while task, batch and tuning logs live in `logs/daily/`, `logs/batch/` and `logs/tuning/`. UI translations are a separate resource layer (`config/i18n/`, 12 languages) that ships with each release and takes no part in the override chain. The public build never uploads the contents of `config/local/` or `config/session/`. Developers may sync development resources and runtime data through their own repositories; the anonymous install ID should never end up in a repository. See the [privacy statement](PRIVACY.md) for LvJiang's network behavior.

A `config/system/app.yaml` excerpt (input simulation + named delay parameters):

```yaml
input_simulation:
  before_click_wait: [0.1, 0.3]     # randomized delay before a click (seconds)
  after_click_wait: [0.1, 0.3]      # randomized delay after a click (seconds)
  mouse_move_duration: [0.5, 0.7]   # mouse travel time (seconds)
  click_random_offset: 5            # random coordinate offset (pixels)
  region_jitter_ratio: 0.25         # region-center jitter ratio

delay_params:
  page_refresh:
    label:
      zh_CN: 页面刷新等待
      en_US: Page Refresh Wait
    range: [2.0, 3.0]
  scroll_settle:
    label:
      zh_CN: 滚动惯性等待
      en_US: Scroll Settle Wait
    range: [3.0, 4.0]
```

Display names in configuration are `zh_CN` / `en_US` bilingual maps; languages without an entry fall back to Chinese. Workflows reference a named delay by key through `wait <key>` (DSL) or `wait_delay(key)` (code).

---

## 🧪 Development

```powershell
# Run the tests
pytest

# Run a single test file (the suites are split into core / ui / workflows / apps / yysls)
pytest tests/workflows/test_parser.py

# Static checks
python -m ruff check src tests scripts
python -m mypy
```

The test baseline must stay clean: `pyproject.toml` promotes unhandled warnings to errors (`filterwarnings = ["error"]`). Text I/O in the source always specifies UTF-8 explicitly and never relies on the system default encoding.

---

## 📚 Documentation

Documentation is organized into numbered layers; each directory README is its index. All of it is written in Chinese.

| Directory | Contents |
|-----------|----------|
| [`PRIVACY.md`](PRIVACY.md) | Privacy statement: network behavior, account sync, and what anonymous tuning data does and does not send |
| [`docs/`](docs/README.md) | Documentation hub with reader-oriented navigation |
| [`docs/50-releases/`](docs/50-releases/README.md) | Release records and version notes |
| [`docs/60-userguide/`](docs/60-userguide/README.md) | User guide: connecting, configuring, running, graduation rate and the phone app |
| [`docs/70-agent/`](docs/70-agent/README.md) | External agent access, discovery and the business-operation contract |
| [`docs/00-meta/`](docs/00-meta/README.md) | Meta: roadmap, backlog, documentation conventions |
| [`docs/10-game/`](docs/10-game/README.md) | Game mechanics as facts: gear system, schools, damage, tuning / transmutation rules |
| [`docs/20-requirements/`](docs/20-requirements/README.md) | Requirements: runtime environment, operation flows, config model, profiles, graduation rate, i18n |
| [`docs/30-architecture/`](docs/30-architecture/README.md) | Architecture: main-window state machine, plugin system |
| [`docs/30-architecture/31-models/`](docs/30-architecture/31-models/README.md) | Data models: gear model, scene implementation, session and context |
| [`docs/30-architecture/32-grammar/`](docs/30-architecture/32-grammar/README.md) | Workflow DSL grammar specification |
| [`docs/30-architecture/33-engine/`](docs/30-architecture/33-engine/README.md) | Engine internals: capture and cropping |
| [`docs/30-architecture/34-scene/`](docs/30-architecture/34-scene/README.md) | Scene / layout definition and editing |
| [`docs/30-architecture/35-workflows/`](docs/30-architecture/35-workflows/README.md) | Business flow orchestration |
| [`docs/30-architecture/36-graduation/`](docs/30-architecture/36-graduation/README.md) | Graduation-rate engine |
| [`docs/30-architecture/37-vision/`](docs/30-architecture/37-vision/README.md) | Vision subsystem: color / template matching / coordinate consistency |
| [`docs/30-architecture/38-platform/`](docs/30-architecture/38-platform/README.md) | Platform and integration: on-device proxy protocol, anonymous telemetry |
| [`docs/30-architecture/39-runtime/`](docs/30-architecture/39-runtime/README.md) | Runtime capabilities: tuning history, map management, concurrent targets, loadout math |
| [`docs/40-development/`](docs/40-development/README.md) | Development log: monthly refactor and decision records |

---

## 🗺️ Roadmap

The project evolved in stages: skeleton → coordinate calibration → POI capture and OCR → UI state detection → affix parsers → input wrappers → workflow DSL and engine → config-driven rule engine → UI polish → Android standalone runtime → phone batch runs and account sync. Current work is tracked in the [backlog](docs/00-meta/02-backlog.md), platform plans in [Android](docs/00-meta/archive/android-platform-progress.md) and [macOS](docs/00-meta/platforms/macos.md); [roadmap](docs/00-meta/01-roadmap.md) keeps historical stage snapshots only and does not describe the current scope.

---

## 🤝 Contributing

Issues and pull requests are welcome. Before submitting:

1. make sure `pytest` passes, and run at least `ruff check` on code changes (`mypy` for type-related work);
2. follow the existing code style and the layered documentation conventions;
3. when adding scenes or workflows, update `config/system/` and the matching docs, and bump `content_version` on every affected config file.

---

## 💬 Feedback and support

Ways to report problems:

- **WeChat group**: "Help → Feedback" in the main window, then "join group by QR code"
- **GitHub Issues**: [github.com/wanda1416/lvjiang/issues](https://github.com/wanda1416/lvjiang/issues)
- **Reporting rules**: [FAQ and feedback](docs/60-userguide/09-faq-and-feedback.md) (Chinese)

**Scope of support:**

| We can help with | Beyond our capacity |
|------------------|---------------------|
| Bug reports (with logs / screenshots) | One-on-one tutoring |
| Feature requests and requirements | Remotely setting up your environment |
| Documentation that is outdated or unusable | Step-by-step walkthroughs |

The author cannot provide one-on-one tutoring. The [user guide](docs/60-userguide/README.md) and the group already cover the vast majority of use cases, so please read the docs or ask in the group first. A bug report must include the version and environment, reproduction steps, expected and actual results, and continuous logs; recognition or click issues also need a screenshot or screen recording. Reports without enough information are not investigated until they are completed.

---

## ☕ Sponsoring the author

LvJiang is free to use with public source code; features and licensing do not change because of sponsorship. If it saved you time, you are welcome to buy the author a coffee:

<details>
<summary>WeChat / Alipay QR codes (click to expand)</summary>

<p align="left">
  <img alt="WeChat QR code" src="docs/assets/support/wechat.png" width="220">
  <img alt="Alipay QR code" src="docs/assets/support/alipay.png" width="220">
</p>

</details>

Sponsorship is entirely voluntary: it buys no features, licenses or priority support, and affects no existing functionality. You can also open the [sponsorship page](https://lvjiang.wyxj.net/#/support), or "Help → Sponsor" in the main window.

---

## 🔒 Data and privacy

LvJiang only goes online in a few situations: announcements, update checks, online config sync (since 0.8.0), feature activation and online membership licensing (since 0.15.0), account and multi-device sync (since 0.15.9, only after you sign in and press sync), and anonymous tuning data collection (since 0.7.0).

- Announcements, update checks and online config sync **only download, never upload**: announcements and update checks have worked this way since the first release, and online config exists to fix problems such as drifted coordinates or tuning rules remotely. It is on by default and can be turned off item by item in "Configuration → Network & Privacy".
- Feature activation and online membership licensing verify your license code and fetch member content; the first activation needs a network connection, after which the app works offline until expiry.
- Account and multi-device sync happens only after you sign in and press "initial sync / sync". It uploads the character roster, sync documents and the profile overview ledger so that devices can be peers; the phone pulls but does not push by default. Without signing in and syncing, the data stays on the local machine.
- Anonymous tuning data collection requires your explicit consent on first launch — decline and not a single record is sent. Once accepted you can still turn it off in settings at any time and delete the local, not-yet-uploaded records in one click.
- LvJiang **never uploads** your screenshots, logs, gear data, game account or character names, and uploads nothing from the `config/` directory.

See the [privacy statement](PRIVACY.md) for the full text. The server code is public as well: [`ops/stats-worker/`](ops/stats-worker/)

---

## ⚠️ Disclaimer

This project is for learning and technical research only. Automating a game may violate its terms of service; any consequences, including account bans, are the user's own responsibility. Use it in compliance with local laws and the game's terms of service. The author accepts no responsibility for misuse.

---

## 📄 License

Released under the [PolyForm Noncommercial License 1.0.0](LICENSE).

- ✅ Permitted: personal study, research, modification, non-commercial distribution
- ❌ Prohibited: any commercial use (including internal commercial use, commercial distribution, commercial services, etc.)
- 🚫 **No commercial licensing**: commercial use is not allowed under any circumstances — please do not contact the author to ask

The full license text is in [LICENSE](LICENSE).

---

Copyright (c) 2026 律匠作者（lvjiang）
