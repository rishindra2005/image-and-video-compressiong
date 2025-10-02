import os
import time
import threading
import concurrent.futures
import queue
from collections import deque
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

import stats
import compressor
from ui import AppUI

def main():
    input_directory = '/home/rishi/Desktop/mummy/Camera'
    output_directory = '/home/rishi/Desktop/mummy/Camera_compressed'

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    handle, error = stats.init_nvml()
    if error:
        console = Console()
        console.print(f"NVIDIA driver error: {error}", style="bold red")
        return

    # Include all supported image/video types from compressor (case-insensitive)
    allowed_exts = set(compressor.IMAGE_EXTS) | set(compressor.VIDEO_EXTS)
    files_to_process = [
        f for f in os.listdir(input_directory)
        if os.path.isfile(os.path.join(input_directory, f))
        and os.path.splitext(f)[1].lower() in allowed_exts
    ]
    total_files = len(files_to_process)
    app_ui = AppUI(total_files)

    console = Console()
    total_original_size = 0
    total_compressed_size = 0

    stop_event = threading.Event()
    lock = threading.Lock()
    # Track pipeline metrics
    completed_timestamps = deque(maxlen=1000)  # timestamps of completed tasks
    submitted_count = 0
    completed_count = 0
    active_jobs = 0
    active_filenames = set()
    active_file_sizes = {}
    progress_by_file = {}
    # Concurrency limits
    video_sem = threading.Semaphore(5)
    image_sem = threading.Semaphore(50)
    # Failed log file (overwrite at start)
    failed_log_path = os.path.join(output_directory, 'failed.txt')
    try:
        with open(failed_log_path, 'w') as f:
            f.write('')
    except Exception as e:
        console = Console()
        console.print(f"Could not initialize failed.txt: {e}", style="bold red")
    # UI event queue to marshal UI updates onto the main thread
    ui_events: "queue.Queue[dict]" = queue.Queue()

    def update_stats(live_instance):
        while not stop_event.is_set():
            system_stats = stats.get_system_stats(handle)
            app_ui.update_system_stats(system_stats)
            # Drain UI events and apply updates from worker threads
            try:
                while True:
                    ev = ui_events.get_nowait()
                    etype = ev.get('type')
                    if etype == 'file_complete':
                        res = ev['payload']
                        app_ui.add_result(
                            res['filename'],
                            stats.format_size(res['original_size']),
                            stats.format_size(res['compressed_size']),
                            res['ratio'],
                            stats.format_size(res['savings'])
                        )
                        # If there was an error, surface it in the UI errors panel
                        if res.get('error'):
                            app_ui.add_error(res['filename'], res.get('error_log', ''))
                        total_ratio_local = (total_original_size / total_compressed_size) if total_compressed_size > 0 else 0
                        total_savings_str_local = stats.format_size(total_original_size - total_compressed_size)
                        app_ui.update_total_stats(total_savings_str_local, total_ratio_local)
                    elif etype == 'progress':
                        payload = ev['payload']
                        fname = payload.get('filename', '')
                        pct = payload.get('percent')
                        spd = payload.get('speed')
                        # Save latest per-file progress for in-progress list rendering
                        if fname:
                            with lock:
                                progress_by_file[fname] = (pct, spd)
                        app_ui.update_current_progress(fname, pct, spd)
                    ui_events.task_done()
            except queue.Empty:
                pass
            # Update pipeline stats (active, queued, completed, throughput, active file list)
            with lock:
                if completed_timestamps:
                    # Compute rate over last 30 seconds
                    now = time.time()
                    window = 30.0
                    # Count how many completions within window
                    count_window = sum(1 for t in completed_timestamps if now - t <= window)
                    rate = count_window / window
                else:
                    rate = 0.0
                queued = max(submitted_count - completed_count - active_jobs, 0)
                cur_active = active_jobs
                completed_local = completed_count
                # Build preformatted active lines: "filename (SIZE)", sorted by size desc
                items = [(name, active_file_sizes.get(name, 0)) for name in active_filenames]
                items.sort(key=lambda x: x[1], reverse=True)
                active_lines = []
                for name, sz in items:
                    pct, spd = progress_by_file.get(name, (None, None))
                    pct_str = f" — {pct:.1f}%" if isinstance(pct, (int, float)) else " — --%"
                    spd_str = f" @ {spd:.2f}x" if isinstance(spd, (int, float)) else ""
                    active_lines.append(f"{name} ({stats.format_size(sz)}){pct_str}{spd_str}")
            app_ui.update_pipeline_stats(cur_active, queued, completed_local, rate, active_lines)
            live_instance.refresh()
            time.sleep(1)

    start_time = time.time()

    with Live(app_ui.layout, console=console, screen=True, redirect_stderr=False) as live:
        stats_thread = threading.Thread(target=update_stats, args=(live,))
        stats_thread.start()

        # Concurrent execution: allow enough threads to service both video and image limits
        max_workers = max( video_sem._value + image_sem._value, (os.cpu_count() or 2) )
        futures = []

        def task(input_path, output_path, filename, ext):
            nonlocal total_original_size, total_compressed_size, completed_count, active_jobs
            # Mark as running when the worker actually starts
            with lock:
                active_jobs += 1
                active_filenames.add(filename)
            # Acquire per-type semaphore
            sem = video_sem if ext in compressor.VIDEO_EXTS else image_sem
            sem.acquire()
            try:
                # Do compression
                if ext in compressor.VIDEO_EXTS:
                    # Compute duration and pass a progress callback that enqueues updates
                    dur = compressor.get_media_duration_seconds(input_path)
                    def _cb(percent, speed_x, out_time_s):
                        try:
                            ui_events.put({'type': 'progress', 'payload': {
                                'filename': filename,
                                'percent': percent,
                                'speed': speed_x,
                            }})
                        except Exception:
                            pass
                    result = compressor.compress_file(input_path, output_path, progress_cb=_cb, duration_s=dur)
                else:
                    result = compressor.compress_file(input_path, output_path)
                compressed_size = stats.get_file_size(output_path)
                original_size_local = stats.get_file_size(input_path)
                ratio = (original_size_local / compressed_size) if compressed_size > 0 else 0
                savings = original_size_local - compressed_size
                # Update totals under lock
                with lock:
                    total_original_size += original_size_local
                    total_compressed_size += compressed_size
                res = {
                    'filename': filename,
                    'original_size': original_size_local,
                    'compressed_size': compressed_size,
                    'ratio': ratio,
                    'savings': savings,
                    'result': result,
                    'error': result.get('error'),
                    'error_log': result.get('error_log', ''),
                }
            except Exception as e:
                original_size_local = stats.get_file_size(input_path)
                res = {
                    'filename': filename,
                    'original_size': original_size_local,
                    'compressed_size': 0,
                    'ratio': 0.0,
                    'savings': 0,
                    'result': None,
                    'error': str(e),
                    'error_log': str(e),
                }
            finally:
                # Mark completion and remove from active
                with lock:
                    completed_count += 1
                    completed_timestamps.append(time.time())
                    active_jobs -= 1
                    active_filenames.discard(filename)
                    active_file_sizes.pop(filename, None)
                    progress_by_file.pop(filename, None)
                    # Append to failed.txt if error
                    if res.get('error'):
                        try:
                            with open(failed_log_path, 'a') as f:
                                f.write(f"{filename}: {res.get('error')}\n{res.get('error_log','')}\n---\n")
                        except Exception:
                            pass
                # Release semaphore
                try:
                    sem.release()
                except Exception:
                    pass
                # Send UI update event to main thread for both success and error
                ui_events.put({'type': 'file_complete', 'payload': res})
            return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for idx, filename in enumerate(files_to_process, start=1):
                input_path = os.path.join(input_directory, filename)
                output_path = os.path.join(output_directory, filename)

                # Update header/footer to reflect submission
                app_ui.set_current_file(filename, index=idx, total=total_files)
                original_size = stats.get_file_size(input_path)
                app_ui.update_footer_current_file(filename, stats.format_size(original_size))

                # Submit task
                with lock:
                    submitted_count += 1
                    active_file_sizes[filename] = original_size
                    # Initialize progress to 0% for videos so UI doesn't show --%
                    if os.path.splitext(filename)[1].lower() in compressor.VIDEO_EXTS:
                        progress_by_file[filename] = (0.0, None)
                fut = executor.submit(task, input_path, output_path, filename, os.path.splitext(filename)[1].lower())
                futures.append(fut)


            # Wait for all futures to complete
            concurrent.futures.wait(futures, return_when=concurrent.futures.ALL_COMPLETED)

        # Wait for all UI events to be processed so progress reflects true completion
        while True:
            with lock:
                all_done = (completed_count == submitted_count)
            if all_done and ui_events.empty():
                break
            time.sleep(0.1)

        # After processing completes, keep the UI open and show completion info
        total_time = time.time() - start_time
        app_ui.layout["footer"].update(
            Panel(
                f"Total compression time: {total_time:.2f} seconds for {total_files} files.\nPress 'x' and Enter to exit.",
                title="[bold green]Complete[/bold green]",
            )
        )
        # Stop background updates before waiting for user input
        stop_event.set()
        stats_thread.join()

        # Read input within Live context so the UI remains visible
        while True:
            try:
                resp = console.input("Press 'x' and Enter to close: ")
            except KeyboardInterrupt:
                resp = 'x'
            if (resp or '').strip().lower() == 'x':
                break

    stats.shutdown_nvml()

if __name__ == "__main__":
    main()