"""Modern Turkish Tk/ttk desktop interface for ProxyPulse."""

from __future__ import annotations

import ctypes
import queue
import sys
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .controller import ScanController, UiEvent
from .database import ProxyRepository
from .exporter import export_csv, export_txt
from .models import CheckConfig, ProxyProtocol, ProxyRecord, ProxySource, ProxyStatus
from .parser import parse_feed
from .sources import DEFAULT_SOURCES


LIGHT = {
    "bg": "#f3f6fb",
    "panel": "#ffffff",
    "panel2": "#eaf0f8",
    "text": "#172033",
    "muted": "#64748b",
    "border": "#d7e0ec",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "success": "#059669",
    "warning": "#d97706",
    "danger": "#dc2626",
    "tree_alt": "#f8fafc",
}

DARK = {
    "bg": "#08111f",
    "panel": "#101c2e",
    "panel2": "#17253a",
    "text": "#edf4ff",
    "muted": "#91a4bf",
    "border": "#243650",
    "accent": "#3b82f6",
    "accent_hover": "#60a5fa",
    "success": "#34d399",
    "warning": "#fbbf24",
    "danger": "#fb7185",
    "tree_alt": "#122036",
}


STATUS_TEXT = {
    ProxyStatus.NEW: "… Bekliyor",
    ProxyStatus.TESTING: "↻ Test ediliyor",
    ProxyStatus.ALIVE: "✓ Çalışıyor",
    ProxyStatus.DEAD: "× Başarısız",
    ProxyStatus.CANCELLED: "■ İptal",
}


def enable_high_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


