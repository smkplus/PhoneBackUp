# 📱 Phone Backup

**Back up photos and videos from your phone to your PC over Wi-Fi. No app, no cable, no cloud.**

![Platform](https://img.shields.io/badge/platform-Windows-0078d4)
![Python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![Dependencies](https://img.shields.io/badge/runtime%20deps-stdlib%20only-brightgreen)

Run the program on your PC, scan the QR code with your phone, pick your photos, done.
Your phone only needs its normal browser, and nothing ever leaves your home network.

```
 ┌────────────┐   Wi-Fi (same network)   ┌──────────────────────┐
 │   Phone    │ ───────────────────────► │  PC: Phone Backup    │
 │  browser   │   photos & videos        │  local server + GUI  │
 └────────────┘                          └──────────┬───────────┘
                                                    ▼
                                      Pictures\PhoneBackup\2026-11\…
```

## ✨ Features

- **No app on the phone.** Works with Safari, Chrome, or any modern mobile browser.
- **QR code pairing.** Scan once, or type the shown address into the browser.
- **Bulk upload** with a progress bar and automatic retry if the connection drops.
- **Duplicate skipping.** Files with the same name and size are never sent twice, so you can select *everything* every time.
- **Auto-organized folders** by photo date: `YYYY-MM` (can be turned off).
- **Original dates preserved** on saved files.
- **Safe writes.** Files are saved with a temporary extension and only finalized once fully received, so a dropped connection never leaves a half-broken photo.
- **Private link.** The URL contains a secret code. Without it, nobody can send anything. Generate a new code at any time to invalidate the old link.
- **Fully local.** No account, no cloud, no tracking.

## 🚀 Quick start (Windows)

1. Install **Python 3.9+** from [python.org](https://www.python.org/downloads/). Keep *Add Python to PATH* and *tcl/tk* ticked.
2. Download or clone this repository.
3. Double-click **`run.bat`**. The window opens with a QR code.
4. On first run, Windows Firewall will ask for permission. Click **Allow** for **Private networks**.

> Prefer a single file? Run **`build_exe.bat`** and you get `dist\PhoneBackup.exe`, which needs no Python to run.

## 📖 How to use

1. Connect your phone and PC to the **same Wi-Fi**.
2. Scan the QR code on the PC screen with your phone camera (or open the address under the QR code in the phone browser).
3. Tap **Choose photos and videos** and select what you want (multi-select and select-all work).
4. Watch the progress. Files appear in your save folder (default `Pictures\PhoneBackup`).

### 🔌 USB mode (Android)

No Wi-Fi needed, and no browsing the phone's folders in Explorer.

1. On the phone, enable **Developer options → USB debugging**.
2. Plug the phone into the PC and tap **Allow** on the phone's prompt.
3. Click **Connect via USB** in the window (or start with `--usb`). The upload page opens on the phone by itself.
4. Choose your photos and send them as usual.

`adb` comes from the `adbutils` package (installed by `run.bat` through `requirements.txt`). If you already have `adb` in PATH or a `platform-tools` folder next to the program, that is used instead; as a last resort the program tries to download Google's platform-tools. If adb is missing, run `py -m pip install adbutils` and restart.

In the desktop window you can change the save folder, open it, copy the address, toggle monthly folders, and create a new access code.

## ⚙️ Command line

```bash
python phone_backup.py                 # normal, with window
python phone_backup.py --nogui --dir D:\Backup
python phone_backup.py --port 9000 --token mysecret
```

| Option      | Description                                           |
|-------------|-------------------------------------------------------|
| `--nogui`   | Run without a window (console only)                   |
| `--dir`     | Save folder (overrides the saved setting)             |
| `--port`    | Port to listen on (default `8765`)                    |
| `--token`   | Fixed access code (default: random, then remembered)  |
| `--usb`     | Connect an Android phone over USB (adb) at startup    |

Settings are stored in `%APPDATA%\PhoneBackup\config.json`.

## 🔒 Security notes

- The connection is plain **HTTP on your local network**. Use it on your **home or trusted Wi-Fi only**, never on public Wi-Fi.
- The secret code in the link is what protects uploads. Don't share the link or the QR code. If it leaks, use **New access code**.
- The server only accepts uploads. It does not expose any files from your PC.

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| Phone can't open the page | Make sure both devices are on the same Wi-Fi, and that you allowed the app for **Private networks** in Windows Firewall. |
| Still can't connect on guest / café Wi-Fi | Many routers isolate devices on guest networks. Use your main network. |
| iPhone photos arrive as JPG instead of HEIC | Go to *Settings → Photos* and choose **Keep Originals**. |
| Port already in use | Start with `--port 9000` (or any free port). |
| QR code area shows `pip install qrcode` | Run `py -m pip install -r requirements.txt`. |

## 📦 Project files

| File | Purpose |
|------|---------|
| `phone_backup.py` | The whole app: local server, mobile upload page, desktop window |
| `run.bat` | Installs requirements and starts the app |
| `build_exe.bat` | Builds a standalone `PhoneBackup.exe` with PyInstaller |
| `requirements.txt` | Optional helpers: `qrcode` (QR display), `arabic-reshaper` and `python-bidi` (Persian window text) |

The server itself uses only the Python standard library.

## 🌐 Language

The desktop window is in English. A Persian window can be enabled by setting `UI_FA = True` near the top of `phone_backup.py`. The page that opens on the phone is currently Persian (right-to-left).

## 📝 License

Add a license of your choice (for example MIT) before publishing.
