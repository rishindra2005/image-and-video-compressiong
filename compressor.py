
import subprocess
import os
import time

VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png'}

def _run_ffmpeg(cmd):
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    end = time.time()
    ok = proc.returncode == 0
    return ok, (end - start), proc.stderr.strip()

def _parse_fps(s: str) -> float | None:
    try:
        if '/' in s:
            num, den = s.split('/')
            num = float(num)
            den = float(den)
            if den != 0:
                return num / den
            return None
        return float(s)
    except Exception:
        return None

def get_video_rotate_tag(path: str) -> str | None:
    """Return the 'rotate' tag value from the first video stream if present (e.g., '90', '180')."""
    try:
        out = subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream_tags=rotate', '-of', 'default=nw=1:nk=1', path
        ], text=True).strip()
        if out and out.upper() != 'N/A':
            return out
    except Exception:
        pass
    return None

def get_media_duration_seconds(path: str) -> float | None:
    # 1) Try container (format) duration
    try:
        out = subprocess.check_output([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=nw=1:nk=1', path
        ], text=True).strip()
        if out:
            val = float(out)
            if val > 0:
                return val
    except Exception:
        pass
    # 2) Try first video stream's duration
    try:
        out = subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=duration', '-of', 'default=nw=1:nk=1', path
        ], text=True).strip()
        if out:
            val = float(out)
            if val > 0:
                return val
    except Exception:
        pass
    # 3) Compute via nb_frames / avg_frame_rate
    try:
        nb_frames = subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=nb_frames', '-of', 'default=nw=1:nk=1', path
        ], text=True).strip()
        fps = subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=avg_frame_rate', '-of', 'default=nw=1:nk=1', path
        ], text=True).strip()
        if nb_frames and nb_frames != 'N/A' and fps:
            n = float(nb_frames)
            r = _parse_fps(fps)
            if r and r > 0 and n > 0:
                return n / r
    except Exception:
        pass
    # 4) Fallback: count frames (slower) to estimate duration when nb_frames missing
    try:
        nb_read_frames = subprocess.check_output([
            'ffprobe', '-v', 'error', '-count_frames', '1', '-select_streams', 'v:0',
            '-show_entries', 'stream=nb_read_frames,avg_frame_rate', '-of', 'default=nw=1:nk=1', path
        ], text=True).strip().splitlines()
        if len(nb_read_frames) >= 2:
            n = float(nb_read_frames[0])
            r = _parse_fps(nb_read_frames[1])
            if r and r > 0 and n > 0:
                return n / r
    except Exception:
        pass
    return None

def _run_ffmpeg_with_progress(cmd, duration_s: float | None, progress_cb=None):
    """Run ffmpeg and parse -progress pipe:1 output. Calls progress_cb(percent, speed_x, out_time_s)."""
    start = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    percent = 0.0
    # Emit an initial progress tick so UI doesn't show --%
    if progress_cb:
        try:
            progress_cb(0.0, None, 0.0)
        except Exception:
            pass
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                # Expect key=value lines
                if '=' not in line:
                    continue
                key, val = line.split('=', 1)
                if key == 'out_time_ms':
                    try:
                        out_ms = int(val)
                        out_s = out_ms / 1_000_000.0
                        if duration_s and duration_s > 0:
                            percent = max(0.0, min(100.0, (out_s / duration_s) * 100.0))
                        else:
                            percent = 0.0
                        if progress_cb:
                            progress_cb(percent, None, out_s)
                    except ValueError:
                        pass
                elif key == 'out_time_us':
                    try:
                        out_us = int(val)
                        out_s = out_us / 1_000_000.0
                        if duration_s and duration_s > 0:
                            percent = max(0.0, min(100.0, (out_s / duration_s) * 100.0))
                        else:
                            percent = 0.0
                        if progress_cb:
                            progress_cb(percent, None, out_s)
                    except ValueError:
                        pass
                elif key == 'out_time':
                    # format HH:MM:SS.micro
                    try:
                        h, m, s = val.split(':')
                        out_s = int(h) * 3600 + int(m) * 60 + float(s)
                        if duration_s and duration_s > 0:
                            percent = max(0.0, min(100.0, (out_s / duration_s) * 100.0))
                        else:
                            percent = 0.0
                        if progress_cb:
                            progress_cb(percent, None, out_s)
                    except Exception:
                        pass
                elif key == 'speed':
                    # e.g., 2.34x
                    if progress_cb:
                        try:
                            spx = float(val.replace('x', '')) if val.endswith('x') else None
                        except Exception:
                            spx = None
                        progress_cb(percent, spx, None)
                elif key == 'progress' and val == 'end':
                    # Emit 100% on end if duration known, otherwise force 100%
                    if progress_cb:
                        try:
                            progress_cb(100.0, None, None)
                        except Exception:
                            pass
        proc.wait()
    finally:
        try:
            proc.stdout and proc.stdout.close()
        except Exception:
            pass
        try:
            proc.stderr and proc.stderr.close()
        except Exception:
            pass
    end = time.time()
    ok = proc.returncode == 0
    # We cannot capture stderr once closed; recommend returning empty here
    return ok, (end - start), ''

