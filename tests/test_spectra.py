import gzip
import unittest
import numpy as np
from rfsim.spectra import resonance,read_spectrum


class SpectraTests(unittest.TestCase):
    def test_lorentzian_peak_width(self):
        f=np.linspace(2,4,20001)
        s=1/(1+1j*2*50*(f/3-1))
        m=resonance(f,s,2,4,'peak')
        self.assertAlmostEqual(m['f0'],3,places=6)
        self.assertAlmostEqual(m['bw'],.06,places=5)
        self.assertAlmostEqual(m['q'],50,places=3)

    def test_notch_width_is_half_power_depth_not_min_plus_3db(self):
        f=np.linspace(2,4,20001)
        power=1-.9/(1+((f-3)/.03)**2)
        m=resonance(f,np.sqrt(power),2,4,'notch')
        self.assertAlmostEqual(m['bw'],.06,delta=.0002)
        self.assertTrue(.54<m['level']<.56)

    def test_no_q_for_boundary_or_unbracketed_resonance(self):
        f=np.linspace(2,4,1001)
        self.assertIsNone(resonance(f,f,2,4,'peak')['q'])
        s=1/(1+1j*2*3*(f/3-1))
        self.assertIsNone(resonance(f,s,2.99,3.01,'peak')['q'])

    def test_shallow_notch_is_rejected(self):
        f=np.linspace(2,4,1001)
        m=resonance(f,np.sqrt(1-.1*np.exp(-((f-3)/.05)**2)),2,4)
        self.assertIsNone(m['q'])

    def test_invalid_data_rejected(self):
        header='frequency_GHz,'+','.join(k+'_'+part for k in ['S1,1','S1,2','S2,1','S2,2'] for part in ['real','imag'])
        # CSV channel headers contain commas and must be properly quoted.
        import csv,io
        out=io.StringIO(); w=csv.writer(out)
        w.writerow(['frequency_GHz']+[k+'_'+p for k in ['S1,1','S1,2','S2,1','S2,2'] for p in ['real','imag']])
        for f in [1,1,2]:w.writerow([f]+[0]*8)
        with self.assertRaises(ValueError):read_spectrum(gzip.compress(out.getvalue().encode()))
