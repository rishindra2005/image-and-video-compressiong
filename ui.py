
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, TimeElapsedColumn
from collections import deque

def make_layout() -> Layout:
    layout = Layout(name="root")
    layout.split(
        Layout(name="header", size=5),
        Layout(ratio=1, name="main"),
        Layout(size=4, name="footer"),
    )
    layout["main"].split_row(Layout(name="side"), Layout(name="body", ratio=2, minimum_size=60))
    # Split body into results (top) and errors (bottom)
    layout["body"].split(Layout(name="results", ratio=3), Layout(name="errors", size=10))
    layout["side"].split(Layout(name="system_stats"), Layout(name="total_stats"))
    return layout

class AppUI:
    def __init__(self, total_files):
        self.layout = make_layout()
        self.results_table = Table(title="File Compression Statistics (latest 40)")
        self.results_table.add_column("Filename", style="cyan")
        self.results_table.add_column("Original Size", style="magenta")
        self.results_table.add_column("Compressed Size", style="green")
        self.results_table.add_column("Ratio", style="blue")
        self.results_table.add_column("Savings", style="yellow")
        # Keep only the latest 40 results, newest first
        self._results = deque(maxlen=40)

        # TQDM-like progress at the top: description | count | bar | percent | elapsed | remaining
        # Use expand to keep alignment stable across refreshes
        self.progress = Progress(
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("{task.completed}/{task.total}", justify="right"),
            BarColumn(bar_width=None),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            expand=True,
        )
        self.task_id = self.progress.add_task("Compressing...", total=total_files)

        self.layout["header"].update(Panel(self.progress, title="Overall Progress", border_style="green"))
        self.layout["results"].update(self.results_table)

        # Errors table for failed jobs
        self._errors = []  # list of (filename, error_snippet)
        self._errors_max = 20
        self.errors_table = Table(title="Failed Jobs (latest)")
        self.errors_table.add_column("File", style="red")
        self.errors_table.add_column("Error", style="white")
        self.layout["errors"].update(Panel(self.errors_table, title="Errors", border_style="red"))

        # Track last-known totals and pipeline status for consistent rendering
        self._last_total_savings = "0 bytes"
        self._last_total_ratio = 0.0
        self._pipeline_status = "Active: 0 | Completed: 0 | Queued: 0 | Rate: 0.00 files/s"
        self._active_list_block = "In Progress:\n(none)"
        self._current_progress_line = "Current: (idle)"

    def update_system_stats(self, stats):
        stats_table = Table(title="System Stats")
        stats_table.add_column("Metric", style="bold")
        stats_table.add_column("Value")
        for key, value in stats.items():
            stats_table.add_row(key, value)
        # Render into the dedicated 'system_stats' pane instead of the parent 'side' container
        self.layout["system_stats"].update(Panel(stats_table, title="Live Stats", border_style="blue"))

    def update_total_stats(self, total_savings, total_ratio):
        # Persist values for re-rendering together with pipeline status
        self._last_total_savings = total_savings
        self._last_total_ratio = total_ratio
        total_stats_panel = Panel(
            f"Total Saved: {total_savings}\nOverall Ratio: {total_ratio:.2f}x\n\n{self._pipeline_status}\n{self._current_progress_line}\n\n{self._active_list_block}",
            title="Total Stats", border_style="magenta"
        )
        self.layout["total_stats"].update(total_stats_panel)

    def update_pipeline_stats(self, active_jobs, queued_jobs, completed_jobs, rate_files_per_sec, active_lines):
        # Update pipeline status and re-render the total stats panel with last totals
        self._pipeline_status = (
            f"Active: {active_jobs} | Completed: {completed_jobs} | Queued: {queued_jobs} | Rate: {rate_files_per_sec:.2f} files/s"
        )
        # Prepare an 'In Progress' block listing current files with sizes (limit to 8 for readability)
        if active_lines:
            display = active_lines[:8]
            more = len(active_lines) - len(display)
            lines = [f" - {line}" for line in display]
            if more > 0:
                lines.append(f" (+{more} more)")
            self._active_list_block = "In Progress:\n" + "\n".join(lines)
        else:
            self._active_list_block = "In Progress:\n(none)"

        total_stats_panel = Panel(
            f"Total Saved: {self._last_total_savings}\nOverall Ratio: {self._last_total_ratio:.2f}x\n\n{self._pipeline_status}\n{self._current_progress_line}\n\n{self._active_list_block}",
            title="Total Stats", border_style="magenta"
        )
        self.layout["total_stats"].update(total_stats_panel)

    def update_current_progress(self, filename: str, percent: float | None, speed_x: float | None):
        # Build a compact one-line status for the most recently reported file
        pct_str = f"{percent:.1f}%" if percent is not None else "--%"
        spd_str = f" @ {speed_x:.2f}x" if (speed_x is not None) else ""
        self._current_progress_line = f"Current: {filename} — {pct_str}{spd_str}"
        # Re-render total stats with the updated progress line
        total_stats_panel = Panel(
            f"Total Saved: {self._last_total_savings}\nOverall Ratio: {self._last_total_ratio:.2f}x\n\n{self._pipeline_status}\n{self._current_progress_line}\n\n{self._active_list_block}",
            title="Total Stats", border_style="magenta"
        )
        self.layout["total_stats"].update(total_stats_panel)

    def add_result(self, filename, original_size, compressed_size, ratio, savings):
        # Store newest first
        self._results.appendleft((filename, original_size, compressed_size, f"{ratio:.2f}x", savings))
        # Rebuild the table to ensure newest appears at the top and limit to 40
        new_table = Table(title="File Compression Statistics (latest 40)")
        new_table.add_column("Filename", style="cyan")
        new_table.add_column("Original Size", style="magenta")
        new_table.add_column("Compressed Size", style="green")
        new_table.add_column("Ratio", style="blue")
        new_table.add_column("Savings", style="yellow")
        for row in self._results:
            new_table.add_row(*row)
        self.results_table = new_table
        self.layout["results"].update(self.results_table)
        self.progress.update(self.task_id, advance=1)

    def set_current_file(self, filename, index=None, total=None):
        # Update the progress description with current index/total (tqdm-like)
        prefix = f"[{index}/{total}] " if index is not None and total is not None else ""
        self.progress.update(self.task_id, description=f"{prefix}Compressing: {filename}")

    def update_footer_current_file(self, filename, size_str):
        # Show current file details in the footer during processing
        self.layout["footer"].update(
            Panel(
                f"File: {filename}\nOriginal Size: {size_str}",
                title="Current File",
                border_style="yellow",
            )
        )

    def add_error(self, filename, error_log: str):
        # Keep a short snippet to avoid blowing up the UI
        snippet = (error_log or "").splitlines()[:2]
        short = " ".join(s.strip() for s in snippet if s.strip())
        if not short:
            short = "ffmpeg failed"
        self._errors.append((filename, short))
        # Trim to last N
        self._errors = self._errors[-self._errors_max:]
        # Rebuild the table
        new_table = Table(title="Failed Jobs (latest)")
        new_table.add_column("File", style="red")
        new_table.add_column("Error", style="white")
        for f, e in self._errors:
            new_table.add_row(f, e)
        self.errors_table = new_table
        self.layout["errors"].update(Panel(self.errors_table, title="Errors", border_style="red"))
