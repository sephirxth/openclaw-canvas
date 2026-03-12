# OpenClaw Canvas — Requirements History

All user requirements in chronological order, with implementation status.

## Phase 1: Architecture & Core

1. **Inventory all OpenClaw services/dashboards** — DONE
   - Agent Monitor (port 8091), TODO Dashboard (port 8077), ClawMetry (port 8900), OpenClaw Dashboard

2. **Merge Agent Monitor + TODO Dashboard + ClawMetry into unified infinite canvas** — DONE
   - Single FastAPI server (port 8090) replaces 3 services
   - Infinite pan/zoom canvas with CSS transform

3. **First-principles derivation of canvas architecture** — DONE
   - Axioms A1-A4, mechanisms M1-M5, dual ontology (mirror/native nodes)
   - Output: DERIVATION.md

## Phase 2: Core Features

4. **TODO panel scrollbar fix** — DONE
   - Wheel event conflict: canvas zoom was swallowing scroll events inside node-body
   - Fix: check if target is scrollable `.node-body`, passthrough at non-edge positions

5. **Node resizing** — DONE
   - Drag handle at bottom-right corner, min 180x120
   - Applies to TODO panel, agent nodes, all node types

6. **TODO inline editing** — DONE
   - Double-click todo text to edit in-place
   - `POST /api/todos/edit/{id}` preserves indent + checkbox, replaces text only

7. **File drag-and-drop from OS** — DONE
   - Drag files (PDF, MD, images) onto canvas
   - Upload to `files/` directory, create file node
   - Image preview, double-click to open in new tab

8. **Task assignment: drag TODO to agent mount zone** — DONE
   - Drag grip on each TODO item, ghost follows cursor
   - Drop on agent mount zone creates assignment
   - Writes `MOUNTED_TASKS.md` to agent workspace
   - Agent reports completion via `STATUS.json` `mounted_done`
   - Mount zone shows pending (amber) / done (green) items with OK/remove buttons

## Phase 3: Bug Fixes & Enhancements

9. **Mount zone bug: tasks not showing after drag-drop** — FIXED
   - Root cause: `body.todo-dragging` class removed before `elementsFromPoint`, causing drop-hint to hide and mount zone to shrink below pointer position
   - Fix: hit-test before class cleanup; robust `.closest('.mount-zone')` lookup; auto-expand node height; CSS `min-height:0` on `.node-body`

10. **Token consumption curves in agent nodes** — DONE
    - Backend: `/api/tokens/history?range=1h` reads `token_history.jsonl`
    - Frontend: SVG area chart with axis labels (Y: token values in K/M, X: timestamps HH:MM), grid lines, gradient fill
    - Auto-refresh every 30s

11. **Agent config editing from canvas** — DONE
    - Gear button in agent header toggles info/config view
    - Config view: editable SOUL.md and TODO.md (monospace textarea), model display (readonly)
    - `GET/PUT /api/agents/{id}/config`

12. **Task check interval selector** — DONE
    - Dropdown in mount zone: 15m / 30m / 1h / 2h / 4h
    - Stored in canvas node config, synced to MOUNTED_TASKS.md header
    - `PUT /api/agents/{id}/task-interval`

13. **Token chart axis labels** — DONE
    - Y-axis: max / mid / min values (K/M format)
    - X-axis: start time / end time (HH:MM)
    - Horizontal grid lines

14. **TODO add/delete tasks** — DONE
    - "+" button in TODO panel header opens inline input
    - "x" button on each todo item deletes it
    - `POST /api/todos/add`, `DELETE /api/todos/delete/{id}`

15. **Agent live activity feed** — DONE
    - "Activity" toggle in agent card body, shows recent session entries
    - `GET /api/agents/{id}/activity` reads latest session JSONL, extracts assistant messages, tool calls, tool results
    - Auto-refreshes with agent data (12s) for running/expanded agents
    - Color-coded: cyan for messages, purple for tools, red for errors

19. **Activity feed timestamps** — PLANNED
    - Each activity entry (message, tool call, tool result) shows a timestamp (HH:MM:SS)

## Planned (Design Confirmed, Pending Implementation)

16. **Task independent modeling & tracking** — DESIGN PHASE
    - Tasks as structured entities with lifecycle: open → assigned → in-progress → blocked → done → reviewed
    - Sidecar `tasks.json` alongside TODO.md (augment, not replace)
    - History log per task: who did what, when, results, artifacts, blockers
    - Agent reports via `task_updates` in STATUS.json
    - Canvas UI: click TODO item to expand full timeline
    - Design doc: see conversation for sidecar model proposal

17. **Box select: drag & zoom** — PLANNED
    - Draw selection rectangle on canvas to select multiple nodes
    - Move selected nodes as group
    - Zoom to fit selection

18. **In-node agent chat** — PLANNED
    - Chat interface inside agent card
    - Send messages to agent, receive responses
    - Agent can update its own STATUS.json / state through chat
    - State changes reflect in agent card in real-time

## Backlog / Evolution Path (from DERIVATION.md)

- v0.2: Native note nodes + connectors
- v0.3: PKM integration (knowledge/ files as nodes, memory timeline)
- v0.4: Luna can write to canvas via API
