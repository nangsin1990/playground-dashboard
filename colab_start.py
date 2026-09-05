# @title 🚀 รัน Playground Dashboard จาก GitHub (เซลล์เดียว)
# ==============================================================================
# 1) แก้แค่ตรงนี้
# ==============================================================================
# ใส่ ngrok token ของคุณเอง ห้าม commit token ขึ้น GitHub
NGROK_TOKEN = ""

# ไม่บังคับ
FRED_API_KEY = ""

# โดเมนประจำบัญชีจาก https://dashboard.ngrok.com/domains
# ห้ามเว้นว่าง ถ้าเว้นว่างระบบจะสุ่มโดเมนใหม่ทุกครั้ง
NGROK_DOMAIN = "backhand-decipher-defeat.ngrok-free.dev"

REPO_URL = "https://github.com/nangsin1990/playground-dashboard.git"
REPO_NAME = "playground-dashboard"
PORT = 8000

# ==============================================================================
# 2) สคริปต์อัตโนมัติ — ดึงโค้ดจาก GitHub แล้วเปิดเซิร์ฟเวอร์
# ==============================================================================
import os
import sys
import time
import shutil
import subprocess

from IPython.display import display, HTML

PROJECT_DIR = f"/content/{REPO_NAME}"


def sh(cmd, check=False):
    print("$", cmd)
    return subprocess.run(cmd, shell=True, check=check)


def _clean_domain(value: str) -> str:
    return (value or "").strip().replace("https://", "").replace("http://", "").strip("/")


print(">>> [1/6] ล้าง process / tunnel เก่า")
sh(f"fuser -k {PORT}/tcp || true")
sh("pkill -f uvicorn || true")
sh("pkill -f ngrok || true")
print("ล้างของเก่าแล้ว")


print("\n>>> [2/6] หาโปรเจกต์ local ก่อน ถ้าไม่มีค่อย clone GitHub")
_here = os.getcwd()
_candidates = [
    _here,
    os.path.join(_here, "playground-dashboard-main"),
    os.path.join(_here, "playground-dashboard"),
    PROJECT_DIR,
    "/content/playground-dashboard-main",
    "/content/playground-dashboard",
]
_local = next((p for p in _candidates if os.path.isfile(os.path.join(p, "backend.py"))), None)
if _local:
    PROJECT_DIR = os.path.abspath(_local)
    print("ใช้โปรเจกต์ที่มีอยู่แล้วที่", PROJECT_DIR, "(ไม่ต้องพึ่ง GitHub)")
elif os.path.isdir(os.path.join(PROJECT_DIR, ".git")):
    sh(f"git -C {PROJECT_DIR} fetch --all --prune")
    sh(f"git -C {PROJECT_DIR} reset --hard origin/main")
    sh(f"git -C {PROJECT_DIR} pull --ff-only origin main")
elif os.path.isdir(PROJECT_DIR):
    shutil.rmtree(PROJECT_DIR)
    sh(f"git clone --depth 1 {REPO_URL} {PROJECT_DIR}", check=True)
else:
    sh(f"git clone --depth 1 {REPO_URL} {PROJECT_DIR}", check=True)

os.chdir(PROJECT_DIR)
print("อยู่ที่", os.getcwd())
print("ไฟล์หลัก:", "backend.py" if os.path.isfile("backend.py") else "ไม่พบ backend.py")
for _need in ("backend.py", "index.html", "style.css", "nav.js"):
    print(" ", _need, "OK" if os.path.isfile(_need) else "หาย")


print("\n>>> [3/6] ติดตั้งไลบรารี")
sh(f"{sys.executable} -m pip install -q pyngrok")
if os.path.isfile("requirements.txt"):
    sh(f"{sys.executable} -m pip install -q -r requirements.txt")
else:
    sh(f'{sys.executable} -m pip install -q fastapi "uvicorn[standard]" yfinance pandas numpy aiofiles python-multipart pyngrok')
print("ติดตั้งเสร็จ")


print("\n>>> [4/6] ตั้งค่า token")
token = (NGROK_TOKEN or os.environ.get("NGROK_TOKEN") or "").strip()
if not token:
    try:
        from google.colab import userdata
        token = (userdata.get("NGROK_TOKEN") or "").strip()
    except Exception:
        token = ""

