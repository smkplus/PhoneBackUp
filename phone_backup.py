# -*- coding: utf-8 -*-
"""
Phone Photo Backup  -  پشتیبان‌گیری عکس از گوشی روی Wi-Fi

برنامه روی ویندوز یک سرور محلی اجرا می‌کند. گوشی (در همان Wi-Fi) آدرس یا QR را باز
می‌کند، عکس/ویدیو انتخاب می‌کند و برنامه آن‌ها را در پوشه‌ی انتخابی ذخیره می‌کند.
فقط به کتابخانه‌های استاندارد پایتون نیاز دارد؛ qrcode / arabic-reshaper / python-bidi
اختیاری‌اند (QR و نمایش درست فارسی).

اجرا:   python phone_backup.py
بدون رابط گرافیکی:   python phone_backup.py --nogui --dir D:\\Backup
"""
import argparse
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "PhoneBackup"
DEFAULT_PORT = 8765
CHUNK = 1024 * 1024

# --------------------------------------------------------------------------
# Optional Persian shaping for tkinter (tkinter has no bidi support on its own)
# --------------------------------------------------------------------------
try:
    import arabic_reshaper
    try:
        from bidi import get_display
    except Exception:  # older python-bidi
        from bidi.algorithm import get_display
    FA = True
except Exception:
    FA = False


# Tk 8.6 has no bidi -> we must reorder (get_display) ourselves.
# Tk 8.7 / 9.x reorders by itself -> only join letters, otherwise text shows reversed.
TK_BIDI = False

# Window language: False = English, True = Persian
UI_FA = False


def _shape(text):
    if not (FA and UI_FA):
        return text
    if TK_BIDI:
        return "\n".join(arabic_reshaper.reshape(line) for line in text.split("\n"))
    return "\n".join(get_display(arabic_reshaper.reshape(line)) for line in text.split("\n"))


S = {
    "title": ("پشتیبان‌گیری عکس از گوشی", "Phone Photo Backup"),
    "scan": ("این کد را با دوربین گوشی اسکن کنید\n(گوشی و کامپیوتر باید روی یک Wi-Fi باشند)",
             "Scan with your phone camera\n(phone and PC must be on the same Wi-Fi)"),
    "or_open": ("یا این آدرس را در مرورگر گوشی باز کنید:", "Or open this address in the phone's browser:"),
    "copy": ("کپی آدرس", "Copy address"),
    "copied": ("کپی شد ✓", "Copied ✓"),
    "new_code": ("ساخت کد جدید", "New access code"),
    "folder": ("عکس‌ها اینجا ذخیره می‌شوند:", "Photos are saved to:"),
    "browse": ("تغییر پوشه...", "Change..."),
    "open": ("باز کردن پوشه", "Open folder"),
    "by_month": ("مرتب‌سازی در پوشه‌های ماهانه (سال-ماه)", "Organize into monthly folders (YYYY-MM)"),
    "stats": ("ذخیره‌شده: {n}   |   تکراری (رد شده): {s}   |   حجم: {size}",
              "Saved: {n}   |   Duplicates skipped: {s}   |   Size: {size}"),
    "log_saved": ("ذخیره شد: {name}", "Saved: {name}"),
    "log_skip": ("تکراری، رد شد: {name}", "Duplicate, skipped: {name}"),
    "log_fail": ("ارسال ناقص ماند: {name}", "Incomplete upload: {name}"),
    "no_net": ("به شبکه وصل نیستید", "Not connected to a network"),
    "port_fail": ("هیچ پورت آزادی پیدا نشد.", "Could not find a free port."),
    "pick_mode": ("عکس‌ها را چطور بفرستید؟", "How do you want to send your photos?"),
    "m_wifi": ("وای‌فای", "Wi-Fi"),
    "m_wifi_sub": ("بدون کابل", "No cable"),
    "m_usb": ("USB", "USB"),
    "m_usb_sub": ("نیاز به دیباگ", "Needs debugging"),
    "m_tether": ("USB Tether", "USB Tether"),
    "m_tether_sub": ("بدون دیباگ", "No debugging"),
    "t1": ("گوشی را با کابل به کامپیوتر وصل کنید.", "Plug the phone into the PC with a cable."),
    "t2": ("روی گوشی USB tethering را روشن کنید (اندروید: Settings ← Hotspot & tethering) یا Personal Hotspot (آیفون).",
           "On the phone, turn on USB tethering (Android: Settings → Hotspot & tethering) or Personal Hotspot (iPhone)."),
    "tether_btn": ("پیدا کردن اتصال", "Find connection"),
    "tether_busy": ("در حال جستجو…", "Searching…"),
    "tether_ok": ("متصل شد. کد را اسکن کنید یا این آدرس را در مرورگر گوشی بنویسید:",
                  "Connected. Scan the code or type this address in the phone's browser:"),
    "tether_none": ("اتصال tethering پیدا نشد. مطمئن شوید USB tethering روشن و کابل وصل است.",
                    "No tethering connection found. Make sure USB tethering is on and the cable is connected."),
    "tether_fail": ("خطا:", "Error:"),
    "tether_fw": ("اگر صفحه باز نشد، در پیام فایروال ویندوز اجازه‌ی Public networks را بدهید.",
                  "If the page does not open, allow the program on Public networks in the Windows Firewall prompt."),
    "m_bt": ("بلوتوث", "Bluetooth"),
    "m_bt_sub": ("کند", "Slow"),
    "w1": ("گوشی و کامپیوتر را به یک وای‌فای وصل کنید.", "Connect the phone and the PC to the same Wi-Fi."),
    "w2": ("این کد را با دوربین گوشی اسکن کنید، یا آدرس زیر را در مرورگر گوشی بنویسید.",
           "Scan this code with the phone camera, or type the address below in the phone's browser."),
    "w3": ("روی «انتخاب عکس» بزنید و عکس‌ها را انتخاب کنید.", "Tap “Choose photos” and select them."),
    "u1": ("روی گوشی USB debugging را روشن کنید:\nSettings ← About phone ← هفت بار Build number ← Developer options",
           "On the phone, turn on USB debugging:\nSettings → About phone → tap Build number 7 times → Developer options → USB debugging"),
    "u2": ("گوشی را با کابل به کامپیوتر وصل کنید و روی گوشی Allow را بزنید.",
           "Plug the phone into the PC with a cable and tap Allow on the phone."),
    "u3": ("دکمه‌ی زیر را بزنید؛ صفحه‌ی ارسال روی گوشی باز می‌شود.",
           "Click the button below. The upload page opens on the phone."),
    "b1": ("گوشی را با کامپیوتر جفت کنید (تنظیمات ویندوز ← بلوتوث).",
           "Pair the phone with this PC (Windows Settings → Bluetooth)."),
    "b2": ("دکمه‌ی زیر را بزنید.", "Click the button below."),
    "b3": ("روی گوشی: عکس‌ها را انتخاب کنید ← Share ← Bluetooth ← این کامپیوتر.",
           "On the phone: select photos → Share → Bluetooth → this PC."),
    "app_sub": ("عکس و ویدیو را به این کامپیوتر بفرستید", "Send photos and videos to this PC"),
    "t_saved": ("ذخیره‌شده", "Saved"),
    "t_skipped": ("تکراری", "Already backed up"),
    "t_size": ("حجم کل", "Total size"),
    "receiving": ("در حال دریافت…", "Receiving photos…"),
    "activity": ("فعالیت", "Activity"),
    "waiting": ("منتظر عکس‌ها…", "Waiting for photos…"),
    "usb": ("اتصال گوشی", "Connect phone"),
    "usb_busy": ("در حال آماده‌سازی...", "Preparing..."),
    "usb_ok": ("متصل شد؛ صفحه روی گوشی باز شد", "Connected. The page opened on your phone."),
    "usb_no_device": ("گوشی پیدا نشد. کابل را وصل و USB debugging را روشن کنید.",
                      "No phone found. Plug in the cable and enable USB debugging."),
    "usb_unauth": ("روی گوشی Allow USB debugging را بزنید و دوباره تلاش کنید.",
                   "Tap Allow on the phone's USB debugging prompt, then retry."),
    "bt": ("باز کردن دریافت بلوتوث", "Open Bluetooth receiver"),
    "bt_hint": ("در پنجره‌ی باز شده Receive a file را انتخاب کنید.", "In the window that opened, choose “Receive a file”."),
    "bt_fail": ("ابزار بلوتوث ویندوز پیدا نشد.", "Windows Bluetooth file transfer not found."),
    "usb_no_adb": ("adb پیدا نشد. اجرا کنید: py -m pip install adbutils و برنامه را دوباره باز کنید.",
                   "adb not found. Run: py -m pip install adbutils, then restart the program."),
    "usb_fail": ("خطا در اتصال USB:", "USB error:"),
    "full_none": ("حداقل یک نوع فایل را تیک بزنید.", "Tick at least one file type."),
    "prog_line": ("{pct}٪  ·  {done} از {total}  ·  {speed}/s  ·  {eta} مانده",
                  "{pct}%  ·  {done} of {total}  ·  {speed}/s  ·  {eta} left"),
    "eta_calc": ("در حال محاسبه…", "calculating…"),
    "eta_lt1": ("کمتر از ۱ دقیقه", "<1 min"),
    "eta_m": ("حدود {m} دقیقه", "~{m} min"),
    "eta_hm": ("حدود {h} ساعت و {m} دقیقه", "~{h} h {m} min"),
    "full_summary": ("{n} فایل کپی شد · {size} · {s} تکراری رد شد",
                     "{n} files copied · {size} · {s} already backed up"),
    "full_failed": ("{f} فایل ناموفق", "{f} failed"),
    "scan_btn": ("نمودار حافظه‌ی گوشی", "Show phone storage chart"),
    "full_btn": ("بک‌آپ کل گوشی", "Back up the whole phone"),
    "scan_busy": ("در حال بررسی حافظه‌ی گوشی… (ممکن است چند دقیقه طول بکشد)",
                  "Scanning phone storage… (may take a few minutes)"),
    "scan_ok": ("نمودار آماده شد", "Chart is ready"),
    "scan_fail": ("بررسی حافظه ممکن نشد.", "Could not read the phone storage."),
    "full_busy": ("در حال کپی کل گوشی…", "Copying the whole phone…"),
    "full_done": ("بک‌آپ کامل شد:", "Full backup finished:"),
    "pie_title": ("حافظه‌ی گوشی", "Phone storage"),
    "c_photos": ("عکس", "Photos"),
    "c_videos": ("ویدیو", "Videos"),
    "c_audio": ("صدا و موزیک", "Audio"),
    "c_docs": ("اسناد", "Documents"),
    "c_apps": ("فایل برنامه (APK)", "App files (APK)"),
    "c_other": ("سایر فایل‌ها", "Other files"),
    "c_system": ("برنامه‌ها و سیستم", "Apps & system"),
    "c_free": ("خالی", "Free"),
    "new_code_warn": ("با ساخت کد جدید، لینک/QR قبلی از کار می‌افتد. ادامه می‌دهید؟",
                      "The previous link/QR will stop working. Continue?"),
}


