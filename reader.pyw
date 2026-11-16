"""
Text Reader - paste text, press Play, it reads it to you.
Runs 100% offline using the Piper voice in the "voices" folder.
"""
import json
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LIB_DIR = APP_DIR / "lib"
if LIB_DIR.is_dir():
    sys.path.insert(0, str(LIB_DIR))

import tkinter as tk
from tkinter import messagebox, ttk

VOICE_FILE = APP_DIR / "voices" / "en_US-lessac-medium.onnx"
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "error.log"
ICON_FILE = APP_DIR / "reader.ico"

MIN_SPEED, MAX_SPEED, SPEED_STEP = 0.1, 2.0, 0.05
# Below this speed the voice itself isn't stretched any further (it starts to sound
# warped). Instead it reads word by word with longer pauses between words.
STRETCH_LIMIT = 0.5
WORD_VOICE_SPEED = 0.75  # how fast each single word is spoken in word-by-word mode
SENTENCE_GAP = 0.10   # extra seconds of silence after each sentence (at 1x)
PARAGRAPH_GAP = 0.45  # extra seconds of silence after each paragraph (at 1x)
PREFETCH = 2          # how many sentences to prepare ahead of the one playing

# Colors
BG = "#f4f4f6"
TEXT_BG = "#ffffff"
ACCENT = "#2f6fed"
ACCENT_DARK = "#1f56c4"
BTN_BG = "#e3e5ea"
FG = "#1d1d1f"
MUTED = "#6b6f78"


