# Text Reader

A local text-to-speech desktop app. Paste text, press play, adjust the speed. Nothing leaves your machine and there are no usage caps.

I built it because browser-based read-aloud tools kept cutting me off at their free-tier limits, and I had a lot of reading to get through.

## Using it

**First time:** double-click `setup.bat`. It installs Piper and the voice model into this folder, announces "Setup is complete" out loud, and puts a Text Reader icon on your desktop. About 160 MB, downloaded once.

**After that:** open Text Reader, paste your text, press Play. Press again to pause. The speed slider runs 0.1x to 2x and remembers where you left it.

Everything installs inside the app folder. To uninstall, delete the folder and the desktop shortcut.

## How it works

Python with a tkinter/ttk interface, [Piper](https://github.com/rhasspy/piper) for synthesis, and sounddevice for playback. Synthesis runs on a `ThreadPoolExecutor` so the interface stays responsive while audio is being generated.

It runs as a `.pyw` so no console window appears. Errors go to `error.log` in the app folder.

## What is not in this repo

`lib/` and `voices/` hold the Piper binaries and the voice model. They are large and not mine to redistribute, so `setup.py` downloads them from the official sources instead.

Voice: `en_US-lessac-medium` from the Piper voices collection. Requires 64-bit Python 3.9 or newer.
