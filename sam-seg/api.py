import hashlib
import subprocess
import sys
import uuid

import requests
from qgis.core import QgsSettings

DEFAULT_API = "https://ocqaprvgw74qifggxnvolqyuea0khywo.lambda-url.eu-central-1.on.aws/"


def api_url():
    return QgsSettings().value("sam_seg/api_url", DEFAULT_API).rstrip("/")


def login_page_url():
    # kasnije: adresa pravog web frontenda
    return QgsSettings().value("sam_seg/login_url", api_url() + "/auth/desktop")


def frontend_url():
    return QgsSettings().value("sam_seg/frontend_url", "http://localhost:3000").rstrip("/")


def pricing_url():
    return frontend_url() + "/pricing"

#  ID mašine koji daje operativni sistem (isti za sve korisnike i instalacije na tom računaru)

def machine_id():
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return winreg.QueryValueEx(key, "MachineGuid")[0].encode()
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split("=")[-1].strip().strip('"').encode()
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(path) as f:
                    return f.read().strip().encode()
            except OSError:
                pass
    except Exception:
        pass
    return b""

# anoniman ID računara (sha256 od ID-a mašine) — za jednokratni free paket.

def device_id():
    raw = machine_id()
    if not raw:  # OS ne daje ID - nasumičan, sačuvan u podešavanjima
        s = QgsSettings()
        raw = (s.value("sam_seg/install_id", "") or "").encode()
        if not raw:
            raw = uuid.uuid4().hex.encode()
            s.setValue("sam_seg/install_id", raw.decode())
    return hashlib.sha256(b"sam-seg:" + raw).hexdigest()


class ApiError(Exception):
    pass
 
# Token ne važi (istekao ili odjavljen) pa korisnik mora ponovo da se prijavi.

class AuthError(ApiError):
    pass


class Api:
    def __init__(self):
        self.token = QgsSettings().value("sam_seg/token", "") or ""

    def set_token(self, token):
        self.token = token
        QgsSettings().setValue("sam_seg/token", token)

    def request(self, method, path, timeout=15, **kw):
        try:
            r = requests.request(method, api_url() + path, timeout=timeout,
                                 headers={"Authorization": f"Bearer {self.token}"}, **kw)
        except requests.RequestException as e:
            raise ApiError(f"Server is not reachable ({e.__class__.__name__})")
        if r.status_code == 401:
            raise AuthError("Session expired, please sign in again")
        if not r.ok:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ApiError(detail if isinstance(detail, str) else str(detail))
        return r.json()

    def me(self):
        return self.request("GET", "/me")

    def claim_free(self):
        return self.request("POST", "/claim-free", json={"device_id": device_id()})["granted"]

    def refine(self, job_id, polygon, points):
        """Korekcija klikovima (SAM 2.1): polygon=None -> nov objekat iz jednog klika."""
        return self.request("POST", f"/jobs/{job_id}/refine", timeout=120,
                            json={"polygon": polygon, "points": points})

    def warmup(self):
        """Pali GPU u pozadini; greška ovde nije bitna."""
        try:
            self.request("POST", "/warmup", timeout=10)
        except ApiError:
            pass

    def upload(self, path):
        """Raster ide direktno na S3 preko presigned POST-a; vraća S3 ključ."""
        up = self.request("POST", "/uploads")
        try:
            with open(path, "rb") as f:
                r = requests.post(up["url"], data=up["fields"], files={"file": f}, timeout=900)
        except requests.RequestException as e:
            raise ApiError(f"Upload failed ({e.__class__.__name__})")
        if not r.ok:
            raise ApiError(f"Upload failed ({r.status_code})")
        return up["key"]

    def download(self, url, path):
        try:
            r = requests.get(url, timeout=300)
        except requests.RequestException as e:
            raise ApiError(f"Download failed ({e.__class__.__name__})")
        if not r.ok:
            raise ApiError(f"Download failed ({r.status_code})")
        with open(path, "wb") as f:
            f.write(r.content)
