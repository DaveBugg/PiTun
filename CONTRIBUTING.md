# Contributing to PiTun

Thanks for considering a contribution. PiTun is a small project — most of
the value of the codebase is in being **legible to one person reading it
cold**, so the bar for changes is "would I be able to debug this in two
years from a 30-second skim?"

By submitting a pull request you agree that your contribution is licensed
under the project's BSD 3-Clause License (see [LICENSE](LICENSE)).

## Quick start

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm ci
npm run dev   # http://localhost:5173

# Tests
cd backend && python -m pytest tests/ -q
cd frontend && npm run test:ci
```

The full Docker stack lives in `docker-compose.yml`. For local development
without RPi-specific bits (TPROXY, nftables) you can skip Docker and run
the two services directly — auth, nodes, routing rules and most of the UI
work fine on a non-Linux dev box.

## Reporting bugs

Open an issue with:
1. PiTun version (sidebar → click `PiTun X.Y.Z` for the version popover,
   then "Copy" — paste the JSON dump).
2. What you expected to happen.
3. What actually happened.
4. Last ~50 lines of `docker compose logs backend` if relevant.

## Pull requests

- Keep changes focused — one PR per concern. A PR that adds an event
  category and *also* refactors the dashboard will get bounced.
- Add a test for any new backend logic that has branching behaviour
  (CRUD endpoints, scheduler decisions, parse routines). UI changes
  don't need tests unless you're adding non-trivial logic.
- Run `pytest` and `npm run build` locally before pushing — same gates
  as CI.
- Match the existing comment style: prefer "why this is the way it is"
  over "what this line does". Rule of thumb: if a line of code can
  generate the comment, the comment isn't earning its keep.
- Don't bump `APP_VERSION` in your PR — releases are tagged by the
  maintainer.

## Code style

- **Python**: black-ish formatting, type hints on public functions, no
  hard line limit. Imports grouped stdlib / 3rd-party / local.
- **TypeScript**: existing patterns in `frontend/src/` — function
  components, React Query for server state, Zustand for global UI state.
  Tailwind utility classes; new shared classes go in `index.css`.
- No comment-bloat. The codebase already has plenty of context comments;
  a new file should match that density, not 3× it.

## Things to keep out of the repo

- Personal LAN IPs, SSH keys, deploy hostnames. Use `~/.ssh/config`
  aliases locally. There's a `notes.md` and `deploy_2nd.py` slot already
  reserved in `.gitignore` for maintainer-specific tooling.
- `.env` files. Use `.env.example` to document new variables.
- Build artifacts, `node_modules/`, geo databases.
- AI-agent memory directories (`.claude/`, `.serena/`, `.cursor/`).
  These store local conversation history and per-developer scratch
  context — never commit them.

## Releases

Tagged `vX.Y.Z` triggers `.github/workflows/release.yml`, which builds
both `linux/amd64` and `linux/arm64` Docker images, exports them as
loadable `.tar.gz` files, and uploads them as workflow artifacts. The
maintainer attaches the artifacts to the GitHub Release manually —
this is intentional, see comments at the top of the workflow file.
