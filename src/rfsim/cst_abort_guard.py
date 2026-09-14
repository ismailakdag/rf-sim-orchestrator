"""A narrow unattended handler for CST's English Keep/Discard abort dialog.

No foreground input, title-only matching, licence actions or process killing.
Ownership requires a running HF solver with the exact job model command argument
and a CST GUI in its parent chain. PID creation times are rechecked before use.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path

from .common import utc_now, write_json_atomic


def exact_model_argument(arguments: list[str], model: Path) -> bool:
    expected = os.path.normcase(os.path.abspath(model))
    return any(os.path.normcase(os.path.abspath(arg.strip('"'))) == expected
               for arg in arguments if arg and not arg.startswith('-'))


def is_abort_confirmation(title: str, labels: list[str]) -> bool:
    normalized = [label.replace('&', '').strip().casefold() for label in labels]
    required = ['how would you like to abort?', 'keep results', 'discard results', 'ok', 'cancel']
    return title.strip().casefold() == 'abort' and all(normalized.count(x) == 1 for x in required)


class AbortGuard:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()
        self.model = self.run_dir / 'model/cst-work/model.cst'
        self.owners: dict[int, float] = {}
        self.handled: set[tuple[int, float, int]] = set()

    def observe_owners(self):
        import psutil
        for process in psutil.process_iter(['name', 'cmdline']):
            try:
                if not (process.info['name'] or '').lower().startswith('solver_hf_'):
                    continue
                if not exact_model_argument(process.info['cmdline'] or [], self.model):
                    continue
                for parent in process.parents():
                    if parent.name().lower() in ('cst design environment_amd64.exe', 'cst design environment.exe'):
                        self.owners[parent.pid] = parent.create_time()
                        break
            except (psutil.Error, OSError):
                continue

    def tick(self) -> int:
        if os.name != 'nt':
            return 0
        import psutil
        from ctypes import wintypes as w
        self.observe_owners()
        user = ctypes.WinDLL('user32', use_last_error=True)
        callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user.EnumWindows.argtypes = [callback, w.LPARAM]
        user.EnumChildWindows.argtypes = [w.HWND, callback, w.LPARAM]
        user.IsWindowVisible.argtypes = [w.HWND]
        user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        user.SendMessageTimeoutW.restype = ctypes.c_ssize_t

        def text(hwnd, class_name=False):
            buffer = ctypes.create_unicode_buffer(1024)
            (user.GetClassNameW if class_name else user.GetWindowTextW)(hwnd, buffer, len(buffer))
            return buffer.value

        def send(hwnd, message):
            result = ctypes.c_size_t()
            if not user.SendMessageTimeoutW(hwnd, message, 0, 0, 2, 2000, ctypes.byref(result)):
                raise RuntimeError('CST dialog did not respond within 2 seconds')
            return result.value

        candidates = []
        def visit(hwnd, _):
            if not user.IsWindowVisible(hwnd) or text(hwnd).strip().casefold() != 'abort':
                return True
            pid = w.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in self.owners:
                candidates.append((hwnd, pid.value))
            return True
        user.EnumWindows(callback(visit), 0)
        count = 0
        for hwnd, pid in candidates:
            try:
                created = self.owners[pid]
                if psutil.Process(pid).create_time() != created:
                    continue
                identity = (pid, created, int(hwnd))
                if identity in self.handled:
                    continue
                children = []
                def child(handle, _):
                    children.append((handle, text(handle), text(handle, True)))
                    return True
                user.EnumChildWindows(hwnd, callback(child), 0)
                if not is_abort_confirmation(text(hwnd), [label for _, label, _ in children]):
                    continue
                buttons = {label.replace('&', '').strip().casefold(): handle
                           for handle, label, kind in children if kind.casefold() == 'button'}
                if not {'keep results', 'discard results', 'ok', 'cancel'} <= buttons.keys():
                    continue
                # Persist the intent before sending any message. The result remains
                # a failed run; keeping partial CST data does not validate it.
                evidence = dict(utc=utc_now(), model=str(self.model), pid=pid,
                                process_created=created, action='keep_results', state='requested')
                log = self.run_dir / 'logs/abort-dialog.json'
                write_json_atomic(log, evidence)
                self.handled.add(identity)
                send(buttons['keep results'], 0x00F5)  # BM_CLICK
                if send(buttons['keep results'], 0x00F0) != 1:  # BM_GETCHECK
                    raise RuntimeError('Keep results selection could not be verified')
                if psutil.Process(pid).create_time() != created:
                    raise RuntimeError('CST process identity changed')
                send(buttons['ok'], 0x00F5)
                evidence.update(state='confirmation_sent', finished_utc=utc_now())
                write_json_atomic(log, evidence)
                count += 1
            except (psutil.Error, OSError, RuntimeError) as exc:
                write_json_atomic(self.run_dir / 'logs/abort-dialog-error.json',
                                  dict(utc=utc_now(), error=str(exc), automatic_retry=False))
        return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Confirm only a job-owned CST Keep results abort dialog')
    parser.add_argument('--run-dir', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps({'confirmations_sent': AbortGuard(args.run_dir).tick()}))
