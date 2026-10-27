# Priprema rastera za zonu i ceo tok detekcije u pozadini (QgsTask, bez zamrzavanja QGIS-a).
import os
import tempfile
import time
import traceback

from osgeo import gdal
from qgis.core import Qgis, QgsMessageLog, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from .api import ApiError

#  Isečak lokalnog rastera na obuhvat zone (bbox u CRS-u rastera: xmin, ymin, xmax, ymax).
def clip_raster(source, bbox, out):
    try:
        ds = gdal.Translate(out, source, projWin=[bbox[0], bbox[3], bbox[2], bbox[1]],
                            creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
    except RuntimeError as e:
        raise ApiError(f"Could not read the selected raster ({e})")
    if ds is None:
        raise ApiError("Could not read the selected raster")
    ds = None
    return out

# Renderovana podloga (RGBA) -> RGB JPEG GeoTIFF isečen tačno na obuhvat zone.(native:rasterize render proširi desno i dole do celog broja tile-ova.)
def compress_rgb(source, bbox, out):

    try:
        ds = gdal.Translate(out, source, bandList=[1, 2, 3], projWin=[bbox[0], bbox[3], bbox[2], bbox[1]],
                            creationOptions=["COMPRESS=JPEG", "JPEG_QUALITY=90", "PHOTOMETRIC=YCBCR", "TILED=YES"])
    except RuntimeError as e:
        raise ApiError(f"Could not prepare the basemap image ({e})")
    if ds is None:
        raise ApiError("Could not prepare the basemap image")
    ds = None
    return out

# prepare() - upload na S3 -> /jobs -> čekanje -> preuzimanje .gpkg

class DetectTask(QgsTask):
    status = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)  # gotovi tile-ovi, ukupno, pronađeno objekata
    features = pyqtSignal(list)           # novi poligoni (GeoJSON, EPSG:4326)

    def __init__(self, api, prepare, body):
        super().__init__("GeoSegment: detecting", QgsTask.CanCancel)
        self.api, self.prepare, self.body = api, prepare, body
        self.workdir = tempfile.mkdtemp(prefix="sam_seg_")
        self.result_path = None
        self.job_id = None
        self.error = None

    def run(self):
        try:
            self.status.emit("Preparing image…")
            path = self.prepare(self.workdir)
            self.setProgress(10)

            self.status.emit("Uploading…")
            key = self.api.upload(path)
            self.setProgress(30)

            self.status.emit("Detecting…")
            job = self.api.request("POST", "/jobs", timeout=60, json={**self.body, "key": key})
            self.job_id = job["id"]
            since = 0
            while job["status"] == "running":
                if self.isCanceled():
                    return False
                time.sleep(3)
                job = self.api.request("GET", f"/jobs/{job['id']}", params={"since": since})
                if job.get("features"):
                    self.features.emit(job["features"])
                since = job.get("next", since)
                p = job.get("progress")
                if p and p["total"]:
                    self.progress.emit(p["done"], p["total"], p["found"])
                    self.setProgress(30 + 60 * p["done"] / p["total"])
            if job["status"] != "done":
                self.error = job.get("error") or "Detection failed"
                return False
            self.setProgress(90)

            self.status.emit("Downloading result…")
            self.result_path = os.path.join(self.workdir, "result.gpkg")
            self.api.download(job["result_url"], self.result_path)
            return True
        except ApiError as e:
            self.error = str(e)
            return False
        except Exception as e:
            self.error = f"{e.__class__.__name__}: {e}"
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoSegment AI", Qgis.Critical)
            return False
