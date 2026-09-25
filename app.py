#!/usr/bin/env python3
"""Native desktop interface for CHONK's local PDF size-ceiling search."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import chonk  # noqa: F401
except ImportError:  # Running from a source checkout without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from chonk import (  # noqa: E402
    AttemptStarted,
    CompressionError,
    CompressionRequest,
    ResultStatus,
    compress_pdf,
    human_size,
    parse_size,
)
from chonk.adapters.text import (  # noqa: E402
    describe_progress,
    describe_success,
    describe_target_not_met,
)
from chonk.backends.ghostscript import find_ghostscript  # noqa: E402


GHOSTSCRIPT_URL = "https://www.ghostscript.com/releases/gsdnld.html"


class ChonkApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CHONK — PDF size optimizer")
        self.root.geometry("720x690")
        self.root.minsize(620, 620)
        self.root.configure(bg="#f4f6f8")

        self.messages: queue.Queue = queue.Queue()
        self.source_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.target_size = tk.StringVar(value="2 MB")
        self.status = tk.StringVar(value="Choose a PDF to get started.")
        self.dependency_status = tk.StringVar()

        self._configure_style()
        self._build_ui()
        self._check_ghostscript()
        self.root.after(100, self._drain_messages)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#f4f6f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure(
            "Title.TLabel",
            background="#f4f6f8",
            foreground="#111827",
            font=("Arial", 24, "bold"),
        )
        style.configure(
            "Tagline.TLabel",
            background="#f4f6f8",
            foreground="#475569",
            font=("Arial", 11),
        )
        style.configure(
            "Section.TLabel",
            background="#ffffff",
            foreground="#111827",
            font=("Arial", 11, "bold"),
        )
        style.configure(
            "Hint.TLabel",
            background="#ffffff",
            foreground="#64748b",
            font=("Arial", 9),
        )
        style.configure(
            "Status.TLabel",
            background="#ffffff",
            foreground="#334155",
            font=("Arial", 10),
        )
        style.configure(
            "Accent.TButton",
            background="#f97316",
            foreground="#ffffff",
            font=("Arial", 10, "bold"),
            padding=(16, 10),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#ea580c"), ("disabled", "#fdba74")],
            foreground=[("disabled", "#ffffff")],
        )
        style.configure("TButton", padding=(10, 7), font=("Arial", 10))
        style.configure("TEntry", padding=7, font=("Arial", 10))
        style.configure("TProgressbar", background="#f97316", troughcolor="#e2e8f0")

    def _build_ui(self) -> None:
        page = ttk.Frame(self.root, style="App.TFrame", padding=(30, 24, 30, 24))
        page.pack(fill="both", expand=True)

        header = ttk.Frame(page, style="App.TFrame")
        header.pack(fill="x", pady=(0, 18))
        ttk.Label(header, text="CHONK", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Fit your PDF under the cap. Keep as much detail as possible.",
            style="Tagline.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        card = ttk.Frame(page, style="Card.TFrame", padding=22)
        card.pack(fill="x")

        ttk.Label(card, text="1  Choose a PDF", style="Section.TLabel").pack(anchor="w")
        source_row = ttk.Frame(card, style="Card.TFrame")
        source_row.pack(fill="x", pady=(9, 5))
        self._entry(source_row, self.source_path).pack(side="left", fill="x", expand=True)
        ttk.Button(source_row, text="Browse…", command=self._choose_source).pack(
            side="left", padx=(10, 0)
        )

        ttk.Label(card, text="2  Set your maximum size", style="Section.TLabel").pack(
            anchor="w", pady=(19, 0)
        )
        ttk.Label(
            card,
            text="CHONK will search for the clearest tested version that fits.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(5, 8))
        self._entry(card, self.target_size, width=20).pack(anchor="w")

        ttk.Label(card, text="3  Save the result", style="Section.TLabel").pack(
            anchor="w", pady=(19, 0)
        )
        output_row = ttk.Frame(card, style="Card.TFrame")
        output_row.pack(fill="x", pady=(9, 0))
        self._entry(output_row, self.output_path).pack(side="left", fill="x", expand=True)
        ttk.Button(output_row, text="Save as…", command=self._choose_output).pack(
            side="left", padx=(10, 0)
        )

        ttk.Label(
            card,
            text="PDFs stay on this device. CHONK does not upload files or collect telemetry.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(18, 0))

        dependency = ttk.Frame(page, style="App.TFrame")
        dependency.pack(fill="x", pady=(12, 8))
        ttk.Label(dependency, textvariable=self.dependency_status, style="Tagline.TLabel").pack(
            side="left"
        )
        self.install_link = ttk.Label(
            dependency,
            text="Ghostscript install guide",
            background="#f4f6f8",
            foreground="#c2410c",
            cursor="hand2",
            font=("Arial", 10, "underline"),
        )
        self.install_link.pack(side="right")
        self.install_link.bind("<Button-1>", lambda _event: webbrowser.open(GHOSTSCRIPT_URL))

        actions = ttk.Frame(page, style="App.TFrame")
        actions.pack(fill="x", pady=(4, 10))
        self.compress_button = ttk.Button(
            actions,
            text="Compress to size limit",
            style="Accent.TButton",
            command=self._start_compression,
        )
        self.compress_button.pack(side="left")
        ttk.Label(actions, textvariable=self.status, style="Tagline.TLabel").pack(
            side="left", padx=(14, 0)
        )

        self.progress = ttk.Progressbar(page, mode="indeterminate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))

        log_card = ttk.Frame(page, style="Card.TFrame", padding=12)
        log_card.pack(fill="both", expand=True)
        ttk.Label(log_card, text="Compression details", style="Section.TLabel").pack(
            anchor="w", pady=(0, 7)
        )
        log_frame = ttk.Frame(log_card, style="Card.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_frame,
            height=10,
            wrap="word",
            state="disabled",
            bg="#f8fafc",
            fg="#334155",
            relief="flat",
            padx=10,
            pady=9,
            font=("Menlo", 9),
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    @staticmethod
    def _entry(parent, variable: tk.StringVar, width: int = 52) -> ttk.Entry:
        return ttk.Entry(parent, textvariable=variable, width=width)

    def _check_ghostscript(self) -> None:
        try:
            find_ghostscript(None)
            self.dependency_status.set("Ghostscript is ready.")
            self.install_link.configure(foreground="#64748b")
        except CompressionError:
            self.dependency_status.set("Ghostscript is required to compress PDFs.")

    def _choose_source(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose a PDF",
            filetypes=(("PDF documents", "*.pdf"), ("All files", "*")),
        )
        if selected:
            source = Path(selected)
            self.source_path.set(str(source))
            if not self.output_path.get().strip():
                self.output_path.set(str(source.with_name(source.stem + "-chonk.pdf")))

    def _choose_output(self) -> None:
        source_text = self.source_path.get().strip()
        default_name = "compressed-chonk.pdf"
        if source_text:
            source = Path(source_text).expanduser()
            default_name = source.stem + "-chonk.pdf"
        selected = filedialog.asksaveasfilename(
            title="Save compressed PDF",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=(("PDF documents", "*.pdf"),),
        )
        if selected:
            self.output_path.set(selected)

    def _start_compression(self) -> None:
        source = Path(self.source_path.get().strip()).expanduser()
        output = Path(self.output_path.get().strip()).expanduser()
        try:
            target_bytes = parse_size(self.target_size.get())
        except ValueError as exc:
            messagebox.showerror("Check the maximum size", str(exc), parent=self.root)
            return

        if not source.is_file():
            messagebox.showerror("Choose a PDF", "Select an existing PDF file first.", parent=self.root)
            return
        if not str(self.output_path.get()).strip():
            messagebox.showerror("Choose where to save", "Select an output PDF path.", parent=self.root)
            return
        if output.suffix.lower() != ".pdf":
            messagebox.showerror("Choose a PDF output", "The output filename must end in .pdf.", parent=self.root)
            return
        try:
            if output.resolve() == source.resolve():
                messagebox.showerror(
                    "Choose a different output",
                    "CHONK will not overwrite the source PDF.",
                    parent=self.root,
                )
                return
        except OSError as exc:
            messagebox.showerror("Invalid output path", str(exc), parent=self.root)
            return

        force = False
        if output.exists():
            force = messagebox.askyesno(
                "Replace existing PDF?",
                f"{output.name} already exists. Replace it after a successful compression?",
                parent=self.root,
            )
            if not force:
                return

        self._clear_log()
        self.status.set("Searching for the best fit…")
        self.compress_button.configure(state="disabled")
        self.progress.start(12)
        worker = threading.Thread(
            target=self._compress_worker,
            args=(source, output, target_bytes, force),
            daemon=True,
        )
        worker.start()

    def _compress_worker(
        self, source: Path, output: Path, target_bytes: int, force: bool
    ) -> None:
        request = CompressionRequest(
            source=source,
            output=output,
            target_bytes=target_bytes,
            overwrite=force,
        )
        result = None
        try:
            result = compress_pdf(
                request, on_progress=lambda event: self.messages.put(("progress", event))
            )
        except (CompressionError, OSError) as exc:
            self.messages.put(("log", f"Error: {exc}\n"))
        except Exception as exc:  # Keep unexpected errors visible in the UI.
            self.messages.put(("log", f"Unexpected error: {exc}\n"))
        self.messages.put(("done", result))

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message[0] == "progress":
                    event = message[1]
                    self._append_log(describe_progress(event))
                    if isinstance(event, AttemptStarted):
                        self.status.set("Comparing compression options…")
                elif message[0] == "log":
                    self._append_log(message[1])
                elif message[0] == "done":
                    self._finish(message[1])
        except queue.Empty:
            pass
        self.root.after(100, self._drain_messages)

    def _finish(self, result) -> None:
        self.progress.stop()
        self.compress_button.configure(state="normal")
        if result is None:
            self.status.set("Compression failed; see the details below.")
        elif result.status is ResultStatus.TARGET_NOT_MET:
            self._append_log(describe_target_not_met(result, cli_hint=False) + "\n")
            self.status.set("Could not meet the requested size limit.")
        else:
            self._append_log(describe_success(result) + "\n")
            actual_size = result.selected.size_bytes
            if actual_size <= result.source_bytes:
                savings = 100 * (1 - actual_size / result.source_bytes)
                change = f"{savings:.1f}% smaller"
            else:
                increase = 100 * (actual_size / result.source_bytes - 1)
                change = f"{increase:.1f}% larger than source"
            self.status.set(f"Done — {human_size(actual_size)}; {change}.")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log(self, value: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", value)
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    ChonkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
