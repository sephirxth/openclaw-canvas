#!/usr/bin/env python3
"""OpenClaw Canvas — Unified spatial dashboard for agents, tasks, and cron."""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import uvicorn

app = FastAPI(title="OpenClaw Canvas")

# ── Paths ──

ROOT = Path(os.environ.get("OPENCLAW_ROOT", "/home/youyuan/.openclaw")).expanduser()
WORKSPACE = ROOT / "workspace"
PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG", str(ROOT / "openclaw.json"))).expanduser()
JSON5_PATH = Path(
    os.environ.get(
        "OPENCLAW_JSON5_PATH",
        "/home/youyuan/.npm-global/lib/node_modules/openclaw/node_modules/json5",
    )
).expanduser()
TODO_PATH = Path(os.environ.get("TODO_PATH", str(WORKSPACE / "TODO.md")))
FEEDBACK_PATH = Path(os.environ.get("FEEDBACK_PATH", str(WORKSPACE / "todo-dashboard/feedback.jsonl")))
CRON_PATH = ROOT / "cron" / "jobs.json"
CANVAS_STATE_PATH = PROJECT_DIR / "canvas-state.json"
HISTORY_PATH = WORKSPACE / "services" / "agent-monitor" / "token_history.jsonl"
CHAT_LABELS_PATH = WORKSPACE / "services" / "agent-monitor" / "chat_labels.json"

LOCAL_TZ = ZoneInfo('Asia/Shanghai')

# ── Caches ──

PREV_TOKEN_SNAPSHOTS: dict[str, dict[str, int]] = {}
LAST_HISTORY_WRITE: dict[str, int] = {}
HISTORY_WRITE_MIN_INTERVAL_MS = 60_000
GATEWAY_SESSIONS_CACHE: dict[str, Any] = {'ts': 0, 'sessions': []}
GATEWAY_CACHE_TTL_MS = 5_000

# ── Utilities ──


def load_config() -> dict[str, Any]:
    node_code = f"""
const fs = require('fs');
const JSON5 = require('{JSON5_PATH.as_posix()}');
const raw = fs.readFileSync('{CONFIG_PATH.as_posix()}', 'utf8');
process.stdout.write(JSON.stringify(obj = JSON5.parse(raw)));
"""
    out = subprocess.check_output(['node', '-e', node_code], text=True, timeout=5)
    return json.loads(out)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return ""


