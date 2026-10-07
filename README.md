# Log Triage

Upload application logs and see the handful of problems behind thousands of lines.
Similar messages are grouped into **clusters**, ranked by frequency and recent trend,
and an optional LLM call explains what a cluster probably means.

**Stack:** Python, FastAPI, SQLAlchemy, PostgreSQL, React (Vite), Docker Compose, pytest.

## How it works

```
log file ──► parse line ──► normalize ──► fingerprint ──► aggregate in memory ──► upsert
             (ts, level,    (IDs, IPs,    (sha1 of the    (counts, hourly          clusters,
              message)       numbers →     template)       buckets, samples)        buckets,
                             placeholders)                                          samples
```

1. **Normalize.** `User 4821 failed login from 10.0.3.7` and `User 977 failed login from 10.0.9.1`
   both become `user <num> failed login from <ip>`. Specific patterns (UUID, IP) run before the
   generic number rule, otherwise the number rule would break IPs apart.
2. **Fingerprint.** The normalized text is hashed. Same hash and same level means same cluster.
3. **Aggregate in memory, write once.** An upload is first reduced to one record per cluster, then
   written with a few chunked queries. Database work scales with the number of *distinct problems*,
   not the number of lines.
4. **Store aggregates, not raw lines.** Each cluster keeps total count, first/last seen, hourly
   counts (for the chart) and up to 5 sample lines. Storage grows with distinct problems.
5. **Trend.** Compares the newest hour in the data with the hour before it:
   `new` (first appeared in the latest hour), `rising` (at least 5 now and at least 2x the previous hour),
   `falling`, `steady`, `quiet`. It uses the data's own latest hour, not the wall clock, so an
   old log file still gives meaningful trends.
6. **LLM summary (optional).** Only the pattern plus up to 5 sample lines are sent, never the whole log.
   Timeout, retry with exponential backoff on 429/5xx, and a deterministic fallback when there is
   no API key or the call fails. Only real LLM answers are cached.

## Run it

**Local development (PostgreSQL via Docker):**
```bash
docker compose up -d db                 # Start persistent PostgreSQL only
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

cd ../frontend
npm install
npm run dev                               # http://localhost:5173 (proxies /api to :8000)
```
The backend and local PostgreSQL connection settings are in the project-root `.env`.
If it doesn't exist, create it with `cp .env.example .env` and add your Gemini key.
The `pgdata` Docker volume persists database contents across restarts; use
`docker compose down -v` only if you intend to delete them.

**Run the whole stack in Docker:**
```bash
docker compose up --build                 # UI on http://localhost:8080
```
Configure Gemini in the root-level `.env` file (ignored by Git): set `GEMINI_API_KEY`,
`LLM_PROVIDER=gemini`, and set `GEMINI_MODEL` and `GEMINI_API_VERSION` there to the
model ID and API version supported by your Google AI Studio API key.
The backend already supports both Gemini and Anthropic. If both keys are set,
`LLM_PROVIDER` selects the provider; otherwise Anthropic is preferred.
Compose passes the database's internal service address to the containerized backend;
the local backend uses `localhost:5432`.

**Demo data:**
```bash
python tools/generate_logs.py --lines 200000 --out sample.log
```
Upload `sample.log` in the UI. It contains steady background noise, one pattern that surges in the
final hour (`rising`) and one that first appears in the final hour (`new`).

**Tests:** `cd backend && python -m pytest` (19 tests: fingerprinting, parsing, API, trends, edge cases).

## Open in VS Code

Prerequisites: Python 3.10+, Node.js 18+, VS Code.

1. Unzip the project and run the one-time setup from the project folder:
   - macOS / Linux: `bash scripts/setup.sh`
   - Windows (PowerShell): `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`
2. Start Docker Desktop, then start the persistent PostgreSQL service from the project root:
   `docker compose up -d db`
3. `File > Open Folder...` and choose the `log-triage` folder. Accept the prompt to install
   the recommended extensions (Python, Pylance, Docker).
