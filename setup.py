"""
One-time setup for Text Reader. Run setup.bat (it calls this).
Everything is installed INSIDE this folder:
  lib     - Piper + audio libraries
  voices  - the voice file
Then a "Text Reader" icon is put on your desktop.
"""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LIB_DIR = APP_DIR / "lib"
VOICE_DIR = APP_DIR / "voices"
VOICE = "en_US-lessac-medium"
VOICE_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
             "en/en_US/lessac/medium/" + VOICE + "{ext}?download=true")
PACKAGES = ["piper-tts==1.8.0", "sounddevice", "numpy"]


def step(n, msg):
    print(f"\n[{n}/4] {msg}")


def fail(msg):
    print("\n*** SETUP STOPPED ***")
    print(msg)
    print("\nCopy everything above this line and send it to Claude.")
    sys.exit(1)


def install_libs():
    step(1, "Installing Piper into the 'lib' folder (about 100 MB, one time)...")
    if sys.version_info < (3, 9):
        fail(f"Python {sys.version.split()[0]} is too old. Python 3.9 or newer is needed.")
    if sys.maxsize <= 2**32:
        fail("This is 32-bit Python. Piper needs 64-bit Python.")
    if subprocess.call([sys.executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL) != 0:
        subprocess.call([sys.executable, "-m", "ensurepip", "--upgrade"])
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--target", str(LIB_DIR),
           "--only-binary=:all:", "--disable-pip-version-check", *PACKAGES]
    if subprocess.call(cmd) != 0:
        fail("Installing the libraries failed.")


def download_voice():
    step(2, "Downloading the voice into the 'voices' folder (about 60 MB, one time)...")
    VOICE_DIR.mkdir(exist_ok=True)
    for ext in (".onnx.json", ".onnx"):
        dest = VOICE_DIR / (VOICE + ext)
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"  {dest.name} already downloaded")
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")

        def progress(blocks, block_size, total):
            if total > 0:
                pct = min(100, blocks * block_size * 100 // total)
                print(f"\r  {dest.name}: {pct}%", end="", flush=True)

        try:
            urllib.request.urlretrieve(VOICE_URL.format(ext=ext), tmp, progress)
        except Exception as e:
            fail(f"Download failed: {e}\nCheck your internet connection and run setup.bat again.")
        tmp.replace(dest)
        print(f"\r  {dest.name}: done      ")


def test_voice():
    step(3, "Testing the voice (you should hear it say setup is complete)...")
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from piper import PiperVoice\n"
        "import numpy as np, sounddevice as sd\n"
        "v = PiperVoice.load(r'%s')\n"
        "a = np.concatenate([c.audio_float_array for c in v.synthesize('Setup is complete. Your text reader is ready.')])\n"
        "sd.play(a, v.config.sample_rate); sd.wait()\n"
        "print('  Voice works.')\n"
    ) % (LIB_DIR, VOICE_DIR / (VOICE + ".onnx"))
    if subprocess.call([sys.executable, "-c", code]) != 0:
        fail("The voice test failed.")


def make_shortcut():
    step(4, "Putting a 'Text Reader' icon on your desktop...")
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    target = pythonw if pythonw.exists() else exe
    env = dict(os.environ,
               TR_TARGET=str(target),
               TR_ARGS=f'"{APP_DIR / "reader.pyw"}"',
               TR_DIR=str(APP_DIR),
               TR_ICON=str(APP_DIR / "reader.ico"))
    ps = (
        "$d = [Environment]::GetFolderPath('Desktop');"
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Text Reader.lnk'));"
        "$s.TargetPath = $env:TR_TARGET;"
        "$s.Arguments = $env:TR_ARGS;"
        "$s.WorkingDirectory = $env:TR_DIR;"
        "$s.IconLocation = $env:TR_ICON;"
        "$s.Description = 'Paste text and have it read aloud';"
        "$s.Save()"
    )
    rc = subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env)
    if rc != 0:
        print("  Couldn't make the desktop icon automatically.")
        print(f"  You can still open the app by double-clicking reader.pyw in {APP_DIR}")
    else:
        print("  Done.")


if __name__ == "__main__":
    print(f"Text Reader setup  (Python {sys.version.split()[0]} at {sys.executable})")
    install_libs()
    download_voice()
    test_voice()
    make_shortcut()
    print("\nAll set! Double-click 'Text Reader' on your desktop to use it.")
