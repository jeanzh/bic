import os
import signal
import subprocess
import sys
import tempfile
from datetime import timedelta

from django.utils import timezone

from .models import CrawlerRun, CrawlerConfig


def trigger_crawl(website_code: str, trigger_type: str = 'manual') -> CrawlerRun:
    """Spawn a crawler subprocess and return the CrawlerRun record."""
    existing = CrawlerRun.objects.filter(
        website_code=website_code, status='running'
    ).first()
    if existing:
        return None

    run = CrawlerRun.objects.create(
        website_code=website_code,
        trigger_type=trigger_type,
        status='pending',
    )

    log_path = os.path.join(tempfile.gettempdir(), f'crawler_{run.id}.log')
    log_file = open(log_path, 'w')

    cmd = [sys.executable, 'manage.py', 'crawl', website_code, str(run.id)]
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
    )

    run.pid = proc.pid
    run.status = 'running'
    run.started_at = timezone.now()
    run.save()
    return run


def stop_crawl(website_code: str):
    """Gracefully stop a running crawler."""
    run = CrawlerRun.objects.filter(
        website_code=website_code, status='running'
    ).first()
    if not run:
        return False, '没有正在运行的爬虫'

    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/PID', str(run.pid)], capture_output=True)
        else:
            os.kill(run.pid, signal.SIGTERM)
    except Exception as e:
        run.status = 'stopped'
        run.finished_at = timezone.now()
        run.error_message = str(e)
        run.save()
        return True, '进程已不存在，已标记为停止'

    run.status = 'stopped'
    run.finished_at = timezone.now()
    run.save()
    return True, '已发送停止信号'


def check_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is alive."""
    if not pid:
        return False
    try:
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # 必须声明 restype，否则 64 位句柄会被截断成 32 位，偶发把存活
            # 进程误判为已退出（这是「进程意外终止」假警报的根因）。
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            # SYNCHRONIZE 对任何存活进程都可授予；PID 不存在则打开失败返回 0
            handle = kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def check_orphan_runs():
    """Mark running CrawlerRuns as failed if their PID is no longer alive."""
    # 刚启动（<60s）的运行跳过，避免进程启动期间的竞态误判
    grace = timezone.now() - timedelta(seconds=60)
    for run in CrawlerRun.objects.filter(status='running'):
        if run.started_at and run.started_at > grace:
            continue
        if run.pid and not check_pid_alive(run.pid):
            run.status = 'failed'
            run.finished_at = timezone.now()
            run.error_message = '进程意外终止'
            run.save()


def get_run_history(website_code: str):
    return CrawlerRun.objects.filter(website_code=website_code).order_by('-created_at')


def get_status(website_code: str) -> dict:
    """Return current crawl status for a website."""
    running = CrawlerRun.objects.filter(
        website_code=website_code, status='running'
    ).first()
    if running:
        return {
            'running': True,
            'status': running.get_status_display(),
            'run_id': running.id,
            'pid': running.pid,
            'started_at': running.started_at,
        }

    latest = CrawlerRun.objects.filter(website_code=website_code).order_by('-created_at').first()
    return {
        'running': False,
        'status': latest.get_status_display() if latest else '未运行',
        'run_id': latest.id if latest else None,
        'pid': None,
        'started_at': None,
        'finished_at': latest.finished_at if latest else None,
        'items_found': latest.items_found if latest else 0,
        'error_message': latest.error_message if latest else '',
    }