4. Pick the interpreter: `Ctrl+Shift+P` (`Cmd+Shift+P` on macOS) > **Python: Select Interpreter** >
   choose the one inside `.venv`.
5. Start the backend with the debugger: Run and Debug panel (`Ctrl+Shift+D`) > **Backend: FastAPI (debug)** > press F5.
   API docs appear at http://localhost:8000/docs.
6. Start the frontend: `Ctrl+Shift+P` > **Tasks: Run Task** > **Frontend: dev server**, then open http://localhost:5173.
7. Make demo data: **Tasks: Run Task** > **Generate sample log (200k lines)**, then upload `sample.log` in the UI.
8. Run tests: Testing panel (beaker icon), or **Tasks: Run Task** > **Backend: run tests**.

Tip: set a breakpoint in `backend/app/ingest.py` (inside `ingest_lines`), upload a small log, and step
through it. That is the best way to learn the pipeline.

## Benchmarking

The figures below are historical results from the earlier SQLite configuration, not a benchmark
of the current PostgreSQL Compose setup. Re-run the benchmark before quoting performance numbers
on a resume.

To measure the current Compose setup, start the stack, generate the same 200,000-line synthetic
input, then upload it:

```bash
docker compose up --build -d
python tools/generate_logs.py --lines 200000 --out sample.log
curl -sS -F "file=@sample.log" http://localhost:8000/api/ingest
```

The response includes `lines_parsed`, `clusters_created`, `seconds`, and `lines_per_second`.
For a clean first-upload measurement, use an empty development database; uploading the same file
again accumulates counts in existing clusters. Record the database, machine, input size, and
whether the upload created new clusters alongside any reported result.

Historical SQLite result for reference:

| Input | Result |
|---|---|
| 200,000 lines (18 MB) | 10 clusters |
| Ingest time | about 5 s, roughly 38,000 lines/s |
| Re-upload of the same file | 0 new clusters, counts accumulate correctly |
| Trend detection | surge pattern flagged `rising` (8,345 vs 654 previous hour); new pattern flagged `new` |

The demo data is synthetic, so these results demonstrate the pipeline rather than performance on
messy real logs. The cost is dominated by Python regex work per line, not the database.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/ingest` | Upload a log file (max 50 MB) |
| GET | `/api/clusters?sort=count\|trend\|recent&level=ERROR` | Ranked clusters |
| GET | `/api/clusters/{id}` | Detail with sample lines and summary |
| GET | `/api/clusters/{id}/trend` | Hourly counts |
| POST | `/api/clusters/{id}/summarize` | LLM summary (fallback if no key) |
| DELETE | `/api/reset` | Clear all data |

## Design decisions and trade-offs

- **Regex normalization instead of ML clustering.** Fast, deterministic, easy to debug and explain.
  Limit: messages that differ in words (not just values) become separate clusters, and very
  free-form text can fragment. A next step would be merging near-duplicate templates by
  edit distance or token similarity.
- **Hourly buckets.** Cheap and enough for a trend chart. Limit: coarse. Minute buckets would
  catch short spikes but multiply storage.
- **Samples capped at 5 per cluster.** Bounds storage; you lose the full raw history.
- **Whole-file upload.** Simple, with a size cap. Streaming ingestion (a tailing agent or a
  queue) is the real-time version.
- **PostgreSQL for local development and Compose.** The ingest path avoids database-specific
  upsert syntax, which keeps it portable but means two concurrent uploads of
  brand-new clusters could race on the unique constraint (see below).

## Known limitations and what I would do at 100x scale

- **Concurrent uploads:** two uploads creating the same new cluster at once can hit the unique
  constraint. Fix: PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`, or serialize ingestion through a
  worker queue.
- **Memory:** an upload is read fully into memory (50 MB cap). Fix: stream to disk and process
  in batches.
- **Throughput:** parsing and regex work is single-threaded Python. Fix: multiprocessing per file
  chunk, or move the hot path to a compiled language or to the log shipper.
- **No authentication or multi-tenancy.** It is a single-user tool as written.
- **Trend logic is a heuristic** (latest hour vs previous hour), not statistical anomaly detection.
