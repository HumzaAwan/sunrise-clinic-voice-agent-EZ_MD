# Sunrise Family Clinic

Local AI **voice** receptionist for a fictional EZ MD client (“Sunrise Family Clinic”). Callers speak in the browser; identity is verified in Python before any chart action. **Synthetic data only. This is not a HIPAA certification.**

---

## What callers can do

| Intent | Behavior |
| --- | --- |
| Prescription refill | After verify: confirm med + pharmacy; queue request; never promise approval |
| Next appointment | After verify: speak next visit or offer to book / callback |
| New appointment | Full new-patient or verified-patient booking flow |
| Lab status | After verify, open hours: status only — no interpretation |
| Billing | After verify, open hours: balance due |
| After hours | Closed message + callback (name, number, short reason) — **no PHI** |
| Guardrails | Refuse medical advice; 911 on emergency; refuse third-party chart; resist injection; staff callback (not a live phone transfer) |

---

## Tech stack (and why)

| Layer | Choice | Role |
| --- | --- | --- |
| Desk | React + Vite + Tailwind + LiveKit client | Browser call UI, Chart, Requests, Settings |
| API | FastAPI + SQLAlchemy async | Admin/chart HTTP API on `127.0.0.1:8000` |
| Workflow | LangGraph | Explicit clinic branches (verify, refill, book, after-hours, safety) |
| Realtime media | Self-hosted LiveKit (Docker) | WebRTC room between browser and voice worker |
| STT | Faster-Whisper (host GPU/CPU) | Speech → text inside the worker |
| LLM | Ollama + Qwen 2.5 3B (host) | Intent / field hints / yes-no — **not** identity match or safety truth |
| TTS | Kokoro (Docker, CPU) | Text → speech |
| Database | Postgres (Docker) or SQLite | Patients, slots, refills, appointments, callbacks, transcripts, audit |
| Cache / sync | Redis (Docker) | Scrubbed session mirror + **shared clinic clock** (API ↔ worker) |
| Traces | Langfuse v2 (Docker, optional) | Scrubbed turn traces |
| Config | `.env` / `.env.example` | Secrets stay out of git |

Everything voice-related is intended to stay **on this machine** for the demo (no hosted STT/LLM/TTS).

---

## Architecture (how pieces connect)

### System diagram

```text
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  Browser (http://127.0.0.1:5173)                                         │
 │  Desk · Chart · Requests · Settings · Call panel (mic + transcript)      │
 └───────────────┬─────────────────────────────┬────────────────────────────┘
                 │ REST                        │ WebRTC (audio)
                 ▼                             ▼
 ┌───────────────────────────┐     ┌──────────────────────────────┐
 │  FastAPI  :8000           │     │  LiveKit server (Docker)     │
 │  /api/sessions → token    │     │  ws://127.0.0.1:7880         │
 │  /api/admin/clock         │     └──────────────┬───────────────┘
 │  Chart / Requests / health│                    │ room audio
 │  Reads/writes Postgres    │                    ▼
 │  Reads/writes Redis clock │     ┌──────────────────────────────┐
 └─────────────┬─────────────┘     │  Voice worker (host process) │
               │                   │  livekit_worker / Agent      │
               │                   │  • Faster-Whisper (STT)      │
               │                   │  • CallState (in memory)     │
               │                   │  • run_turn → LangGraph      │
               │                   │  • Ollama Qwen (LLM)         │
               │                   │  • Kokoro TTS via HTTP       │
               │                   └──────┬───────────┬───────────┘
               │                          │           │
               ▼                          ▼           ▼
        ┌────────────┐            ┌──────────┐  ┌────────────┐
        │  Postgres  │◄───────────│  Redis   │  │  Ollama    │
        │  (or SQLite)│  mirror   │  clock + │  │  :11434    │
        │  durable   │  scrubbed  │  session │  └────────────┘
        │  clinic DB │  view      │  keys    │
        └────────────┘            └──────────┘
               ▲
               │ optional scrubbed traces
        ┌──────┴───────┐
        │  Langfuse    │
        │  :3000       │
        └──────────────┘
```

### One call turn (voice path)

```text
Caller speaks
    → LiveKit captures audio
    → Worker STT (Whisper) produces text
    → GenGuard cancels any stale TTS if barge-in
    → run_turn(db_session, CallState, text)
         → LangGraph: safety → office hours → route → domain node
         → Python parsers / verify_identity / slot book / fixed safety lines
         → Ollama used only where allowed (intent, soft extraction, yes/no)
         → Commit durable rows to Postgres/SQLite
         → Append redacted transcript lines
         → Mirror scrubbed CallState view to Redis
         → Optional Langfuse span
    → Worker TTS (Kokoro) speaks `call.say`
    → LiveKit plays audio back to browser
```