def gateway_call(method: str, params: dict | None = None, timeout_ms: int = 5000) -> dict | None:
    try:
        out = subprocess.check_output(
            ['openclaw', 'gateway', 'call', method, '--json',
             '--params', json.dumps(params or {}, ensure_ascii=False),
             '--timeout', str(timeout_ms)],
            text=True, timeout=max(2, timeout_ms // 1000 + 2),
        )
        return json.loads(out)
    except Exception:
        return None


def agent_id_from_session_key(key: str | None) -> str | None:
    if not key:
        return None
    m = re.match(r'^agent:([^:]+):', key)
    return m.group(1) if m else None


# ── Agent Monitor Logic ──


def load_gateway_sessions(limit: int = 200) -> list[dict]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if now_ms - int(GATEWAY_SESSIONS_CACHE.get('ts') or 0) < GATEWAY_CACHE_TTL_MS:
        return list(GATEWAY_SESSIONS_CACHE.get('sessions') or [])
    resp = gateway_call('sessions.list', {'limit': limit}, timeout_ms=5000) or {}
    sessions = list(resp.get('sessions') or [])
    GATEWAY_SESSIONS_CACHE['ts'] = now_ms
    GATEWAY_SESSIONS_CACHE['sessions'] = sessions
    return sessions


def best_gateway_session_for(agent_id: str) -> dict | None:
    sessions = [s for s in load_gateway_sessions() if agent_id_from_session_key(s.get('key')) == agent_id]
    if not sessions:
        return None
    sessions.sort(key=lambda s: (
        1 if s.get('totalTokensFresh') else 0,
        1 if int(s.get('totalTokens') or 0) > 0 else 0,
        int(s.get('updatedAt') or 0),
    ), reverse=True)
    return sessions[0]


def freshest_gateway_session_for(agent_id: str) -> dict | None:
    sessions = [s for s in load_gateway_sessions() if agent_id_from_session_key(s.get('key')) == agent_id]
    if not sessions:
        return None
    sessions.sort(key=lambda s: int(s.get('updatedAt') or 0), reverse=True)
    return sessions[0]


def extract_token_stats(meta: dict | None) -> dict[str, int]:
    meta = meta or {}
    stats = {
        'total': int(meta.get('totalTokens') or 0),
        'input': int(meta.get('inputTokens') or 0),
        'output': int(meta.get('outputTokens') or 0),
        'cache_read': int(meta.get('cacheRead') or 0),
        'cache_write': int(meta.get('cacheWrite') or 0),
        'updated_at': int(meta.get('updatedAt') or 0),
    }
    if stats['total'] > 0:
        return stats
    session_file = meta.get('sessionFile')
    if not session_file:
        return stats
    try:
        lines = Path(session_file).read_text(encoding='utf-8', errors='ignore').splitlines()
        for line in reversed(lines):
            obj = json.loads(line)
            usage = (obj.get('message') or {}).get('usage') or obj.get('usage') or {}
            if usage:
                stats['input'] = int(usage.get('input') or usage.get('input_tokens') or 0)
                stats['output'] = int(usage.get('output') or usage.get('output_tokens') or 0)
                stats['cache_read'] = int(usage.get('cacheRead') or usage.get('cache_read') or 0)
                stats['cache_write'] = int(usage.get('cacheWrite') or usage.get('cache_write') or 0)
                total = usage.get('totalTokens') or usage.get('total') or usage.get('total_tokens')
                if total is None:
                    total = stats['input'] + stats['output'] + stats['cache_read'] + stats['cache_write']
                stats['total'] = int(total or 0)
                stats['updated_at'] = int(meta.get('updatedAt') or 0)
                return stats
        return stats
    except Exception:
        return stats


def compute_token_activity(agent_id: str, token_stats: dict[str, int]) -> dict:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    prev = PREV_TOKEN_SNAPSHOTS.get(agent_id)
    PREV_TOKEN_SNAPSHOTS[agent_id] = {**token_stats, 'seen_at': now_ms}
    if not prev:
        return {**token_stats, 'delta_total': 0, 'delta_input': 0, 'delta_output': 0,
                'delta_cache_read': 0, 'delta_cache_write': 0, 'window_ms': 0, 'live': False}
    return {
        **token_stats,
        'delta_total': token_stats['total'] - prev.get('total', 0),
        'delta_input': token_stats['input'] - prev.get('input', 0),
        'delta_output': token_stats['output'] - prev.get('output', 0),
        'delta_cache_read': token_stats['cache_read'] - prev.get('cache_read', 0),
        'delta_cache_write': token_stats['cache_write'] - prev.get('cache_write', 0),
        'window_ms': now_ms - prev.get('seen_at', now_ms),
        'live': (token_stats['total'] - prev.get('total', 0)) > 0,
    }


def parse_first_unfinished_todo(path: Path) -> str | None:
    for line in read_text(path).splitlines():
        m = re.match(r'^\s*(?:-|\d+\.)\s*\[([ /x])\]\s+(.+)$', line)
        if m and m.group(1) != 'x':
            return re.sub(r'`[^`]+`', '', m.group(2)).strip()
    return None


def parse_active_task(path: Path) -> str | None:
    for line in read_text(path).splitlines():
        s = line.strip()
        if s.startswith('\u4f60\u5f53\u524d\u552f\u4e00\u4f18\u5148\u4efb\u52a1\uff1a'):
            return s.replace('\u4f60\u5f53\u524d\u552f\u4e00\u4f18\u5148\u4efb\u52a1\uff1a', '').strip('** ')
        if s.startswith('- [/]') or s.startswith('- [ ]'):
            return s
    return None


def parse_iso_or_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
        except Exception:
            return None
    return None


def resolve_group_bindings(cfg: dict) -> dict[str, dict[str, str]]:
    labels = load_json(CHAT_LABELS_PATH, {}) or {}
    result: dict[str, dict[str, str]] = {}
    for item in cfg.get('bindings', []):
        match = item.get('match', {})
        peer = match.get('peer', {})
        if match.get('channel') == 'feishu' and peer.get('kind') == 'group':
            cid = peer.get('id')
            if cid:
                result[item['agentId']] = {'chat_id': cid, 'chat_name': labels.get(cid, cid)}
    return result


def latest_relevant_file(workspace: Path) -> dict | None:
    ignore = {'.git', '.openclaw', 'memory', '__pycache__'}
    skip_names = {'IDENTITY.md', 'SOUL.md', 'USER.md', 'MEMORY.md', 'AGENTS.md', 'TOOLS.md', 'HEARTBEAT.md', 'STATUS.json'}
    candidates = [p for p in workspace.rglob('*') if p.is_file()
                  and not any(part in ignore for part in p.parts) and p.name not in skip_names]
    if not candidates:
        return None
    p = max(candidates, key=lambda x: x.stat().st_mtime)
    return {'path': str(p), 'mtime': int(p.stat().st_mtime), 'name': p.name}


def summarize_agent(agent: dict, bindings: dict) -> dict:
    aid = agent['id']
    workspace = Path(agent['workspace'])
    status = load_json(workspace / 'STATUS.json', {}) or {}

    freshest = freshest_gateway_session_for(aid)
    token_sess = best_gateway_session_for(aid)
    telemetry_source = 'gateway'

    if freshest is None and token_sess is None:
        sessions = load_json(ROOT / 'agents' / aid / 'sessions' / 'sessions.json', {}) or {}
        latest_key, latest_meta, latest_up = None, None, None
        for key, meta in sessions.items():
            up = meta.get('updatedAt')
            if isinstance(up, (int, float)) and (latest_up is None or up > latest_up):
                latest_up, latest_key, latest_meta = int(up), key, meta
        freshest = latest_meta
        token_sess = latest_meta
        telemetry_source = 'local'
    else:
        latest_key = (freshest or {}).get('key')
        latest_up = int((freshest or {}).get('updatedAt') or 0) or None

    token_stats = extract_token_stats(token_sess)
    tokens = compute_token_activity(aid, token_stats)
    tokens['fresh'] = bool((token_sess or {}).get('totalTokensFresh'))

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    recent_up = int(token_stats.get('updated_at') or latest_up or 0)
    age_ms = (now_ms - recent_up) if recent_up else None

    explicit = status.get('status') if isinstance(status, dict) else None
    if explicit:
        computed = explicit
    elif recent_up and age_ms is not None and age_ms < 45_000 and (tokens['fresh'] or token_stats['total'] > 0):
        computed = 'running'
    elif latest_up and now_ms - latest_up < 15 * 60_000:
        computed = 'active'
    elif latest_up and now_ms - latest_up < 2 * 3600_000:
        computed = 'idle'
    else:
        computed = 'stale'

    task = ((status.get('task') if isinstance(status, dict) else None)
            or parse_first_unfinished_todo(workspace / 'TODO.md')
            or parse_active_task(workspace / 'ACTIVE_TASK.md'))

    return {
        'agent_id': aid,
        'name': agent.get('name') or aid,
        'emoji': (agent.get('identity') or {}).get('emoji'),
        'status': computed,
        'task': task,
        'step': status.get('step') if isinstance(status, dict) else None,
        'blocker': status.get('blocker') if isinstance(status, dict) else None,
        'next_action': status.get('next') if isinstance(status, dict) else None,
        'tokens': tokens,
        'binding': bindings.get(aid),
        'latest_artifact': latest_relevant_file(workspace),
    }


def maybe_append_history(agents: list[dict]) -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    lines = []
    for a in agents:
        aid = a['agent_id']
        t = a.get('tokens') or {}
        total = int(t.get('total') or 0)
        if total <= 0:
            continue
        if now_ms - LAST_HISTORY_WRITE.get(aid, 0) < HISTORY_WRITE_MIN_INTERVAL_MS:
            continue
        lines.append(json.dumps({
            'ts': now_ms, 'agent_id': aid, 'total': total,
            'input': int(t.get('input') or 0), 'output': int(t.get('output') or 0),
            'cache_read': int(t.get('cache_read') or 0), 'cache_write': int(t.get('cache_write') or 0),
            'status': a.get('status'),
        }, ensure_ascii=False))
        LAST_HISTORY_WRITE[aid] = now_ms
    if lines:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')


# ── TODO Logic ──

CHECKBOX_RE = re.compile(r'^(\s*)(?:-\s+|\d+\.\s+)\[([ x/])\]\s+(.+)$')
TAG_DEFS = {
    'type:intention': {'label': 'intention', 'color': '#a78bfa'},
    'type:decision': {'label': 'decision', 'color': '#f472b6'},
    'type:reference': {'label': 'reference', 'color': '#94a3b8'},
    'luna:solo': {'label': 'luna:solo', 'color': '#4ade80'},
    'needs:youyuan': {'label': 'needs:youyuan', 'color': '#f59e0b'},
    'urgent': {'label': 'urgent', 'color': '#f87171'},
    'strategic': {'label': 'strategic', 'color': '#818cf8'},
}
MAINLINE_DEFS = {
    'A': {'label': 'A Traffic', 'color': '#f87171'},
    'B': {'label': 'B Gaming', 'color': '#818cf8'},
    'C': {'label': 'C Inner', 'color': '#a78bfa'},
    'D': {'label': 'D Tools', 'color': '#4ade80'},
}


def extract_tags(text: str) -> list[str]:
    tags = re.findall(r'`([^`]+)`', text)
    result = []
    for t in tags:
        t = t.strip()
        if t in TAG_DEFS or re.match(r'^main:[A-D]$', t) or t.startswith('domain:'):
            result.append(t)
        elif t.startswith('needs:'):
            result.append('needs:youyuan')
        elif t in ('urgent', 'urgent-strategic'):
            result.append('urgent')
        elif t == 'strategic':
            result.append('strategic')
        elif t.startswith('luna:solo'):
            result.append('luna:solo')
        elif t.startswith('type:'):
            result.append(t)
    return result


def parse_todo_md(content: str) -> dict:
    lines = content.split('\n')
    current_section = current_subsection = None
    todos = []
    tid = 0
    for i, line in enumerate(lines):
        if line.startswith('## '):
            current_section = line.lstrip('# ').strip()
            current_subsection = None
        elif line.startswith('### '):
            current_subsection = line.lstrip('# ').strip()
        else:
            m = CHECKBOX_RE.match(line)
            if m:
                state = m.group(2)
                text = m.group(3)
                tags = extract_tags(text)
                mainline = next((t[5] for t in tags if t.startswith('main:') and len(t) == 6), None)
                tid += 1
                todos.append({
                    'id': tid, 'line': i, 'checked': state == 'x', 'in_progress': state == '/',
                    'text': text, 'section': current_section or '', 'subsection': current_subsection or '',
                    'tags': tags, 'mainline': mainline,
                    'urgent': 'urgent' in tags, 'solo': 'luna:solo' in tags,
                    'needs_youyuan': 'needs:youyuan' in tags, 'bold': text.startswith('**'),
                })
    return {
        'todos': todos, 'total': len(todos),
        'done': sum(1 for t in todos if t['checked']),
        'in_progress': sum(1 for t in todos if t['in_progress']),
        'urgent': sum(1 for t in todos if t['urgent'] and not t['checked']),
        'solo': sum(1 for t in todos if t['solo'] and not t['checked']),
        'needs_youyuan': sum(1 for t in todos if t['needs_youyuan'] and not t['checked']),
    }


def toggle_todo_line(content: str, line_num: int) -> str:
    lines = content.split('\n')
    if 0 <= line_num < len(lines):
        m = CHECKBOX_RE.match(lines[line_num])
        if m:
            prefix = lines[line_num][:m.start(2)]
            state = m.group(2)
            suffix = lines[line_num][m.end(2):]
            lines[line_num] = prefix + ('x' if state in (' ', '/') else ' ') + suffix
    return '\n'.join(lines)


# ── Cron Logic ──


def load_cron_jobs() -> list[dict]:
    data = load_json(CRON_PATH, {}) or {}
    jobs = []
    for job in data.get('jobs', []):
        state = job.get('state') or {}
        schedule = job.get('schedule') or {}
        jobs.append({
            'id': job.get('id'),
            'name': job.get('name'),
            'description': job.get('description'),
            'enabled': job.get('enabled', False),
            'agent_id': job.get('agentId'),
            'schedule_expr': schedule.get('expr') or f"every {schedule.get('everyMs', 0) // 60000}m",
            'last_status': state.get('lastStatus'),
            'last_run_ms': state.get('lastRunAtMs'),
            'next_run_ms': state.get('nextRunAtMs'),
            'last_duration_ms': state.get('lastDurationMs'),
            'consecutive_errors': state.get('consecutiveErrors', 0),
            'last_error': state.get('lastError'),
        })
    return jobs


# ── Task Assignments ──

ASSIGNMENTS_PATH = PROJECT_DIR / "task-assignments.json"


def load_assignments() -> list[dict]:
    data = load_json(ASSIGNMENTS_PATH, {'assignments': []}) or {'assignments': []}
    return data.get('assignments', [])


def save_assignments(assignments: list[dict]) -> None:
    ASSIGNMENTS_PATH.write_text(
        json.dumps({'assignments': assignments}, ensure_ascii=False, indent=2), encoding='utf-8')


def get_task_check_interval(agent_id: str) -> int:
    """Get task check interval in minutes from canvas state."""
    state = load_json(CANVAS_STATE_PATH, {}) or {}
    for node in state.get('nodes', []):
        if node.get('config', {}).get('agent_id') == agent_id:
            return node.get('config', {}).get('task_check_minutes', 60)
    return 60


def sync_mounted_tasks(agent_id: str, assignments: list[dict]) -> None:
    """Write MOUNTED_TASKS.md to agent workspace so the agent can read it."""
    try:
        cfg = load_config()
    except Exception:
        return
    agent = next((a for a in cfg.get('agents', {}).get('list', []) if a['id'] == agent_id), None)
    if not agent:
        return
    workspace = Path(agent['workspace'])
    pending = [a for a in assignments if a['agent_id'] == agent_id and a['status'] == 'pending']
    mount_path = workspace / 'MOUNTED_TASKS.md'
    if not pending:
        if mount_path.exists():
            mount_path.unlink()
        return
    interval = get_task_check_interval(agent_id)
    lines = [
        "# Mounted Tasks",
        "",
        f"Check interval: every {interval} minutes",
        "",
        "Assigned via OpenClaw Canvas. Work on these, then write completion to STATUS.json:",
        "`mounted_done: [{id: \"<assignment_id>\", result: \"<what you did>\"}]`",
        "",
    ]
    for t in pending:
        ts = datetime.fromtimestamp(t['assigned_at'] / 1000, LOCAL_TZ).strftime('%m-%d %H:%M')
        lines.append(f"- [ ] {t['todo_text']}")
        lines.append(f"  - assignment_id: `{t['id']}`")
        lines.append(f"  - assigned: {ts}")
        lines.append("")
    mount_path.write_text('\n'.join(lines), encoding='utf-8')


def check_agent_completions() -> None:
    """Read mounted_done from agent STATUS.json to auto-complete assignments."""
    assignments = load_assignments()
    if not assignments:
        return
    pending_ids = {a['id'] for a in assignments if a['status'] == 'pending'}
    if not pending_ids:
        return
    try:
        cfg = load_config()
    except Exception:
        return
    changed = False
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for agent in cfg.get('agents', {}).get('list', []):
        status = load_json(Path(agent['workspace']) / 'STATUS.json', {}) or {}
        for item in (status.get('mounted_done') or []):
            aid = item.get('id')
            if aid not in pending_ids:
                continue
            match = next((a for a in assignments if a['id'] == aid), None)
            if match and match['status'] == 'pending':
                match['status'] = 'done'
                match['completed_at'] = now_ms
                match['result'] = item.get('result', '')
                changed = True
    if changed:
        save_assignments(assignments)


# ── Canvas State ──


def generate_default_canvas(agents: list[dict]) -> dict:
    nodes = []
    # Agent cards in a 3-column grid
    for i, a in enumerate(agents):
        col, row = i % 3, i // 3
        nodes.append({
            'id': f"agent-{a['agent_id']}",
            'type': 'agent',
            'x': col * 320, 'y': row * 360,
            'w': 280, 'h': 320,
            'config': {'agent_id': a['agent_id']},
        })
    # TODO panel to the left
    nodes.append({
        'id': 'todo-panel', 'type': 'todo',
        'x': -420, 'y': 0, 'w': 380, 'h': 560, 'config': {},
    })
    # Cron panel below agents
    grid_h = max(1, (len(agents) + 2) // 3) * 360
    nodes.append({
        'id': 'cron-panel', 'type': 'cron',
        'x': 0, 'y': grid_h + 40, 'w': 580, 'h': 320, 'config': {},
    })
    return {'viewport': {'x': 200, 'y': 100, 'zoom': 0.85}, 'nodes': nodes}


def load_canvas_state(agents: list[dict]) -> dict:
    state = load_json(CANVAS_STATE_PATH)
    if state and state.get('nodes'):
        return state
    state = generate_default_canvas(agents)
    save_canvas_state(state)
    return state


def save_canvas_state(state: dict) -> None:
    CANVAS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANVAS_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


# ── Agent Activity ──


def latest_session_file(agent_id: str) -> Path | None:
    session_dir = ROOT / 'agents' / agent_id / 'sessions'
    if not session_dir.exists():
        return None
    jsonls = sorted(session_dir.glob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonls[0] if jsonls else None


def tail_lines(path: Path, n: int = 40) -> list[str]:
    """Read last n lines from file efficiently."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            # Read last ~64KB at most
            chunk = min(size, 65536)
            f.seek(size - chunk)
            data = f.read().decode('utf-8', errors='ignore')
            return data.strip().splitlines()[-n:]
    except Exception:
        return []


def extract_activity(agent_id: str, max_items: int = 8) -> list[dict]:
    """Extract recent activity entries from agent's latest session."""
    sf = latest_session_file(agent_id)
    if not sf:
        return []
    lines = tail_lines(sf, 40)
    activities = []
    for raw in reversed(lines):
        if len(activities) >= max_items:
            break
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        msg = obj.get('message') or obj
        role = msg.get('role', '')
        ts = msg.get('timestamp') or obj.get('timestamp')

        if role == 'assistant':
            content = msg.get('content', '')
            text = ''
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            text += block.get('text', '')
                        elif block.get('type') == 'tool_use':
                            activities.append({
                                'type': 'tool_call',
                                'tool': block.get('name', '?'),
                                'ts': ts,
                            })
            if text.strip():
                activities.append({
                    'type': 'message',
                    'text': text.strip()[:300],
                    'ts': ts,
                })
        elif role == 'toolResult':
            tool_name = msg.get('toolName', '')
            content = msg.get('content', '')
            preview = ''
            if isinstance(content, str):
                preview = content[:150]
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        preview = block.get('text', '')[:150]
                        break
            if tool_name:
                activities.append({
                    'type': 'tool_result',
                    'tool': tool_name,
                    'preview': preview,
                    'error': bool(msg.get('isError')),
                    'ts': ts,
                })
    activities.reverse()
    return activities


# ── API Endpoints ──


@app.get('/api/agents')
def api_agents():
    cfg = load_config()
    bindings = resolve_group_bindings(cfg)
    agents = [summarize_agent(a, bindings) for a in cfg.get('agents', {}).get('list', [])]
    maybe_append_history(agents)
    return JSONResponse({'agents': agents, 'ts': int(datetime.now(timezone.utc).timestamp() * 1000)})


@app.get('/api/agents/{agent_id}/activity')
def api_agent_activity(agent_id: str, n: int = 8):
    return JSONResponse({'activity': extract_activity(agent_id, max_items=min(n, 20))})


@app.get('/api/todos')
def api_todos():
    if not TODO_PATH.exists():
        raise HTTPException(404, "TODO.md not found")
    return JSONResponse(parse_todo_md(TODO_PATH.read_text(encoding='utf-8')))


@app.post('/api/todos/toggle/{todo_id}')
def api_toggle(todo_id: int):
    content = TODO_PATH.read_text(encoding='utf-8')
    data = parse_todo_md(content)
    todo = next((t for t in data['todos'] if t['id'] == todo_id), None)
    if not todo:
        raise HTTPException(404)
    TODO_PATH.write_text(toggle_todo_line(content, todo['line']), encoding='utf-8')
    return {'ok': True, 'checked': not todo['checked']}


@app.post('/api/todos/edit/{todo_id}')
async def api_edit_todo(todo_id: int, request: Request):
    body = await request.json()
    new_text = body.get('text', '').strip()
    if not new_text:
        raise HTTPException(400, "text required")
    content = TODO_PATH.read_text(encoding='utf-8')
    data = parse_todo_md(content)
    todo = next((t for t in data['todos'] if t['id'] == todo_id), None)
    if not todo:
        raise HTTPException(404)
    lines = content.split('\n')
    line = lines[todo['line']]
    m = CHECKBOX_RE.match(line)
    if m:
        # Preserve indent + checkbox, replace text
        lines[todo['line']] = line[:m.start(3)] + new_text
        TODO_PATH.write_text('\n'.join(lines), encoding='utf-8')
    return {'ok': True}


@app.post('/api/todos/feedback')
async def api_feedback(request: Request):
    body = await request.json()
    msg = body.get('message', '').strip()
    if not msg:
        raise HTTPException(400, "message required")
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FEEDBACK_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'ts': datetime.now().isoformat(), 'todo_id': body.get('todo_id'), 'message': msg}, ensure_ascii=False) + '\n')
    return {'ok': True}


