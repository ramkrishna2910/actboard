"""Rich terminal dashboard — live-updating display with progress bars."""

import threading
import time

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.progress_bar import ProgressBar

import pipeline_events

console = Console()

SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class DashboardState:
    def __init__(self):
        self.stages = {}       # node_id -> {status, detail, label, start_time}
        self.agents = {}       # label -> {status, tokens_in, tokens_out, cost, start_time}
        self.total_tokens = 0
        self.total_cost = 0.0
        self.agents_done = 0
        self.agents_total = 0
        self.start_time = None
        self.logs = []
        self.tick = 0

    def elapsed(self) -> str:
        if not self.start_time:
            return "00:00"
        e = int(time.time() - self.start_time)
        return f"{e // 60:02d}:{e % 60:02d}"

    def agent_elapsed(self, info: dict) -> str:
        if not info.get("start_time"):
            return ""
        e = int(time.time() - info["start_time"])
        return f"{e}s"


state = DashboardState()


def process_events():
    for e in pipeline_events.drain():
        etype = e["type"]
        node = e.get("node", "")

        if etype == "pipeline_start":
            state.start_time = time.time()
            state.logs.append("[cyan]Pipeline started[/cyan]")

        elif etype == "stage_start":
            label = node.replace("fetch_", "").replace("_", " ").title()
            state.stages[node] = {"status": "active", "detail": "", "label": label, "start_time": time.time()}
            state.logs.append(f"[cyan]{label} started[/cyan]")

        elif etype == "stage_complete":
            if node in state.stages:
                count = e.get("item_count", "")
                state.stages[node]["status"] = "complete"
                state.stages[node]["detail"] = f"{count} items" if count != "" else "done"
            state.logs.append(f"[green]{state.stages.get(node, {}).get('label', node)} complete[/green]")

        elif etype == "stage_error":
            if node in state.stages:
                state.stages[node]["status"] = "error"
                state.stages[node]["detail"] = e.get("error", "")[:40]
            state.logs.append(f"[red]{node} error: {e.get('error', '')}[/red]")

        elif etype == "analyze_start":
            state.agents_total = e.get("task_count", 0)
            for label in e.get("tasks", []):
                state.agents[label] = {"status": "idle", "tokens_in": 0, "tokens_out": 0, "cost": 0, "start_time": None}
            state.logs.append(f"[cyan]Dispatching {state.agents_total} agents[/cyan]")

        elif etype == "agent_start":
            label = e.get("label", "")
            if label in state.agents:
                state.agents[label]["status"] = "active"
                state.agents[label]["start_time"] = time.time()

        elif etype == "agent_complete":
            label = e.get("label", "")
            if label in state.agents:
                state.agents[label]["status"] = "complete"
                state.agents[label]["tokens_in"] = e.get("tokens_in", 0)
                state.agents[label]["tokens_out"] = e.get("tokens_out", 0)
                state.agents[label]["cost"] = e.get("cost", 0)
            state.agents_done += 1
            tin = e.get("tokens_in", 0)
            tout = e.get("tokens_out", 0)
            state.total_tokens += tin + tout
            state.total_cost += e.get("cost", 0)
            state.logs.append(f"[green]{label} done ({tin + tout:,} tokens)[/green]")

        elif etype == "agent_error":
            label = e.get("label", "")
            if label in state.agents:
                state.agents[label]["status"] = "error"
            state.agents_done += 1
            state.logs.append(f"[red]{label} error[/red]")

        elif etype == "notion_source":
            name = e.get("source_name", "")
            state.logs.append(f"[cyan]Notion: {name} ({e.get('item_count', 0)} items)[/cyan]")

        elif etype == "notion_complete":
            state.logs.append(f"[green]Published: {e.get('page_url', '')}[/green]")

        elif etype == "pipeline_complete":
            state.logs.append(f"[green bold]Done in {e.get('duration', 0):.1f}s[/green bold]")

        elif etype == "pipeline_error":
            state.logs.append(f"[red bold]Pipeline error: {e.get('error', '')}[/red bold]")


def _status_icon(status: str) -> str:
    if status == "active":
        return f"[cyan bold]{SPINNER_CHARS[state.tick % len(SPINNER_CHARS)]}[/cyan bold]"
    elif status == "complete":
        return "[green bold]\u2713[/green bold]"
    elif status == "error":
        return "[red bold]\u2717[/red bold]"
    return "[dim]\u2219[/dim]"


