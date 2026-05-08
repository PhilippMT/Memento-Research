# AutoResearch — OMC Research Lab

## Prerequisites

- **OneManCompany** cloned at `~/projects/OneManCompany` with `.venv` set up
- API keys configured in `.onemancompany/.env`

## Quick Start

```bash
# Pull latest and restart (reset data + backend)
git pull && ./scripts/reset.sh

# Open in browser
open http://localhost:8000
```

## Commands

```bash
# Full reset: stop backend → wipe data → copy company config → start backend
./scripts/reset.sh

# Stop backend only
./scripts/reset.sh --stop

# Start backend only (no data reset)
./scripts/reset.sh --start
```

## Logs

```bash
# Live backend logs
tail -f /tmp/omc-backend.log
```
