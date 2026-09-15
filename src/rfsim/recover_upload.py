"""Repair only the known compact model pointer omission; keep the original ZIP."""
import argparse,hashlib,json,os,zipfile
from pathlib import Path
from .common import validate_result_zip,sha256_file
from .worker import ApiClient

def repair(original, target, job_id):
    with zipfile.ZipFile(original) as source:
        manifest=json.loads(source.read('manifest.json'))
        names=source.namelist()
        if any(n.startswith('model/') and not n.endswith('/') for n in names):
            validate_result_zip(original,job_id,10*1024**3)
            return original
        if 'source/model.vba' not in names:
            raise ValueError('No retained model VBA; cannot repair')
        pointer=json.dumps({'model_vba':'source/model.vba','sources':'source/pinned-source',
                            'parameters':'logs/legacy-job.json','binary_cst_retained':False,
                            'original_zip_sha256':sha256_file(original)},indent=2).encode()
        manifest['artifacts'].append({'path':'model/reproduction.json','size':len(pointer),'sha256':hashlib.sha256(pointer).hexdigest()})
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as dest:
            for entry in source.infolist():
                if entry.filename=='manifest.json':continue
                with source.open(entry) as f,dest.open(entry.filename,'w') as out:
                    import shutil;shutil.copyfileobj(f,out)
            dest.writestr('model/reproduction.json',pointer)
            dest.writestr('manifest.json',json.dumps(manifest).encode())
    validate_result_zip(target,job_id,10*1024**3)
    return target

def main():
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--run-dir',required=True,type=Path);p.add_argument('--worker-id',required=True)
    args=p.parse_args();run=args.run_dir.resolve();job=json.loads((run/'job.json').read_text(encoding='utf-8-sig'))
    client=ApiClient(args.url,os.environ['RF_SIM_TOKEN'])
    status=client.json('GET','/api/v1/jobs/'+job['job_id'])
    if status['state']=='completed':print(json.dumps({'already_completed':True}));return
    if status['state']!='needs_attention' or status['worker_id']!=args.worker_id:raise ValueError('Host state or worker mismatch')
    original=run.parent/(run.name+'.zip')
    import time
    target=run.parent/(run.name+f'.recovered-{time.time_ns()}.zip')
    upload=repair(original,target,job['job_id'])
    print(json.dumps(client.upload('/api/v1/recover-results/'+job['job_id'],upload,args.worker_id,'')))
if __name__=='__main__':main()