if not token or token.lower() in {"xxx", "your_token", "ngrok_token"}:
    raise ValueError("ยังไม่มี NGROK_TOKEN — ใส่ในช่องด้านบน แล้วรันเซลล์ใหม่")

from pyngrok import ngrok, conf
conf.get_default().auth_token = token
try:
    ngrok.kill()
except Exception:
    pass

if FRED_API_KEY:
    os.environ["FRED_API_KEY"] = FRED_API_KEY
    print("ตั้งค่า ngrok + FRED แล้ว")
else:
    print("ตั้งค่า ngrok แล้ว")

domain = _clean_domain(NGROK_DOMAIN or os.environ.get("NGROK_DOMAIN") or "")
if not domain:
    try:
        from google.colab import userdata
        domain = _clean_domain(userdata.get("NGROK_DOMAIN") or "")
    except Exception:
        domain = ""
if not domain:
    raise ValueError("ต้องใส่ NGROK_DOMAIN จากแดชบอร์ด ngrok ห้ามให้สุ่ม")


print("\n>>> [4.5/6] ลอง mount Google Drive สำหรับ cache")
try:
    if not os.path.isdir("/content/drive/MyDrive"):
        from google.colab import drive
        drive.mount("/content/drive")
    print("Drive พร้อม" if os.path.isdir("/content/drive/MyDrive") else "ไม่มี Drive ใช้ /tmp แทน")
except Exception as e:
    print("ข้าม Drive:", e)

print("\n>>> [5/6] สตาร์ท uvicorn")
log_path = "/tmp/playground-uvicorn.log"
log_f = open(log_path, "w")
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "backend:app", "--host", "0.0.0.0", "--port", str(PORT)],
    cwd=PROJECT_DIR,
    stdout=log_f,
    stderr=subprocess.STDOUT,
)

ok = False
for i in range(45):
    time.sleep(1)
    if server.poll() is not None:
        print("uvicorn ดับตั้งแต่สตาร์ท อ่าน log:")
        print(open(log_path).read()[-2000:])
        raise RuntimeError("uvicorn เปิดไม่ขึ้น")
    probe = subprocess.run(
        [sys.executable, "-c", f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{PORT}/api/health', timeout=2)"],
        capture_output=True,
    )
    if probe.returncode == 0:
        ok = True
        print(f"เซิร์ฟเวอร์พร้อมแล้ว ({i+1}s) health=ok")
        break

if not ok:
    print(open(log_path).read()[-2000:])
    raise RuntimeError("รอ /api/health ไม่ทัน ห้ามเปิด ngrok")


print("\n>>> [6/6] เปิด ngrok โดเมนเดิม ที่ 127.0.0.1 ไม่ใช่ [::1]")
try:
    tunnel = ngrok.connect(addr=f"127.0.0.1:{PORT}", url=f"https://{domain}", bind_tls=True)
except Exception:
    try:
        tunnel = ngrok.connect(addr=f"127.0.0.1:{PORT}", domain=domain, bind_tls=True)
    except TypeError:
        tunnel = ngrok.connect(addr=f"127.0.0.1:{PORT}", domain=domain)

public_url = (tunnel.public_url or "").rstrip("/")
if domain not in public_url:
    raise RuntimeError(f"ได้โดเมนผิด ต้องการ {domain} แต่ได้ {public_url}")

dash = public_url + "/"
print("=" * 52)
print("พร้อมใช้งาน")
print("Public URL :", public_url)
print("Dashboard  :", dash)
print("index.html :", public_url + "/index.html")
print("=" * 52)
display(HTML(f'<p style="font-size:16px">เปิดแดชบอร์ด: <a href="{dash}" target="_blank">{dash}</a></p>'))

print("เซลล์นี้จะค้างไว้เพื่อให้เซิร์ฟเวอร์ไม่ดับ กด Stop เพื่อปิด")
try:
    while True:
        if server.poll() is not None:
            print("uvicorn หยุดเอง อ่าน log:")
            print(open(log_path).read()[-2000:])
            try:
                ngrok.kill()
            except Exception:
                pass
            break
        time.sleep(15)
except KeyboardInterrupt:
    print("กำลังปิดเซิร์ฟเวอร์")
    server.terminate()
    try:
        ngrok.kill()
    except Exception:
        pass
    print("ปิดแล้ว")
