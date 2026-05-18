# AutoResearch — OMC Research Lab

## Prerequisites

- Repo cloned locally
- API keys configured in repo-root `.env`

## Quick Start

```bash
# Pull latest and restart (bootstrap runtime data + backend)
git pull && bash start.sh

# Open in browser
open http://localhost:8000
```

## Commands

```bash
# Rebuild .onemancompany from tracked repo files and restart backend
bash start.sh

# Stop backend only
bash start.sh --stop

# Start backend only (auto-bootstrap .onemancompany if needed)
bash start.sh --start
```

This project does not use OMC's interactive onboarding wizard.
`start.sh` bootstraps `.onemancompany/` directly from checked-in `company/`,
`config.yaml`, and `.env`.

## Logs

```bash
# Live backend logs
tail -f /tmp/memento-research-backend.log
```
