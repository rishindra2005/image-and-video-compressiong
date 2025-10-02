import os
import subprocess
import time
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.layout import Layout
from rich.align import Align
import psutil
from pynvml import *

def get_file_size(file_path):
    try:
        return os.path.getsize(file_path)
    except FileNotFoundError:
        return 0

def format_size(size_bytes):
    if size_bytes > 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes > 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} bytes"

def make_layout() -> Layout:
    layout = Layout(name="root")
    layout.split(
        Layout(name="header", size=3),
        Layout(ratio=1, name="main"),
        Layout(size=10, name="footer"),
    )
    layout["main"].split_row(Layout(name="side"), Layout(name="body", ratio=2, minimum_size=60))
    layout["side"].split(Layout(name="system_stats"), Layout(name="total_stats"))
    return layout

def compress_media(input_dir, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    try:
        nvmlInit()
        handle = nvmlDeviceGetHandleByIndex(0)
    except NVMLError as error:
        console.print(f"NVIDIA driver error: {error}", style="bold red")
        return

    files_to_process = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
    total_files = len(files_to_process)

    console = Console()
    results_table = Table(title="File Compression Statistics")
    results_table.add_column("Filename", style="cyan")
    results_table.add_column("Original Size", style="magenta")
    results_table.add_column("Compressed Size", style="green")
    results_table.add_column("Ratio", style="blue")
    results_table.add_column("Savings", style="yellow")

    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
    )
    task_id = progress.add_task("Compressing...", total=total_files)

    layout = make_layout()
    layout["header"].update(Panel(progress, title="Overall Progress", border_style="green"))
    layout["body"].update(results_table)

    total_original_size = 0
    total_compressed_size = 0

    start_time = time.time()

    with Live(layout, console=console, screen=True, redirect_stderr=False) as live:
        for filename in files_to_process:
            input_path = os.path.join(input_dir, filename)
            output_path = os.path.join(output_dir, filename)

            original_size = get_file_size(input_path)

            command = []
            if filename.lower().endswith('.mp4'):
                command = [
                    'ffmpeg', '-y', '-i', input_path, '-r', '24', '-c:v', 'h264_nvenc',
                    '-preset', 'slow', '-rc', 'vbr', '-cq', '32', '-c:a', 'copy', output_path
                ]
            elif filename.lower().endswith('.jpg'):
                command = ['ffmpeg', '-y', '-i', input_path, '-q:v', '8', output_path]
            else:
                progress.update(task_id, advance=1)
                continue

            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            layout["header"].update(Panel(f"Compressing: {filename}", title="Current File", border_style="yellow"))

            while process.poll() is None:
                # Update system stats
                cpu_usage = psutil.cpu_percent()
                cpu_temp = psutil.sensors_temperatures().get('coretemp', [None])[0]
                cpu_temp_str = f"{cpu_temp.current}°C" if cpu_temp else "N/A"
                gpu_util = nvmlDeviceGetUtilizationRates(handle)
                gpu_mem = nvmlDeviceGetMemoryInfo(handle)
                gpu_temp = nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU)
                power_usage = nvmlDeviceGetPowerUsage(handle) / 1000.0 # In Watts

                stats_table = Table(title="System Stats")
                stats_table.add_column("Metric", style="bold")
                stats_table.add_column("Value")
                stats_table.add_row("CPU Usage", f"{cpu_usage}%")
                stats_table.add_row("CPU Temp", cpu_temp_str)
                stats_table.add_row("GPU Usage", f"{gpu_util.gpu}%")
                stats_table.add_row("GPU Temp", f"{gpu_temp}°C")
                stats_table.add_row("GPU RAM", f"{gpu_mem.used / gpu_mem.total * 100:.2f}% ({format_size(gpu_mem.used)})")
                stats_table.add_row("GPU Power", f"{power_usage:.2f}W")
                layout["side"].update(Panel(stats_table, title="Live Stats", border_style="blue"))
                time.sleep(0.5)

            compressed_size = get_file_size(output_path)
            total_original_size += original_size
            total_compressed_size += compressed_size
            compression_ratio = original_size / compressed_size if compressed_size > 0 else 0
            savings = original_size - compressed_size
            results_table.add_row(
                filename,
                format_size(original_size),
                format_size(compressed_size),
                f"{compression_ratio:.2f}x",
                format_size(savings)
            )

            total_ratio = total_original_size / total_compressed_size if total_compressed_size > 0 else 0
            total_savings = total_original_size - total_compressed_size
            total_stats_panel = Panel(
                f"Total Saved: {format_size(total_savings)}\nOverall Ratio: {total_ratio:.2f}x",
                title="Total Stats", border_style="magenta"
            )
            layout["footer"].update(total_stats_panel)

            progress.update(task_id, advance=1)

    total_time = time.time() - start_time
    console.print(Panel(f"Total compression time: {total_time:.2f} seconds for {total_files} files.", title="[bold green]Complete[/bold green]"))
    console.print("Press 'x' and Enter to exit.")
    while input() != 'x':
        pass

    nvmlShutdown()

def main():
    input_directory = '/home/rishi/Desktop/mummy/test'
    output_directory = '/home/rishi/Desktop/mummy/test_compressed'
    compress_media(input_directory, output_directory)

if __name__ == "__main__":
    main()
