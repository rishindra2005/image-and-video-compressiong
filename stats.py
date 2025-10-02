
import os
import psutil
from pynvml import *
import time

def get_file_size(file_path):
    try:
        return os.path.getsize(file_path)
    except FileNotFoundError:
        return 0


def init_nvml():
    try:
        nvmlInit()
        return nvmlDeviceGetHandleByIndex(0), None
    except NVMLError as error:
        return None, error

def shutdown_nvml():
    nvmlShutdown()

# Module-level state for disk I/O sampling
_last_disk = None
_last_time = None

def _disk_io_rate_mb_s():
    global _last_disk, _last_time
    try:
        now = time.time()
        cur = psutil.disk_io_counters()
        if _last_disk is None or _last_time is None:
            _last_disk, _last_time = cur, now
            return "N/A", "N/A"
        dt = max(now - _last_time, 1e-6)
        read_mb_s = (cur.read_bytes - _last_disk.read_bytes) / dt / (1024 * 1024)
        write_mb_s = (cur.write_bytes - _last_disk.write_bytes) / dt / (1024 * 1024)
        _last_disk, _last_time = cur, now
        return f"{read_mb_s:.2f} MB/s", f"{write_mb_s:.2f} MB/s"
    except Exception:
        return "N/A", "N/A"
def _read_sysfs_cpu_temp():
    """Attempt to read CPU temperature from Linux sysfs thermal zones."""
    base = "/sys/class/thermal"
    try:
        if not os.path.isdir(base):
            return None
        for name in os.listdir(base):
            if not name.startswith("thermal_zone"):
                continue
            path = os.path.join(base, name, "type")
            try:
                with open(path, "r") as f:
                    sensor_type = f.read().strip().lower()
                # Heuristics for CPU related zones
                if any(key in sensor_type for key in ["cpu", "x86", "package", "soc", "acpitz", "cpu_thermal"]):
                    with open(os.path.join(base, name, "temp"), "r") as tf:
                        millideg = int(tf.read().strip())
                        return millideg / 1000.0
            except Exception:
                continue
    except Exception:
        pass
    return None

def get_cpu_temp_str():
    """Return CPU temperature as a formatted string, using psutil then sysfs as fallbacks."""
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # Prefer common CPU sensor keys if present
            preferred_keys = ["coretemp", "k10temp", "cpu_thermal", "acpitz", "pch_cannonlake"]
            entries = None
            for key in preferred_keys:
                if key in temps and temps[key]:
                    entries = temps[key]
                    break
            # If not found, flatten all entries and pick the one with the highest current temp
            if not entries:
                flat = [item for sub in temps.values() for item in sub]
                entries = flat if flat else None
            if entries:
                # Choose an entry that looks like package/core; fallback to the first with a valid current
                chosen = None
                for e in entries:
                    label = getattr(e, "label", "") or ""
                    if any(tag in label.lower() for tag in ["package", "tctl", "cpu", "core 0", "tdie"]):
                        chosen = e
                        break
                if not chosen:
                    chosen = next((e for e in entries if getattr(e, "current", None) is not None), None)
                if chosen and getattr(chosen, "current", None) is not None:
                    return f"{chosen.current:.0f}°C"
    except Exception:
        pass

    # Fallback: try Linux sysfs
    sysfs_temp = _read_sysfs_cpu_temp()
    if sysfs_temp is not None:
        return f"{sysfs_temp:.0f}°C"
    return "N/A"

def get_system_stats(handle):
    # Always compute CPU and RAM, independent of GPU availability
    cpu_usage = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    ram_used = mem.used
    ram_total = mem.total
    ram_percent = mem.percent
    cpu_temp_str = get_cpu_temp_str()

    # GPU stats only if handle is available
    if handle:
        try:
            gpu_util = nvmlDeviceGetUtilizationRates(handle)
            gpu_mem = nvmlDeviceGetMemoryInfo(handle)
            gpu_temp = nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU)
            power_usage = nvmlDeviceGetPowerUsage(handle) / 1000.0  # In Watts
            # NVENC/NVDEC engine utilization
            enc_util, _ = nvmlDeviceGetEncoderUtilization(handle)
            dec_util, _ = nvmlDeviceGetDecoderUtilization(handle)
            # PCIe throughput (KB/s) -> MB/s
            try:
                rx_kb = nvmlDeviceGetPcieThroughput(handle, NVML_PCIE_UTIL_RX_BYTES)
                tx_kb = nvmlDeviceGetPcieThroughput(handle, NVML_PCIE_UTIL_TX_BYTES)
                pcie_rx = f"{rx_kb / 1024:.2f} MB/s"
                pcie_tx = f"{tx_kb / 1024:.2f} MB/s"
            except NVMLError:
                pcie_rx = pcie_tx = "N/A"
            gpu_usage_str = f"{gpu_util.gpu}%"
            gpu_temp_str = f"{gpu_temp}°C"
            gpu_ram_str = f"{gpu_mem.used / gpu_mem.total * 100:.2f}% ({format_size(gpu_mem.used)})"
            gpu_power_str = f"{power_usage:.2f}W"
            gpu_enc_str = f"{enc_util}%"
            gpu_dec_str = f"{dec_util}%"
        except NVMLError:
            gpu_usage_str = gpu_temp_str = gpu_ram_str = gpu_power_str = gpu_enc_str = gpu_dec_str = pcie_rx = pcie_tx = "N/A"
    else:
        gpu_usage_str = gpu_temp_str = gpu_ram_str = gpu_power_str = gpu_enc_str = gpu_dec_str = pcie_rx = pcie_tx = "N/A"

    # Disk I/O rates
    disk_read, disk_write = _disk_io_rate_mb_s()

    return {
        "CPU Usage": f"{cpu_usage}%",
        "CPU Temp": cpu_temp_str,
        "RAM Usage": f"{ram_percent:.0f}% ({format_size(ram_used)} / {format_size(ram_total)})",
        "GPU Usage": gpu_usage_str,
        "GPU Temp": gpu_temp_str,
        "GPU RAM": gpu_ram_str,
        "GPU Power": gpu_power_str,
        "GPU Encoder": gpu_enc_str,
        "GPU Decoder": gpu_dec_str,
        "PCIe RX": pcie_rx,
        "PCIe TX": pcie_tx,
        "Disk Read": disk_read,
        "Disk Write": disk_write,
    }

def format_size(size_bytes):
    if size_bytes > 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes > 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} bytes"

