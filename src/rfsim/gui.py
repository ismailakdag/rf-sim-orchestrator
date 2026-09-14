from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk


def _token(explicit: str | None) -> str:
    value = explicit or os.environ.get("RF_SIM_TOKEN")
    if not value or len(value) < 32:
        raise RuntimeError("RF_SIM_TOKEN ortam değişkeni en az 32 karakter olmalıdır.")
    return value


class WorkerGui:
    def __init__(self, root: tk.Tk, config_path: str, explicit_token: str | None):
        from .cli import load_config
        from .worker import ApiClient, Worker, default_worker_id
        from pathlib import Path

        self.root = root
        self.root.title("RF Sim — Okul İstemcisi")
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        config = load_config(config_path)
        wc = config["worker"]
        self.poll_seconds = int(wc.get("poll_seconds", 10))
        self.worker = Worker(
            ApiClient(wc["host_url"], _token(explicit_token)), wc.get("worker_id", default_worker_id()),
            Path(wc["data_dir"]), config["runners"], int(wc.get("heartbeat_seconds", 30)),
            int(float(wc.get("min_free_gb", 0)) * 1024**3), bool(wc.get("cleanup_after_upload", False)),
            list(wc.get("cst_roots", [])),
        )
        frame = ttk.Frame(root, padding=16)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Okul bilgisayarı", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        self.status = tk.StringVar(value="Hazır — CST başlatılmadı")
        ttk.Label(frame, textvariable=self.status, wraplength=560).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 14))
        ttk.Button(frame, text="Bağlantıyı test et", command=self.test).grid(row=2, column=0, sticky="ew", padx=(0, 6))
        self.start_button = ttk.Button(frame, text="İşçiyi başlat", command=self.start)
        self.start_button.grid(row=2, column=1, sticky="ew", padx=(6, 0))
        self.stop_button = ttk.Button(frame, text="Güvenli durdur", command=self.stop, state="disabled")
        self.stop_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(frame, text="Durdurma, çalışan işi kesmez; iş tamamlandıktan sonra yeni iş alınmasını önler.", wraplength=560).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _background(self, fn):
        def run():
            try:
                result = fn()
                self.root.after(0, lambda: self.status.set(str(result)))
            except Exception as exc:
                self.root.after(0, lambda: self.status.set(f"Hata: {type(exc).__name__}: {exc}"))
        threading.Thread(target=run, daemon=True).start()

    def test(self):
        self.status.set("Host bağlantısı ve yetenek bildirimi sınanıyor…")
        def probe():
            from .capabilities import detect_cst_installations

            if not self.worker.presence("idle").get("accepted"):
                return "Host yanıtı doğrulanamadı."
            installs = detect_cst_installations(self.worker.cst_roots)
            if installs:
                cst = ", ".join(f"CST {item.get('major') or '?'}" for item in installs)
                detail = f" Yerel taramada {cst} bulundu."
            else:
                detail = " Yerel taramada CST bulunamadı; mock iş çalışır, gerçek CST işi alınmamalıdır."
            return (
                "Bağlantı başarılı; tek seferlik istemci kaydı gönderildi."
                + detail
                + " Kuyruktaki işi almak için ‘İşçiyi başlat’ düğmesine basın."
            )
        self._background(probe)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.status.set("Çalışıyor; uygun iş bekleniyor.")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        def run():
            try:
                self.worker.loop(self.poll_seconds, self.stop_event)
                message = "Güvenli biçimde durdu."
            except Exception as exc:
                message = f"İşçi durdu: {type(exc).__name__}: {exc}"
            self.root.after(0, lambda: self._finished(message))
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def _finished(self, message: str):
        self.status.set(message)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def stop(self):
        self.status.set("Durdurma istendi; çalışan iş varsa bitmesi bekleniyor.")
        self.stop_event.set()

    def close(self):
        if self.thread and self.thread.is_alive():
            messagebox.showinfo("RF Sim", "Önce Güvenli durdur düğmesine basın. Çalışan iş bitince pencere kapanabilir.")
            return
        self.root.destroy()


from .monitor_gui import MonitorGui


def run_worker_gui(config_path: str, token: str | None = None) -> None:
    root = tk.Tk(); WorkerGui(root, config_path, token); root.mainloop()


def run_monitor_gui(url: str, token: str, local_current: str | None = None) -> None:
    root = tk.Tk(); MonitorGui(root, url, token, local_current); root.mainloop()
