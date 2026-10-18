"""Read-only resource checks; process termination remains the owner's job."""
from pathlib import Path
import shutil
import psutil
from .contracts import TaskFailure
from .market_profile import bounded_profile


def _profile(profile):
    if profile!=bounded_profile():raise ValueError('Unknown resource profile')


def _disk(path):
    path=Path(path).absolute()
    while not path.exists():path=path.parent
    return int(shutil.disk_usage(path).free)


def preflight(workdir,profile):
    _profile(profile)
    available=int(psutil.virtual_memory().available);free=_disk(workdir)
    if available<profile['min_available_bytes'] or free<profile['min_disk_bytes']:
        raise TaskFailure('RESOURCE_UNAVAILABLE')
    return dict(available_bytes=available,disk_free_bytes=free)


def check_tree(owned_process,profile):
    _profile(profile)
    rss=owned_process.rss_bytes();free=_disk(getattr(owned_process,'resource_directory',owned_process.cwd))
    if rss>profile['max_tree_rss_bytes'] or free<profile['min_runtime_disk_bytes']:
        raise TaskFailure('RESOURCE_LIMIT')
    return dict(rss_bytes=rss,disk_free_bytes=free)


def check_time(elapsed,profile):
    _profile(profile)
    if elapsed>profile['max_seconds']:raise TaskFailure('TIME_LIMIT')


def local_rss():
    # Evidence sampling only; authoritative enforcement uses OwnedProcess handles.
    process=psutil.Process();total=process.memory_info().rss
    for child in process.children(recursive=True):
        try:total+=child.memory_info().rss
        except psutil.NoSuchProcess:pass
    return int(total)
