"""A local window where the user, not the agent, looks at the result.

:func:`request_review` renders nothing itself: the caller passes two PNGs
(original and compressed, same page). They go to ``python -m chonk.review``
over a pipe, never through a temporary file the agent could read. The window
shows them side by side with Accept and Reject buttons. Only the decision
comes back.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from typing import BinaryIO, Literal

Decision = Literal["approved", "rejected", "dismissed", "unavailable"]

EXIT_APPROVED, EXIT_REJECTED, EXIT_DISMISSED, EXIT_UNAVAILABLE = 0, 10, 11, 12
_DECISIONS: dict[int, Decision] = {
    EXIT_APPROVED: "approved",
    EXIT_REJECTED: "rejected",
    EXIT_DISMISSED: "dismissed",
}


def _pack(blob: bytes) -> bytes:
    return struct.pack(">Q", len(blob)) + blob


def _unpack(stream: BinaryIO) -> bytes:
    header = stream.read(8)
    if len(header) != 8:
        raise EOFError
    (length,) = struct.unpack(">Q", header)
    blob = stream.read(length)
    if len(blob) != length:
        raise EOFError
    return blob


def request_review(original_png: bytes, compressed_png: bytes, *, page: int, pages: int,
                   ssim: float, output_bytes: int, target_bytes: int,
                   timeout: float | None = 1800) -> Decision:
    """Show the window and wait for the user's decision."""
    header = json.dumps({
        "page": int(page), "pages": int(pages), "ssim": float(ssim),
        "output_bytes": int(output_bytes), "target_bytes": int(target_bytes),
    }).encode()
    payload = _pack(header) + _pack(original_png) + _pack(compressed_png)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "chonk.review"],
            input=payload, capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return "dismissed"
    except OSError:
        return "unavailable"
    return _DECISIONS.get(completed.returncode, "unavailable")


# --------------------------------------------------------------------------
# Child-process side


def _has_display() -> bool:
    if os.environ.get("CHONK_NO_GUI"):  # headless sessions (SSH, CI) must never wait on a dialog
        return False
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main() -> int:
    try:
        header = json.loads(_unpack(sys.stdin.buffer))
        original_png = _unpack(sys.stdin.buffer)
        compressed_png = _unpack(sys.stdin.buffer)
    except (EOFError, ValueError):
        return EXIT_UNAVAILABLE
    if not _has_display():
        return EXIT_UNAVAILABLE
    try:
        import io
        import tkinter as tk

        from PIL import Image, ImageTk

        root = tk.Tk()
    except Exception:
        return EXIT_UNAVAILABLE

    decision = {"code": EXIT_DISMISSED}
    root.title("CHONK: check the compressed page")
    root.configure(bg="#f4f6f8")
    images = [Image.open(io.BytesIO(original_png)), Image.open(io.BytesIO(compressed_png))]

    summary = (
        f"Page {header['page'] + 1} of {header['pages']}: the page that changed most "
        f"(similarity {header['ssim']:.3f}). Size {header['output_bytes']:,} of "
        f"{header['target_bytes']:,} bytes allowed.\n"
        "This window is on your computer only. Your AI assistant sees just your Accept or Reject."
    )
    tk.Label(root, text=summary, bg="#f4f6f8", fg="#111827", justify="left",
             font=("Arial", 11), padx=12, pady=10).pack(anchor="w")

    panes = tk.Frame(root, bg="#f4f6f8")
    panes.pack(fill="both", expand=True, padx=12)
    canvases: list[tk.Canvas] = []
    photos: list[ImageTk.PhotoImage] = []
    state = {"actual_size": False}

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    fit_w = max(200, int(screen_w * 0.45))
    fit_h = max(200, int(screen_h * 0.72))

    for column, title in enumerate(("Original", "Compressed")):
        frame = tk.Frame(panes, bg="#f4f6f8")
        frame.grid(row=0, column=column, sticky="nsew", padx=6)
        panes.columnconfigure(column, weight=1)
        tk.Label(frame, text=title, bg="#f4f6f8", font=("Arial", 11, "bold")).pack(anchor="w")
        canvas = tk.Canvas(frame, width=fit_w, height=fit_h, bg="white", highlightthickness=1)
        canvas.pack(fill="both", expand=True)
        canvases.append(canvas)
    panes.rowconfigure(0, weight=1)

    def draw() -> None:
        photos.clear()
        for canvas, image in zip(canvases, images):
            shown = image
            if not state["actual_size"]:
                scale = min(fit_w / image.width, fit_h / image.height, 1.0)
                shown = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                                     Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(shown)
            photos.append(photo)
            canvas.delete("all")
            canvas.create_image(0, 0, image=photo, anchor="nw")
            canvas.configure(scrollregion=(0, 0, shown.width, shown.height))

    def scroll(event) -> None:  # keep both views in step
        delta = -1 if (getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4) else 1
        horizontal = bool(event.state & 0x1)
        for canvas in canvases:
            (canvas.xview_scroll if horizontal else canvas.yview_scroll)(delta, "units")

    def drag_start(event) -> None:
        for canvas in canvases:
            canvas.scan_mark(event.x, event.y)

    def drag_move(event) -> None:
        for canvas in canvases:
            canvas.scan_dragto(event.x, event.y, gain=1)

    for canvas in canvases:
        canvas.bind("<MouseWheel>", scroll)
        canvas.bind("<Button-4>", scroll)
        canvas.bind("<Button-5>", scroll)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_move)

    def finish(code: int) -> None:
        decision["code"] = code
        root.destroy()

    def toggle() -> None:
        state["actual_size"] = not state["actual_size"]
        zoom.configure(text="Fit to window" if state["actual_size"] else "Actual size")
        draw()

    buttons = tk.Frame(root, bg="#f4f6f8")
    buttons.pack(fill="x", padx=12, pady=10)
    zoom = tk.Button(buttons, text="Actual size", command=toggle)
    zoom.pack(side="left")
    tk.Button(buttons, text="Reject", command=lambda: finish(EXIT_REJECTED)).pack(side="right", padx=(8, 0))
    tk.Button(buttons, text="Accept", command=lambda: finish(EXIT_APPROVED)).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", lambda: finish(EXIT_DISMISSED))
    root.bind("<Escape>", lambda _event: finish(EXIT_DISMISSED))

    draw()
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return decision["code"]


if __name__ == "__main__":
    raise SystemExit(main())
