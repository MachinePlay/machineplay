"""Runner hardware, reported once at connect, and sampled live utilization."""

import platform

import psutil

from machineplay.schemas import HardwareInfo, Telemetry


def _cpu_model() -> str:
    """e.g. "AMD Ryzen 9 5950X 16-Core Processor"; psutil doesn't expose it."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def read_hardware() -> HardwareInfo:
    return HardwareInfo(
        cpu_model=_cpu_model(),
        cpu_physical_cores=psutil.cpu_count(logical=False) or 0,
        cpu_logical_cores=psutil.cpu_count(logical=True) or 0,
        ram_total_bytes=psutil.virtual_memory().total,
    )


def prime_cpu_percent() -> None:
    """Seed psutil's baseline so the first real sample isn't 0.0."""
    psutil.cpu_percent(interval=None)


def read_telemetry() -> Telemetry:
    """CPU percent is averaged since the previous call."""
    vm = psutil.virtual_memory()
    return Telemetry(
        cpu_percent=psutil.cpu_percent(interval=None),
        ram_used_bytes=vm.used,
        ram_percent=vm.percent,
    )