def compress_file(input_path, output_path, *, progress_cb=None, duration_s: float | None = None):
    """Compress a file using ffmpeg.
    - Videos: use h264_nvenc for GPU encoding, VBR, preset slow.
    - Images: attempt mjpeg_nvenc (GPU). If not available, fallback to CPU mjpeg.
    Returns: dict with {'type','duration_sec','error','error_log'}
    """
    ext = os.path.splitext(input_path)[1].lower()
    filename = os.path.basename(input_path)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if ext in VIDEO_EXTS:
        rotate_tag = get_video_rotate_tag(input_path)
        # Try 1: NVENC with CUDA hwaccel (device decode + encode)
        base1 = [
            '-hwaccel', 'cuda',
            '-hwaccel_output_format', 'cuda',
            '-i', input_path,
            '-r', '24',  # frame limiting
            '-c:v', 'h264_nvenc',
            '-preset', 'p1',  # fastest NVENC preset for throughput
            '-rc', 'vbr',
            '-cq', '34',  # increase compression (higher CQ -> smaller size)
            '-c:a', 'copy',
            # Preserve metadata and orientation tags; optimize MP4 for playback
            '-map_metadata', '0', '-movflags', 'use_metadata_tags+faststart',
            *( ['-metadata:s:v:0', f'rotate={rotate_tag}'] if rotate_tag else [] ),
            output_path
        ]
        if progress_cb is not None:
            cmd1 = ['ffmpeg', '-y', '-hide_banner', '-nostats', '-loglevel', 'error', '-progress', 'pipe:1', *base1]
        else:
            cmd1 = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', *base1]
        if progress_cb is not None:
            ok, dur, err = _run_ffmpeg_with_progress(cmd1, duration_s, progress_cb)
        else:
            ok, dur, err = _run_ffmpeg(cmd1)
        if ok:
            return {'type': 'video-gpu', 'duration_sec': dur, 'error': None, 'error_log': ''}

        # Try 2: NVENC without enforcing CUDA hwaccel (software decode, GPU encode)
        base2 = [
            '-i', input_path,
            '-r', '24',
            '-c:v', 'h264_nvenc',
            '-preset', 'p1',
            '-rc', 'vbr',
            '-cq', '34',
            '-c:a', 'copy',
            '-map_metadata', '0', '-movflags', 'use_metadata_tags+faststart',
            *( ['-metadata:s:v:0', f'rotate={rotate_tag}'] if rotate_tag else [] ),
            output_path
        ]
        if progress_cb is not None:
            cmd2 = ['ffmpeg', '-y', '-hide_banner', '-nostats', '-loglevel', 'error', '-progress', 'pipe:1', *base2]
        else:
            cmd2 = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', *base2]
        if progress_cb is not None:
            ok2, dur2, err2 = _run_ffmpeg_with_progress(cmd2, duration_s, progress_cb)
        else:
            ok2, dur2, err2 = _run_ffmpeg(cmd2)
        if ok2:
            return {'type': 'video-gpu-swdec', 'duration_sec': dur2, 'error': None, 'error_log': ''}

        # Try 3: CPU fallback with libx264 and re-encode audio to AAC
        base3 = [
            '-i', input_path,
            '-r', '24',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '28',
            '-c:a', 'aac', '-b:a', '128k',
            '-map_metadata', '0', '-movflags', 'use_metadata_tags+faststart',
            *( ['-metadata:s:v:0', f'rotate={rotate_tag}'] if rotate_tag else [] ),
            output_path
        ]
        if progress_cb is not None:
            cmd3 = ['ffmpeg', '-y', '-hide_banner', '-nostats', '-loglevel', 'error', '-progress', 'pipe:1', *base3]
            ok3, dur3, err3 = _run_ffmpeg_with_progress(cmd3, duration_s, progress_cb)
        else:
            cmd3 = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', *base3]
            ok3, dur3, err3 = _run_ffmpeg(cmd3)
        if ok3:
            return {'type': 'video-cpu', 'duration_sec': dur3, 'error': None, 'error_log': ''}

        # All attempts failed
        combined_err = "\n".join([msg for msg in [err, err2, err3] if msg])
        return {'type': 'video-failed', 'duration_sec': 0.0, 'error': 'ffmpeg_failed', 'error_log': combined_err}
    elif ext in IMAGE_EXTS:
        # Try GPU JPEG encoder first
        gpu_cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-hwaccel', 'cuda',
            '-i', input_path,
            '-c:v', 'mjpeg_nvenc',
            '-q:v', '16',
            '-f', 'image2',
            output_path
        ]
        ok, dur, err = _run_ffmpeg(gpu_cmd)
        if ok:
            return {'type': 'image-gpu', 'duration_sec': dur, 'error': None, 'error_log': ''}
        # Fallback to CPU
        cpu_cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', input_path,
            '-q:v', '16',
            output_path
        ]
        ok2, dur2, err2 = _run_ffmpeg(cpu_cmd)
        if ok2:
            return {'type': 'image-cpu', 'duration_sec': dur2, 'error': None, 'error_log': ''}
        return {'type': 'image-failed', 'duration_sec': 0.0, 'error': 'ffmpeg_failed', 'error_log': err2}
    else:
        # Unsupported type; skip
        return {'type': 'skip', 'duration_sec': 0.0, 'error': None, 'error_log': ''}
