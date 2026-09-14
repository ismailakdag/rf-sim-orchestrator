"""Exact real mesh-axis equality within each material pair, after archive checks."""
import hashlib, json, struct
from pathlib import Path
import numpy as np

def axes(directory):
    raw=(directory/'mesh-grid.bin').read_bytes()
    record=json.loads((directory/'record.json').read_text(encoding='utf-8-sig'))
    if hashlib.sha256(raw).hexdigest()!=record['mesh_grid_fingerprint']['sha256']:
        raise ValueError('Mesh fingerprint differs')
    sizes=struct.unpack_from('<3i',raw,8)
    if min(sizes)<2 or len(raw)!=32+8*sum(sizes):
        raise ValueError('Unknown mesh layout')
    result=np.split(np.frombuffer(raw,dtype='<f8',offset=32),np.cumsum(sizes)[:-1])
    if not all(np.isfinite(a).all() and (np.diff(a)>0).all() for a in result):
        raise ValueError('Invalid mesh axes')
    if int(np.prod(np.asarray(sizes)-1))!=record['mesh_cells']:
        raise ValueError('Mesh cell count differs')
    return result

def check(job):
    if not job.get('paired_reference_archive'):
        return
    reference=Path(job['paired_reference_archive']); contrast=Path(job['archive'])
    equal=[bool(np.array_equal(a,b)) for a,b in zip(axes(reference),axes(contrast))]
    result=dict(reference=str(reference),contrast=str(contrast),axes_equal=equal,passed=all(equal),
                scope='Empirically decoded mesh axes gated by hashes/layout/cell counts; within-pair equality only, not convergence.')
    (contrast/'pair-mesh-gate.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    if not all(equal):
        raise ValueError(f'Pair mesh equality failed: {equal}')
