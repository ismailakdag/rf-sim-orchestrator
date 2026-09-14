"""Verified, lazy result loading and explicitly defined resonance descriptors."""
from __future__ import annotations
import csv
import gzip
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from pathlib import Path
import numpy as np
from .common import sha256_file, validate_result_zip


CHANNELS = {'S11': 'S1,1', 'S12': 'S1,2', 'S21': 'S2,1', 'S22': 'S2,2'}


def read_spectrum(data):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
        raw = handle.read(32 * 1024**2 + 1)
    if len(raw) > 32 * 1024**2:
        raise ValueError('S-parametre dosyası beklenen boyutu aşıyor.')
    records = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    f = np.array([float(r['frequency_GHz']) for r in records])
    channels = {label: np.array([complex(float(r[key + '_real']), float(r[key + '_imag'])) for r in records]) for label, key in CHANNELS.items()}
    if len(f) < 3 or not np.all(np.isfinite(f)) or not np.all(np.diff(f) > 0) or any(not np.all(np.isfinite(v)) for v in channels.values()):
        raise ValueError('Frekans ekseni veya kompleks veri geçersiz.')
    return f, channels


def resonance(f, s, low, high, mode='notch'):
    mask = (f >= low) & (f <= high)
    x, power = f[mask], np.abs(s[mask])**2
    result = dict(f0=None, bw=None, q=None, left=None, right=None, level=None, reason='')
    if len(x) < 5:
        result['reason'] = 'Seçili aralıkta en az beş örnek gerekli.'
        return result
    index = int(np.argmin(power) if mode == 'notch' else np.argmax(power))
    result['f0'] = float(x[index])
    if index in (0, len(x)-1):
        result['reason'] = 'Ekstremum aralık sınırında; rezonans doğrulanmadı.'
        return result
    if mode == 'notch':
        edge = max(1, min(len(x)//10, index, len(x)-index-1))
        l, r = float(np.median(power[:edge])), float(np.median(power[-edge:]))
        if min(l, r) <= 0 or abs(10*np.log10(l/r)) > 1:
            result['reason'] = 'Çukurun iki yanında tutarlı taban yok (fark > 1 dB). Aralığı daralt.'
            return result
        baseline = (l+r)/2
        if power[index] >= baseline/2:
            result['reason'] = 'Çukur derinliği 3 dB altında; güvenilir genişlik üretilmedi.'
            return result
        level = (baseline + power[index])/2
    else:
        level = power[index]/2
    result['level'] = float(level)
    def crossing(i, j):
        for k in range(i, j, -1 if j < i else 1):
            n = k + (-1 if j < i else 1)
            a, b = power[k]-level, power[n]-level
            if a*b <= 0 and power[n] != power[k]:
                return float(x[k] + (level-power[k])*(x[n]-x[k])/(power[n]-power[k]))
        return None
    left, right = crossing(index, 0), crossing(index, len(x)-1)
    if left is None or right is None:
        result['reason'] = 'İki eşik kesişimi aralık içinde bulunamadı; BW ve Q yok.'
        return result
    result.update(left=left, right=right, bw=right-left, q=float(x[index]/(right-left)),
                  reason='Q_BW = f0/BW: eğri genişliği göstergesi; fiziksel/yüksüz Q uyumu değildir. f0 örnek noktasından, kesişimler doğrusal interpolasyondan.')
    return result


class ResultRepository:
    def __init__(self, client, current=None, cache=None):
        self.client, self.current = client, Path(current) if current else None
        self.cache = Path(cache or Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'RFSim/plot-cache')

    def catalog(self):
        entries, errors = [], []
        if self.current:
            for state_path in (self.current.parent / 'campaigns').glob('*/state.json'):
                try:
                    state = json.loads(state_path.read_text(encoding='utf-8-sig'))
                    for run in state.get('completed', []):
                        archive = Path(run['archive'])
                        if (archive / 'sparameters.csv.gz').is_file() and (archive / 'verified.json').is_file():
                            entries.append(dict(key='local:' + str(archive), label=f"{run['id']} · Yerel · {state_path.parent.name}",
                                                id=run['id'], archive=str(archive), origin='local'))
                except (ValueError, OSError, KeyError):
                    errors.append('Bir yerel kampanya okunamadı: ' + state_path.parent.name)
        try:
            for job in self.client.json('GET', '/api/v1/jobs')['jobs']:
                if job['state'] == 'completed' and job.get('result_sha256'):
                    if 'mock' in job['job_id'].lower():
                        continue
                    entries.append(dict(key='remote:'+job['job_id'], label=f"{job['job_id']} · {job.get('worker_id') or 'Uzak'}",
                                        id=job['job_id'], origin='remote', sha256=job['result_sha256']))
        except Exception:
            errors.append('Uzak sonuç listesi okunamadı.')
        return sorted({e['key']: e for e in entries}.values(), key=lambda e:e['label'], reverse=True), errors

    def load(self, entry):
        if entry['origin'] == 'local':
            root = Path(entry['archive'])
            verified = json.loads((root/'verified.json').read_text(encoding='utf-8-sig'))
            if verified.get('results_validated') is not True:
                raise ValueError('Koşu doğrulanmamış.')
            def read(name):
                data = (root/name).read_bytes()
                if hashlib.sha256(data).hexdigest() != verified['files'][name]['sha256']:
                    raise ValueError('Arşiv karması uyuşmuyor: '+name)
                return data
            data = read('sparameters.csv.gz')
            vba = read('model.vba')
            record = json.loads(read('record.json').decode('utf-8-sig'))
        else:
            self.cache.mkdir(parents=True, exist_ok=True)
            target = self.cache / (entry['sha256']+'.zip')
            if not target.is_file() or sha256_file(target) != entry['sha256']:
                request = urllib.request.Request(self.client.base_url + '/api/v1/results/' + entry['id'], headers={'Authorization': 'Bearer '+self.client.token})
                temp = target.with_suffix('.tmp')
                try:
                    with urllib.request.urlopen(request, timeout=60) as response, temp.open('wb') as out:
                        total = 0
                        while block := response.read(1024**2):
                            total += len(block)
                            if total > 512*1024**2:
                                raise ValueError('Grafik paketi 512 MB sınırını aşıyor.')
                            out.write(block)
                    if sha256_file(temp) != entry['sha256']:
                        raise ValueError('İndirilen paket karması uyuşmuyor.')
                    validate_result_zip(temp, entry['id'], 1024**3)
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
            with zipfile.ZipFile(target) as z:
                data = z.read('results/sparameters.csv.gz')
                vba = z.read('source/model.vba')
                record = json.loads(z.read('parameters/record.json').decode('utf-8-sig'))
        if record.get('results_validated') is not True:
            raise ValueError('Koşu kaydı doğrulanmış bilimsel sonuç içermiyor.')
        f, channels = read_spectrum(data)
        return dict(entry=entry, f=f, channels=channels, vba=vba, compressed=data, record=record)
