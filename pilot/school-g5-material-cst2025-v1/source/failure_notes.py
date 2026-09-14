"""Operational failure records, separate from scientific result archives."""
import json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
def load(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}
def classify(messages):
    text=str(messages).lower()
    if 'solver matrix' in text:return 'solver_matrix_error_root_cause_unknown'
    if 'license' in text:return 'license_message_needs_review'
    if 'memory' in text:return 'memory_message_needs_review'
    if 'timeout' in text or 'deadline' in text:return 'time_limit_or_timeout'
    return 'unclassified_technical_failure'
def probe():
    try:
        p=subprocess.run([sys.executable,str(Path(__file__).with_name('safety_probe.py'))],capture_output=True,text=True,timeout=35)
        if p.returncode:raise RuntimeError(p.stderr[-2000:])
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as exc:return dict(safe_to_continue=False,reason='idle_check_unconfirmed',error=repr(exc))
def capture(job,error,safety):
    folder=Path(job['archive']);record=load(folder/'record.json');timing=load(folder/'timing.json')
    log=folder/'worker.stdout.log'
    tail='\n'.join(log.read_text(encoding='utf-8',errors='replace').splitlines()[-30:]) if log.exists() else ''
    return dict(id=job['id'],pair_id=job.get('pair_id'),recorded_utc=datetime.now(timezone.utc).isoformat(),
        error=repr(error),stage=record.get('failure_stage',record.get('last_successful_stage')),
        messages=record.get('messages',[]),solver_run_info=record.get('solver_run_info'),
        timing=timing,worker_started_utc=record.get('started_utc'),
        classification=classify([record.get('messages'),record.get('solver_run_info'),repr(error)]),
        timing_note='Missing end time means process/API interruption; observation time is not solver end time.',
        worker_log_tail=tail,archive=str(folder),job=job,safety=safety,retry_status='pending_manual_reschedule_with_new_run_id')
