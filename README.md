# OpenClaw Canvas

Unified infinite-canvas spatial dashboard for AI agent orchestration. Monitor agents, manage tasks, track token consumption, and assign work — all from a single drag-and-drop canvas.

## What It Does

OpenClaw Canvas replaces multiple monitoring dashboards (agent monitor, TODO dashboard, metrics) with one spatial interface. You see your entire AI agent fleet at a glance: who's running, what they're working on, how many tokens they've consumed, and what's next.

### Key Features

- **Infinite canvas** — Pan, zoom, and arrange nodes freely. Your layout persists between sessions.
- **Agent monitoring** — Real-time status (running / active / idle / stale), token consumption with delta tracking, current task display, and live activity feed showing tool calls and messages.
- **Token consumption charts** — SVG area charts with 1h/1d/1w history, auto-refreshing.
- **TODO management** — Parse and display TODO.md with sections, tags, and filtering. Add, edit, delete, and toggle tasks inline.
- **Task assignment** — Drag a TODO item onto an agent's mount zone. The agent picks it up via `MOUNTED_TASKS.md` and reports completion through `STATUS.json`.
- **Agent config editing** — Edit SOUL.md and TODO.md directly from the canvas. View model configuration.
- **File drop** — Drag files from your OS onto the canvas to create file nodes with image preview.
- **Cron job panel** — View scheduled jobs, their status, last run time, and error counts.

## When to Use This

- You run multiple AI agents (Claude, GPT, etc.) through OpenClaw and need a control plane
- You want a spatial overview of agent activity instead of checking logs
- You need to assign tasks to agents and track completion
- You want to monitor token spend across agents in real time

## Quick Start

```bash
# Install dependencies
pip install fastapi uvicorn python-multipart

# Run the server
python server.py
# → OpenClaw Canvas starting on 0.0.0.0:8090
```

Open `http://localhost:8090` in your browser. The canvas auto-discovers agents from your OpenClaw configuration.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCLAW_ROOT` | `~/.openclaw` | OpenClaw installation root |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8090` | Server port |
| `TODO_PATH` | `$OPENCLAW_ROOT/workspace/TODO.md` | Path to TODO file |

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn
- **Frontend**: Vanilla HTML/CSS/JS (single `index.html`, zero build step)
- **Canvas**: CSS `transform` based pan/zoom with pointer event handling
- **Charts**: Inline SVG with gradient fills

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents` | GET | List all agents with status, tokens, tasks |
| `/api/agents/{id}/activity` | GET | Recent session activity (messages, tool calls) |
| `/api/agents/{id}/config` | GET/PUT | Read/write agent SOUL.md and TODO.md |
| `/api/todos` | GET | Parse TODO.md into structured data |
| `/api/todos/toggle/{id}` | POST | Toggle task checkbox |
| `/api/todos/add` | POST | Add new task |
| `/api/crons` | GET | List cron jobs with status |
| `/api/assignments` | GET/POST | Task assignments to agents |
| `/api/tokens/history` | GET | Token consumption time series |
| `/api/canvas` | GET/PUT | Load/save canvas layout state |

## Architecture

```
Browser (index.html)
  ↕ REST API
FastAPI (server.py)
  ├── reads openclaw.json (agent config)
  ├── reads gateway sessions (token telemetry)
  ├── reads/writes TODO.md
  ├── reads cron/jobs.json
  ├── writes MOUNTED_TASKS.md (task dispatch)
  └── reads STATUS.json (task completion)
```

Single-file backend (`server.py`) + single-file frontend (`index.html`). No build tools, no npm, no bundler.

## Requirements

- Python 3.11+
- [OpenClaw](https://github.com/nicepkg/openclaw) installed and configured
- Node.js (for JSON5 config parsing)


## Related Projects

| Project | What It Does |
|---------|-------------|
| [OpenClaw Agent Monitor](https://github.com/sephirxth/openclaw-agent-monitor) | Lightweight real-time agent activity dashboard — if you need a focused "is it working?" view instead of a full canvas |
| [WitMani Game Animator](https://github.com/sephirxth/WitMani-game-animator) | AI sprite sheet generator — Claude Code plugin for game character animations |
| [LLM Code Test](https://github.com/sephirxth/LLM_code_test) | Benchmark comparing Claude, Gemini, DeepSeek, Grok on code generation |

[All projects →](https://github.com/sephirxth)

## License

MIT
