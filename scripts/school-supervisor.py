"""Idle-only source updater and one-job supervisor. Never kills a solver.

The original checkout and pinned runner paths are never changed. Releases are
immutable Git archives; each worker subprocess uses exactly one release.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile


def run(args, **kwargs):
    return subprocess.run(args, check=True, timeout=120, capture_output=True,
                          text=True, **kwargs).stdout.strip()


def cst_present():
    import psutil
    # Called only after our previous worker exited. Unresolved host leases
    # separately prevent taking a job after an ambiguous runner failure.
    return any('solver_hf' in (p.info['name'] or '').lower()
               for p in psutil.process_iter(['name']))


def check_origin(url):
    return url.rstrip('/').removesuffix('.git').casefold() in {
        'https://github.com/ismailakdag/rf-sim-orchestrator',
        'git@github.com:ismailakdag/rf-sim-orchestrator'}


def extract_release(bundle, target):
    with zipfile.ZipFile(bundle) as z:
        for member in z.infolist():
            path = (target / member.filename).resolve()
            if not path.is_relative_to(target.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError('Unsafe release archive member')
        z.extractall(target)


def update(repo, storage):
    origin = run(['git', '-C', str(repo), 'remote', 'get-url', 'origin'])
    if not check_origin(origin):
        raise RuntimeError('Unexpected update origin')
    run(['git', '-C', str(repo), 'fetch', 'origin', 'main'])
    revision = run(['git', '-C', str(repo), 'rev-parse', 'FETCH_HEAD'])
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise RuntimeError('Invalid revision')
    target = storage / revision
    ready = target / '.verified-release'
    if ready.is_file():
        return target, revision
    if target.exists():
        raise RuntimeError('Incomplete release retained for review: ' + revision)
    target.mkdir(parents=True)
    archive = storage / (revision + '.zip')
    run(['git', '-C', str(repo), 'archive', '--format=zip', '-o', str(archive), revision])
    extract_release(archive, target)
    env = dict(os.environ, PYTHONPATH=str(target / 'src'), PYTHONDONTWRITEBYTECODE='1')
    # Run the candidate's regression suite before activation. Failure keeps
    # the previous active path; no dependency install or environment mutation.
    run([sys.executable, '-m', 'unittest', 'discover', '-s', str(target / 'tests')],
        cwd=target, env=env)
    ready.write_text(revision, encoding='ascii')
    return target, revision


def api(config, route, body=None):
    request = urllib.request.Request(config['host_url'].rstrip('/') + '/api/v1/' + route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + os.environ['RF_SIM_TOKEN'], 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def blocked(config):
    return [j for j in api(config, 'jobs')['jobs']
            if j.get('worker_id') == config['worker_id'] and j['state'] in ('leased', 'needs_attention')]


def recover_known_start_failure(config, job_id):
    """One explicitly named migration recovery; never retries arbitrary failures."""
    if not re.fullmatch('[A-Za-z0-9_-]+', job_id):
        raise RuntimeError('Invalid recovery job')
    original = api(config, 'jobs/' + job_id)
    if original['state'] == 'resolved':
        return
    import psutil
    if any('solver_hf' in (p.info['name'] or '').lower() or
           'cst design environment' in (p.info['name'] or '').lower()
           for p in psutil.process_iter(['name'])):
        raise RuntimeError('Migration recovery requires no CST processes')
    if original['state'] != 'needs_attention' or original['worker_id'] != config['worker_id']:
        raise RuntimeError('Recovery job state/owner mismatch')
    record = Path(config['data_dir']) / 'runs' / job_id / 'source/cst-archive/record.json'
    data = json.loads(record.read_text(encoding='utf-8-sig'))
    if data.get('solver_started') is not False or data.get('failure_stage') != 'solver_start':
        raise RuntimeError('Recovery evidence does not prove solver never started')
    document = original['document'].copy()
    document['job_id'] = job_id + '-retry1'
    document['metadata'] = dict(document.get('metadata', {}), recovery_of=job_id)
    # Idempotent submission; the host rejects different documents at one ID.
    api(config, 'jobs', document)
    api(config, 'jobs/' + job_id + '/resolve', {
        'reason': 'Migration: local record solver_started=false, failure_stage=solver_start; '
                  'no CST/solver processes. Original retained; fresh retry1 submitted.'})


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--recover-job')
    p.add_argument('--one-cycle', action='store_true')
    args = p.parse_args()
    base = args.config.resolve().parent
    storage = base / 'updates'
    storage.mkdir(exist_ok=True)
    status = base / 'supervisor-status.json'
    active = args.repo.resolve()
    state = {}
    if status.is_file():
        state = json.loads(status.read_text(encoding='utf-8'))
        previous = state.get('active_release')
        if previous and (Path(previous) / '.verified-release').is_file():
            active = Path(previous)
    state['active_release'] = str(active)

    def save(**fields):
        state.update(fields, utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        temp = status.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2), encoding='utf-8')
        temp.replace(status)

    while not (base / 'STOP-SUPERVISOR').exists():
        try:
            config = tomllib.loads(args.config.read_text(encoding='utf-8-sig'))
            wc = config['worker']
            if args.recover_job:
                recover_known_start_failure(wc, args.recover_job)
                args.recover_job = None
            pending = blocked(wc)
            previous_check = json.loads(status.read_text(encoding='utf-8')).get('last_update_check', 0) if status.exists() else 0
            if not cst_present() and time.time() - previous_check >= 300:
                save(last_update_check=time.time())
                try:
                    active, revision = update(args.repo, storage)
                    save(active_release=str(active), revision=revision, update_error=None)
                except Exception as exc:
                    save(update_error=str(exc))
            if pending or cst_present():
                reason = 'Unresolved job; awaiting host review' if pending else 'CST process present; no new job or update'
                previous_workers = api(wc, 'workers')['workers']
                previous = next((w for w in previous_workers if w['worker_id'] == wc['worker_id']), {})
                api(wc, 'workers/presence', dict(worker_id=wc['worker_id'], hostname=__import__('socket').gethostname(),
                    runners=list(config['runners']), state='needs_attention',
                    capabilities=previous.get('capabilities', {}),
                    free_bytes=__import__('shutil').disk_usage(base).free,
                    total_bytes=__import__('shutil').disk_usage(base).total,
                    current_job_id=pending[0]['job_id'] if pending else None, note=reason))
                save(state='waiting', reason=reason)
            else:
                save(state='worker_running')
                env = dict(os.environ, PYTHONPATH=str(active / 'src'), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
                # No timeout: the pinned runner owns its job lifecycle.
                child = subprocess.run([sys.executable, '-m', 'rfsim', 'worker', '--once', '--config', str(args.config)], env=env)
                save(state='between_jobs', last_worker_exit=child.returncode)
        except Exception as exc:
            save(state='connection_or_supervisor_error', error=str(exc))
        if args.one_cycle:
            return
        time.sleep(15)
    save(state='stopped')


if __name__ == '__main__':
    main()
