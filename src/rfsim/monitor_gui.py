from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from .monitor import collect, timestamp
from .worker import ApiClient


class MonitorGui:
    def __init__(self, root, url, token, local_current=None):
        self.root = root
        self.client = ApiClient(url, token, timeout=5)
        self.local_current = local_current
        self.messages = queue.Queue()
        self.busy = False
        self.closed = False
        self.rows = {}
        self.refresh_after = None
        self.paused = tk.BooleanVar(value=False)
        root.title('RF Sim — Bilgisayarlar ve CST koşuları')
        root.geometry('1240x650')
        root.minsize(920, 560)
        root.configure(background='#f4f5f7')
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#f4f5f7')
        style.configure('TLabel', background='#f4f5f7', foreground='#172d42')
        style.configure('Title.TLabel', font=('Segoe UI', 21, 'bold'))
        style.configure('TButton', padding=(12, 7))
        style.configure('Treeview', rowheight=36, background='white', fieldbackground='white', foreground='#172d42', borderwidth=0)
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'), padding=(8, 10))
        style.map('Treeview', background=[('selected', '#dceafa')], foreground=[('selected', '#102c49')])
        frame = ttk.Frame(root, padding=24)
        frame.grid(sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1, minsize=140)
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text='Simülasyon ağı', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        self.button = ttk.Button(header, text='Şimdi yenile', command=self.refresh)
        self.button.grid(row=0, column=1, padx=(12, 0))
        ttk.Checkbutton(header, text='İzlemeyi duraklat', variable=self.paused, command=self.toggle).grid(row=0, column=2, padx=(14, 0))
        self.summary = tk.StringVar(value='Bilgisayarlar okunuyor…')
        ttk.Label(frame, textvariable=self.summary).grid(row=1, column=0, sticky='w', pady=(8, 8))
        ttk.Label(frame, text='10 saniyede bir güncellenir · Makine seçerek iş ayrıntısını görebilirsin.').grid(row=2, column=0, sticky='w', pady=(0, 16))
        table = ttk.Frame(frame)
        table.grid(row=3, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = [('name', 'Bilgisayar', 170), ('connection', 'Bağlantı', 100),
                   ('activity', 'Durum', 165), ('stage', 'Son bildirilen aşama', 230),
                   ('elapsed', 'Solver süresi', 100), ('progress', 'Tamamlanan işler', 170),
                   ('disk', 'Boş disk', 90), ('cst', 'CST', 65)]
        self.tree = ttk.Treeview(table, columns=[x[0] for x in columns], show='headings', selectmode='browse', height=7)
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=width, stretch=key in ('name', 'stage', 'activity', 'progress'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        scroll.grid(row=1, column=0, sticky='ew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        self.tree.configure(xscrollcommand=scroll.set, yscrollcommand=vertical.set)
        self.tree.tag_configure('offline', foreground='#596572')
        self.tree.tag_configure('warning', foreground='#8a3e09')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        ttk.Label(frame, text='Seçili bilgisayar', font=('Segoe UI', 12, 'bold')).grid(row=4, column=0, sticky='w', pady=(20, 8))
        self.details = tk.Text(frame, height=6, wrap='word', font=('Segoe UI', 10), background='white', foreground='#172d42', relief='flat', padx=12, pady=10)
        self.details.grid(row=5, column=0, sticky='ew')
        self.details.configure(state='disabled')
        self.status = tk.StringVar(value='Bağlantı kuruluyor…')
        ttk.Label(frame, textvariable=self.status).grid(row=6, column=0, sticky='w', pady=(12, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.message_after = root.after(100, self.drain)
        self.refresh()

    def refresh(self):
        if self.closed or self.busy:
            return
        if self.refresh_after:
            self.root.after_cancel(self.refresh_after)
            self.refresh_after = None
        self.busy = True
        self.button.configure(state='disabled')
        def fetch():
            try:
                self.messages.put(collect(self.client, self.local_current))
            except Exception:
                self.messages.put(([], ['İzleme verisi okunamadı; yeniden denenecek.']))
        threading.Thread(target=fetch, daemon=True).start()

    def drain(self):
        if self.closed:
            return
        try:
            rows, errors = self.messages.get_nowait()
            self.busy = False
            self.button.configure(state='normal')
            self.render(rows, errors)
            if not self.paused.get():
                self.refresh_after = self.root.after(10000, self.refresh)
        except queue.Empty:
            pass
        self.message_after = self.root.after(100, self.drain)

    def render(self, rows, errors=()):
        selection = self.tree.selection()
        if errors:
            for old in self.rows.values():
                if old['id'].startswith('remote:'):
                    rows.append(dict(old, connection='Bilinmiyor', activity='Host bağlantısı yok', tone='offline'))
        self.rows = {row['id']: row for row in rows}
        for key in self.tree.get_children():
            if key not in self.rows:
                self.tree.delete(key)
        for key, row in self.rows.items():
            values = [row[name] for name in ('name', 'connection', 'activity', 'stage', 'elapsed', 'progress')]
            values += ['—' if row['disk'] is None else f"{row['disk']/1024**3:.1f} GB", row['cst']]
            if self.tree.exists(key):
                self.tree.item(key, values=values, tags=(row['tone'],))
            else:
                self.tree.insert('', 'end', iid=key, values=values, tags=(row['tone'],))
        live = sum(r['connection'] in ('Yerel', 'Çevrimiçi') for r in rows)
        working = sum(r['activity'] in ('Çalışıyor', 'İş yürütülüyor') for r in rows)
        self.summary.set(f'{len(rows)} bilgisayar · {live} bağlantı doğrulandı · {working} iş yürütülüyor')
        if selection and selection[0] in self.rows:
            self.tree.selection_set(selection)
        elif rows:
            self.tree.selection_set(rows[0]['id'])
        self.select()
        self.status.set(' | '.join(errors) if errors else 'Son okuma: ' + datetime.now().strftime('%d.%m.%Y %H:%M:%S') + ' · Pencereyi kapatmak simülasyonları durdurmaz.')

    def select(self, _event=None):
        selected = self.tree.selection()
        row = self.rows.get(selected[0]) if selected else None
        text = 'Henüz kayıtlı bilgisayar yok. İşçiler hosta bağlandığında otomatik listelenir.'
        if row:
            text = (f"{row['name']}  ·  İşçi: {row['worker']}\n"
                    f"İş: {row['job']}\nSon sinyal: {timestamp(row['last'])}\n"
                    f"Kapsam: {row['scope']}\n{row['note'] or 'Hata notu yok.'}")
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def toggle(self):
        if self.paused.get():
            if self.refresh_after:
                self.root.after_cancel(self.refresh_after)
                self.refresh_after = None
            self.status.set('Panel yenilemesi duraklatıldı; simülasyonlar devam ediyor.')
        else:
            self.refresh()

    def close(self):
        self.closed = True
        for pending in (self.refresh_after, self.message_after):
            if pending:
                self.root.after_cancel(pending)
        self.root.destroy()