### Per-user / per-call session model

| Store | What it holds | Lifetime |
| --- | --- | --- |
| **CallState (worker RAM)** | Source of truth for the live call: `session_id`, phase, intent, draft booking fields, verification ticket state, last agent line, in-memory lines | One LiveKit job / one Call button until End |
| **Postgres / SQLite** | Durable clinic data: patients, meds, slots, appointments, refill requests, callbacks, doctor notes, email confirmations, transcript rows, audit rows | Survives restarts |
| **Redis** | (1) `sunrise:clinic:clock` — Settings override shared by API + worker<br>(2) `sunrise:call:{session_id}` — scrubbed session snapshot (no raw SSN / verify secrets) | Clock until cleared; call key ~1h TTL |
| **FastAPI memory** | Optional admin session map for non-voice paths; chart tools use verification tickets issued after successful verify | Process-local |
| **Browser** | LiveKit room token from `POST /api/sessions`; mic/speaker only — no patient DB in the client | Until End / disconnect |

**Important:** the voice worker and the FastAPI process are **separate**. Chart UI reads the DB via the API. The live conversation state lives in the **worker’s** `CallState`. Redis keeps the **clock** consistent so after-hours Settings actually affect the agent. Redis session mirrors are scrubbed views for ops/desk — not a second brain for the model.

### Database vs Redis (clear split)

```text
Postgres/SQLite                          Redis
─────────────────                        ─────────────────
Patients, meds, labs, balances           Shared clinic clock override
Available slots + bookings               Scrubbed live-call snapshot
Refills, callbacks, doctor notes         (optional ops visibility)
Transcripts (redacted verify turns)
Audit log (action/outcome, minimal PHI)
```

### Clinic graph (control flow)

```text
start
  → safety? (emergency / advice / third-party / injection)
  → office open?
        no  → after_hours (callback form only)
        yes → route intent
              → verify (if chart action needs it)
              → refill | appointment | booking | labs | billing
              → handoff | off_topic
```

Safety lines (911, advice refusal, third-party refusal, recording notice wording) are **fixed Python strings**, not free-form model output.

---

## Limitations (read this)

These are intentional demo constraints, not accidental omissions:

1. **Small local models** — Whisper + Qwen 2.5 3B can mis-hear names, times, emails, and phones, and can hallucinate fields. **Identity match, last-four SSN handling, 10-digit phone checks, slot selection, and safety phrases run in Python.** The LLM is not trusted to authorize PHI or book alone.
2. **Not production HIPAA** — Synthetic data, localhost bind, no signed BAA claim, no multi-tenant auth, no hardened retention/encryption program. README HIPAA section = aligned demo controls only.
3. **One interactive voice call at a time** on this GPU/host class — not a 100-concurrent call center.
4. **Handoff is a callback record**, not a PSTN warm transfer (browser call has no clinic phone bridge).
5. **Email confirmation is simulated** — a DB/log row, not SMTP delivery.
6. **Insurance member ID** may be asked in speech; durable save prioritizes provider / self-pay (member ID capture is limited).
7. **Clinic clock is America/New_York** — “real clock” may show closed while your laptop local time looks open. Use Settings override for demos; override sync needs **Redis**.
8. **Docker services** (LiveKit, Redis, Kokoro, Postgres, Langfuse) must be up for the full voice path; Ollama and the worker run on the host.
9. **No real PHI** — seed patients only. Do not put real patient data in `.env`, logs, or recordings.

---

## Setup from scratch (Windows)

Follow these steps if the machine has **nothing** installed yet. Goal: desk call at http://127.0.0.1:5173 with live mic.

**Hybrid model:** Docker runs Postgres, Redis, LiveKit, Kokoro, Langfuse. The host runs Python API, voice worker (Whisper), Ollama (Qwen), and the React desk. That is intentional so the GPU can serve Whisper/Ollama outside the container.

### 0. Hardware / OS expectations

- Windows 10/11 with admin rights for installs  
- **8 GB+ RAM** (16 GB+ preferred)  
- Disk free: **~20 GB+** (Docker images + Whisper + Qwen)  
- Optional NVIDIA GPU + recent drivers for faster Whisper (`WHISPER_DEVICE=cuda`); CPU works with `WHISPER_DEVICE=cpu`  
- Headphones + mic for the voice demo  

### 1. Install system tools

1. **Git** — https://git-scm.com/download/win  
2. **Python 3.11** — https://www.python.org/downloads/ (check “Add python.exe to PATH”)  
   Confirm:

   ```powershell
   py -3.11 --version
   ```

3. **Node.js 20+ LTS** — https://nodejs.org/  
   Confirm:

   ```powershell
   node --version
   npm --version
   ```

