"""Read-only monitoring: worker presence, job heartbeat, and a local Python queue."""
from __future__ import annotations

import json
import re
import shutil
import socket
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .common import parse_utc

_RECORDS = {}
_DOCUMENTS = {}


def queue_estimate(total, done, skipped, records, elapsed=None, available=True):
    """Serial queue estimate; failed attempts and upload delays are not samples."""
    samples = [r.get('total_seconds') for r in records]
    samples = [v for v in samples if isinstance(v, (int, float)) and math.isfinite(v) and v > 0]
    remaining = max(0, total-done-skipped)
    mean = statistics.mean(samples) if samples else None
    result = dict(queue_progress=f'{done}/{total} tamam · {skipped} atlandı',
                  average=duration(mean), planned=duration(mean*total if mean else None),
                  remaining='—', estimate_note=f'{len(samples)} başarılı koşu; model oluşturma ve dışa aktarma dahil. Tahmin, süre sınırı değildir.')
    if remaining == 0:
        result['remaining'] = 'Tamamlandı'
    elif not available:
        result['remaining'] = 'Durakladı / güncel değil'
    elif mean is not None:
        if elapsed is not None and elapsed >= mean:
            result['remaining'] = 'Aktif iş ortalamayı aştı'
        else:
            result['remaining'] = '≈ ' + duration(mean*remaining-(elapsed or 0))
    if len(samples) < 3:
        result['estimate_note'] += ' Üçten az örnek: ön tahmin.'
    return result


def record_summary(row, records):
    cells = [r.get('mesh_cells') for r in records if isinstance(r.get('mesh_cells'), int)]
    row['mesh'] = (f'{min(cells):,}–{max(cells):,} hücre' if cells and min(cells) != max(cells)
                   else f'{cells[0]:,} hücre' if cells else 'Henüz alınmadı')
    row['mesh_note'] = 'Tamamlanan koşuların gerçek ağı; aktif işin canlı mesh bilgisi değildir.'
    solves = [r['solve_seconds'] for r in records if isinstance(r.get('solve_seconds'), (int, float))]
    row['solver_average'] = duration(statistics.mean(solves) if solves else None)


def local_records(completed):
    records = []
    for run in completed:
        try:
            p = Path(run['archive'])/'record.json'
            key = (str(p), p.stat().st_mtime_ns)
            if key not in _RECORDS:
                r = json.loads(p.read_text(encoding='utf-8-sig'))
                if r.get('results_validated') is not True:
                    continue
                _RECORDS[key] = r
            records.append(_RECORDS[key])
        except (OSError, ValueError, KeyError):
            continue
    return records


def enrich_remote(rows, jobs, client, now=None):
    from .spectra import ResultRepository
    repository = ResultRepository(client)
    for job in jobs:
        if 'document' not in job:
            key = (client.base_url, job['job_id'])
            if key not in _DOCUMENTS:
                _DOCUMENTS[key] = client.json('GET', '/api/v1/jobs/'+job['job_id'])['document']
            job['document'] = _DOCUMENTS[key]
    for row in rows:
        assigned = [j for j in jobs if j.get('worker_id') == row['worker'] or
                    j.get('document', {}).get('metadata', {}).get('required_worker_id') == row['worker']]
        if not assigned:
            continue
        active = next((j for j in assigned if j['job_id'] == row['job']), None)
        selected = active or max(assigned, key=lambda j:j.get('submitted_utc') or '')
        document = selected.get('document', {})
        campaign = document.get('metadata', {}).get('campaign')
        group = [j for j in assigned if j.get('document', {}).get('metadata', {}).get('campaign') == campaign
                 and j.get('document', {}).get('runner') == document.get('runner')]
        # Missing campaign metadata must not pool unrelated jobs.
        if not campaign:
            group = [selected]
        records = []
        for job in group:
            if job['state'] != 'completed' or not job.get('result_sha256'):
                continue
            key = (client.base_url, job['result_sha256'])
            try:
                if key not in _RECORDS:
                    _RECORDS[key] = repository.load(dict(origin='remote', id=job['job_id'], sha256=job['result_sha256']))['record']
                records.append(_RECORDS[key])
            except Exception:
                row['note'] += ' Süre/mesh sonuç paketi okunamadı.'
        _, elapsed = stage_label(active.get('note')) if active else ('', None)
        blocked = any(j['state'] in ('needs_attention', 'failed') for j in group)
        row.update(queue_estimate(len(group), sum(j['state']=='completed' for j in group),
                   sum(j['state']=='resolved' for j in group), records, elapsed,
                   row['connection']=='Çevrimiçi' and not blocked))
        record_summary(row, records)
        row['scope'] = campaign or selected['job_id']
        starts = [r.get('started_utc') for r in records if r.get('started_utc')]
        ends = [r.get('finished_utc') for r in records if r.get('finished_utc')]
        end = parse_utc(max(ends)) if ends and all(j['state'] in ('completed', 'resolved') for j in group) else (now or datetime.now(timezone.utc))
        row['queue_elapsed'] = duration(age(min(starts), end)) if starts else '—'
        row['estimate_note'] += ' Yalnız hosta bırakılmış işler; koşullu sonraki liste dahil değil.'


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
        plan = current.get('active_campaign') or current.get('last_completed_campaign')
        if not plan:
            row['stage'] = 'Etkin kampanya yok'
            return row
        plan_path = (root / plan).resolve()
        state = json.loads((plan_path.parent / 'state.json').read_text(encoding='utf-8-sig'))
        records = local_records(state.get('completed', []))
        row.update(queue_estimate(len(state.get('jobs', [])), len(state.get('completed', [])),
                                 len(state.get('skipped', [])), records))
        record_summary(row, records)
        finished = state.get('status') in ('completed', 'finished', 'finished_with_skips')
        end = parse_utc(state['updated_utc']) if finished else datetime.now(timezone.utc)
        row['queue_elapsed'] = duration(age(state.get('started_utc'), end))
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
        elapsed = None
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
                elapsed = beat.get('elapsed_seconds')
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
        row.update(queue_estimate(len(state.get('jobs', [])), len(state.get('completed', [])),
                                 len(state.get('skipped', [])), records,
                                 elapsed, bool(solver or worker_live)))
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
        remote = remote_rows(workers, jobs)
        enrich_remote(remote, jobs, client)
        rows.extend(remote)
    except Exception as exc:
        errors.append('Hosta ulaşılamadı: ' + type(exc).__name__ + '. Son uzak kayıtlar güncel sayılmaz.')
    return rows, errors
