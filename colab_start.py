# @title 🚀 รัน Playground Dashboard จาก GitHub (เซลล์เดียว)
# ==============================================================================
# 1) แก้แค่ตรงนี้
# ==============================================================================
# ใส่ ngrok token ของคุณเอง ห้าม commit token ขึ้น GitHub
NGROK_TOKEN = "3EFiI4bmbKbzeO6A71kGqYIEkeA_r5WKYcmEx6DZys8G7iFG"

# ไม่บังคับ
FRED_API_KEY = "ea7bacc8b83ce8b795b7562141b460c4"

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


print(">>> [1/6] ล้าง process / tunnel เก่า")
sh(f"fuser -k {PORT}/tcp || true")
sh("pkill -f uvicorn || true")
sh("pkill -f ngrok || true")
print("ล้างของเก่าแล้ว")


print("\n>>> [2/6] ดึงโค้ดล่าสุดจาก GitHub")
if os.path.isdir(os.path.join(PROJECT_DIR, ".git")):
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

if not token:
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
for i in range(30):
    time.sleep(1)
    if server.poll() is not None:
        print("uvicorn ดับตั้งแต่สตาร์ท อ่าน log:")
        print(open(log_path).read()[-2000:])
        raise RuntimeError("uvicorn เปิดไม่ขึ้น")
    probe = subprocess.run(
        ["python3", "-c", f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{PORT}/api/health', timeout=2)"],
        capture_output=True,
    )
    if probe.returncode == 0:
        ok = True
        print(f"เซิร์ฟเวอร์พร้อมแล้ว ({i+1}s)")
        break

if not ok:
    print(open(log_path).read()[-2000:])
    raise RuntimeError("รอ /api/health ไม่ทัน")


print("\n>>> [6/6] เปิด ngrok")
public_url = ngrok.connect(PORT, bind_tls=True).public_url
dash = public_url.rstrip("/") + "/"
print("=" * 52)
print("พร้อมใช้งาน")
print("Public URL :", public_url)
print("Dashboard  :", dash)
print("=" * 52)
display(HTML(f'<p style="font-size:16px">เปิดแดชบอร์ด: <a href="{dash}" target="_blank">{dash}</a></p>'))

print("เซลล์นี้จะค้างไว้เพื่อให้เซิร์ฟเวอร์ไม่ดับ กด Stop เพื่อปิด")
try:
    while True:
        if server.poll() is not None:
            print("uvicorn หยุดเอง อ่าน log:")
            print(open(log_path).read()[-2000:])
            break
        time.sleep(30)
except KeyboardInterrupt:
    print("กำลังปิดเซิร์ฟเวอร์")
    server.terminate()
    try:
        ngrok.kill()
    except Exception:
        pass
    print("ปิดแล้ว")