def _progress_bar(info: dict, width: int = 20) -> str:
    """Build a text-based progress bar for active agents."""
    status = info["status"]
    if status == "complete":
        return f"[green]{'█' * width}[/green]"
    elif status == "error":
        return f"[red]{'█' * 3}{'░' * (width - 3)}[/red]"
    elif status == "active":
        # Animate: fill based on elapsed time (assume ~60s per agent)
        elapsed = time.time() - info.get("start_time", time.time())
        progress = min(elapsed / 60.0, 0.95)  # cap at 95% until done
        filled = int(progress * width)
        # Animate the leading edge
        chars = "░▒▓█"
        lead = chars[state.tick % len(chars)]
        bar = f"[cyan]{'█' * filled}{lead}{'░' * (width - filled - 1)}[/cyan]"
        return bar
    else:
        return f"[dim]{'░' * width}[/dim]"


def build_display() -> Table:
    state.tick += 1

    # Stages table
    stages_table = Table(title="[bold]Sources[/bold]", show_header=True, header_style="bold dim",
                         expand=True, border_style="dim", padding=(0, 1))
    stages_table.add_column("", width=2)
    stages_table.add_column("Stage", min_width=14)
    stages_table.add_column("Progress", min_width=22)
    stages_table.add_column("Detail", min_width=10)

    for node_id, info in state.stages.items():
        icon = _status_icon(info["status"])
        bar = _progress_bar(info, 18)
        stages_table.add_row(icon, info["label"], bar, info.get("detail", ""))

    # Agents table
    agents_table = Table(title="[bold]Agents[/bold]", show_header=True, header_style="bold dim",
                         expand=True, border_style="dim", padding=(0, 1))
    agents_table.add_column("", width=2)
    agents_table.add_column("Agent", min_width=26)
    agents_table.add_column("Progress", min_width=22)
    agents_table.add_column("Tokens", min_width=14, justify="right")
    agents_table.add_column("Time", min_width=5, justify="right")

    for label, info in state.agents.items():
        icon = _status_icon(info["status"])
        bar = _progress_bar(info, 18)
        tokens = ""
        elapsed = ""
        if info["tokens_in"] or info["tokens_out"]:
            tokens = f"{info['tokens_in'] + info['tokens_out']:,}"
        if info["status"] == "active":
            elapsed = f"[cyan]{state.agent_elapsed(info)}[/cyan]"
        elif info["status"] == "complete" and info.get("start_time"):
            elapsed = f"[dim]{state.agent_elapsed(info)}[/dim]"
        short_label = label if len(label) <= 30 else label[:28] + ".."
        agents_table.add_row(icon, short_label, bar, tokens, elapsed)

    # Overall progress bar
    if state.agents_total > 0:
        pct = state.agents_done / state.agents_total
        bar_w = 40
        filled = int(pct * bar_w)
        overall_bar = f"[green]{'█' * filled}[/green][dim]{'░' * (bar_w - filled)}[/dim]"
        progress_text = f"  {overall_bar} {state.agents_done}/{state.agents_total} agents  |  {state.total_tokens:,} tokens  |  {state.elapsed()}"
    else:
        progress_text = f"  [dim]Waiting for agents...[/dim]  |  {state.elapsed()}"

    # Log (last 6 lines)
    recent_logs = state.logs[-6:] if state.logs else ["[dim]Waiting...[/dim]"]
    log_text = "\n".join(recent_logs)

    # Combine
    outer = Table.grid(expand=True)
    outer.add_row(stages_table)
    outer.add_row(agents_table)
    outer.add_row(Text.from_markup(progress_text))
    outer.add_row(Panel(log_text, title="Log", border_style="dim", height=8))

    return outer


def run_dashboard(pipeline_fn):
    pipeline_events.enable()

    pipeline_thread = threading.Thread(target=pipeline_fn, daemon=True)
    pipeline_thread.start()

    with Live(build_display(), console=console, refresh_per_second=4, screen=True) as live:
        while pipeline_thread.is_alive() or not pipeline_events._event_queue.empty():
            process_events()
            live.update(build_display())
            time.sleep(0.25)
        process_events()
        live.update(build_display())
        time.sleep(1)

    console.print()
    console.print(f"[bold green]Pipeline complete[/bold green] in {state.elapsed()}")
    console.print(f"  Tokens: {state.total_tokens:,}  |  Cost: ${state.total_cost:.3f}  |  Agents: {state.agents_done}/{state.agents_total}")
    if state.logs:
        last = state.logs[-1]
        if "Published" in last or "page_url" in last:
            console.print(f"  {last}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    import os
    os.chdir(str(__import__("pathlib").Path(__file__).parent))

    from main import main as pipeline_main
    run_dashboard(pipeline_main)
