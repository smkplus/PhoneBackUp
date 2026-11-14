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
import socket
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
    "folder": ("پوشه ذخیره:", "Save folder:"),
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


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_fa(n):
    """Size with Persian unit names, so the GUI line reads naturally right-to-left."""
    if not (FA and UI_FA):
        return human(n)
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
# GUI (tkinter)
# --------------------------------------------------------------------------
def run_gui(srv):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:  # crisp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    SIDE = "e" if UI_FA else "w"
    global TK_BIDI
    try:
        v = root.tk.call("info", "patchlevel")
        TK_BIDI = tuple(int(x) for x in str(v).split(".")[:2]) >= (8, 7)
    except Exception:
        TK_BIDI = False
    root.title(tr("title", native=True))
    root.geometry("820x560")
    root.minsize(760, 520)
    font = ("Tahoma", 10)
    root.option_add("*Font", font)

    left = ttk.Frame(root, padding=14)
    left.pack(side="left", fill="y")
    right = ttk.Frame(root, padding=14)
    right.pack(side="left", fill="both", expand=True)

    # --- left: QR + URL
    ttk.Label(left, text=tr("scan"), justify="center").pack(pady=(0, 8))
    QR_SIZE = 260
    canvas = tk.Canvas(left, width=QR_SIZE, height=QR_SIZE, bg="white", highlightthickness=1,
                       highlightbackground="#cbd5e1")
    canvas.pack()
    ttk.Label(left, text=tr("or_open")).pack(pady=(12, 4))
    url_var = tk.StringVar()
    url_entry = ttk.Entry(left, textvariable=url_var, width=38, justify="left")
    url_entry.pack()
    btn_row = ttk.Frame(left)
    btn_row.pack(pady=8)
    copy_btn = ttk.Button(btn_row, text=tr("copy"))
    copy_btn.pack(side="left", padx=4)
    new_btn = ttk.Button(btn_row, text=tr("new_code"))
    new_btn.pack(side="left", padx=4)

    def draw_qr(url):
        canvas.delete("all")
        try:
            import qrcode
            qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(url)
            qr.make(fit=True)
            m = qr.get_matrix()
            n = len(m)
            cell = max(1, QR_SIZE // n)
            off = (QR_SIZE - cell * n) // 2
            for y, row in enumerate(m):
                for x, v in enumerate(row):
                    if v:
                        canvas.create_rectangle(off + x * cell, off + y * cell,
                                                off + (x + 1) * cell, off + (y + 1) * cell,
                                                fill="black", outline="black")
        except Exception:
            canvas.create_text(QR_SIZE // 2, QR_SIZE // 2, text="pip install qrcode",
                               fill="#475569", font=("Consolas", 10))

    def refresh_url():
        url = build_url()
        url_var.set(url)
        draw_qr(url)

    def copy_url():
        root.clipboard_clear()
        root.clipboard_append(url_var.get())
        copy_btn.config(text=tr("copied"))
        root.after(1500, lambda: copy_btn.config(text=tr("copy")))

    def new_code():
        if messagebox.askyesno(tr("title", native=True), tr("new_code_warn", native=True)):
            STATE.cfg["token"] = secrets.token_urlsafe(6)
            save_config(STATE.cfg)
            refresh_url()

    copy_btn.config(command=copy_url)
    new_btn.config(command=new_code)

    # --- right: folder, options, stats, log
    ttk.Label(right, text=tr("folder")).pack(anchor=SIDE)
    folder_var = tk.StringVar(value=str(STATE.save_dir))
    frow = ttk.Frame(right)
    frow.pack(fill="x", pady=4)
    ttk.Entry(frow, textvariable=folder_var, state="readonly").pack(side="left", fill="x", expand=True)

    def choose_folder():
        d = filedialog.askdirectory(initialdir=str(STATE.save_dir))
        if d:
            STATE.cfg["save_dir"] = os.path.normpath(d)
            folder_var.set(STATE.cfg["save_dir"])
            save_config(STATE.cfg)

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

    ttk.Button(frow, text=tr("browse"), command=choose_folder).pack(side="left", padx=(6, 0))
    ttk.Button(frow, text=tr("open"), command=open_folder).pack(side="left", padx=(6, 0))

    month_var = tk.BooleanVar(value=STATE.by_month)

    def on_month():
        STATE.cfg["by_month"] = month_var.get()
        save_config(STATE.cfg)

    ttk.Checkbutton(right, text=tr("by_month"), variable=month_var, command=on_month).pack(anchor=SIDE, pady=(6, 10))

    stats_var = tk.StringVar()
    ttk.Label(right, textvariable=stats_var, font=("Tahoma", 10, "bold")).pack(anchor=SIDE)

    log_frame = ttk.Frame(right)
    log_frame.pack(fill="both", expand=True, pady=(8, 0))
    log = tk.Text(log_frame, wrap="word", state="disabled", borderwidth=0, highlightthickness=1,
                  highlightbackground="#cbd5e1", cursor="arrow", height=10)
    log.tag_configure("r", justify="right" if UI_FA else "left")
    sb = ttk.Scrollbar(log_frame, orient="vertical", command=log.yview)
    log.config(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    log.pack(side="left", fill="both", expand=True)

    counts = {"n": 0, "s": 0, "bytes": 0}

    def update_stats():
        stats_var.set(tr("stats", n=counts["n"], s=counts["s"], size=human_fa(counts["bytes"])))

    def add_log(key, name):
        log.config(state="normal")
        log.insert("1.0", tr(key, name=name) + "\n", "r")
        if int(log.index("end-1c").split(".")[0]) > 500:
            log.delete("501.0", "end")
        log.config(state="disabled")

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
                update_stats()
        except queue.Empty:
            pass
        root.after(250, poll)

    def on_close():
        try:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        finally:
            root.destroy()

    refresh_url()
    update_stats()
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