class ProxyPulseApp:
    def __init__(self, root: tk.Tk, repository: ProxyRepository | None = None) -> None:
        self.root = root
        self.repository = repository or ProxyRepository()
        self.ui_queue: "queue.Queue[UiEvent]" = queue.Queue(maxsize=10_000)
        self.controller = ScanController(self.repository, self.ui_queue)
        self.records: dict[tuple[str, str, int], ProxyRecord] = {
            record.key: record for record in self.repository.load_all()
        }
        self.visible_records: list[ProxyRecord] = []
        self.active_run_id = 0
        self.refresh_pending = False
        self._refresh_after_id: str | None = None
        self._item_by_key: dict[tuple[str, str, int], str] = {}
        self._last_refresh_ts: float = 0.0
        self._result_counter: int = 0
        self.dark_mode = bool(self.repository.get_setting("dark_mode", True))
        self.palette = DARK if self.dark_mode else LIGHT
        self.source_enabled = {
            source.name: bool(self.repository.get_setting(f"source:{source.name}", source.enabled))
            for source in DEFAULT_SOURCES
        }
        self.config = CheckConfig(
            timeout_seconds=float(self.repository.get_setting("timeout_seconds", 2.6)),
            concurrency=int(self.repository.get_setting("concurrency", 850)),
            attempts=int(self.repository.get_setting("attempts", 1)),
            test_url=str(self.repository.get_setting("test_url", "https://api.iplocate.io/ip")),
            fallback_url=str(self.repository.get_setting("fallback_url", "https://api.ipify.org?format=json")),
            verify_tls=bool(self.repository.get_setting("verify_tls", True)),
        ).normalized()
        # Otomatik hızlandırma migrasyonu: eski yavaş varsayılanları en mantıklı stabil değere yükselt
        try:
            _stored_to = self.repository.get_setting("timeout_seconds", None)
            _stored_cc = self.repository.get_setting("concurrency", None)
            migrated = False
            if _stored_to is not None and float(_stored_to) in (5.0, 3.5):
                self.repository.set_setting("timeout_seconds", 2.6)
                self.config = CheckConfig(
                    test_url=self.config.test_url,
                    fallback_url=self.config.fallback_url,
                    timeout_seconds=2.6,
                    concurrency=self.config.concurrency,
                    attempts=self.config.attempts,
                    verify_tls=self.config.verify_tls,
                ).normalized()
                migrated = True
            if _stored_cc is not None and int(_stored_cc) in (300, 400, 700):
                self.repository.set_setting("concurrency", 850)
                self.config = CheckConfig(
                    test_url=self.config.test_url,
                    fallback_url=self.config.fallback_url,
                    timeout_seconds=self.config.timeout_seconds,
                    concurrency=850,
                    attempts=self.config.attempts,
                    verify_tls=self.config.verify_tls,
                ).normalized()
                migrated = True
            if migrated:
                pass
        except Exception:
            pass
        # 0 = sınırsız (tüm havuzu test et) — eski varsayılan 1000 idi, artık 0
        raw_max = self.repository.get_setting("max_checks", 0)
        try:
            parsed_max = int(raw_max)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed_max = 0
        if parsed_max == 1000:
            parsed_max = 0
            self.repository.set_setting("max_checks", 0)
        self.max_checks = 0 if parsed_max <= 0 else max(1, min(parsed_max, 100_000))
        self.sort_column = "score"
        self.sort_reverse = True
        self._build_variables()
        self._configure_window()
        self._configure_styles()
        self._build_ui()
        self._apply_filter()
        self.root.after(50, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_variables(self) -> None:
        self.search_var = tk.StringVar()
        self.protocol_var = tk.StringVar(value="Tümü")
        self.status_var = tk.StringVar(value="Tümü")
        self.speed_var = tk.StringVar(value="Tümü")
        self.result_count_var = tk.StringVar(value="0 kayıt")
        self.phase_var = tk.StringVar(value="Hazır")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="")
        self.card_vars = {
            "total": tk.StringVar(value="0"),
            "alive": tk.StringVar(value="0"),
            "fast": tk.StringVar(value="0"),
            "avg": tk.StringVar(value="—"),
            "sources": tk.StringVar(value="0"),
        }

    def _configure_window(self) -> None:
        self.root.title("ProxyPulse — Proxy Kontrol Merkezi")
        self.root.geometry(str(self.repository.get_setting("geometry", "1360x820")))
        self.root.minsize(1080, 680)
        self.root.configure(bg=self.palette["bg"])

    def _configure_styles(self) -> None:
        p = self.palette
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=p["bg"], foreground=p["text"])
        style.configure("TFrame", background=p["bg"])
        style.configure("Panel.TFrame", background=p["panel"])
        style.configure("TLabel", background=p["bg"], foreground=p["text"])
        style.configure("Panel.TLabel", background=p["panel"], foreground=p["text"])
        style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        style.configure("PanelMuted.TLabel", background=p["panel"], foreground=p["muted"])
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20), background=p["bg"], foreground=p["text"])
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), background=p["bg"], foreground=p["muted"])
        style.configure("CardValue.TLabel", font=("Segoe UI Semibold", 22), background=p["panel"], foreground=p["text"])
        style.configure("CardTitle.TLabel", font=("Segoe UI", 9), background=p["panel"], foreground=p["muted"])
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(16, 10), background=p["accent"], foreground="#ffffff", borderwidth=0)
        style.map("Accent.TButton", background=[("active", p["accent_hover"]), ("disabled", p["border"])])
        style.configure("Secondary.TButton", padding=(13, 9), background=p["panel2"], foreground=p["text"], borderwidth=0)
        style.map("Secondary.TButton", background=[("active", p["border"])])
        style.configure("Danger.TButton", padding=(13, 9), background=p["danger"], foreground="#ffffff", borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#b91c1c")])
        style.configure("Treeview", background=p["panel"], fieldbackground=p["panel"], foreground=p["text"], borderwidth=0, rowheight=32)
        style.configure("Treeview.Heading", background=p["panel2"], foreground=p["text"], relief="flat", font=("Segoe UI Semibold", 9), padding=(8, 8))
        style.map("Treeview", background=[("selected", p["accent"])], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", p["border"])])
        style.configure("TCombobox", fieldbackground=p["panel"], background=p["panel2"], foreground=p["text"], padding=(8, 7), arrowcolor=p["muted"])
        style.map("TCombobox", fieldbackground=[("readonly", p["panel"])], selectbackground=[("readonly", p["panel"])], selectforeground=[("readonly", p["text"])])
        style.configure("TEntry", fieldbackground=p["panel"], foreground=p["text"], insertcolor=p["text"], padding=(10, 8), bordercolor=p["border"])
        style.configure("Horizontal.TProgressbar", troughcolor=p["panel2"], background=p["accent"], borderwidth=0, thickness=7)
        style.configure("TNotebook", background=p["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=p["panel2"], foreground=p["muted"], padding=(16, 8))
        style.map("TNotebook.Tab", background=[("selected", p["panel"])], foreground=[("selected", p["text"])])

    def _build_ui(self) -> None:
        self.container = ttk.Frame(self.root, padding=(24, 18, 24, 14))
        self.container.pack(fill="both", expand=True)
        self._build_header()
        self._build_cards()
        self._build_toolbar()
        self._build_filters()
        self._build_content()
        self._build_statusbar()
        self._bind_shortcuts()

    def _build_header(self) -> None:
        header = ttk.Frame(self.container)
        header.pack(fill="x", pady=(0, 15))
        title_box = ttk.Frame(header)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="◉  ProxyPulse", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="Güncel proxy havuzunu topla, doğrula ve performansa göre sırala", style="Subtitle.TLabel").pack(anchor="w", pady=(3, 0))
        ttk.Button(header, text="☾ / ☀  Tema", style="Secondary.TButton", command=self._toggle_theme).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="⚙  Ayarlar", style="Secondary.TButton", command=self._open_settings).pack(side="right")

    def _build_cards(self) -> None:
        frame = ttk.Frame(self.container)
        frame.pack(fill="x", pady=(0, 14))
        cards = [
            ("Toplam havuz", "total", self.palette["accent"]),
            ("Çalışan", "alive", self.palette["success"]),
            ("≤ 1 sn hızlı", "fast", "#8b5cf6"),
            ("Ort. gecikme", "avg", self.palette["warning"]),
            ("Kaynak", "sources", "#06b6d4"),
        ]
        self.card_frames: list[tk.Frame] = []
        for index, (title, key, color) in enumerate(cards):
            frame.columnconfigure(index, weight=1)
            card = tk.Frame(frame, bg=self.palette["panel"], highlightbackground=self.palette["border"], highlightthickness=1)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0 if index == len(cards) - 1 else 6))
            stripe = tk.Frame(card, bg=color, height=4)
            stripe.pack(fill="x")
            inner = ttk.Frame(card, style="Panel.TFrame", padding=(16, 11, 16, 13))
            inner.pack(fill="both")
            ttk.Label(inner, text=title, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(inner, textvariable=self.card_vars[key], style="CardValue.TLabel").pack(anchor="w", pady=(4, 0))
            self.card_frames.append(card)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self.container)
        toolbar.pack(fill="x", pady=(0, 12))
        self.collect_button = ttk.Button(toolbar, text="↻  Kaynakları Topla", style="Secondary.TButton", command=self._start_collect)
        self.collect_button.pack(side="left")
        self.pipeline_button = ttk.Button(toolbar, text="▶  Topla + Test Et", style="Accent.TButton", command=self._start_pipeline)
        self.pipeline_button.pack(side="left", padx=8)
        self.test_button = ttk.Button(toolbar, text="✓  Havuzu Test Et", style="Secondary.TButton", command=self._start_check_all)
        self.test_button.pack(side="left")
        self.stop_button = ttk.Button(toolbar, text="■  Durdur", style="Danger.TButton", state="disabled", command=self._stop)
        self.stop_button.pack(side="left", padx=8)
        self.clear_button = ttk.Button(toolbar, text="🗑  Listeyi Temizle", style="Secondary.TButton", command=self._clear_all)
        self.clear_button.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="⇩  İçe Aktar", style="Secondary.TButton", command=self._import_file).pack(side="right")
        ttk.Button(toolbar, text="⇧  Dışa Aktar", style="Secondary.TButton", command=self._export_dialog).pack(side="right", padx=8)

    def _build_filters(self) -> None:
        panel = ttk.Frame(self.container, style="Panel.TFrame", padding=10)
        panel.pack(fill="x", pady=(0, 10))
        self.search_entry = ttk.Entry(panel, textvariable=self.search_var, width=34)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.insert(0, "")
        protocol = ttk.Combobox(panel, textvariable=self.protocol_var, values=("Tümü", "HTTP", "HTTPS", "SOCKS4", "SOCKS5"), width=10, state="readonly")
        protocol.pack(side="left", padx=(8, 0))
        status = ttk.Combobox(panel, textvariable=self.status_var, values=("Tümü", "Çalışan", "Başarısız", "Bekleyen"), width=11, state="readonly")
        status.pack(side="left", padx=(8, 0))
        speed = ttk.Combobox(panel, textvariable=self.speed_var, values=("Tümü", "Çok hızlı", "Hızlı", "Orta", "Yavaş"), width=11, state="readonly")
        speed.pack(side="left", padx=(8, 0))
        ttk.Button(panel, text="Filtreyi temizle", style="Secondary.TButton", command=self._clear_filters).pack(side="left", padx=(8, 0))
        ttk.Label(panel, textvariable=self.result_count_var, style="PanelMuted.TLabel").pack(side="left", padx=(12, 2))
        for variable in (self.search_var, self.protocol_var, self.status_var, self.speed_var):
            variable.trace_add("write", lambda *_: self._schedule_refresh())

    def _build_content(self) -> None:
        paned = ttk.Panedwindow(self.container, orient="vertical")
        paned.pack(fill="both", expand=True)
        table_panel = ttk.Frame(paned, style="Panel.TFrame", padding=(1, 1, 1, 1))
        details_panel = ttk.Frame(paned, style="Panel.TFrame", padding=(14, 8, 14, 8))
        paned.add(table_panel, weight=5)
        paned.add(details_panel, weight=1)
        columns = ("status", "proxy", "protocol", "latency", "score", "country", "anonymous", "ssl", "source", "tested")
        self.tree = ttk.Treeview(table_panel, columns=columns, show="headings", selectmode="extended")
        headings = {
            "status": ("Durum", 118),
            "proxy": ("Proxy", 170),
            "protocol": ("Tür", 78),
            "latency": ("Gecikme", 90),
            "score": ("Skor", 62),
            "country": ("Ülke", 110),
            "anonymous": ("Anonimlik", 100),
            "ssl": ("TLS", 65),
            "source": ("Kaynak", 170),
            "tested": ("Son test", 155),
        }
        for column, (label, width) in headings.items():
            self.tree.heading(column, text=label, command=lambda c=column: self._sort_by(c))
            anchor = "e" if column in {"latency", "score"} else "w"
            self.tree.column(column, width=width, minwidth=55, stretch=column in {"proxy", "source"}, anchor=anchor)
        yscroll = ttk.Scrollbar(table_panel, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_panel, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_panel.rowconfigure(0, weight=1)
        table_panel.columnconfigure(0, weight=1)
        self.tree.tag_configure("alive", foreground=self.palette["success"])
        self.tree.tag_configure("dead", foreground=self.palette["danger"])
        self.tree.tag_configure("testing", foreground=self.palette["warning"])
        self.tree.tag_configure("new", foreground=self.palette["muted"])
        self.tree.bind("<<TreeviewSelect>>", self._show_selected_details)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.detail_title = ttk.Label(details_panel, text="Bir proxy seçerek ayrıntıları görüntüleyin", style="Panel.TLabel", font=("Segoe UI Semibold", 10))
        self.detail_title.pack(anchor="w")
        self.detail_text = ttk.Label(details_panel, text="", style="PanelMuted.TLabel", justify="left")
        self.detail_text.pack(anchor="w", fill="x", pady=(4, 0))
        self.context_menu = tk.Menu(self.root, tearoff=False)
        self.context_menu.add_command(label="Proxy'yi kopyala (socks5:// http://)", command=self._copy_selected)
        self.context_menu.add_command(label="Seçileni yeniden test et", command=self._test_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Seçimi TXT dışa aktar (socks5:// http://)", command=lambda: self._export_selected("txt"))
        self.context_menu.add_command(label="Seçimi CSV dışa aktar", command=lambda: self._export_selected("csv"))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑  Başarısızları temizle", command=self._clear_dead)
        self.context_menu.add_command(label="🗑  Listeyi tamamen temizle", command=self._clear_all)

    def _build_statusbar(self) -> None:
        frame = ttk.Frame(self.container)
        frame.pack(fill="x", pady=(10, 0))
        ttk.Label(frame, textvariable=self.phase_var, style="Muted.TLabel").pack(side="left")
        ttk.Label(frame, textvariable=self.progress_text_var, style="Muted.TLabel").pack(side="right")
        self.progress = ttk.Progressbar(frame, variable=self.progress_var, maximum=100, mode="determinate", length=260)
        self.progress.pack(side="right", padx=(10, 12))

    def _bind_shortcuts(self) -> None:
        self.root.bind("<F5>", lambda _event: self._start_pipeline())
        self.root.bind("<Control-f>", lambda _event: self.search_entry.focus_set())
        self.root.bind("<Control-e>", lambda _event: self._export_dialog())
        self.root.bind("<Escape>", lambda _event: self._stop() if self.controller.running else None)

    def _selected_sources(self) -> list[ProxySource]:
        return [replace(source, enabled=self.source_enabled.get(source.name, source.enabled)) for source in DEFAULT_SOURCES]

    def _set_running(self, running: bool) -> None:
        normal = "disabled" if running else "normal"
        self.collect_button.configure(state=normal)
        self.pipeline_button.configure(state=normal)
        self.test_button.configure(state=normal)
        self.clear_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self.progress_var.set(0)

    def _activate_run(self, run_id: int | None) -> None:
        if run_id is None:
            return
        self.active_run_id = run_id
        self._set_running(True)
        self.phase_var.set("İşlem başlatılıyor…")
        self.progress_text_var.set("")

    def _start_collect(self) -> None:
        self._activate_run(self.controller.start_collect(self._selected_sources()))

    def _start_pipeline(self) -> None:
        self._activate_run(self.controller.start_pipeline(self._selected_sources(), self.config, self.max_checks))

    def _start_check_all(self) -> None:
        records = list(self.records.values())
        if not records:
            messagebox.showinfo("ProxyPulse", "Önce kaynaklardan proxy toplayın veya bir liste içe aktarın.", parent=self.root)
            return
        # 0 = sınırsız : ne kadar varsa hepsini test et
        if self.max_checks and self.max_checks > 0:
            records = sorted(records, key=lambda r: (r.advertised_latency_ms or 99_999, -r.score))[: self.max_checks]
        else:
            records = sorted(records, key=lambda r: (r.advertised_latency_ms or 99_999, -r.score))
        self._activate_run(self.controller.start_check(records, self.config))

    def _test_selected(self) -> None:
        records = self._selected_records()
        if records:
            self._activate_run(self.controller.start_check(records, self.config))

    def _stop(self) -> None:
        if self.controller.running:
            self.controller.cancel()
            self.phase_var.set("Durduruluyor…")
            self.stop_button.configure(state="disabled")

    def _clear_all(self) -> None:
        if self.controller.running:
            messagebox.showwarning("ProxyPulse", "İşlem devam ederken liste temizlenemez. Önce Durdur'a basın.", parent=self.root)
            return
        if not self.records:
            messagebox.showinfo("ProxyPulse", "Liste zaten boş.", parent=self.root)
            return
        total = len(self.records)
        if not messagebox.askyesno(
            "Listeyi Temizle",
            f"{total:,} kayıt ve veritabanındaki tüm geçmiş temizlenecek.\n\nEmin misiniz? Bu işlem geri alınamaz.",
            parent=self.root,
        ):
            return
        try:
            self.repository.clear()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Temizleme hatası", str(exc), parent=self.root)
            return
        self.records.clear()
        self.visible_records.clear()
        self._item_by_key.clear()
        self.tree.delete(*self.tree.get_children())
        self._update_cards()
        self.result_count_var.set("0 / 0 kayıt")
        self.phase_var.set("Liste tamamen temizlendi")
        self.progress_var.set(0)
        self.progress_text_var.set("")
        self.detail_title.configure(text="Bir proxy seçerek ayrıntıları görüntüleyin")
        self.detail_text.configure(text="")
        self.sort_column = "score"
        self.sort_reverse = True

    def _clear_dead(self) -> None:
        if self.controller.running:
            messagebox.showwarning("ProxyPulse", "İşlem devam ederken temizleme yapılamaz.", parent=self.root)
            return
        dead_keys = [key for key, rec in self.records.items() if rec.status is ProxyStatus.DEAD]
        if not dead_keys:
            messagebox.showinfo("ProxyPulse", "Temizlenecek başarısız kayıt yok.", parent=self.root)
            return
        if not messagebox.askyesno("Başarısızları Temizle", f"{len(dead_keys):,} başarısız kayıt silinecek. Emin misiniz?", parent=self.root):
            return
        try:
            self.repository.clear_by_status(["dead"])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Temizleme hatası", str(exc), parent=self.root)
            return
        for key in dead_keys:
            self.records.pop(key, None)
        self._apply_filter()
        self._update_cards()
        self.phase_var.set(f"{len(dead_keys):,} başarısız kayıt temizlendi")

    def _drain_events(self) -> None:
        # GUI donmaması için: ana thread'de en fazla 8ms ve 90 event işle
        deadline = time.perf_counter() + 0.008
        processed = 0
        while processed < 90 and time.perf_counter() < deadline:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if event.run_id == self.active_run_id:
                self._handle_event(event)
            processed += 1
        # test sırasında 16ms, boşta 40ms -> 60fps hedef
        has_more = not self.ui_queue.empty()
        self.root.after(16 if has_more else 40, self._drain_events)

    def _fast_update_row(self, record: ProxyRecord) -> None:
        # Test sırasında tam tablo rebuild yapmadan tek satırı anında güncelle (donmayı önler)
        try:
            item_id = self._item_by_key.get(record.key)
            if not item_id or not self.tree.exists(item_id):
                return
            # Filtre dışı kalmışsa bir sonraki tam yenileme düzeltecek; şimdilik sadece görünür satırı güncelle
            self.tree.item(item_id, values=self._row_values(record), tags=(record.status.value, self.tree.item(item_id, "tags")[1] if len(self.tree.item(item_id, "tags")) > 1 else ""))
        except tk.TclError:
            pass

    def _handle_event(self, event: UiEvent) -> None:
        if event.kind == "phase":
            self.phase_var.set(str(event.payload))
        elif event.kind == "source":
            result = event.payload
            mark = "✓" if result.ok else "×"
            self.phase_var.set(f"{mark} {result.source.name}: {len(result.proxies):,} kayıt")
        elif event.kind == "collected":
            for record in event.payload["records"]:
                self.records[record.key] = record
            self.card_vars["sources"].set(str(event.payload["source_ok"]))
            self._schedule_refresh(force=True)
        elif event.kind == "direct_ip":
            if event.payload:
                self.phase_var.set("Bağlantı tabanı hazır; proxy testleri başlıyor…")
        elif event.kind == "selection":
            self.progress_text_var.set(f"Test seçimi: {event.payload:,}")
        elif event.kind == "result":
            payload = event.payload
            record = payload["record"]
            self.records[record.key] = record
            completed, total = payload["completed"], payload["total"]
            # progress bar'ı her event'te değil, batch'li güncelle (GIL yükünü azalt)
            self.progress_var.set((completed / total) * 100 if total else 0)
            self.progress_text_var.set(f"{completed:,} / {total:,}")
            self._result_counter += 1
            # tek satır hızlı güncelleme -> arayüz akıcı
            self._fast_update_row(record)
            # tam sıralı yenileme throttle'lı (400ms) -> donma biter
            self._schedule_refresh(force=False)
        elif event.kind == "done":
            for record in event.payload.get("records", []):
                self.records[record.key] = record
            alive = sum(record.status is ProxyStatus.ALIVE for record in self.records.values())
            self.phase_var.set(f"Tamamlandı — {alive:,} çalışan proxy")
            self.progress_text_var.set("Bitti")
            self._set_running(False)
            self._schedule_refresh(force=True)
        elif event.kind == "cancelled":
            self.phase_var.set("İşlem durduruldu")
            self.progress_text_var.set("İptal")
            self._set_running(False)
            self._schedule_refresh(force=True)
        elif event.kind == "error":
            self._set_running(False)
            self.phase_var.set("İşlem hatayla sonlandı")
            messagebox.showerror("ProxyPulse", event.payload["message"], parent=self.root)

    def _schedule_refresh(self, force: bool = False) -> None:
        # Arayüz donmaması için throttle: dev listelerde çok daha seyrek (660k için 2.2s)
        if self.refresh_pending and not force:
            return
        if self._refresh_after_id is not None:
            try:
                self.root.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
            self._refresh_after_id = None
        if self.refresh_pending and force:
            self.refresh_pending = False
        if self.refresh_pending and not force:
            return
        self.refresh_pending = True
        if force:
            delay = 35
        elif self.controller.running:
            n = len(self.records)
            if n > 150_000:
                delay = 2200
            elif n > 60_000:
                delay = 1400
            elif n > 15_000:
                delay = 700
            else:
                delay = 380
        else:
            delay = 140
        self._refresh_after_id = self.root.after(delay, self._apply_filter)

    def _apply_filter(self) -> None:
        start_ts = time.perf_counter()
        self.refresh_pending = False
        self._refresh_after_id = None
        self._last_refresh_ts = start_ts
        query = self.search_var.get().strip().lower()
        protocol = self.protocol_var.get().lower()
        status = self.status_var.get()
        speed = self.speed_var.get()
        filtered: list[ProxyRecord] = []
        # Hızlı yol: tüm filtreler "Tümü" ve arama boşsa haystack oluşturmadan kopyala (660k için 130ms → 8ms)
        is_default_filter = not query and protocol == "tümü" and status == "Tümü" and speed == "Tümü"
        if is_default_filter:
            filtered = list(self.records.values())
        else:
            # O(n) filtre - 10k için ~5ms
            for record in self.records.values():
                haystack = f"{record.endpoint} {record.source_text} {record.country} {record.country_code}".lower()
                if query and query not in haystack:
                    continue
                if protocol != "tümü" and record.protocol.value != protocol:
                    continue
                if status == "Çalışan" and record.status is not ProxyStatus.ALIVE:
                    continue
                if status == "Başarısız" and record.status is not ProxyStatus.DEAD:
                    continue
                if status == "Bekleyen" and record.status not in {ProxyStatus.NEW, ProxyStatus.TESTING, ProxyStatus.CANCELLED}:
                    continue
                if speed != "Tümü" and record.speed_label != speed:
                    continue
                filtered.append(record)
        MAX_TREE = 4500
        # 50k+ dev listelerde heap ile top 4500 sırala (660k için 400ms → ~120ms, 3x hızlı)
        if len(filtered) > 50000:
            import heapq

            key_map_heap: dict[str, Any] = {
                "status": lambda r: (0 if r.status is ProxyStatus.ALIVE else 1 if r.status is ProxyStatus.NEW else 2),
                "proxy": lambda r: (r.host, r.port),
                "protocol": lambda r: r.protocol.value,
                "latency": lambda r: r.latency_ms if r.latency_ms is not None else float("inf"),
                "score": lambda r: r.score,
                "country": lambda r: (r.country_code, r.country),
                "anonymous": lambda r: r.anonymity,
                "source": lambda r: r.source_text,
                "tested": lambda r: r.tested_at,
                "ssl": lambda r: bool(r.advertised_ssl),
            }
            key_func = key_map_heap.get(self.sort_column, key_map_heap["score"])
            if self.sort_reverse:
                display_records = heapq.nlargest(MAX_TREE, filtered, key=key_func)
            else:
                display_records = heapq.nsmallest(MAX_TREE, filtered, key=key_func)
            # visible_records truncated olarak tut (export için yeterli; dev listede full sort bellek/CPU israfı)
            self.visible_records = display_records
            truncated = len(filtered) > MAX_TREE
        else:
            self.visible_records = self._sort_records(filtered)
            truncated = len(self.visible_records) > MAX_TREE
            display_records = self.visible_records[:MAX_TREE] if truncated else self.visible_records
        # Seçimi koru (önceki seçim set'i)
        try:
            selected_keys = {
                (self.tree.set(item, "protocol").lower(), self.tree.set(item, "proxy"))
                for item in self.tree.selection()
            }
        except tk.TclError:
            selected_keys = set()
        # Toplu silme - en pahalı kısım; sadece gerektiğinde yap
        try:
            self.tree.delete(*self.tree.get_children())
        except tk.TclError:
            pass
        self._item_by_key.clear()
        # Toplu ekleme: Treeview 4500 satır ~60-90ms, 10k ~200ms+ donma
        for index, record in enumerate(display_records):
            try:
                item = self.tree.insert("", "end", values=self._row_values(record), tags=(record.status.value, "alt" if index % 2 else ""))
            except tk.TclError:
                break
            self._item_by_key[record.key] = item
            if (record.protocol.value, record.endpoint) in selected_keys:
                try:
                    self.tree.selection_add(item)
                except tk.TclError:
                    pass
            # GIL'i bırak, arayüz responsive kalsın (her 800 satırda bir)
            if index % 800 == 0 and (time.perf_counter() - start_ts) > 0.04:
                try:
                    self.root.update_idletasks()
                except tk.TclError:
                    pass
        if truncated:
            self.result_count_var.set(f"{len(filtered):,} / {len(self.records):,} kayıt (ilk {MAX_TREE:,} gösteriliyor)")
        else:
            self.result_count_var.set(f"{len(filtered):,} / {len(self.records):,} kayıt")
        self._update_cards()

    def _sort_records(self, records: list[ProxyRecord]) -> list[ProxyRecord]:
        key_map: dict[str, Any] = {
            "status": lambda r: (0 if r.status is ProxyStatus.ALIVE else 1 if r.status is ProxyStatus.NEW else 2),
            "proxy": lambda r: (r.host, r.port),
            "protocol": lambda r: r.protocol.value,
            "latency": lambda r: r.latency_ms if r.latency_ms is not None else float("inf"),
            "score": lambda r: r.score,
            "country": lambda r: (r.country_code, r.country),
            "anonymous": lambda r: r.anonymity,
            "source": lambda r: r.source_text,
            "tested": lambda r: r.tested_at,
            "ssl": lambda r: bool(r.advertised_ssl),
        }
        return sorted(records, key=key_map.get(self.sort_column, key_map["score"]), reverse=self.sort_reverse)

    def _sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = column in {"score", "tested"}
        self._apply_filter()

    @staticmethod
    def _format_anonymity(value: str) -> str:
        return {"elite": "Elit", "anonymous": "Anonim", "transparent": "Şeffaf", "unknown": "Bilinmiyor"}.get(value, value or "Bilinmiyor")

    def _row_values(self, record: ProxyRecord) -> tuple[str, ...]:
        status = STATUS_TEXT[record.status]
        if record.status is ProxyStatus.ALIVE:
            status = f"✓ {record.speed_label}"
        latency = "—" if record.latency_ms is None else f"{record.latency_ms:.0f} ms"
        country = " ".join(part for part in (record.country_code, record.country) if part) or "—"
        ssl_text = "Evet" if record.advertised_ssl else "Hayır" if record.advertised_ssl is False else "—"
        tested = record.tested_at.replace("T", " ").replace("+00:00", " UTC") if record.tested_at else "—"
        return (
            status,
            record.endpoint,
            record.protocol.value.upper(),
            latency,
            str(record.score) if record.score else "—",
            country,
            self._format_anonymity(record.anonymity),
            ssl_text,
            record.source_text or "—",
            tested,
        )

    def _update_cards(self) -> None:
        rows = list(self.records.values())
        alive = [record for record in rows if record.status is ProxyStatus.ALIVE]
        fast = [record for record in alive if record.latency_ms is not None and record.latency_ms <= 1000]
        average = sum(record.latency_ms or 0 for record in alive) / len(alive) if alive else None
        sources = {source for record in rows for source in record.sources}
        self.card_vars["total"].set(f"{len(rows):,}")
        self.card_vars["alive"].set(f"{len(alive):,}")
        self.card_vars["fast"].set(f"{len(fast):,}")
        self.card_vars["avg"].set("—" if average is None else f"{average:.0f} ms")
        self.card_vars["sources"].set(f"{len(sources):,}")

    def _selected_records(self) -> list[ProxyRecord]:
        by_identity = {(record.protocol.value, record.endpoint): record for record in self.visible_records}
        selected: list[ProxyRecord] = []
        for item in self.tree.selection():
            identity = (self.tree.set(item, "protocol").lower(), self.tree.set(item, "proxy"))
            record = by_identity.get(identity)
            if record:
                selected.append(record)
        return selected

    def _show_selected_details(self, _event: Any = None) -> None:
        selected = self._selected_records()
        if not selected:
            return
        record = selected[0]
        self.detail_title.configure(text=f"{record.protocol.value.upper()}://{record.endpoint}")
        anonymity = "IP gizliyor" if record.hides_ip else "Doğrudan IP görüldü" if record.hides_ip is False else "Ölçülmedi"
        text = (
            f"Durum: {STATUS_TEXT[record.status]}   •   Skor: {record.score or '—'}   •   Çıkış IP: {record.exit_ip or '—'}   •   {anonymity}\n"
            f"Kaynaklar: {record.source_text or '—'}"
        )
        if record.error:
            text += f"\nSon hata: {record.error}"
        self.detail_text.configure(text=text)

    def _show_context_menu(self, event: tk.Event[Any]) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _copy_selected(self) -> None:
        selected = self._selected_records()
        if selected:
            self.root.clipboard_clear()
            # doğru şema: socks5:// http:// https:// şeklinde kopyala
            self.root.clipboard_append("\n".join(f"{record.protocol.value}://{record.endpoint}" for record in selected))

    def _clear_filters(self) -> None:
        self.search_var.set("")
        self.protocol_var.set("Tümü")
        self.status_var.set("Tümü")
        self.speed_var.set("Tümü")

    def _import_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Proxy listesi seç", filetypes=(("Proxy listeleri", "*.txt *.json"), ("Tüm dosyalar", "*.*")))
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8-sig", errors="replace")
            if len(content.encode("utf-8")) > 25_000_000:
                raise ValueError("Dosya 25 MB sınırını aşıyor")
            records = parse_feed(content, f"Dosya: {Path(path).name}")
            for record in records:
                current = self.records.get(record.key)
                if current:
                    current.merge(record)
                else:
                    self.records[record.key] = record
            self.repository.upsert_many(records)
            self.phase_var.set(f"{len(records):,} proxy içe aktarıldı")
            self._apply_filter()
        except (OSError, ValueError) as exc:
            messagebox.showerror("İçe aktarma hatası", str(exc), parent=self.root)

    def _export_dialog(self) -> None:
        rows = [record for record in self.visible_records if record.status is ProxyStatus.ALIVE]
        if not rows:
            rows = self.visible_records
        if not rows:
            messagebox.showinfo("ProxyPulse", "Dışa aktarılacak kayıt yok.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Proxy listesini dışa aktar", defaultextension=".csv", filetypes=(("CSV", "*.csv"), ("Metin", "*.txt")))
        if path:
            self._export(rows, path)

    def _export_selected(self, extension: str) -> None:
        rows = self._selected_records()
        if not rows:
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=f".{extension}", filetypes=((extension.upper(), f"*.{extension}"),))
        if path:
            self._export(rows, path)

    def _export(self, rows: list[ProxyRecord], path: str) -> None:
        try:
            count = export_txt(rows, path) if Path(path).suffix.lower() == ".txt" else export_csv(rows, path)
            self.phase_var.set(f"{count:,} kayıt dışa aktarıldı: {Path(path).name}")
        except OSError as exc:
            messagebox.showerror("Dışa aktarma hatası", str(exc), parent=self.root)

    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("ProxyPulse Ayarları")
        dialog.geometry("650x650")
        dialog.minsize(590, 540)
        dialog.configure(bg=self.palette["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        outer = ttk.Frame(dialog, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Test ayarları", style="Title.TLabel").pack(anchor="w", pady=(0, 12))
        form = ttk.Frame(outer, style="Panel.TFrame", padding=16)
        form.pack(fill="x")
        concurrency = tk.StringVar(value=str(self.config.concurrency))
        timeout = tk.StringVar(value=str(self.config.timeout_seconds))
        attempts = tk.StringVar(value=str(self.config.attempts))
        maximum = tk.StringVar(value=str(self.max_checks))
        test_url = tk.StringVar(value=self.config.test_url)
        fields = [
            ("Eşzamanlı test (1–1000)", concurrency),
            ("Zaman aşımı, saniye (1–60)", timeout),
            ("Deneme sayısı (1–3)", attempts),
            ("Koşu başına azami proxy (0 = sınırsız)", maximum),
            ("Doğrulama adresi", test_url),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(form, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(form, textvariable=variable, width=38).grid(row=row, column=1, sticky="ew", padx=(16, 0), pady=5)
        form.columnconfigure(1, weight=1)
        # Başlık + toplu seçim butonları
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(18, 6))
        ttk.Label(header, text=f"Kaynaklar ({len(DEFAULT_SOURCES)} kaynak)", style="Panel.TLabel", font=("Segoe UI Semibold", 12)).pack(side="left")
        btn_all = ttk.Frame(header)
        btn_all.pack(side="right")
        # source_vars önce oluşturulacak; butonlar sonra bağlanacak (closure)
        source_vars: dict[str, tk.BooleanVar] = {}
        ttk.Button(btn_all, text="Tümünü Seç", style="Secondary.TButton", command=lambda: [var.set(True) for var in source_vars.values()]).pack(side="right", padx=(4, 0))
        ttk.Button(btn_all, text="Hiçbirini Seç", style="Secondary.TButton", command=lambda: [var.set(False) for var in source_vars.values()]).pack(side="right")
        # Scrollable kaynak listesi (125+ kaynak için)
        scroll_container = ttk.Frame(outer, style="Panel.TFrame")
        scroll_container.pack(fill="both", expand=True, pady=(2, 0))
        canvas = tk.Canvas(scroll_container, bg=self.palette["panel"], highlightthickness=0, bd=0, height=260)
        vsb = ttk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        source_frame = ttk.Frame(canvas, style="Panel.TFrame", padding=14)
        canvas_window = canvas.create_window((0, 0), window=source_frame, anchor="nw")

        def _on_frame_configure(_event: Any) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        source_frame.bind("<Configure>", _on_frame_configure)

        def _on_canvas_configure(event: Any) -> None:
            canvas.itemconfig(canvas_window, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event: Any) -> None:
            # Windows: delta 120, Linux: Button-4/5
            try:
                if event.num == 4:
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    canvas.yview_scroll(1, "units")
                else:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Button-4>", _on_mousewheel)
        canvas.bind("<Button-5>", _on_mousewheel)
        # kaynakları ekle
        for source in DEFAULT_SOURCES:
            variable = tk.BooleanVar(value=self.source_enabled.get(source.name, source.enabled))
            source_vars[source.name] = variable
            line = ttk.Frame(source_frame, style="Panel.TFrame")
            line.pack(fill="x", pady=2)
            ttk.Checkbutton(line, text=source.name, variable=variable).pack(side="left")
            try:
                host = source.url.split("/")[2]
            except IndexError:
                host = source.url[:32]
            ttk.Label(line, text=host, style="PanelMuted.TLabel").pack(side="right")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(16, 0))

        def save() -> None:
            try:
                new_config = CheckConfig(
                    test_url=test_url.get(),
                    fallback_url=self.config.fallback_url,
                    timeout_seconds=float(timeout.get()),
                    concurrency=int(concurrency.get()),
                    attempts=int(attempts.get()),
                    verify_tls=True,
                ).normalized()
                raw_value = int(maximum.get())
                if raw_value <= 0:
                    new_maximum = 0
                else:
                    new_maximum = max(1, min(raw_value, 100_000))
            except ValueError:
                messagebox.showerror("Ayar hatası", "Sayısal alanları geçerli değerlerle doldurun.", parent=dialog)
                return
            self.config = new_config
            self.max_checks = new_maximum
            for name, variable in source_vars.items():
                self.source_enabled[name] = variable.get()
                self.repository.set_setting(f"source:{name}", variable.get())
            self.repository.set_setting("concurrency", new_config.concurrency)
            self.repository.set_setting("timeout_seconds", new_config.timeout_seconds)
            self.repository.set_setting("attempts", new_config.attempts)
            self.repository.set_setting("max_checks", new_maximum)
            self.repository.set_setting("test_url", new_config.test_url)
            dialog.destroy()
            self.phase_var.set("Ayarlar kaydedildi")

        ttk.Button(buttons, text="Vazgeç", style="Secondary.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Kaydet", style="Accent.TButton", command=save).pack(side="right", padx=8)

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self.palette = DARK if self.dark_mode else LIGHT
        self.repository.set_setting("dark_mode", self.dark_mode)
        self.root.configure(bg=self.palette["bg"])
        self._configure_styles()
        for card in self.card_frames:
            card.configure(bg=self.palette["panel"], highlightbackground=self.palette["border"])
        self.tree.tag_configure("alive", foreground=self.palette["success"])
        self.tree.tag_configure("dead", foreground=self.palette["danger"])
        self.tree.tag_configure("testing", foreground=self.palette["warning"])
        self.tree.tag_configure("new", foreground=self.palette["muted"])

    def _on_close(self) -> None:
        self.controller.cancel()
        self.repository.set_setting("geometry", self.root.geometry())
        self.root.destroy()


def run() -> None:
    enable_high_dpi()
    root = tk.Tk()
    ProxyPulseApp(root)
    root.mainloop()
