"""Read-only monitoring: worker presence, job heartbeat, and a local Python queue."""
from __future__ import annotations

import json
import re
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path

from .common import parse_utc


STAGES = {'solver_started': 'Solver başlatıldı', 'source_snapshotted': 'Kaynak hazırlanıyor',
          'model_built': 'Model oluşturuldu', 'results_exported': 'Sonuçlar alındı',
          'results_validated': 'Sonuçlar doğrulandı', 'worker_running': 'İş yürütülüyor',
          'runner_active': 'İş yürütülüyor', 'cst_model_built': 'Model oluşturuldu'}


def age(timestamp, now=None):
    if not timestamp:
        return None
    try:
        return max(0, ((now or datetime.now(timezone.utc)) - parse_utc(timestamp)).total_seconds())
    except (ValueError, TypeError):
        return None


def duration(seconds):
    if seconds is None:
        return '—'
    value = max(0, int(seconds))
    hours, rest = divmod(value, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def timestamp(value):
    try:
        return parse_utc(value).astimezone().strftime('%d.%m.%Y %H:%M:%S %Z')
    except (ValueError, TypeError):
        return '—'


def stage_label(note):
    note = (note or '').removeprefix('worker stage: ')
    match = re.fullmatch(r'solver_running:(\d+)s', note)
    if match:
        return 'Solver çalışıyor', int(match[1])
    if note.startswith('interaction_required:'):
        return 'Pencere müdahalesi gerekiyor: ' + note.split(':', 1)[1], None
    return STAGES.get(note, note or '—'), None


def remote_rows(workers, jobs, now=None):
    now = now or datetime.now(timezone.utc)
    by_id = {j['job_id']: j for j in jobs}
    rows = []
    for worker in workers:
        key = worker['worker_id']
        job = by_id.get(worker.get('current_job_id'), {})
        # A live job heartbeat is stronger evidence than older presence data.
        if job.get('worker_id') != key:
            job = {}
        last = worker.get('last_seen_utc')
        if job.get('state') == 'leased' and age(job.get('updated_utc'), now) is not None:
            if age(last, now) is None or age(job['updated_utc'], now) < age(last, now):
                last = job['updated_utc']
        online = age(last, now) is not None and age(last, now) <= 90
        state = job.get('state') or worker.get('state')
        activity = {'leased': 'Çalışıyor', 'running': 'Çalışıyor', 'idle': 'İş bekliyor',
                    'needs_attention': 'Müdahale gerekli', 'failed': 'Hata', 'stopping': 'Durduruluyor',
                    'completed': 'Tamamlandı', 'resolved': 'Kesilen iş kapatıldı'}.get(state, state or 'Bilinmiyor')
        if not online:
            activity = 'Güncel işlem bilinmiyor'
        note = job.get('note') or worker.get('note') or ''
        stage, elapsed = stage_label(note)
        history = [j for j in jobs if j.get('worker_id') == key]
        done = sum(j['state'] == 'completed' for j in history)
        issue = sum(j['state'] in ('failed', 'needs_attention', 'resolved') for j in history)
        installs = worker.get('capabilities', {}).get('cst_installations', [])
        rows.append(dict(id='remote:' + key, name=worker.get('hostname') or key, worker=key,
                         connection='Çevrimiçi' if online else 'Çevrimdışı', activity=activity,
                         stage=stage if online else 'Son kayıt: ' + stage, elapsed=duration(elapsed),
                         disk=worker.get('free_bytes'), last=last, job=worker.get('current_job_id') or '—',
                         cst=', '.join(str(i.get('major') or '?') for i in installs) or '—',
                         progress=f'{done} tamam / {issue} kesinti-hata',
                         note=note, scope='Host geçmişi; kesilen eski denemeler dahildir.',
                         tone='offline' if not online else ('warning' if state in ('needs_attention', 'failed') or 'interaction_required:' in note else 'normal')))
    return rows


def local_row(current_path: Path):
    row = dict(id='local', name=socket.gethostname(), worker='Bu bilgisayar', connection='Yerel',
               activity='İş bekliyor', stage='—', elapsed='—', disk=None, last=None, job='—',
               cst='—', progress='—', note='', scope='Yerel Python kuyruğu', tone='normal')
    try:
        current_path = current_path.resolve(strict=True)
        root = current_path.parent
        row['disk'] = shutil.disk_usage(root).free
        current = json.loads(current_path.read_text(encoding='utf-8-sig'))
        plan = current.get('active_campaign')
        if not plan:
            row['stage'] = 'Etkin kampanya yok'
            return row
        plan_path = (root / plan).resolve()
        state = json.loads((plan_path.parent / 'state.json').read_text(encoding='utf-8-sig'))
        row.update(job=state.get('active_job') or '—', last=state.get('updated_utc'),
                   progress=f"{len(state.get('completed', []))}/{len(state.get('jobs', []))} tamam · {len(state.get('skipped', []))} atlandı",
                   scope=str(plan_path.parent), note=state.get('stop_reason') or '')
        row['stage'], _ = stage_label(state.get('worker_stage') or state.get('stage'))
        if state.get('status') in ('completed', 'finished', 'finished_with_skips'):
            row['activity'] = 'Tamamlandı'
            return row
        import psutil
        active_path = state.get('active_job_path')
        job = json.loads(Path(active_path).read_text(encoding='utf-8-sig')) if active_path else {}
        work = Path(job['work']).resolve() if job.get('work') else None
        solver = None
        if work:
            from .cst_abort_guard import exact_model_argument
            for proc in psutil.process_iter(['name', 'cmdline']):
                try:
                    if (proc.info['name'] or '').lower().startswith('solver_hf_') and exact_model_argument(proc.info['cmdline'] or [], work / 'model.cst'):
                        solver = proc
                        break
                except (psutil.Error, OSError):
                    continue
            heartbeat = work / 'heartbeat.json'
            if heartbeat.is_file():
                beat = json.loads(heartbeat.read_text(encoding='utf-8-sig'))
                row['elapsed'] = duration(beat.get('elapsed_seconds'))
                row['last'] = beat.get('utc') or row['last']
        worker_live = False
        if state.get('worker_pid') and active_path:
            try:
                args = psutil.Process(state['worker_pid']).cmdline()
                worker_live = any(Path(a).resolve() == Path(active_path).resolve() for a in args if a and not a.startswith('-'))
            except (psutil.Error, OSError):
                pass
        if solver:
            row.update(activity='Çalışıyor', stage='Solver çalışıyor')
            row['elapsed'] = row['elapsed'] if row['elapsed'] != '—' else duration(datetime.now().timestamp() - solver.create_time())
            args = ' '.join(solver.cmdline())
            version = re.search(r'(?:cst-version:|CST Studio Suite )(20\d\d)', args, re.I)
            row['cst'] = version[1] if version else '—'
        elif worker_live:
            row['activity'] = 'İş yürütülüyor'
        else:
            row.update(activity='Süreç doğrulanamadı', tone='warning', note='Durum dosyasındaki aktif işlem canlı süreçle doğrulanamadı. Son kayıt gösteriliyor.')
    except Exception as exc:
        row.update(activity='Yerel kayıt okunamadı', tone='warning', note=str(exc))
    return row


def collect(client, local_current=None):
    rows, errors = [], []
    if local_current:
        rows.append(local_row(Path(local_current)))
    try:
        workers = client.json('GET', '/api/v1/workers')['workers']
        jobs = client.json('GET', '/api/v1/jobs')['jobs']
        rows.extend(remote_rows(workers, jobs))
    except Exception as exc:
        errors.append('Hosta ulaşılamadı: ' + type(exc).__name__ + '. Son uzak kayıtlar güncel sayılmaz.')
    return rows, errors
