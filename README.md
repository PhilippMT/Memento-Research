# AutoResearch — OMC Research Lab

## Prerequisites

- Repo cloned locally
- API keys configured in repo-root `.env`

## Quick Start

```bash
# 1. Create a local env file
cp .env.example .env

# 2. Edit .env and set at least:
#    OPENROUTER_API_KEY=...
#    DEFAULT_LLM_MODEL=...
#    HOST=0.0.0.0
#    PORT=8000

# 3. Start the app (bootstraps runtime data + backend)
bash start.sh

# Open in browser
open http://localhost:8000
```

## Commands

```bash
# Rebuild .onemancompany from tracked repo files and restart backend
bash start.sh

# Same as above, but explicit
bash start.sh restart

# Check whether the backend is listening
bash start.sh status

# Stop backend only
bash start.sh stop

# Start backend only (auto-bootstrap .onemancompany if needed)
bash start.sh start
```

This project does not use OMC's interactive onboarding wizard.
`start.sh` bootstraps `.onemancompany/` directly from checked-in `company/`,
and `.env`.
If you change repo-root `.env`, run `bash start.sh` once so the updated config
is copied into `.onemancompany/.env`.

## Logs

```bash
# Live backend logs
tail -f /tmp/memento-research-backend.log
```
