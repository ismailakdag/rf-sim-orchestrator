from __future__ import annotations
import gzip
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from .spectra import ResultRepository, CHANNELS, resonance


class SpectraWindow:
    def __init__(self, parent, client, current):
        self.root = tk.Toplevel(parent)
        self.root.title('RF Sim — S-parametreleri ve karşılaştırma')
        self.root.geometry('1380x850')
        self.root.minsize(1100, 700)
        self.repo = ResultRepository(client, current)
        self.jobs = queue.Queue()
        self.entries, self.visible, self.loaded = [], [], []
        self.busy = False
        self.closed = False
        self.last_hover = 0
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text='S-parametreleri', font=('Segoe UI', 20, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 14))
        sidebar = ttk.Frame(frame)
        sidebar.grid(row=1, column=0, sticky='nsew', padx=(0, 18))
        sidebar.rowconfigure(2, weight=1)
        ttk.Label(sidebar, text='Tamamlanan koşular · en fazla 4 seç').grid(row=0, column=0, sticky='w')
        self.search = tk.StringVar()
        ttk.Entry(sidebar, textvariable=self.search, width=35).grid(row=1, column=0, sticky='ew', pady=8)
        self.search.trace_add('write', lambda *_: self.filter())
        self.list = tk.Listbox(sidebar, selectmode='extended', exportselection=False, width=38,
                               font=('Segoe UI', 9), borderwidth=0, highlightthickness=1,
                               background='white', foreground='#213547', selectbackground='#dceafa', selectforeground='#102c49')
        self.list.grid(row=2, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(sidebar, command=self.list.yview)
        scroll.grid(row=2, column=1, sticky='ns')
        self.list.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(sidebar, orient='horizontal', command=self.list.xview)
        horizontal.grid(row=3, column=0, sticky='ew')
        self.list.configure(xscrollcommand=horizontal.set)
        ttk.Button(sidebar, text='Seçilenleri çiz', command=self.load).grid(row=4, column=0, sticky='ew', pady=(10, 6))
        ttk.Button(sidebar, text='Koşu listesini yenile', command=self.catalog).grid(row=5, column=0, sticky='ew')
        ttk.Label(sidebar, text='Ctrl ile çoklu seçim yap.\nArama: kampanya, PC veya koşu kimliği.\nSensör görseli bu arşivlerde yok.', wraplength=270).grid(row=6, column=0, sticky='w', pady=12)
        main = ttk.Frame(frame)
        main.grid(row=1, column=1, sticky='nsew')
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        controls = ttk.Frame(main)
        controls.grid(row=0, column=0, sticky='ew')
        self.channel = tk.StringVar(value='S21')
        self.view = tk.StringVar(value='Genlik (dB)')
        self.mode = tk.StringVar(value='Çukur · yarı derinlik')
        for i, (label, variable, values, width) in enumerate([
            ('Kanal', self.channel, list(CHANNELS), 6),
            ('Görünüm', self.view, ['Genlik (dB)', 'Genlik', 'Faz (°)', 'Gerçek', 'Sanal'], 14),
            ('BW yöntemi', self.mode, ['Çukur · yarı derinlik', 'Tepe · yarı güç'], 23)]):
            ttk.Label(controls, text=label).grid(row=0, column=i, sticky='w')
            box = ttk.Combobox(controls, textvariable=variable, values=values, state='readonly', width=width)
            box.grid(row=1, column=i, padx=(0, 10), pady=(4, 8))
            box.bind('<<ComboboxSelected>>', self.plot)
        band = ttk.Frame(main)
        band.grid(row=1, column=0, sticky='ew')
        self.low, self.high = tk.StringVar(value='1'), tk.StringVar(value='4.5')
        ttk.Label(band, text='Analiz aralığı (GHz)').pack(side='left')
        ttk.Entry(band, textvariable=self.low, width=7).pack(side='left', padx=6)
        ttk.Label(band, text='–').pack(side='left')
        ttk.Entry(band, textvariable=self.high, width=7).pack(side='left', padx=6)
        ttk.Button(band, text='Uygula', command=self.plot).pack(side='left')
        ttk.Button(band, text='İlk koşu CSV', command=lambda:self.export('csv')).pack(side='right', padx=5)
        ttk.Button(band, text='İlk koşu VBA', command=lambda:self.export('vba')).pack(side='right')
        self.fig = Figure(figsize=(8, 4.8), dpi=100, facecolor='white')
        self.ax = self.fig.add_subplot(111)
        self.fig.subplots_adjust(left=.1, right=.97, top=.88, bottom=.14)
        self.canvas = FigureCanvasTkAgg(self.fig, main)
        self.canvas.get_tk_widget().grid(row=2, column=0, sticky='nsew', pady=(12, 0))
        tools = ttk.Frame(main)
        tools.grid(row=3, column=0, sticky='ew')
        NavigationToolbar2Tk(self.canvas, tools)
        self.metrics = ttk.Treeview(main, columns=('run', 'f0', 'bw', 'q'), show='headings', height=4)
        for key,label,width in [('run','Koşu',160),('f0','f0 adayı (GHz)',120),('bw','BW (MHz)',110),('q','Q_BW* = f0/BW',145)]:
            self.metrics.heading(key,text=label);self.metrics.column(key,width=width,minwidth=80)
        self.metrics.grid(row=4,column=0,sticky='ew',pady=(8,0))
        self.detail = tk.StringVar(value='Bir veya birkaç koşu seçerek grafiği aç.')
        self.detail_label = ttk.Label(main,textvariable=self.detail,wraplength=750,justify='left')
        self.detail_label.grid(row=5,column=0,sticky='ew',pady=(8,0))
        main.bind('<Configure>',lambda e:self.detail_label.configure(wraplength=max(350,e.width-10)))
        self.status = tk.StringVar(value='Koşular okunuyor…')
        ttk.Label(frame,textvariable=self.status,wraplength=1250).grid(row=2,column=0,columnspan=2,sticky='w',pady=(10,0))
        self.canvas.mpl_connect('motion_notify_event',self.hover)
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.timer=self.root.after(100,self.drain)
        self.catalog()

    def task(self,kind,fn):
        if self.busy:
            self.status.set('Önceki okuma tamamlanıyor…');return
        self.busy=True
        def work():
            try:self.jobs.put((kind,fn(),None))
            except Exception as exc:self.jobs.put((kind,None,str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def catalog(self):
        self.status.set('Tamamlanan koşular listeleniyor…')
        self.task('catalog',self.repo.catalog)

    def filter(self):
        query=self.search.get().casefold()
        self.visible=[e for e in self.entries if query in e['label'].casefold()]
        self.list.delete(0,'end')
        for entry in self.visible:self.list.insert('end',entry['label'])

    def load(self):
        picked=[self.visible[i] for i in self.list.curselection()]
        if not 1<=len(picked)<=4:
            self.status.set('Karşılaştırmak için 1–4 koşu seç.');return
        self.status.set('Seçilen paketler okunuyor ve karmaları doğrulanıyor…')
        self.task('load',lambda:[self.repo.load(e) for e in picked])

    def drain(self):
        if self.closed:return
        try:
            kind,data,error=self.jobs.get_nowait();self.busy=False
            if error:self.status.set('Açılamadı: '+error)
            elif kind=='catalog':
                self.entries,errors=data;self.filter()
                self.status.set(f'{len(self.entries)} tamamlanmış koşu. '+ ' '.join(errors))
            else:
                self.loaded=data;self.plot()
                self.status.set('Doğrulanmış veri yüklendi. Fareyle noktaları incele; araç çubuğuyla yakınlaştır veya PNG kaydet.')
        except queue.Empty:pass
        self.timer=self.root.after(100,self.drain)

    def plot(self,_event=None):
        if not self.loaded:return
        try:
            low,high=float(self.low.get().replace(',','.')),float(self.high.get().replace(',','.'))
            if not np.isfinite(low+high) or low>=high:raise ValueError()
        except ValueError:
            self.status.set('Geçerli alt ve üst frekans gir (alt < üst).');return
        self.ax.clear();self.metrics.delete(*self.metrics.get_children())
        colors=['#146b8b','#c26622','#7762a3','#32855b']
        self.curves=[];notes=[]
        for n,data in enumerate(self.loaded):
            f,s=data['f'],data['channels'][self.channel.get()]
            mode=self.view.get()
            y={'Genlik (dB)':lambda:20*np.log10(np.maximum(np.abs(s),1e-15)),
               'Genlik':lambda:np.abs(s),'Faz (°)':lambda:np.unwrap(np.angle(s))*180/np.pi,
               'Gerçek':lambda:s.real,'Sanal':lambda:s.imag}[mode]()
            label=f"{n+1}. {data['entry']['id']}"
            self.ax.plot(f,y,color=colors[n],linewidth=1.8,label=label)
            self.curves.append((f,s,y,label,colors[n]))
            measure=resonance(f,s,low,high,'notch' if self.mode.get().startswith('Çukur') else 'peak')
            fmt=lambda v,scale=1:'—' if v is None else f'{v*scale:.5g}'
            self.metrics.insert('','end',values=(label,fmt(measure['f0']),fmt(measure['bw'],1000),fmt(measure['q'])))
            notes.append(label+': '+measure['reason'])
            if measure['f0'] is not None:
                self.ax.axvline(measure['f0'],color=colors[n],alpha=.35,linestyle=':',linewidth=1)
            if measure['left'] is not None:
                self.ax.axvspan(measure['left'],measure['right'],color=colors[n],alpha=.06)
        self.ax.set_xlim(low,high)
        visible_y=[y[(f>=low)&(f<=high)] for f,s,y,_,_ in self.curves]
        visible_y=[y for y in visible_y if len(y)]
        if visible_y:
            ymin=min(float(np.min(y)) for y in visible_y); ymax=max(float(np.max(y)) for y in visible_y)
            padding=max((ymax-ymin)*.08,1e-9)
            self.ax.set_ylim(ymin-padding,ymax+padding)
        self.ax.set_xlabel('Frekans (GHz)',color='#42556a')
        self.ax.set_ylabel(self.view.get(),color='#42556a')
        self.ax.set_title(self.channel.get()+' · '+self.view.get(),loc='left',fontsize=14,fontweight='bold',color='#172d42',pad=16)
        self.ax.grid(True,color='#e7edf2',linewidth=.7)
        self.ax.spines[['top','right']].set_visible(False)
        self.ax.spines[['left','bottom']].set_color('#cbd5df')
        self.ax.tick_params(colors='#42556a',labelsize=9)
        self.ax.legend(loc='best',frameon=False,fontsize=9)
        self.annotation=self.ax.annotate('',xy=(0,0),xytext=(12,12),textcoords='offset points',fontsize=9,
                                         bbox=dict(boxstyle='round,pad=.5',fc='white',ec='#b5c5d4',alpha=.97))
        self.annotation.set_visible(False)
        self.cursor=self.ax.axvline(low,color='#6e8192',linewidth=.8,alpha=.5,visible=False)
        self.detail.set('\n'.join(notes))
        self.canvas.draw_idle()

    def hover(self,event):
        if not self.loaded or not hasattr(self,'annotation'):return
        if event.inaxes!=self.ax or event.xdata is None:
            self.annotation.set_visible(False);self.cursor.set_visible(False);self.canvas.draw_idle();return
        if time.monotonic()-self.last_hover<.04:return
        self.last_hover=time.monotonic()
        lines=[]
        for f,s,y,label,color in self.curves:
            k=int(np.argmin(np.abs(f-event.xdata)))
            lines.append(f'{label}  {f[k]:.6f} GHz\n{y[k]:.6g} | Re {s[k].real:.6g}  Im {s[k].imag:.6g}')
        self.annotation.xy=(event.xdata,event.ydata)
        right=event.xdata>sum(self.ax.get_xlim())/2
        upper=event.ydata>sum(self.ax.get_ylim())/2
        self.annotation.set_position((-12 if right else 12,-12 if upper else 12))
        self.annotation.set_ha('right' if right else 'left')
        self.annotation.set_va('top' if upper else 'bottom')
        self.annotation.set_text('\n'.join(lines));self.annotation.set_visible(True)
        self.cursor.set_xdata([event.xdata,event.xdata]);self.cursor.set_visible(True)
        self.canvas.draw_idle()

    def export(self,kind):
        if not self.loaded:
            self.status.set('Önce bir koşu yükle.');return
        data=self.loaded[0]
        path=filedialog.asksaveasfilename(parent=self.root,initialfile=data['entry']['id']+'.'+kind,defaultextension='.'+kind)
        if path:
            try:
                from pathlib import Path
                Path(path).write_bytes(data['vba'] if kind=='vba' else gzip.decompress(data['compressed']))
                self.status.set('Kaydedildi: '+path)
            except OSError as exc:self.status.set('Kaydedilemedi: '+str(exc))

    def close(self):
        self.closed=True;self.root.after_cancel(self.timer);self.root.destroy()