@app.get('/api/crons')
def api_crons():
    return JSONResponse({'jobs': load_cron_jobs()})


@app.get('/api/assignments')
def api_assignments(agent_id: str = None):
    check_agent_completions()
    assignments = load_assignments()
    if agent_id:
        assignments = [a for a in assignments if a['agent_id'] == agent_id]
    return JSONResponse({'assignments': assignments})


@app.post('/api/assignments')
async def api_create_assignment(request: Request):
    body = await request.json()
    agent_id = body.get('agent_id')
    todo_text = body.get('todo_text', '').strip()
    todo_id = body.get('todo_id')
    if not agent_id or not todo_text:
        raise HTTPException(400, "agent_id and todo_text required")
    assignment = {
        'id': secrets.token_hex(4),
        'agent_id': agent_id,
        'todo_text': todo_text,
        'todo_id': todo_id,
        'status': 'pending',
        'assigned_at': int(datetime.now(timezone.utc).timestamp() * 1000),
        'completed_at': None,
        'result': None,
    }
    assignments = load_assignments()
    assignments.append(assignment)
    save_assignments(assignments)
    sync_mounted_tasks(agent_id, assignments)
    return JSONResponse(assignment)


@app.post('/api/assignments/{assignment_id}/review')
def api_review_assignment(assignment_id: str):
    assignments = load_assignments()
    a = next((x for x in assignments if x['id'] == assignment_id), None)
    if not a:
        raise HTTPException(404)
    a['status'] = 'reviewed'
    save_assignments(assignments)
    return JSONResponse(a)