def log_error(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- settings
def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        speed = float(data.get("speed", 1.0))
        if MIN_SPEED <= speed <= MAX_SPEED:
            data["speed"] = round(speed, 2)
            return data
    except Exception:
        pass
    return {"speed": 1.0}


def save_settings(data):
    try:
        SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- text
def split_text(text):
    """Turn pasted text into a list of (sentence, extra_gap_seconds)."""
    pieces = []
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        # Join lines inside a paragraph (copied PDFs break lines mid-sentence)
        para = " ".join(para.split())
        if not para:
            continue
        # A sentence ends at . ! ? (plus any closing quote/bracket), a space, then a
        # capital letter, number or opening quote. Needing a capital next keeps
        # things like "the U.S. office" together.
        raw, start = [], 0
        for m in re.finditer(r"[.!?…]+[\"'”’)\]]*(\s+)(?=[A-Z0-9\"'“‘(\[])", para):
            raw.append(para[start:m.start(1)])
            start = m.end()
        raw.append(para[start:])
        sents = []
        for s in raw:
            s = s.strip()
            if not s:
                continue
            # Keep very short bits (like "Dr." or "1.") attached to the next part
            if sents and len(sents[-1]) < 25:
                sents[-1] = sents[-1] + " " + s
            else:
                sents.append(s)
        for s in sents:
            for part in _break_long(s):
                pieces.append([part, SENTENCE_GAP])
        if pieces:
            pieces[-1][1] = PARAGRAPH_GAP
    return [(s, g) for s, g in pieces if re.search(r"\w", s)]


def _break_long(sentence, limit=300):
    """Split very long run-on sentences at commas/semicolons so playback starts fast."""
    if len(sentence) <= limit:
        return [sentence]
    parts = re.split(r"(?<=[,;:])\s+", sentence)
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 1 > limit:
            out.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        out.append(cur)
    # Anything still huge (no punctuation at all): cut on spaces
    final = []
    for chunk in out:
        while len(chunk) > limit:
            cut = chunk.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            final.append(chunk[:cut])
            chunk = chunk[cut:].strip()
        final.append(chunk)
    return final


# ---------------------------------------------------------------- audio engine
class Engine:
    def __init__(self):
        self.voice = None
        self.np = None
        self.sd = None
        self.SynthesisConfig = None
        self.sample_rate = 22050
        self.speed = 1.0

        self.lock = threading.Lock()
        self.buf = None          # numpy array currently playing
        self.pos = 0
        self.paused = False
        self.chunk_done = threading.Event()

        self.session = 0         # bumping this cancels the current reading
        self.executor = ThreadPoolExecutor(max_workers=1)  # one synth at a time

    # -- setup
    def load(self):
        import numpy as np
        import sounddevice as sd
        from piper import PiperVoice, SynthesisConfig
        self.np, self.sd, self.SynthesisConfig = np, sd, SynthesisConfig
        self.voice = PiperVoice.load(str(VOICE_FILE))
        self.sample_rate = self.voice.config.sample_rate
        # Warm up so the first Play is instant
        self._synth("Ready.", 0.0, 1.0)

    # -- synthesis
    def _say(self, text, speed):
        np = self.np
        cfg = self.SynthesisConfig(length_scale=1.0 / speed)
        chunks = [c.audio_float_array for c in self.voice.synthesize(text, syn_config=cfg)]
        return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)

    def _trim(self, audio, threshold=0.01, pad=0.03):
        np = self.np
        loud = np.flatnonzero(np.abs(audio) > threshold)
        if len(loud) == 0:
            return audio[:0]
        p = int(self.sample_rate * pad)
        return audio[max(0, loud[0] - p):loud[-1] + p]

    def _synth(self, sentence, gap, speed):
        np = self.np
        sr = self.sample_rate
        if speed >= STRETCH_LIMIT:
            audio = self._say(sentence, speed)
            silence = np.zeros(int(sr * gap / speed), np.float32)
            return np.concatenate([audio, silence])

        # Very slow: read word by word at the slowest natural-sounding speed,
        # with pauses between words sized so the overall pace matches the slider.
        words = sentence.split()
        clips = [self._trim(self._say(w, WORD_VOICE_SPEED)) for w in words]
        clips = [c for c in clips if len(c)] or [np.zeros(0, np.float32)]
        spoken = sum(len(c) for c in clips)
        natural = self._say(sentence, STRETCH_LIMIT)          # how long it takes said normally
        target = len(natural) * (STRETCH_LIMIT / speed)       # how long it should take now
        word_gap = max(int(0.08 * sr), int((target - spoken) / len(clips)))
        parts = []
        for c in clips:
            parts.append(c)
            parts.append(np.zeros(word_gap, np.float32))
        parts.append(np.zeros(int(sr * min(gap / speed, 2.5)), np.float32))
        return np.concatenate(parts)

    def _get(self, cache, items, i, speed):
        """cache maps (index, speed) -> Future, one cache per reading session."""
        key = (i, speed)
        fut = cache.get(key)
        if fut is None:
            s, g = items[i]
            fut = self.executor.submit(self._synth, s, g, speed)
            cache[key] = fut
        return fut

    # -- playback callback (runs on the audio thread)
    def _callback(self, outdata, frames, time_info, status):
        with self.lock:
            if self.paused or self.buf is None:
                outdata.fill(0)
                return
            k = min(len(self.buf) - self.pos, frames)
            outdata[:k, 0] = self.buf[self.pos:self.pos + k]
            outdata[k:].fill(0)
            self.pos += k
            if self.pos >= len(self.buf):
                self.buf = None
                self.chunk_done.set()

    # -- public controls
    def start(self, items, on_finish, on_error):
        self.stop()
        self.session += 1
        self.paused = False
        t = threading.Thread(target=self._run, args=(items, self.session, on_finish, on_error), daemon=True)
        t.start()

    def stop(self):
        self.session += 1
        with self.lock:
            self.buf = None
        self.chunk_done.set()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    # -- the reading loop
    def _run(self, items, session, on_finish, on_error):
        sd = self.sd
        cache = {}
        try:
            with sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype="float32",
                                 callback=self._callback):
                for i in range(len(items)):
                    if session != self.session:
                        return
                    speed = self.speed
                    audio = self._get(cache, items, i, speed).result()
                    for j in range(i + 1, min(i + 1 + PREFETCH, len(items))):
                        self._get(cache, items, j, speed)
                    # forget old sentences
                    for key in [k for k in cache if k[0] < i]:
                        del cache[key]
                    if session != self.session:
                        return

                    with self.lock:
                        self.chunk_done.clear()
                        self.buf, self.pos = audio, 0
                    playing_speed = speed

                    while not self.chunk_done.wait(0.05):
                        if session != self.session:
                            return
                        if self.speed != playing_speed:
                            # Speed changed mid-sentence: switch right where we are
                            new_speed = self.speed
                            new_audio = self._get(cache, items, i, new_speed).result()
                            for j in range(i + 1, min(i + 1 + PREFETCH, len(items))):
                                self._get(cache, items, j, new_speed)
                            with self.lock:
                                if self.buf is not None and session == self.session:
                                    frac = self.pos / max(len(self.buf), 1)
                                    self.buf = new_audio
                                    self.pos = int(frac * len(new_audio))
                            playing_speed = new_speed
                # let the last bit of audio drain
                sd.sleep(150)
            if session == self.session:
                on_finish()
        except Exception:
            log_error(traceback.format_exc())
            if session == self.session:
                on_error()