4. **Docker Desktop** — https://www.docker.com/products/docker-desktop/  
   - Enable WSL2 backend when prompted  
   - Start Docker Desktop and wait until it says **Running**  
   Confirm:

   ```powershell
   docker version
   docker compose version
   ```

5. **Ollama** (host LLM) — https://ollama.com/download  
   Confirm the app is running, then:

   ```powershell
   ollama --version
   ```

### 2. Get the code

```powershell
git clone <YOUR_REPO_URL> sunrise-family-clinic
cd sunrise-family-clinic
```

Or unzip the project and `cd` into the `sunrise-family-clinic` folder (the one that contains `docker-compose.yml`, `backend`, `frontend`).

### 3. Python virtualenv + requirements

From `sunrise-family-clinic` (repo root):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
```

| File | What it installs |
| --- | --- |
| [`requirements.txt`](requirements.txt) | Points at `backend/requirements.txt` (API, LangGraph, Redis, LiveKit voice, Whisper) |
| [`backend/requirements.txt`](backend/requirements.txt) | Pinned runtime dependencies |
| [`backend/requirements-dev.txt`](backend/requirements-dev.txt) | Same + `pytest` / `pytest-asyncio` |

First install can take several minutes (LiveKit agents, torch/Whisper wheels, etc.).

**Alternative** (editable package from `pyproject.toml`):

```powershell
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\python.exe -m uv pip install -e "backend[dev,voice]"
```

Either path is fine; graders can use the `requirements.txt` flow alone.

### 4. Frontend packages

```powershell
cd frontend
npm install
cd ..
```

### 5. Environment file (how you get `.env`)

**`.env` is gitignored** (secrets / local overrides). The repo ships **`.env.example`** with safe localhost defaults and no real patient data.

```powershell
copy .env.example .env
```

That is the only step graders need: copy the example, edit if your ports differ, never commit `.env`.

Open `.env` and keep localhost defaults. Important keys:

| Variable | Typical value | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://clinic:clinic@127.0.0.1:5432/sunrise` | Matches Docker Postgres |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Shared clock + session mirror |
| `LIVEKIT_URL` | `ws://127.0.0.1:7880` | Self-hosted LiveKit |
| `LIVEKIT_API_KEY` / `SECRET` | from `.env.example` | Must match `docker/livekit/livekit.yaml` |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Pull in step 7 |
| `WHISPER_MODEL` | `distil-large-v3` | Downloaded on first worker start |
| `WHISPER_DEVICE` | `cuda` or `cpu` | Use `cpu` if no NVIDIA GPU |
| `KOKORO_BASE_URL` | `http://127.0.0.1:8880/v1` | Docker Kokoro |
| `CLOCK_OVERRIDE` | empty | Prefer Settings UI for demos |

Do **not** commit `.env`.

### 6. Start Docker services

With Docker Desktop running:

```powershell
docker compose up -d
docker compose ps
```

Wait until these are healthy/up:

- `postgres` — clinic DB (`5432`)  
- `redis` — (`6379`)  
- `livekit` — (`7880`)  
- `kokoro` — TTS (`8880`)  
- `langfuse-web` — traces UI (`3000`)  

First pull is large (LiveKit + Kokoro + Langfuse images).

Optional: without Postgres, you can point `DATABASE_URL` at SQLite (`sqlite+aiosqlite:///./data/sunrise.db`) — Redis/LiveKit/Kokoro are still required for the full voice path.

### 7. Download the LLM (Ollama)

```powershell
ollama pull qwen2.5:3b
ollama list
```

Confirm `qwen2.5:3b` appears. Leave Ollama running in the background.

### 8. Preload Whisper (optional but recommended)

First voice-worker start downloads the model. To warm it now:

```powershell
.\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('distil-large-v3', device='cpu', compute_type='int8'); print('whisper-ok')"
```

Use `device='cuda'` if you have a working NVIDIA stack and set `WHISPER_DEVICE=cuda` in `.env`.

### 9. Start API, desk, and voice worker

**Option A — helper script** (starts Docker if possible, then API + worker + Vite):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_local.ps1
```

**Option B — three terminals** (clearer for debugging):

Terminal 1 — API:

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 — voice worker (keep this window open):

```powershell
cd backend
..\.venv\Scripts\python.exe -m app.workers.livekit_worker dev
```

Wait until you see the worker **registered** with LiveKit. First run may download Whisper weights.

Terminal 3 — desk:

```powershell
cd frontend
npm run dev
```

### 10. Verify everything

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_system.ps1
```

Or open:

| Check | URL |
| --- | --- |
| Desk (call UI) | http://127.0.0.1:5173 |
| API health | http://127.0.0.1:8000/api/health |
| Langfuse | http://127.0.0.1:3000 |
| Ollama tags | http://127.0.0.1:11434/api/tags |

