# Mreža tile-ova — mora da prati tiles() u modal_app/segment.py i tile_count() na backendu.
import math
from dataclasses import dataclass

from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

TILE, OVERLAP = 1024, 128

@dataclass
class Grid:
    # Slika koja ide na server: CRS, gornji levi ugao, veličina piksela i dimenzije u pikselima.
    crs: QgsCoordinateReferenceSystem
    x0: float
    y0: float
    px: float
    py: float
    width: int
    height: int

    def rect(self, c, r, w, h):
        return QgsRectangle(self.x0 + c * self.px, self.y0 - (r + h) * self.py,
                            self.x0 + (c + w) * self.px, self.y0 - r * self.py)

    def bbox(self):
        return self.rect(0, 0, self.width, self.height)


def snap_to_raster(box, extent, px, py):
    # Poravnaj obuhvat na piksele rastera (kao gdal.Translate projWin).
    c0 = max(0, math.floor((box.xMinimum() - extent.xMinimum()) / px))
    r0 = max(0, math.floor((extent.yMaximum() - box.yMaximum()) / py))
    c1 = math.ceil((box.xMaximum() - extent.xMinimum()) / px)
    r1 = math.ceil((extent.yMaximum() - box.yMinimum()) / py)
    return extent.xMinimum() + c0 * px, extent.yMaximum() - r0 * py, c1 - c0, r1 - r0


def tile_windows(width, height, tile_px):
    # (prozor, core) u pikselima; core zone se ne preklapaju i dele sliku bez praznina.
    ov = round(OVERLAP * tile_px / TILE)
    half, step = ov // 2, tile_px - ov
    for r in range(0, max(height - ov, 1), step):
        for c in range(0, max(width - ov, 1), step):
            win = (c, r, min(tile_px, width - c), min(tile_px, height - r))
            c0, r0 = (c + half if c else 0), (r + half if r else 0)
            c1 = c + tile_px - half if c + tile_px < width else width
            r1 = r + tile_px - half if r + tile_px < height else height
            yield win, (c0, r0, c1 - c0, r1 - r0)
