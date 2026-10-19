# How to run Cinelume

Cinelume is a local web app: one FastAPI process serves both the API and the built
React UI on <http://127.0.0.1:8000>. Nothing leaves your machine except the one-time
Whisper model download.

## Requirements

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11 – 3.13 | 3.13 works |
| FFmpeg | 7.x **full build** | needs `libass`, `afftdn`, `sidechaincompress`, `loudnorm` |
| Node.js | 20+ | only to build the UI (or `npm run dev`) |

## Easy way (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

That installs anything missing via winget, creates `venv\`, installs the backend,
builds the frontend, and can pre-download a Whisper model. Then:

```bat
scripts\start.bat
```

which starts the server on `http://127.0.0.1:8000` and opens your browser.

Stop it with `Ctrl+C` in that window.

## Manual way

From the repository root:

```bat
:: 1. virtual environment
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip

:: 2. backend + tools (add ",dev" for pytest/ruff)
venv\Scripts\python.exe -m pip install -e "backend[asr,dev]"

:: 3. frontend (optional - only for the UI)
cd frontend
npm install
npm run build
cd ..

:: 4. run
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
```

Open <http://127.0.0.1:8000>.

> Run **one** uvicorn worker only — job state (SQLite, process pools) lives in the
> process. That is what `scripts\start.bat` does.

## Frontend development mode

```bat
cd frontend
npm install
npm run dev
```

Vite serves the UI on <http://127.0.0.1:5173> and proxies `/api` to the backend on
port 8000, so start the backend too. With `npm run build` the compiled UI is picked up
by FastAPI automatically (it mounts `frontend/dist` at `/` when the folder exists).

## Download a Whisper model

Subtitles need a model (default `small`):

- **UI** → System screen → Download, or
- **API** → `POST /api/models/small/download` (progress at `/api/models/small/events`).

Without a model everything else still works; jobs just run without subtitles. Loading
is fully offline after the download.

## Command line (no UI)

```bat
venv\Scripts\python.exe -m app.cli cut video.mp4 --out output.mp4 --no-subs
```

Useful flags: `--min-silence 0.5`, `--auto-threshold`, `--no-denoise`, `--no-subs`,
`--options options.json`, `--keep-intermediates`. Subtitles need a downloaded model;
`--no-subs` skips them.

## Configuration

Create a `.env` next to `README.md` (copy `.env.example`). Everything has a default:

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` | uploads, work dirs, outputs, models, music, job DB |
| `FFMPEG_PATH` / `FFPROBE_PATH` | on PATH | full paths override |
| `MAX_UPLOAD_GB` / `MAX_DURATION_MIN` | 10 / 180 | upload limits |
| `MAX_CONCURRENT_JOBS` | 1 | keep at 1 (CPU oversubscription) |
| `UPLOAD_TTL_HOURS` / `WORK_TTL_HOURS` | 24 / 24 | automatic cleanup |
| `HOST` / `PORT` | 127.0.0.1 / 8000 | bind address |
| `ACCESS_TOKEN` | unset | set only if you expose the server; then send `Authorization: Bearer …` |

## Tests

```bat
venv\Scripts\python.exe -m pytest -q
venv\Scripts\python.exe -m ruff check backend
cd frontend && npm run build
```

Two ASR tests are skipped unless `CINELUME_SLOW=1` (they download the `tiny` model).

## Benchmark (tuning)

```bat
venv\Scripts\python.exe -m scripts.benchmark --duration 600 --counts 1,2,4
```

Runs the same synthetic clip through 1/2/4 workers and prints wall time, speedup and
peak RSS.

## Where my files go

Everything lives under `data\` (gitignored): `uploads\<id>\`, `work\<job>\`,
`outputs\<job>\`, `models\`, `music\`, and `jobs.db`. Delete a job from the History
screen (or let the TTL clean it up) to reclaim the space.

## Troubleshooting

See `docs\TROUBLESHOOTING.md` — FFmpeg missing, CUDA DLLs, model download, sync,
disk space, antivirus locks and long paths.
