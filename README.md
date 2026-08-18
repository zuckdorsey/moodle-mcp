<div align="center">

# moodle-mcp

> **Model Context Protocol server for Moodle LMS** — connect Hermes, Claude Code, and OpenCode to your Moodle. Fetch assignments, grades, and deadlines, sync to Obsidian, and get WhatsApp alerts.

[![Python](https://img.shields.io/badge/python-%3E%3D3.10-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-1.6%2B-7B68EE?style=flat-square)](https://modelcontextprotocol.io)
[![MseeP.ai Security](https://mseep.net/pr/loyaniu-moodle-mcp-badge.png)](https://mseep.ai/app/loyaniu-moodle-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

[Overview](#overview) • [Features](#features) • [Quick Start](#quick-start) • [Configuration](#configuration) • [Agent Setup](#agent-setup) • [Tools](#tools) • [Obsidian Sync](#obsidian-sync)

</div>

---

## Overview

`moodle-mcp` bridges the Moodle Web Services API with the [Model Context Protocol](https://modelcontextprotocol.io), so AI agents can act as your academic assistant. It was built for **Polibatam** (multi-class filtering) but works with any Moodle instance that has Web Services enabled.

Fork of [loyaniu/moodle-mcp](https://github.com/loyaniu/moodle-mcp) — extended from 22 to **40 tools** with Obsidian sync, material downloads, submission tools, calendar integration, concurrent fetching, and semester auto-archive.

> [!TIP]
> Works with any Moodle LMS — just point `MOODLE_URL` at your instance. The Polibatam class filter (`MOODLE_MY_CLASS`) is optional.

---

## Features

- **Assignments & deadlines** — filtered by class slot, sorted by urgency, with actionable task lists
- **Grades & progress** — course health checks, study load, and completion tracking
- **Course content & search** — sections, modules, materials, announcements, recent activity
- **Obsidian sync** — one-command export of dashboards, deadlines, and course notes (with semester auto-archive)
- **Material downloads** — list and download course files and assignment attachments
- **Submissions** — submit text, check status, and read feedback
- **Calendar** — upcoming events, create reminders (H-2), mark activities complete
- **Agent-ready** — single `install-mcp.sh` for Hermes, Claude Code, and OpenCode

---

## Quick Start

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/zuckdorsey/moodle-mcp/main/scripts/install-mcp.sh | bash
```

Auto-detects installed agents and configures each one. For a dry run:

```bash
bash scripts/install-mcp.sh --dry-run
```

### Verify

```bash
# Hermes (profile: akademik)
hermes --profile akademik mcp test moodle
# → ✓ Connected  ✓ Tools discovered: 40

# Claude Code
claude mcp list

# Run tests
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

> [!NOTE]
> Prefer local source over PyPI when developing — the launcher sets `PYTHONPATH` to `src/` automatically.

---

## Configuration

### 1. Get your Moodle token

1. Open `https://<your-moodle>/user/managetoken.php`
2. Find the row with **Moodle mobile web service** in `Service`
3. Copy the token

### 2. Create `.env`

```bash
cp .env.example .env
```

```dotenv
MOODLE_URL=https://your-moodle.example.com/webservice/rest/server.php
MOODLE_TOKEN=your_token_here

# Optional — Polibatam class-slot filter (e.g. Pagi C, Siang A)
MOODLE_MY_CLASS=Pagi C

# Optional — custom vault path
OBSIDIAN_VAULT_PATH=/home/you/Obsidian Vault
```

| Variable | Required | Description |
|---|---|---|
| `MOODLE_URL` | yes | Moodle REST endpoint (`.../webservice/rest/server.php`) |
| `MOODLE_TOKEN` | yes | Mobile web service token |
| `MOODLE_MY_CLASS` | no | Regex filter for multi-class assignment titles |
| `OBSIDIAN_VAULT_PATH` | no | Vault root (default: `~/Obsidian Vault`) |

### 3. Install package

```bash
# with uv (recommended)
uv pip install -e .

# or pip
pip install -e .
```

> [!WARNING]
> Never commit `.env` — it contains a token with full API access. It is already in `.gitignore`.

---

## Agent Setup

Pick your agent — all use the same local launcher at `scripts/moodle_mcp_local_launch.py`.

### Hermes Agent (recommended)

```bash
bash scripts/install-mcp.sh --agent hermes --profile akademik
```

Manual (`~/.hermes/profiles/akademik/config.yaml`):

```yaml
mcp_servers:
  moodle:
    command: /home/you/Programming/Python/moodle-mcp/.venv/bin/python
    args: [/home/you/Programming/Python/moodle-mcp/scripts/moodle_mcp_local_launch.py]
    env:
      MOODLE_URL: ${MOODLE_URL}
      MOODLE_TOKEN: ${MOODLE_TOKEN}
      MOODLE_MY_CLASS: "Pagi C"
      OBSIDIAN_VAULT_PATH: "/home/you/Obsidian Vault"
```

### Claude Code

```bash
bash scripts/install-mcp.sh --agent claude-code
# or manually
claude mcp add -s user moodle-mcp -- python3 /path/to/moodle-mcp/scripts/moodle_mcp_local_launch.py
```

Global JSON (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "moodle-mcp": {
      "command": "python3",
      "args": ["/path/to/moodle-mcp/scripts/moodle_mcp_local_launch.py"],
      "env": {
        "MOODLE_URL": "https://your-moodle/webservice/rest/server.php",
        "MOODLE_TOKEN": "your_token_here"
      }
    }
  }
}
```

### OpenCode

```bash
bash scripts/install-mcp.sh --agent opencode
```

`~/.config/opencode/config.json`:

```json
{
  "mcp": {
    "moodle-mcp": {
      "command": "python3",
      "args": ["/path/to/moodle-mcp/scripts/moodle_mcp_local_launch.py"],
      "environment": {
        "MOODLE_URL": "https://your-moodle/webservice/rest/server.php",
        "MOODLE_TOKEN": "your_token_here"
      }
    }
  }
}
```

### Claude Desktop / Cursor

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "moodle-mcp": {
      "command": "uvx",
      "args": ["moodle-mcp"],
      "env": {
        "MOODLE_URL": "https://your-moodle/webservice/rest/server.php",
        "MOODLE_TOKEN": "your_token_here"
      }
    }
  }
}
```

Restart the desktop app — tools appear in the picker.

---

## Tools

40 tools across 7 groups. Call them by name from any connected agent (e.g. *"list my Moodle courses"* → `get_my_courses`).

<details>
<summary><strong>Courses & Content (6)</strong></summary>

| Tool | Description |
|---|---|
| `get_my_courses` | Enrolled courses |
| `get_course_content` | Sections & modules for a course |
| `search_course_materials` | Search across all materials |
| `get_course_announcements` | News forum announcements |
| `get_recent_activity` | Recent activity since timestamp |
| `get_course_updates` | New materials/announcements |

</details>

<details>
<summary><strong>Assignments & Deadlines (12)</strong></summary>

| Tool | Description |
|---|---|
| `get_assignments` | Assignments (class-slot filtered) |
| `get_assignment_status` | Submission & grading status |
| `get_upcoming_deadlines` | Deadlines sorted by due date |
| `get_overdue_assignments` | Past-due, unsubmitted |
| `get_actionable_tasks` | Prioritized urgency list |
| `analyze_assignment` | Status + requirements + materials |
| `extract_assignment_requirements` | Deliverables & criteria |
| `find_relevant_materials` | Content relevant to assignment |
| `decompose_task` | Subtasks with critical path |
| `create_implementation_plan` | Timeline, resources, milestones |
| `submit_assignment_text` | Submit text answer |
| `get_assignment_feedback` | Feedback & rubric results |

</details>

<details>
<summary><strong>Grades & Progress (5)</strong></summary>

| Tool | Description |
|---|---|
| `get_grades` | Overview or per-course detail |
| `get_course_progress` | Completion progress |
| `get_course_health` | Health check (progress + grades + overdue) |
| `get_study_load` | Assignment distribution by week |
| `get_submission_status_detail` | Detailed submission & feedback |

</details>

<details>
<summary><strong>Calendar & Completion (4)</strong></summary>

| Tool | Description |
|---|---|
| `get_upcoming_events` | Upcoming Moodle events |
| `create_calendar_event` | Create H-2 reminder |
| `get_activity_completion` | Completion status |
| `mark_activity_complete` | Mark activity complete |

</details>

<details>
<summary><strong>Overviews & Q&A (5)</strong></summary>

| Tool | Description |
|---|---|
| `semester_dashboard` | Courses + deadlines + grades |
| `daily_briefing` | Overdue, today, recent grades |
| `weekly_review` | Submitted, graded, overdue, progress |
| `ask_moodle` | Natural language routing |
| `get_course_updates` | Course change detection |

</details>

<details>
<summary><strong>Obsidian & Downloads (6)</strong></summary>

| Tool | Description |
|---|---|
| `sync_moodle_to_obsidian` | Full vault sync (auto-archive on semester rollover) |
| `export_deadlines_to_obsidian` | Deadlines only |
| `export_course_outline` | Course outline as note |
| `list_course_material_files` | List downloadable files |
| `download_course_materials` | Download to `Materials/` |
| `download_assignment_attachments` | Download assignment files |

</details>

> [!TIP]
> Try: *"what's due this week?"* → `get_upcoming_deadlines`, *"analyze tugas basis data"* → `analyze_assignment`, *"sync ke Obsidian"* → `sync_moodle_to_obsidian`.

---

## Obsidian Sync

Sync creates notes under `Academic/Moodle` in your vault:

```
Obsidian Vault/Academic/
  Moodle/                    ← current semester (always up-to-date)
    .semester_courses.json   ← hidden state (course IDs)
    Dashboard.md
    Deadlines.md
    Grades.md
    Courses/
  Archive/
    Semester-2026-07/        ← auto-created on rollover
      Dashboard.md
      Courses/
      Archive-README.md
```

**Semester auto-archive** triggers when >50% of course IDs change (or all are new). A single-course swap is ignored.

```bash
PYTHONPATH=src python - <<'PY'
from moodle_mcp import api
print(api.sync_moodle_to_obsidian())
PY
```

---

## Security Notes

> [!CAUTION]
> Your Moodle token grants full API access as your user. Treat it like a password.

- Tokens are loaded server-side and never sent to the model.
- Inject `MOODLE_URL` / `MOODLE_TOKEN` as environment secrets in CI.
- `moodle.py` uses browser-like `User-Agent` + POST for Cloudflare compatibility.

---

## Acknowledgements

Fork of [loyaniu/moodle-mcp](https://github.com/loyaniu/moodle-mcp) — credit to all original contributors:

| Contributor | Role |
|---|---|
| [Zhonglin Niu](https://github.com/loyaniu) | Original author, Moodle REST integration, core server |
| [Vitalii Liudvynskyi](https://github.com/v-liudwinski) | Dashboard, study load, health checks |
| [Daniel Sticker](https://github.com/stickerdaniel) | Course content & search fixes |
| [Lawrence Sinclair](https://github.com/lwsinclair) | Security assessment badge |

**Additions in this fork**

- Obsidian sync + semester auto-archive
- Material downloads & assignment attachments
- Submission & feedback tools, calendar & completion
- Polibatam class-slot filter (`MOODLE_MY_CLASS`)
- Concurrent fetch (~10× faster actionable tasks)
- Universal `install-mcp.sh` for Hermes / Claude Code / OpenCode