Health should show LiveKit / Ollama / Kokoro up when those services are running.

### 11. First test call

1. Open http://127.0.0.1:5173 → **Desk**  
2. Pick your headset mic → **Call** → allow microphone  
3. Hear: “Thank you for calling Sunrise Family Clinic. This call may be recorded…”  
4. Try: “I need a refill of my lisinopril.” then  
   “Sarah Johnson, March fourteenth nineteen eighty-five, three three four four.” then “Yes.”  
5. Open **Requests** — refill row should appear  
6. **End** when finished  

Clinic clock is **America/New_York**. If the office shows closed during your local daytime, Settings → override e.g. `2026-11-25T10:00:00-05:00` → **Use override** (needs Redis).

### 12. Automated tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

### 13. Stop

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_local.ps1
docker compose down
```

Stop Ollama from the system tray if you want GPU/RAM freed.

### Setup checklist

- [ ] Git, Python 3.11, Node, Docker Desktop, Ollama installed  
- [ ] `docker compose up -d` — Postgres, Redis, LiveKit, Kokoro, Langfuse  
- [ ] `.venv` + `pip install -r requirements.txt` (+ `backend\requirements-dev.txt` for tests)  
- [ ] `frontend` `npm install`  
- [ ] `.env` from `.env.example`  
- [ ] `ollama pull qwen2.5:3b`  
- [ ] API `:8000`, worker registered, desk `:5173`  
- [ ] Mic call hears greeting + recording notice  

### Common setup failures

| Symptom | Fix |
| --- | --- |
| `docker compose` fails / engine not running | Start Docker Desktop; finish WSL2 install; reboot if asked |
| LiveKit down / Call fails | `docker compose up -d livekit` and confirm `LIVEKIT_*` match `docker/livekit/livekit.yaml` |
| Ollama down | Start Ollama app; `ollama pull qwen2.5:3b` |
| Kokoro / no TTS | `docker compose up -d kokoro`; open http://127.0.0.1:8880 |
| CUDA / Whisper crash | Set `WHISPER_DEVICE=cpu` in `.env`, restart worker |
| After-hours Settings ignored | Ensure Redis is up; override is shared via Redis to the worker |
| Port in use | Stop old API/worker/Vite (`scripts\stop_local.ps1`) |

---

## Identity verification

Caller says **full name**, **date of birth**, and **last four of SSN**. `verify_identity` matches all three to one patient row in Python. The model does not receive SSN digits. Wrong details → “I couldn’t verify those details” (never which field). Three failures → end verify and offer staff callback. Verification turns are stored as a redaction placeholder.

Demo success caller: **Sarah Johnson**, March 14, 1985, last four **3344**. Never speak or display a full SSN.

## Appointment booking

Confirm first visit → name, DOB, 10-digit phone, email, insurance or self-pay, short reason, optional doctor/time → offer up to three slots → read back name/time/doctor/reason and **spell the email** → on yes: write patient + appointment + confirmation, remove slot, speak arrive-15-minutes instructions.

## After hours

Timezone: **America/New_York**, Mon–Fri 9:00–5:00. Closed → hours + nine-one-one reminder + callback only (name, number, short reason). No labs, balances, meds, or appointments. Settings clock override is stored in Redis so the **worker** sees the same open/closed state as the API.

Demo closed: `2026-06-13T22:00:00-04:00`. Demo open: e.g. `2026-11-25T10:00:00-05:00`.

## Guardrails

| Case | Behavior |
| --- | --- |
| Medical advice / dose change | Refuse; optional short doctor note + phone → Requests |
| Emergency (e.g. chest pain) | nine-one-one / ER; stop normal flow |
| Someone else’s chart | Refuse; no status spoken |
| Prompt injection | Refuse mass disclosure |
| Human / upset / 3× verify fail | Callback / handoff record |

## HIPAA-aligned controls (demo)

- **Verify before disclosure** — chart tools need a verification ticket; closed hours block chart tools  
- **Minimum necessary** — last-four SSN only; no full SSN in seed/logs/LLM  
- **Match in code** — not by stuffing the patient table into the prompt  
- **Mask / redact** — verification transcripts placeholder; audit stores action/outcome without raw digits  
- **Secrets** — `.env` + `.env.example`; API on localhost  
- **Recording notice** — spoken at call start  
- **Synthetic data only**  

**BAAs:** demo STT/LLM/TTS are local; LiveKit is self-hosted. Hosted vendors would need a current BAA before real PHI. This repository does not claim certification or a signed BAA.

## Demo video

Record **5–7 minutes** (or ≤5 on free Loom) with a real mic. Script: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

Required scenes: verified refill · failed verify · new-patient booking · after-hours callback · medical advice · emergency · third party.
