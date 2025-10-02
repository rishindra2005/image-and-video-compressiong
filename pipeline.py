import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Dict

import stats
import compressor

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

def classify(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


def ensure_output_path(input_path: str, output_dir: str) -> str:
    fname = os.path.basename(input_path)
    return os.path.join(output_dir, fname)


def compress_task(input_path: str, output_path: str) -> Dict:
    start = time.time()
    compressor.compress_file(input_path, output_path)
    original_size = stats.get_file_size(input_path)
    compressed_size = stats.get_file_size(output_path)
    end = time.time()
    return {
        "filename": os.path.basename(input_path),
        "input_path": input_path,
        "output_path": output_path,
        "original_size": original_size,
        "compressed_size": compressed_size,
        "duration": end - start,
    }


def run_pipeline(files: List[str], input_dir: str, output_dir: str, gpu_workers: int = 2, cpu_workers: int = 4):
    """
    Submit image and video compressions concurrently to different pools.
    Yields results dicts as jobs complete.
    """
    videos = []
    images = []
    for f in files:
        in_path = os.path.join(input_dir, f)
        kind = classify(in_path)
        if kind == "video":
            videos.append(in_path)
        elif kind == "image":
            images.append(in_path)

    futures = []
    with ThreadPoolExecutor(max_workers=gpu_workers) as gpu_pool, ThreadPoolExecutor(max_workers=cpu_workers) as cpu_pool:
        # Submit videos to GPU pool
        for vp in videos:
            outp = ensure_output_path(vp, output_dir)
            futures.append(gpu_pool.submit(compress_task, vp, outp))
        # Submit images to CPU pool
        for ip in images:
            outp = ensure_output_path(ip, output_dir)
            futures.append(cpu_pool.submit(compress_task, ip, outp))

        for fut in as_completed(futures):
            yield fut.result()