@app.delete('/api/assignments/{assignment_id}')
def api_delete_assignment(assignment_id: str):
    assignments = load_assignments()
    a = next((x for x in assignments if x['id'] == assignment_id), None)
    if not a:
        raise HTTPException(404)
    agent_id = a['agent_id']
    assignments = [x for x in assignments if x['id'] != assignment_id]
    save_assignments(assignments)
    sync_mounted_tasks(agent_id, assignments)
    return {'ok': True}


UPLOAD_DIR = PROJECT_DIR / "files"


@app.post('/api/files/upload')
async def api_file_upload(file: UploadFile = File(...)):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitize filename
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename or 'unnamed')
    dest = UPLOAD_DIR / safe_name
    # Avoid overwrite
    if dest.exists():
        stem, ext = dest.stem, dest.suffix
        dest = UPLOAD_DIR / f"{stem}_{secrets.token_hex(2)}{ext}"
    content = await file.read()
    dest.write_bytes(content)
    return {'path': str(dest), 'name': dest.name, 'size': len(content)}


@app.get('/api/files/preview')
def api_file_preview(path: str):
    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get('/api/files/open')
def api_file_open(path: str):
    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.get('/api/tokens/history')
def api_token_history(agent_id: str = None, range: str = '1h'):
    window_ms = {'1w': 7*86400_000, '1d': 86400_000}.get(range, 3600_000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff = now_ms - window_ms
    result: dict[str, list] = {}
    if not HISTORY_PATH.exists():
        return JSONResponse({'series': result})
    for line in HISTORY_PATH.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if int(obj.get('ts') or 0) < cutoff:
            continue
        aid = obj.get('agent_id')
        if agent_id and aid != agent_id:
            continue
        if aid:
            result.setdefault(aid, []).append(obj)
    return JSONResponse({'series': result, 'range': range})


@app.put('/api/agents/{agent_id}/task-interval')
async def api_set_task_interval(agent_id: str, request: Request):
    body = await request.json()
    minutes = int(body.get('minutes', 60))
    # Update canvas state node config
    state = load_json(CANVAS_STATE_PATH, {}) or {}
    for node in state.get('nodes', []):
        if node.get('config', {}).get('agent_id') == agent_id:
            node['config']['task_check_minutes'] = minutes
    save_canvas_state(state)
    # Re-sync MOUNTED_TASKS.md with updated interval
    assignments = load_assignments()
    sync_mounted_tasks(agent_id, assignments)
    return {'ok': True, 'minutes': minutes}


@app.post('/api/todos/add')
async def api_add_todo(request: Request):
    body = await request.json()
    text = body.get('text', '').strip()
    if not text:
        raise HTTPException(400, "text required")
    content = TODO_PATH.read_text(encoding='utf-8')
    lines = content.split('\n')
    # Find the first section with tasks, append after the last unchecked item
    insert_idx = None
    for i, line in enumerate(lines):
        if CHECKBOX_RE.match(line):
            insert_idx = i + 1  # after the last checkbox line found so far
    if insert_idx is None:
        # No checkboxes found; append at end
        insert_idx = len(lines)
    new_line = f"- [ ] {text}"
    lines.insert(insert_idx, new_line)
    TODO_PATH.write_text('\n'.join(lines), encoding='utf-8')
    return {'ok': True}


@app.delete('/api/todos/delete/{todo_id}')
def api_delete_todo(todo_id: int):
    content = TODO_PATH.read_text(encoding='utf-8')
    data = parse_todo_md(content)
    todo = next((t for t in data['todos'] if t['id'] == todo_id), None)
    if not todo:
        raise HTTPException(404)
    lines = content.split('\n')
    del lines[todo['line']]
    TODO_PATH.write_text('\n'.join(lines), encoding='utf-8')
    return {'ok': True}


@app.get('/api/agents/{agent_id}/config')
def api_agent_config(agent_id: str):
    cfg = load_config()
    agent = next((a for a in cfg.get('agents', {}).get('list', []) if a['id'] == agent_id), None)
    if not agent:
        raise HTTPException(404, "Agent not found")
    workspace = Path(agent['workspace'])
    soul = read_text(workspace / 'SOUL.md') or read_text(workspace / 'IDENTITY.md')
    todo = read_text(workspace / 'TODO.md')
    model_cfg = agent.get('model', {})
    return JSONResponse({
        'agent_id': agent_id,
        'name': agent.get('name'),
        'workspace': str(workspace),
        'soul': soul,
        'todo': todo,
        'model': model_cfg,
    })


@app.put('/api/agents/{agent_id}/config')
async def api_update_agent_config(agent_id: str, request: Request):
    cfg = load_config()
    agent = next((a for a in cfg.get('agents', {}).get('list', []) if a['id'] == agent_id), None)
    if not agent:
        raise HTTPException(404, "Agent not found")
    workspace = Path(agent['workspace'])
    body = await request.json()
    if 'soul' in body:
        target = workspace / 'SOUL.md'
        if not target.exists() and (workspace / 'IDENTITY.md').exists():
            target = workspace / 'IDENTITY.md'
        target.write_text(body['soul'], encoding='utf-8')
    if 'todo' in body:
        (workspace / 'TODO.md').write_text(body['todo'], encoding='utf-8')
    return {'ok': True}


@app.get('/api/canvas')
def api_canvas_load():
    cfg = load_config()
    agents = [{'agent_id': a['id']} for a in cfg.get('agents', {}).get('list', [])]
    return JSONResponse(load_canvas_state(agents))


@app.put('/api/canvas')
async def api_canvas_save(request: Request):
    state = await request.json()
    save_canvas_state(state)
    return {'ok': True}


@app.get('/', response_class=HTMLResponse)
def index():
    return HTMLResponse((PROJECT_DIR / 'index.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '8090'))
    print(f"OpenClaw Canvas starting on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level='info')