def tr(key, native=False, **kw):
    """native=True: for the window title bar and message boxes (Windows draws them itself)."""
    text = S[key][0 if (FA and UI_FA) else 1].format(**kw)
    return text if native else _shape(text)


# --------------------------------------------------------------------------
# Config / state
# --------------------------------------------------------------------------
CONFIG_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME / "config.json"


def load_config():
    cfg = {
        "save_dir": str(Path.home() / "Pictures" / "PhoneBackup"),
        "token": secrets.token_urlsafe(6),
        "by_month": True,
        "port": DEFAULT_PORT,
    }
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class State:
    def __init__(self, cfg):
        self.cfg = cfg
        self.events = queue.Queue()
        self.port = int(cfg["port"])

    # live values (read by the request handler on every request)
    @property
    def save_dir(self):
        return Path(self.cfg["save_dir"])

    @property
    def token(self):
        return self.cfg["token"]

    @property
    def by_month(self):
        return bool(self.cfg["by_month"])

    def emit(self, kind, name="", size=0):
        self.events.put((kind, name, size))


STATE = None  # set in main()

# --------------------------------------------------------------------------
# File helpers
# --------------------------------------------------------------------------
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize(name):
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        return ""
    if name.split(".")[0].upper() in RESERVED:
        name = "_" + name
    return name[:180]


def target_dir(mtime_ms):
    d = STATE.save_dir
    if STATE.by_month and mtime_ms > 0:
        try:
            d = d / time.strftime("%Y-%m", time.localtime(mtime_ms / 1000))
        except (OverflowError, OSError, ValueError):
            pass
    return d


def find_slot(directory, name, size):
    """Return (path, is_duplicate). Same name + same size == already backed up."""
    p = directory / name
    if not p.exists():
        return p, False
    if p.stat().st_size == size:
        return p, True
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        q = directory / f"{stem}_{i}{ext}"
        if not q.exists():
            return q, False
        if q.stat().st_size == size:
            return q, True
        i += 1


def human(n, dec=False):
    """dec=True: 1 GB = 1000 MB (how phones show sizes); otherwise 1024."""
    n = float(n)
    k = 1000 if dec else 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < k or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= k


def human_fa(n, dec=False):
    """Size with Persian unit names, so the GUI line reads naturally right-to-left."""
    if not (FA and UI_FA):
        return human(n, dec)
    n = float(n)
    for unit in ("بایت", "کیلوبایت", "مگابایت", "گیگابایت", "ترابایت"):
        if n < 1024 or unit == "ترابایت":
            return f"{n:.0f} {unit}" if unit == "بایت" else f"{n:.1f} {unit}"
        n /= 1024


# --------------------------------------------------------------------------
# Mobile web page (Persian, RTL)
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="fa" dir="rtl"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>پشتیبان‌گیری از گوشی</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--fg:#f1f5f9;--muted:#94a3b8;--accent:#38bdf8;--ok:#4ade80;--err:#f87171;--warn:#fbbf24}
*{box-sizing:border-box}
body{margin:0;padding:16px;font-family:Tahoma,system-ui,sans-serif;background:var(--bg);color:var(--fg);line-height:1.7}
.wrap{max-width:560px;margin:0 auto}
h1{font-size:1.25rem;margin:8px 0 4px}
p.sub{color:var(--muted);margin:0 0 16px;font-size:.9rem}
label.pick{display:block;text-align:center;background:var(--accent);color:#04202e;font-weight:bold;
  padding:18px;border-radius:14px;font-size:1.05rem;cursor:pointer}
