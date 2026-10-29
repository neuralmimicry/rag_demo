"""Exercise real queue methods without starting Flask or external services."""
import ast
from pathlib import Path
import threading
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'refiner/runtime/refiner_web.py'


def manager_type():
    tree = ast.parse(SOURCE.read_text())
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'JobManager')
    methods = {'__init__', '_rehydrate_after_startup', '_remember_loaded_job', 'reconcile_tokens', '_cleanup_old_jobs'}
    original.body = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), original], type_ignores=[])
    ast.fix_missing_locations(module)
    ready = threading.Event()
    ready.set()
    env = {'threading': threading, 'queue': __import__('queue'), 'DEFAULT_WORKERS': 1, '_JOB_RUNTIME_READY': ready,
           '_ensure_dirs': lambda: None, '_now_iso': lambda: '2026-10-26T20:00:00Z',
           '_timestamp_sort_key': str, 'JOB_RETENTION_DAYS': 0,
           'logger': types.SimpleNamespace(warning=lambda *a: None)}
    exec(compile(module, str(SOURCE), 'exec'), env)
    return env['JobManager']


class RestartRehydrationTests(unittest.TestCase):
    def test_slow_history_does_not_hold_queue_lock_or_block_startup(self):
        cls = manager_type()
        entered, release = threading.Event(), threading.Event()
        cls._worker_loop = lambda *args: None
        def restore(self):
            entered.set()
            release.wait(2)
        cls._load_jobs_from_disk = restore
        manager = cls(workers=1)
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(manager.lock.acquire(timeout=0.2))
            manager.lock.release()
        finally:
            release.set()
            manager._startup_rehydrate_thread.join(2)
        self.assertFalse(manager._startup_rehydrate_thread.is_alive())

    def test_restore_never_stops_a_newer_live_job_or_rewrites_equal_metadata(self):
        cls = manager_type()
        manager = cls.__new__(cls)
        manager.lock, manager.jobs = threading.RLock(), {}
        def job(identifier, updated, status):
            return types.SimpleNamespace(job_id=identifier, updated_at=updated, status=status,
                                         finished_at=None, persist=lambda **k: None)
        live = job('new', '2026-10-26T21:00:00Z', 'running')
        manager.jobs[live.job_id] = live
        self.assertFalse(manager._remember_loaded_job(job('new', '2026-10-25', 'running')))
        self.assertFalse(manager._remember_loaded_job(job('new', live.updated_at, 'completed')))
        self.assertIs(manager.jobs['new'], live)
        self.assertEqual(live.status, 'running')
        interrupted = job('old', '2026-10-25', 'running')
        self.assertTrue(manager._remember_loaded_job(interrupted))
        self.assertEqual(interrupted.status, 'stopped')
        self.assertIsNotNone(interrupted.finished_at)


if __name__ == '__main__':
    unittest.main()