# ---------------------------------------------------------------- window
class App:
    def __init__(self, root):
        self.root = root
        self.engine = Engine()
        self.settings = load_settings()
        self.engine.speed = self.settings["speed"]
        self.state = "loading"   # loading | idle | playing | paused
        self.loaded_text = None

        root.title("Text Reader")
        root.configure(bg=BG)
        root.geometry("780x560")
        root.minsize(480, 320)
        if ICON_FILE.exists():
            try:
                root.iconbitmap(str(ICON_FILE))
            except Exception:
                pass

        # Bottom bar
        bar = tk.Frame(root, bg=BG)
        bar.pack(side="bottom", fill="x", padx=16, pady=(0, 16))

        self.play_btn = tk.Button(bar, text="Loading voice...", width=14, font=("Segoe UI", 12, "bold"),
                                  bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
                                  activeforeground="white", relief="flat", cursor="hand2",
                                  padx=10, pady=6, command=self.toggle, state="disabled")
        self.play_btn.pack(side="left")

        self.status = tk.Label(bar, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.status.pack(side="left", padx=12)

        speed_box = tk.Frame(bar, bg=BG)
        speed_box.pack(side="right")
        tk.Label(speed_box, text="Speed", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(side="left", padx=(0, 6))
        self.speed_var = tk.DoubleVar(value=self.engine.speed)
        self.slider = ttk.Scale(speed_box, from_=MIN_SPEED, to=MAX_SPEED, orient="horizontal",
                                length=300, variable=self.speed_var, cursor="hand2",
                                command=self._slider_moved)
        self.slider.pack(side="left")
        self.speed_label = tk.Label(speed_box, text="", width=6, bg=BG, fg=FG,
                                    font=("Segoe UI", 11, "bold"))
        self.speed_label.pack(side="left", padx=(6, 0))
        self._speed_after = None
        self._show_speed(self.engine.speed)

        # Text box
        box = tk.Frame(root, bg=BG)
        box.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        self.text = tk.Text(box, wrap="word", height=1, width=1, font=("Segoe UI", 12), bg=TEXT_BG, fg=FG,
                            relief="flat", padx=14, pady=12, undo=True,
                            highlightthickness=1, highlightbackground="#d6d8de",
                            highlightcolor=ACCENT, insertbackground=FG)
        scroll = tk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self._placeholder_on()
        self.text.bind("<FocusIn>", lambda e: self._placeholder_off())
        self.text.bind("<Button-3>", self._context_menu)

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Paste", command=self._paste)
        self.menu.add_command(label="Clear all", command=self._clear)

        root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self._load_voice, daemon=True).start()

    # -- placeholder text
    PLACEHOLDER = "Paste your text here, then press Play."

    def _placeholder_on(self):
        self.text.insert("1.0", self.PLACEHOLDER)
        self.text.configure(fg=MUTED)
        self.has_placeholder = True

    def _placeholder_off(self):
        if self.has_placeholder:
            self.text.delete("1.0", "end")
            self.text.configure(fg=FG)
            self.has_placeholder = False

    def get_text(self):
        if self.has_placeholder:
            return ""
        return self.text.get("1.0", "end").strip()

    # -- right-click menu
    def _context_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def _paste(self):
        self._placeholder_off()
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            return
        try:
            self.text.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.text.insert("insert", clip)

    def _clear(self):
        self._placeholder_off()
        self.text.delete("1.0", "end")

    # -- voice loading
    def _load_voice(self):
        try:
            if not VOICE_FILE.exists():
                raise FileNotFoundError("Voice file missing")
            self.engine.load()
            self.root.after(0, self._ready)
        except Exception:
            log_error(traceback.format_exc())
            self.root.after(0, self._load_failed)

    def _ready(self):
        self.state = "idle"
        self.play_btn.configure(state="normal")
        self._set_play_look()
        self.status.configure(text="Ready")

    def _load_failed(self):
        self.play_btn.configure(text="Not set up")
        self.status.configure(text="")
        messagebox.showerror("Text Reader",
                             "The voice couldn't load.\n\nRun setup.bat in the Text Reader folder, "
                             "then open the app again.\n\nDetails were saved to error.log.")

    # -- controls
    def toggle(self):
        if self.state == "loading":
            return
        if self.state == "playing":
            self.engine.pause()
            self.state = "paused"
            self.status.configure(text="Paused")
        else:
            text = self.get_text()
            if not text:
                self.status.configure(text="Paste some text first")
                return
            if self.state == "paused" and text == self.loaded_text:
                self.engine.resume()
            else:
                items = split_text(text)
                if not items:
                    self.status.configure(text="Nothing to read")
                    return
                self.loaded_text = text
                self.engine.start(items, self._finished_threadsafe, self._error_threadsafe)
            self.state = "playing"
            self.status.configure(text="Reading...")
        self._set_play_look()

    def _set_play_look(self):
        if self.state == "playing":
            self.play_btn.configure(text="❚❚  Pause")
        elif self.state == "paused":
            self.play_btn.configure(text="▶  Resume")
        else:
            self.play_btn.configure(text="▶  Play")

    def _finished_threadsafe(self):
        self.root.after(0, self._finished)

    def _finished(self):
        self.state = "idle"
        self.loaded_text = None
        self.status.configure(text="Done")
        self._set_play_look()

    def _error_threadsafe(self):
        self.root.after(0, self._play_error)

    def _play_error(self):
        self.state = "idle"
        self._set_play_look()
        self.status.configure(text="")
        messagebox.showerror("Text Reader",
                             "Something went wrong playing audio. Check that your speakers or "
                             "headphones are connected, then press Play again.\n\nDetails were saved to error.log.")

    def _slider_moved(self, value):
        # Snap to steps of 0.05 (0.5, 0.55, 0.6 ...)
        s = round(round(float(value) / SPEED_STEP) * SPEED_STEP, 2)
        self._show_speed(s)
        # Wait until the slider stops moving for a moment, then apply it
        if self._speed_after:
            self.root.after_cancel(self._speed_after)
        self._speed_after = self.root.after(250, lambda: self.set_speed(s))

    def _show_speed(self, s):
        self.speed_label.configure(text=f"{s:.2f}".rstrip("0").rstrip(".") + "x")

    def set_speed(self, s):
        s = round(min(MAX_SPEED, max(MIN_SPEED, s)), 2)
        self._speed_after = None
        self.speed_var.set(s)
        self._show_speed(s)
        self.engine.speed = s
        self.settings["speed"] = s
        save_settings(self.settings)

    def close(self):
        self.engine.stop()
        self.root.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
        except Exception:
            pass
    root = tk.Tk()

    def report(exc, val, tb):
        log_error("".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror("Text Reader", f"Unexpected error: {val}\n\nDetails were saved to error.log.")
    root.report_callback_exception = report

    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_error(traceback.format_exc())
        raise