label.pick:active{transform:scale(.98)}
input[type=file]{display:none}
.card{background:var(--card);border-radius:14px;padding:14px;margin-top:16px}
.bar{height:10px;background:#334155;border-radius:6px;overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;width:0;background:var(--accent);transition:width .15s}
#sum{font-size:.95rem}
ul{list-style:none;margin:12px 0 0;padding:0;max-height:46vh;overflow:auto}
li{padding:8px 0;border-top:1px solid #334155;font-size:.85rem;display:flex;gap:8px;justify-content:space-between;align-items:center}
li .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;text-align:left;flex:1}
li .s{white-space:nowrap}
.ok{color:var(--ok)}.err{color:var(--err)}.skip{color:var(--warn)}.wait{color:var(--muted)}
.note{color:var(--muted);font-size:.8rem;margin-top:12px}
</style></head><body><div class="wrap">
<h1>📱 ارسال عکس به کامپیوتر</h1>
<p class="sub">عکس‌ها و ویدیوها از طریق Wi-Fi مستقیماً روی کامپیوتر شما ذخیره می‌شوند؛ هیچ‌جا آپلود نمی‌شوند.</p>
<label class="pick" for="pick">انتخاب عکس و ویدیو</label>
<input id="pick" type="file" multiple accept="image/*,video/*">
<label class="pick" for="pickdir" style="margin-top:10px;background:#334155;color:#e2e8f0">انتخاب کل یک پوشه (مثلاً DCIM)</label>
<input id="pickdir" type="file" webkitdirectory multiple>
<div class="card" id="panel" hidden>
  <div id="sum"></div>
  <div class="bar"><i id="bar"></i></div>
  <ul id="list"></ul>
</div>
<p class="note">تا پایان ارسال، این صفحه را باز نگه دارید و صفحه گوشی را خاموش نکنید.
فایل‌های تکراری (هم‌نام و هم‌حجم) دوباره ارسال نمی‌شوند.</p>
</div>
<script>
const base = location.pathname.replace(/\/?$/, '/');
const $ = id => document.getElementById(id);
const queue = [];
let running = false, total = 0, done = 0, sent = 0, skipped = 0, failed = 0, bytesAll = 0, bytesDone = 0;

function fmt(n){ const u=['B','KB','MB','GB']; let i=0; while(n>=1024&&i<3){n/=1024;i++} return n.toFixed(i?1:0)+' '+u[i]; }

function summary(){
  $('sum').textContent = `${done} از ${total} — ارسال‌شده: ${sent} | تکراری: ${skipped} | خطا: ${failed}  (${fmt(bytesDone)} از ${fmt(bytesAll)})`;
  $('bar').style.width = (bytesAll ? Math.min(100, bytesDone / bytesAll * 100) : 100) + '%';
}

const MEDIA = /\.(jpe?g|png|gif|webp|heic|heif|dng|bmp|mp4|mov|m4v|3gp|mkv|avi|webm)$/i;
function onPick(e){
  let files = Array.from(e.target.files);
  if (e.target.id === 'pickdir') files = files.filter(f => MEDIA.test(f.name));
  e.target.value = '';
  if (!files.length) return;
  $('panel').hidden = false;
  if (!running) { total = done = sent = skipped = failed = bytesAll = bytesDone = 0; $('list').innerHTML = ''; }
  for (const f of files) {
    const li = document.createElement('li');
    li.innerHTML = '<span class="n"></span><span class="s wait">در صف</span>';
    li.firstChild.textContent = f.name;
    $('list').prepend(li);
    queue.push({ f, st: li.lastChild });
    total++; bytesAll += f.size;
  }
  summary();
  if (!running) run();
}
$('pick').addEventListener('change', onPick);
$('pickdir').addEventListener('change', onPick);

function setSt(el, cls, txt){ el.className = 's ' + cls; el.textContent = txt; }

async function exists(f){
  const q = `check?name=${encodeURIComponent(f.name)}&size=${f.size}&m=${f.lastModified||0}`;
  const r = await fetch(base + q, {cache:'no-store'});
  if (!r.ok) throw new Error('check');
  return (await r.json()).exists;
}

function upload(f, st, onBytes){
  return new Promise((res, rej) => {
    const x = new XMLHttpRequest();
    x.open('POST', base + 'upload');
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.setRequestHeader('X-Filename', encodeURIComponent(f.name));
    x.setRequestHeader('X-Modified', String(f.lastModified || 0));
    x.upload.onprogress = e => { onBytes(e.loaded); setSt(st, 'wait', Math.round(e.loaded / (e.total||f.size) * 100) + '%'); };
    x.onload = () => x.status === 200 ? res(JSON.parse(x.responseText)) : rej(new Error('http ' + x.status));
    x.onerror = () => rej(new Error('network'));
    x.send(f);
  });
}

async function run(){
  running = true;
  window.onbeforeunload = () => 'ارسال هنوز تمام نشده است';
  while (queue.length) {
    const { f, st } = queue.shift();
    let ok = false;
    for (let attempt = 0; attempt < 3 && !ok; attempt++) {
      try {
        setSt(st, 'wait', 'بررسی...');
        if (await exists(f)) { skipped++; bytesDone += f.size; setSt(st, 'skip', 'تکراری'); ok = true; break; }
        let last = 0;
        await upload(f, st, loaded => { bytesDone += loaded - last; last = loaded; summary(); });
        bytesDone += f.size - last;
        sent++; setSt(st, 'ok', '✓ ذخیره شد'); ok = true;
      } catch (e) {
        if (attempt < 2) { setSt(st, 'wait', 'تلاش مجدد...'); await new Promise(r => setTimeout(r, 1500)); }
      }
    }
    if (!ok) { failed++; setSt(st, 'err', 'ناموفق'); }
    done++; summary();
  }
  running = false;
  window.onbeforeunload = null;
}
</script></body></html>
"""


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "PhoneBackup"
    wbufsize = 65536  # send headers + body together in one packet (faster on phones)

    def setup(self):
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def log_message(self, *args):  # silence console logging
        pass

    # ---- helpers
    def _reply(self, code, body=b"", ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._reply(code, json.dumps(obj), "application/json; charset=utf-8")

    def _route(self):
        """Return the sub-path after the secret token, or None if the token is wrong."""
        parts = urllib.parse.urlsplit(self.path)
        segs = parts.path.strip("/").split("/", 1)
        if not segs or not secrets.compare_digest(segs[0], STATE.token):
            return None, parts
        return (segs[1] if len(segs) > 1 else ""), parts

    # ---- GET
    def do_GET(self):
        sub, parts = self._route()
        if sub is None:
            return self._reply(404, "Not found", "text/plain")
        if sub == "":
            return self._reply(200, PAGE)
        if sub == "check":
            q = urllib.parse.parse_qs(parts.query)
            try:
                name = sanitize(q.get("name", [""])[0])
                size = int(q.get("size", ["-1"])[0])
                mtime = int(float(q.get("m", ["0"])[0]))
            except ValueError:
                return self._json(400, {"exists": False})
            if not name or size < 0:
                return self._json(400, {"exists": False})
            _, dup = find_slot(target_dir(mtime), name, size)
            if dup:
                STATE.emit("skipped", name, size)
            return self._json(200, {"exists": dup})
        self._reply(404, "Not found", "text/plain")

    # ---- POST
    def do_POST(self):
        sub, _ = self._route()
        if sub != "upload":
            self.close_connection = True
            return self._reply(404, "Not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.close_connection = True
            return self._json(411, {"status": "error"})
        if length < 0:
            self.close_connection = True
            return self._json(400, {"status": "error"})

        name = sanitize(urllib.parse.unquote(self.headers.get("X-Filename", "")))
        if not name:
            name = time.strftime("upload_%Y%m%d_%H%M%S") + f"_{secrets.token_hex(2)}"
        try:
            mtime = int(float(self.headers.get("X-Modified", "0")))
        except ValueError:
            mtime = 0

        directory = target_dir(mtime)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path, dup = find_slot(directory, name, length)
        except OSError:
            self.close_connection = True
            return self._json(500, {"status": "error"})

        if dup:  # unlikely (client checks first) – drain and report
            remaining = length
            while remaining > 0:
                data = self.rfile.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
            STATE.emit("skipped", name, length)
            return self._json(200, {"status": "skipped"})

        tmp = path.with_name(path.name + f".{secrets.token_hex(3)}.part")
        received = 0
        try:
            with open(tmp, "wb") as out:
                while received < length:
                    data = self.rfile.read(min(CHUNK, length - received))
                    if not data:
                        break
                    out.write(data)
                    received += len(data)
            if received != length:
                raise ConnectionError("incomplete")
            os.replace(tmp, path)
            if mtime > 0:
                try:
                    os.utime(path, (mtime / 1000, mtime / 1000))
                except (OSError, OverflowError, ValueError):
                    pass
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            STATE.emit("failed", name, received)
            self.close_connection = True
            try:
                return self._json(500, {"status": "error"})
            except Exception:
                return
        STATE.emit("saved", path.name, length)
        self._json(200, {"status": "saved", "name": path.name})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def server_bind(self):
        # skip the default getfqdn() lookup, which can take many seconds on Windows
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def start_server(state):
    last_err = None
    for port in range(state.port, state.port + 20):
        try:
            srv = Server(("0.0.0.0", port), Handler)
            state.port = port
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return srv
        except OSError as e:
            last_err = e
    raise last_err or OSError("no free port")


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is actually sent
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def build_url():
    return f"http://{local_ip()}:{STATE.port}/{STATE.token}/"


# --------------------------------------------------------------------------
# USB mode (Android): adb reverse makes the phone's localhost:PORT reach this PC
# --------------------------------------------------------------------------
ADB_ZIP = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
ADB_EXE = "adb.exe" if os.name == "nt" else "adb"


def find_adb():
    for p in (Path(sys.argv[0]).resolve().parent / "platform-tools" / ADB_EXE,
              CONFIG_PATH.parent / "platform-tools" / ADB_EXE):
        if p.exists():
            return str(p)
    found = shutil.which("adb")
    if found:
        return found
    try:  # adbutils (pip) ships adb.exe inside the package
        import importlib.util
        spec = importlib.util.find_spec("adbutils")
        p = Path(list(spec.submodule_search_locations)[0]) / "binaries" / ADB_EXE
        if p.exists():
            return str(p)
    except Exception:
        pass
    return None


def download_adb():
    import io
    import urllib.request
    import zipfile
    with urllib.request.urlopen(ADB_ZIP, timeout=60) as r:
        data = r.read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(CONFIG_PATH.parent)


def adb(*args, timeout=20):
    r = subprocess.run([find_adb(), *args], capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (r.stdout + r.stderr).strip()


def phone_device():
    """Return (serial, None) for the first USB phone, or (None, error_key)."""
    if not find_adb():
        try:
            download_adb()
        except Exception:
            pass
        if not find_adb():
            return None, "usb_no_adb"
    adb("start-server", timeout=40)
    devs = [l.split("\t") for l in adb("devices").splitlines() if "\t" in l]
    if not devs or devs[0][1] not in ("device", "unauthorized"):
        return None, "usb_no_device"
    if devs[0][1] == "unauthorized":
        return None, "usb_unauth"
    return devs[0][0], None


def usb_start():
    serial, err = phone_device()
    if err:
        return err
    s = ["-s", serial]
    port = f"tcp:{STATE.port}"
    adb(*s, "reverse", port, port)
    adb(*s, "shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", f"http://localhost:{STATE.port}/{STATE.token}/")
    return "usb_ok"


def open_bluetooth():
    try:
        subprocess.Popen(["fsquirt.exe"])
        return "bt_hint"
    except OSError:
        return "bt_fail"


TETHER_PS = ("Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -match "
             "'RNDIS|Remote NDIS|NCM|Android|Apple Mobile|CDC' } | ForEach-Object "
             "{ (Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4).IPAddress }")


def tether_ips():
    ips = []
    if os.name == "nt":
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", TETHER_PS], capture_output=True,
                               text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ips = [l.strip() for l in r.stdout.splitlines() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", l.strip())]
        except Exception:
            pass
    if not ips:
        try:
            found = {r[4][0] for r in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        except OSError:
            found = set()
        ips = sorted(i for i in found if i.startswith(("192.168.42.", "172.20.10.")))
    return [i for i in ips if not i.startswith(("127.", "169.254."))]


def tether_task():
    STATE.emit("tether", "tether_busy")
    try:
        ips = tether_ips()
        key = f"tether_ok:http://{ips[0]}:{STATE.port}/{STATE.token}/" if ips else "tether_none"
    except Exception as e:
        key = f"tether_fail:{e}"[:200]
    STATE.emit("tether", key)


EXT_CAT = {}
for _cat, _exts in (
        ("c_photos", "jpg jpeg png gif webp heic heif bmp dng raw"),
        ("c_videos", "mp4 mkv mov avi 3gp webm m4v"),
        ("c_audio", "mp3 m4a aac wav ogg opus flac amr"),
        ("c_docs", "pdf doc docx xls xlsx ppt pptx txt csv epub"),
        ("c_apps", "apk xapk")):
    for _e in _exts.split():
        EXT_CAT[_e] = _cat
PHONE_ROOT = "/storage/emulated/0"


CAT_DIR = {"c_photos": "Photos", "c_videos": "Videos", "c_audio": "Audio", "c_docs": "Documents",
           "c_apps": "Apps", "c_other": "Other"}


def adb_text(serial, cmd, timeout=900):
    r = subprocess.run([find_adb(), "-s", serial, "shell", cmd], capture_output=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.decode("utf-8", "replace")


def list_phone_files(serial):
    """[(size, mtime, path, category)] for every readable file in the phone's shared storage."""
    out = adb_text(serial, f"find {PHONE_ROOT}/ -type f -exec stat -c '%s|%Y|%n' {{}} + 2>/dev/null")
    files = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            base = parts[2].rsplit("/", 1)[-1]
            ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
            files.append((int(parts[0]), int(parts[1]), parts[2], EXT_CAT.get(ext, "c_other")))
    return files


def storage_info(serial):
    """(total, used, free) in bytes, or None."""
    try:
        df = adb_text(serial, f"df -k {PHONE_ROOT} 2>/dev/null || df -k /data", 30).splitlines()
        tok = " ".join(df[1:]).split()
        return tuple(int(tok[i]) * 1024 for i in (1, 2, 3))
    except Exception:
        return None


def emit_pie(files, info):
    sizes = {k: 0 for k in CAT_DIR}
    for sz, _, _, cat in files:
        sizes[cat] += sz
    total, used, avail = info
    parts = list(sizes.items()) + [("c_system", max(used - sum(sizes.values()), 0)), ("c_free", avail)]
    STATE.emit("pie", json.dumps({"parts": parts, "total": total, "used": used}))


def scan_phone(serial):
    info = storage_info(serial)
    files = list_phone_files(serial)
    if not info or not files:
        return "scan_fail"
    emit_pie(files, info)
    return "scan_ok"


def plan_slot(directory, name, size, planned):
    """Return (path, is_duplicate). Same name + same size == already backed up (on disk or planned)."""
    stem, ext = os.path.splitext(name)
    i = 0
    while True:
        q = directory / (name if i == 0 else f"{stem}_{i}{ext}")
        have = planned.get(q)
        if have is None and q.exists():
            have = q.stat().st_size
        if have is None:
            return q, False
        if have == size:
            return q, True
        i += 1


def eta_text(sec):
    if sec < 60:
        return tr("eta_lt1")
    m = int(sec / 60 + 0.5)
    return tr("eta_m", m=m) if m < 60 else tr("eta_hm", h=m // 60, m=m % 60)


def make_puller(serial):
    """pull(remote, local): adbutils (one connection, fast); falls back to the adb program."""
    st = {"dev": None}
    try:
        import adbutils
        st["dev"] = adbutils.adb.device(serial)
    except Exception:
        pass

    def pull(src, dst):
        if st["dev"] is not None:
            try:
                st["dev"].sync.pull(src, dst)
                return
            except (AttributeError, TypeError):
                st["dev"] = None
            except Exception:
                pass
        r = subprocess.run([find_adb(), "-s", serial, "pull", src, dst], capture_output=True, timeout=3600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise OSError(r.stdout.decode("utf-8", "replace")[-200:])
    return pull


def full_backup(serial, cats):
    """Copy the chosen file types into <save folder>\\FullBackup\\<Type>\\ with live progress."""
    files = list_phone_files(serial)
    info = storage_info(serial)
    if files and info:
        emit_pie(files, info)
    base = STATE.save_dir / "FullBackup"
    planned, todo, skipped = {}, [], 0
    for size, mtime, path, cat in files:
        if cat not in cats or "/.thumbnails/" in path or "/Android/" in path:
            continue
        name = sanitize(path)
        if not name:
            continue
        dst, dup = plan_slot(base / CAT_DIR[cat], name, size, planned)
        if dup:
            skipped += 1
            continue
        planned[dst] = size
        todo.append((path, dst, size, mtime))
    total = max(sum(t[2] for t in todo), 1)

    cur = {"done": 0, "part": None, "stop": False}
    t0 = time.time()

    def ticker():
        while not cur["stop"]:
            time.sleep(0.5)
            d = cur["done"]
            try:
                if cur["part"]:
                    d += os.path.getsize(cur["part"])
            except OSError:
                pass
            d = min(d, total)
            el = time.time() - t0
            speed = d / el if el > 3 and d else 0
            eta = (total - d) / speed if speed else -1
            STATE.emit("prog", json.dumps({"done": d, "total": total, "speed": speed, "eta": eta}))

    threading.Thread(target=ticker, daemon=True).start()
    pull = make_puller(serial)
    n = failed = copied = 0
    try:
        for path, dst, size, mtime in todo:
            part = dst.with_name(dst.name + ".part")
            cur["part"] = part
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                pull(path, str(part))
                os.replace(part, dst)
                os.utime(dst, (mtime, mtime))
                n += 1
                copied += size
            except Exception:
                failed += 1
                try:
                    part.unlink()
                except OSError:
                    pass
            cur["part"] = None
            cur["done"] += size
    finally:
        cur["stop"] = True
    STATE.emit("prog", json.dumps({"done": total, "total": total, "speed": 0, "eta": 0}))
    msg = tr("full_summary", n=n, size=human_fa(copied, True), s=skipped)
    if failed:
        msg += " · " + tr("full_failed", f=failed)
    return f"full_done:{msg}\n{base}"


def phone_task(kind, cats=()):
    STATE.emit("usb", "scan_busy" if kind == "scan" else "full_busy")
    try:
        serial, err = phone_device()
        if err:
            key = err
        else:
            key = scan_phone(serial) if kind == "scan" else full_backup(serial, set(cats))
    except Exception as e:
        key = f"usb_fail:{e}"[:200]
    STATE.emit("usb", key)


def usb_task():
    STATE.emit("usb", "usb_busy")
    try:
        key = usb_start()
    except Exception as e:
        key = f"usb_fail:{e}"[:200]
    STATE.emit("usb", key)


# --------------------------------------------------------------------------
# GUI (tkinter)
# --------------------------------------------------------------------------
def make_icon(kind, color, px=40):
    """Anti-aliased icon drawn with Pillow (rendered large, then scaled down)."""
    from PIL import Image, ImageDraw
    k = 8
    img = Image.new("RGBA", (44 * k, 44 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def P(*pts):
        return [c * k for c in pts]

    if kind == "wifi":
        for r in (8, 16, 24):
            d.arc(P(22 - r, 34 - r, 22 + r, 34 + r), start=225, end=315, fill=color, width=int(3.4 * k))
        d.ellipse(P(18.5, 30.5, 25.5, 37.5), fill=color)
    elif kind == "usb":
        d.rounded_rectangle(P(11, 16, 33, 33), radius=3 * k, fill=color)
        d.rectangle(P(16, 6, 19.5, 16), fill=color)
        d.rectangle(P(24.5, 6, 28, 16), fill=color)
        d.line(P(22, 33, 22, 42), fill=color, width=int(4 * k))
    elif kind == "tether":
        d.rectangle(P(5, 9, 17, 35), outline=color, width=int(3 * k))
        d.rectangle(P(22, 13, 40, 28), outline=color, width=int(3 * k))
        d.line(P(17, 22, 22, 22), fill=color, width=int(3 * k))
        d.line(P(31, 28, 31, 34), fill=color, width=int(3 * k))
        d.line(P(25, 35, 37, 35), fill=color, width=int(3 * k))
    else:
        d.line(P(11, 14, 31, 30, 22, 38, 22, 6, 31, 14, 11, 30), fill=color, width=int(3.4 * k), joint="curve")
    return img.resize((px * 3, px * 3), Image.LANCZOS)


def run_gui(srv):
    import tkinter as tk
    import customtkinter as ctk
    from tkinter import filedialog, messagebox

    BG, CARD, SEL, ACC, ACC_H = "#f1f5f9", "#ffffff", "#eff6ff", "#2563eb", "#1d4ed8"
    TXT, MUTED, BORDER, GOOD, BAD = "#0f172a", "#64748b", "#e2e8f0", "#16a34a", "#dc2626"
    FONT = "Segoe UI" if os.name == "nt" else "Helvetica"

    def F(size, bold=False):
        return ctk.CTkFont(family=FONT, size=size, weight="bold" if bold else "normal")

    ctk.set_appearance_mode("light")
    root = ctk.CTk(fg_color=BG)
    SIDE = "e" if UI_FA else "w"
    global TK_BIDI
    try:
        v = root.tk.call("info", "patchlevel")
        TK_BIDI = tuple(int(x) for x in str(v).split(".")[:2]) >= (8, 7)
    except Exception:
        TK_BIDI = False
    root.title(tr("title", native=True))
    root.geometry("1020x800")
    root.resizable(False, False)
    root.grid_columnconfigure(0, weight=0)
    root.grid_columnconfigure(1, weight=1)
    root.grid_rowconfigure(0, weight=1)

    left = ctk.CTkFrame(root, width=470, fg_color="transparent")
    left.grid(row=0, column=0, sticky="nsew", padx=(22, 8), pady=20)
    left.pack_propagate(False)  # same width in every mode
    right = ctk.CTkFrame(root, fg_color="transparent")
    right.grid(row=0, column=1, sticky="nsew", padx=(8, 22), pady=20)

    # ---- header
    ctk.CTkLabel(left, text=tr("title"), font=F(24, True), text_color=TXT).pack(anchor=SIDE)
    ctk.CTkLabel(left, text=tr("app_sub"), font=F(13), text_color=MUTED).pack(anchor=SIDE, pady=(0, 14))

    # ---- mode cards
    cards = ctk.CTkFrame(left, fg_color="transparent")
    cards.pack(fill="x")
    for i in range(4):
        cards.grid_columnconfigure(i, weight=1, uniform="c")
    host = ctk.CTkFrame(left, corner_radius=0, fg_color=CARD, border_width=1, border_color=BORDER)
    host.pack(fill="both", expand=True, pady=(14, 0))
    modes, panels = {}, {}

    def select_mode(key):
        for k, m in modes.items():
            on = k == key
            m["frame"].configure(fg_color=SEL if on else CARD, border_color=ACC if on else BORDER)
            m["icon"].configure(image=m["img_on"] if on else m["img_off"])
            panels[k].pack_forget()
        panels[key].pack(fill="both", expand=True, padx=20, pady=18)

    def make_card(col, key, title, sub):
        f = ctk.CTkFrame(cards, corner_radius=0, fg_color=CARD, border_width=2, border_color=BORDER)
        f.grid(row=0, column=col, sticky="nsew", padx=4)
        img_on = ctk.CTkImage(make_icon(key, ACC), size=(40, 40))
        img_off = ctk.CTkImage(make_icon(key, MUTED), size=(40, 40))
        ic = ctk.CTkLabel(f, text="", image=img_off)
        ic.pack(pady=(12, 2))
        t = ctk.CTkLabel(f, text=tr(title), font=F(13, True), text_color=TXT)
        t.pack()
        s = ctk.CTkLabel(f, text=tr(sub), font=F(11), text_color=MUTED)
        s.pack(pady=(0, 12))
        for w in (f, ic, t, s):
            w.bind("<Button-1>", lambda e, k=key: select_mode(k))
        try:
            f.configure(cursor="hand2")
        except Exception:
            pass
        modes[key] = {"frame": f, "icon": ic, "img_on": img_on, "img_off": img_off}

    make_card(0, "wifi", "m_wifi", "m_wifi_sub")
    make_card(1, "usb", "m_usb", "m_usb_sub")
    make_card(2, "tether", "m_tether", "m_tether_sub")
    make_card(3, "bt", "m_bt", "m_bt_sub")

    def step(parent, n, key):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=5)
        ctk.CTkLabel(row, text=str(n), width=28, height=28, corner_radius=0, fg_color=ACC,
                     text_color="white", font=F(12, True)).pack(side="left", anchor="n", padx=(0, 12))
        ctk.CTkLabel(row, text=tr(key), font=F(13), text_color=TXT, justify="left", anchor="w",
                     wraplength=350).pack(side="left", fill="x", expand=True)

    def primary(parent, key, cmd):
        return ctk.CTkButton(parent, text=tr(key), command=cmd, height=46, corner_radius=0, fg_color=ACC,
                             hover_color=ACC_H, font=F(14, True))

    def secondary(parent, key):
        return ctk.CTkButton(parent, text=tr(key), height=34, corner_radius=0, fg_color="#e2e8f0",
                             hover_color="#cbd5e1", text_color=TXT, font=F(12))

    # ---- Wi-Fi panel
    wifi_p = ctk.CTkFrame(host, fg_color="transparent")
    panels["wifi"] = wifi_p
    step(wifi_p, 1, "w1")
    step(wifi_p, 2, "w2")
    qr_lbl = ctk.CTkLabel(wifi_p, text="", fg_color="white", corner_radius=0)
    qr_lbl.pack(pady=(6, 8))
    url_entry = ctk.CTkEntry(wifi_p, height=34, corner_radius=0, border_color=BORDER, fg_color="#f8fafc",
                             text_color=TXT, font=F(12))
    url_entry.pack(fill="x")
    btn_row = ctk.CTkFrame(wifi_p, fg_color="transparent")
    btn_row.pack(pady=8)
    copy_btn = secondary(btn_row, "copy")
    copy_btn.pack(side="left", padx=4)
    new_btn = secondary(btn_row, "new_code")
    new_btn.pack(side="left", padx=4)
    step(wifi_p, 3, "w3")

    # ---- USB panel
    usb_p = ctk.CTkFrame(host, fg_color="transparent")
    panels["usb"] = usb_p
    step(usb_p, 1, "u1")
    step(usb_p, 2, "u2")
    step(usb_p, 3, "u3")
    primary(usb_p, "usb", lambda: threading.Thread(target=usb_task, daemon=True).start()).pack(fill="x", pady=(14, 8))
    ctk.CTkButton(usb_p, text=tr("scan_btn"), command=lambda: threading.Thread(
        target=phone_task, args=("scan",), daemon=True).start(), height=38, corner_radius=0, fg_color="#e2e8f0",
        hover_color="#cbd5e1", text_color=TXT, font=F(13, True)).pack(fill="x", pady=(0, 8))
    cat_vars = {}
    cat_row = ctk.CTkFrame(usb_p, fg_color="transparent")
    cat_row.pack(fill="x", pady=(2, 8))
    for i, k in enumerate(CAT_DIR):
        cat_vars[k] = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(cat_row, text=tr(k), variable=cat_vars[k], font=F(11), text_color=TXT, fg_color=ACC,
                        hover_color=ACC_H, corner_radius=0, border_width=2, checkbox_width=18,
                        checkbox_height=18).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 8), pady=3)
    for c in range(3):
        cat_row.grid_columnconfigure(c, weight=1, uniform="k")

    prog_bar = ctk.CTkProgressBar(usb_p, height=14, corner_radius=0, progress_color=ACC, fg_color="#e2e8f0")
    prog_bar.set(0)
    prog_lbl = ctk.CTkLabel(usb_p, text="", font=F(12, True), text_color=ACC, anchor="w")
    prog_shown = {"on": False}

    def start_full():
        cats = [k for k, v in cat_vars.items() if v.get()]
        if not cats:
            STATE.emit("usb", "full_none")
            return
        prog_bar.set(0)
        prog_lbl.configure(text="")
        threading.Thread(target=phone_task, args=("full", cats), daemon=True).start()

    primary(usb_p, "full_btn", start_full).pack(fill="x", pady=(0, 8))
    usb_lbl = ctk.CTkLabel(usb_p, text="", font=F(12, True), text_color=MUTED, justify="left", anchor="w",
                           wraplength=380)
    usb_lbl.pack(anchor="w")

    # ---- Bluetooth panel
    bt_p = ctk.CTkFrame(host, fg_color="transparent")
    panels["bt"] = bt_p
    step(bt_p, 1, "b1")
    step(bt_p, 2, "b2")
    bt_lbl = ctk.CTkLabel(bt_p, text="", font=F(12, True), text_color=MUTED, justify="left", anchor="w",
                          wraplength=380)
    primary(bt_p, "bt", lambda: bt_lbl.configure(text=tr(open_bluetooth()))).pack(fill="x", pady=(14, 8))
    bt_lbl.pack(anchor="w")
    step(bt_p, 3, "b3")

    tether_p = ctk.CTkFrame(host, fg_color="transparent")
    panels["tether"] = tether_p
    step(tether_p, 1, "t1")
    step(tether_p, 2, "t2")
    primary(tether_p, "tether_btn", lambda: threading.Thread(target=tether_task, daemon=True).start()).pack(
        fill="x", pady=(12, 6))
    tether_lbl = ctk.CTkLabel(tether_p, text="", font=F(12, True), text_color=MUTED, justify="left", anchor="w",
                              wraplength=400)
    tether_lbl.pack(anchor="w")
    ctk.CTkLabel(tether_p, text=tr("tether_fw"), font=F(11), text_color=MUTED, justify="left", anchor="w",
                 wraplength=400).pack(side="bottom", anchor="w")
    tether_res = ctk.CTkFrame(tether_p, fg_color="transparent")
    tether_qr = ctk.CTkLabel(tether_res, text="", fg_color="white")
    tether_qr.pack(pady=(6, 6))
    tether_entry = ctk.CTkEntry(tether_res, height=34, corner_radius=0, border_color=BORDER, fg_color="#f8fafc",
                                text_color=TXT, font=F(12))
    tether_entry.pack(fill="x")
    tether_copy = secondary(tether_res, "copy")
    tether_copy.pack(pady=(6, 0))

    qr_imgs, tq_imgs = [], []

    def make_qr(url, size):
        try:
            import qrcode
            qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(url)
            qr.make(fit=True)
            pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            return ctk.CTkImage(pil, size=(size, size))
        except Exception:
            return None

    def draw_qr(url):
        img = make_qr(url, 190)
        if img:
            qr_imgs[:] = [img]
            qr_lbl.configure(image=img, text="")
        else:
            qr_lbl.configure(image=None, text="pip install qrcode pillow", width=190, height=190, text_color=MUTED)

    def copy_text(text, btn):
        root.clipboard_clear()
        root.clipboard_append(text)
        btn.configure(text=tr("copied"))
        root.after(1500, lambda: btn.configure(text=tr("copy")))

    def refresh_url():
        url = build_url()
        url_entry.configure(state="normal")
        url_entry.delete(0, "end")
        url_entry.insert(0, url)
        url_entry.configure(state="readonly")
        draw_qr(url)

    def copy_url():
        copy_text(url_entry.get(), copy_btn)

    def new_code():
        if messagebox.askyesno(tr("title", native=True), tr("new_code_warn", native=True)):
            STATE.cfg["token"] = secrets.token_urlsafe(6)
            save_config(STATE.cfg)
            refresh_url()

    copy_btn.configure(command=copy_url)
    tether_copy.configure(command=lambda: copy_text(tether_entry.get(), tether_copy))
    new_btn.configure(command=new_code)

    # ---- right side: destination card
    dest = ctk.CTkFrame(right, corner_radius=0, fg_color=CARD, border_width=1, border_color=BORDER)
    dest.pack(fill="x")
    ctk.CTkLabel(dest, text=tr("folder"), font=F(14, True), text_color=TXT).pack(anchor=SIDE, padx=18, pady=(16, 6))
    frow = ctk.CTkFrame(dest, fg_color="transparent")
    frow.pack(fill="x", padx=18)
    folder_entry = ctk.CTkEntry(frow, height=36, corner_radius=0, border_color=BORDER, fg_color="#f8fafc",
                                text_color=TXT, font=F(12))
    folder_entry.pack(side="left", fill="x", expand=True)

    def set_folder_text():
        folder_entry.configure(state="normal")
        folder_entry.delete(0, "end")
        folder_entry.insert(0, str(STATE.save_dir))
        folder_entry.configure(state="readonly")

    def choose_folder():
        d = filedialog.askdirectory(initialdir=str(STATE.save_dir))
        if d:
            STATE.cfg["save_dir"] = os.path.normpath(d)
            save_config(STATE.cfg)
            set_folder_text()

    def open_folder():
        STATE.save_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(STATE.save_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{STATE.save_dir}"')
            else:
                os.system(f'xdg-open "{STATE.save_dir}" &')
        except Exception:
            pass

    browse_btn = secondary(frow, "browse")
    browse_btn.configure(command=choose_folder)
    browse_btn.pack(side="left", padx=(8, 0))
    open_btn = secondary(frow, "open")
    open_btn.configure(command=open_folder)
    open_btn.pack(side="left", padx=(8, 0))

    month_var = tk.BooleanVar(value=STATE.by_month)

    def on_month():
        STATE.cfg["by_month"] = month_var.get()
        save_config(STATE.cfg)

    tk.Checkbutton(dest, text=tr("by_month"), variable=month_var, command=on_month, bg=CARD, fg=TXT,
                   activebackground=CARD, activeforeground=TXT, selectcolor="white", relief="flat",
                   highlightthickness=0, bd=0, font=(FONT, 10), cursor="hand2").pack(anchor=SIDE, padx=18,
                                                                                    pady=(12, 16))

    # ---- stat tiles
    tiles = ctk.CTkFrame(right, fg_color="transparent")
    tiles.pack(fill="x", pady=14)
    for i in range(3):
        tiles.grid_columnconfigure(i, weight=1, uniform="t")
    tile_vals = {}
    for i, (key, cap) in enumerate((("n", "t_saved"), ("s", "t_skipped"), ("size", "t_size"))):
        t = ctk.CTkFrame(tiles, corner_radius=0, fg_color=CARD, border_width=1, border_color=BORDER)
        t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
        tile_vals[key] = ctk.CTkLabel(t, text="0", font=F(26, True), text_color=ACC)
        tile_vals[key].pack(pady=(14, 0))
        ctk.CTkLabel(t, text=tr(cap), font=F(11), text_color=MUTED).pack(pady=(0, 14))

    status_lbl = ctk.CTkLabel(right, text=tr("waiting"), font=F(13, True), text_color=MUTED)
    status_lbl.pack(anchor=SIDE, pady=(0, 8))

    # ---- activity log
    logcard = ctk.CTkFrame(right, corner_radius=0, fg_color=CARD, border_width=1, border_color=BORDER)
    logcard.pack(fill="both", expand=True)
    ctk.CTkLabel(logcard, text=tr("activity"), font=F(14, True), text_color=TXT).pack(anchor=SIDE, padx=18,
                                                                                    pady=(14, 4))
    log = ctk.CTkTextbox(logcard, fg_color=CARD, text_color=TXT, font=F(12), wrap="word", state="disabled")
    log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    try:
        log._textbox.tag_configure("r", justify="right" if UI_FA else "left")
    except Exception:
        pass

    # ---- pie chart card (shown after "Show phone storage chart")
    PIE_COL = {"c_photos": "#2563eb", "c_videos": "#7c3aed", "c_audio": "#db2777", "c_docs": "#f59e0b",
               "c_apps": "#0891b2", "c_other": "#64748b", "c_system": "#334155", "c_free": "#cbd5e1"}
    pie_card = ctk.CTkFrame(right, corner_radius=0, fg_color=CARD, border_width=1, border_color=BORDER)
    ctk.CTkLabel(pie_card, text=tr("pie_title"), font=F(14, True), text_color=TXT).pack(anchor=SIDE, padx=18,
                                                                                        pady=(12, 2))
    pie_cv = tk.Canvas(pie_card, width=440, height=172, bg=CARD, highlightthickness=0)
    pie_cv.pack(padx=10, pady=(0, 10))

    def draw_pie(data):
        parts = [(k, v) for k, v in data["parts"] if v > 0]
        tot = sum(v for _, v in parts) or 1
        pie_cv.delete("all")
        start = 90.0
        for k, v in parts:
            ext = v / tot * 360
            if ext >= 359.9:
                pie_cv.create_oval(8, 8, 164, 164, fill=PIE_COL[k], outline=CARD, width=2)
            else:
                pie_cv.create_arc(8, 8, 164, 164, start=start, extent=-ext, fill=PIE_COL[k], outline=CARD,
                                  width=2, style="pieslice")
            start -= ext
        pie_cv.create_oval(46, 46, 126, 126, fill=CARD, outline=CARD)
        pie_cv.create_text(86, 86, text=f"{human(data['used'], True)}\n/ {human(data['total'], True)}", fill=TXT,
                           font=(FONT, 9, "bold"), justify="center")
        for i, (k, v) in enumerate(parts):
            y = 14 + i * 20
            pie_cv.create_rectangle(186, y - 6, 198, y + 6, fill=PIE_COL[k], outline=PIE_COL[k])
            pie_cv.create_text(206, y, anchor="w", fill=TXT, font=(FONT, 10),
                               text=f"{tr(k)}   {human(v, True)}  ({v / tot * 100:.0f}%)")

    counts = {"n": 0, "s": 0, "bytes": 0}
    current = {"mode": "wifi"}

    def update_stats():
        tile_vals["n"].configure(text=str(counts["n"]))
        tile_vals["s"].configure(text=str(counts["s"]))
        tile_vals["size"].configure(text=human_fa(counts["bytes"]))
        if counts["n"] or counts["s"]:
            status_lbl.configure(text=tr("receiving"), text_color=GOOD)

    def add_log(key, name):
        log.configure(state="normal")
        log.insert("1.0", tr(key, name=name) + "\n", "r")
        if int(log.index("end-1c").split(".")[0]) > 500:
            log.delete("501.0", "end")
        log.configure(state="disabled")

    def show(mode):
        current["mode"] = mode
        select_mode(mode)

    def poll():
        try:
            while True:
                kind, name, size = STATE.events.get_nowait()
                if kind == "saved":
                    counts["n"] += 1
                    counts["bytes"] += size
                    add_log("log_saved", name)
                elif kind == "skipped":
                    counts["s"] += 1
                    add_log("log_skip", name)
                elif kind == "failed":
                    add_log("log_fail", name)
                elif kind == "tether":
                    key, _, detail = name.partition(":")
                    ok = key == "tether_ok"
                    tether_lbl.configure(text=tr(key) + ("\n" + detail if detail and not ok else ""),
                                         text_color=GOOD if ok else MUTED if key == "tether_busy" else BAD)
                    if ok:
                        img = make_qr(detail, 150)
                        if img:
                            tq_imgs[:] = [img]
                            tether_qr.configure(image=img)
                        tether_entry.configure(state="normal")
                        tether_entry.delete(0, "end")
                        tether_entry.insert(0, detail)
                        tether_entry.configure(state="readonly")
                        tether_res.pack(fill="x")
                    else:
                        tether_res.pack_forget()
                    if current["mode"] != "tether":
                        show("tether")
                elif kind == "prog":
                    d = json.loads(name)
                    frac = min(d["done"] / d["total"], 1.0)
                    if not prog_shown["on"]:
                        prog_shown["on"] = True
                        prog_bar.pack(fill="x", pady=(0, 4), before=usb_lbl)
                        prog_lbl.pack(anchor="w", pady=(0, 4), before=usb_lbl)
                    prog_bar.set(frac)
                    eta = eta_text(d["eta"]) if d["eta"] >= 0 else tr("eta_calc")
                    prog_lbl.configure(text=tr("prog_line", pct=int(frac * 100), done=human(d["done"], True),
                                               total=human(d["total"], True), speed=human(d["speed"], True),
                                               eta=eta))
                elif kind == "pie":
                    draw_pie(json.loads(name))
                    pie_card.pack(fill="x", pady=(0, 12), before=logcard)
                elif kind == "usb":
                    key, _, detail = name.partition(":")
                    usb_lbl.configure(text=tr(key) + ("\n" + detail if detail else ""),
                                      text_color=GOOD if key in ("usb_ok", "scan_ok", "full_done")
                                      else MUTED if key.endswith("_busy") else BAD)
                    if current["mode"] != "usb":
                        show("usb")
                update_stats()
        except queue.Empty:
            pass
        root.after(250, poll)

    def on_close():
        try:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        finally:
            root.destroy()

    set_folder_text()
    refresh_url()
    update_stats()
    show("wifi")
    poll()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    global STATE
    ap = argparse.ArgumentParser(description="Receive phone photos over Wi-Fi")
    ap.add_argument("--nogui", action="store_true", help="run without the window (console only)")
    ap.add_argument("--dir", help="save folder (overrides saved setting)")
    ap.add_argument("--port", type=int, help="port (default 8765)")
    ap.add_argument("--token", help="fixed access token (default: random, remembered)")
    ap.add_argument("--usb", action="store_true", help="connect an Android phone over USB (adb) at startup")
    args = ap.parse_args()

    cfg = load_config()
    if args.dir:
        cfg["save_dir"] = args.dir
    if args.port:
        cfg["port"] = args.port
    if args.token:
        cfg["token"] = args.token
    STATE = State(cfg)
    STATE.save_dir.mkdir(parents=True, exist_ok=True)
    save_config(cfg)

    try:
        srv = start_server(STATE)
    except OSError:
        print(S["port_fail"][1])
        if not args.nogui:
            try:
                import tkinter
                from tkinter import messagebox
                r = tkinter.Tk()
                r.withdraw()
                messagebox.showerror(tr("title", native=True), tr("port_fail", native=True))
            except Exception:
                pass
        sys.exit(1)

    if args.usb:
        threading.Thread(target=usb_task, daemon=True).start()

    if args.nogui:
        print("Save folder:", STATE.save_dir)
        print("Open on phone:", build_url())
        try:
            while True:
                kind, name, size = STATE.events.get()
                print(f"[{kind}] {name} {human(size)}")
        except KeyboardInterrupt:
            srv.shutdown()
    else:
        run_gui(srv)


if __name__ == "__main__":
    main()
