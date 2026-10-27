from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor


class PolygonDrawTool(QgsMapTool):
    """Crtanje zone: levi klik dodaje tačku; dupli klik ili klik na postojeću tačku završava.
    Backspace briše poslednju tačku, Esc kreće ispočetka."""

    finished = pyqtSignal(QgsGeometry)
    SNAP_PX = 10

    def __init__(self, canvas):
        super().__init__(canvas)
        self.band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self.band.setStrokeColor(QColor(255, 140, 0))
        self.band.setFillColor(QColor(0, 0, 0, 0))  # zona: samo ivice
        self.band.setWidth(2)
        self.points = []
        self.done = False
        self.setCursor(Qt.CrossCursor)

    def reset(self):
        self.points, self.done = [], False
        self.band.reset(QgsWkbTypes.PolygonGeometry)

    def canvasMoveEvent(self, e):
        if self.points and not self.done:
            self._redraw(e.mapPoint())

    def canvasReleaseEvent(self, e):
        if self.done or e.button() != Qt.LeftButton:
            return
        if len(self.points) >= 3 and self._near_existing_point(e.pos()):
            return self._finish()
        self.points.append(e.mapPoint())
        self._redraw()

    def canvasDoubleClickEvent(self, e):
        # prvi klik iz duplog klika je već dodao tačku u canvasReleaseEvent
        if not self.done and len(self.points) >= 3:
            self._finish()

    def keyPressEvent(self, e):
        if self.done:
            return
        if e.key() in (Qt.Key_Backspace, Qt.Key_Delete) and self.points:
            self.points.pop()
            self._redraw()
        elif e.key() == Qt.Key_Escape:
            self.reset()

    def _near_existing_point(self, pos):
        for p in self.points:
            c = self.toCanvasCoordinates(p)
            if (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2 <= self.SNAP_PX ** 2:
                return True
        return False

    def _redraw(self, hover=None):
        pts = self.points + ([hover] if hover is not None else [])
        self.band.reset(QgsWkbTypes.PolygonGeometry)
        for i, p in enumerate(pts):
            self.band.addPoint(p, i == len(pts) - 1)

    def _finish(self):
        self.done = True
        self._redraw()
        self.finished.emit(QgsGeometry.fromPolygonXY([self.points]))


class ClickTool(QgsMapTool):
    """Samo javlja klikove na mapi: (tačka u CRS-u mape, da li je levi klik)."""

    clicked = pyqtSignal(QgsPointXY, bool)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, e):
        if e.button() in (Qt.LeftButton, Qt.RightButton):
            self.clicked.emit(e.mapPoint(), e.button() == Qt.LeftButton)

# Linija preseka (Split): levi klik dodaje tačku, dupli klik ili desni klik završava, Esc poništava.

class LineDrawTool(QgsMapTool):

    finished = pyqtSignal(QgsGeometry)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.band.setStrokeColor(QColor(220, 38, 38))
        self.band.setWidth(2)
        self.points = []
        self.setCursor(Qt.CrossCursor)

    def reset(self):
        self.points = []
        self.band.reset(QgsWkbTypes.LineGeometry)

    def canvasMoveEvent(self, e):
        if self.points:
            self._redraw(e.mapPoint())

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            return self._finish()
        if e.button() == Qt.LeftButton:
            self.points.append(e.mapPoint())
            self._redraw()

    def canvasDoubleClickEvent(self, e):
        self._finish()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reset()

    def _redraw(self, hover=None):
        self.band.reset(QgsWkbTypes.LineGeometry)
        for p in self.points + ([hover] if hover is not None else []):
            self.band.addPoint(p)

    def _finish(self):
        if len(self.points) >= 2:
            self.finished.emit(QgsGeometry.fromPolylineXY(self.points))
        self.reset()

#    Brzi pregled: tasteri dok je fokus na mapi. A/→ zadrži, D/Delete obriši, ← nazad, E ispravi, Esc kraj. Space ostaje QGIS-u (pomeranje mape)."""
class ReviewTool(QgsMapTool):


    action = pyqtSignal(str)
    KEYS = {Qt.Key_A: "keep", Qt.Key_Right: "keep", Qt.Key_D: "remove", Qt.Key_Delete: "remove",
            Qt.Key_Left: "back", Qt.Key_E: "edit", Qt.Key_Escape: "stop"}

    def keyPressEvent(self, e):
        act = self.KEYS.get(e.key())
        if act is None or e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            return e.ignore()
        e.accept()
        if not e.isAutoRepeat():  # držanje tastera ne sme da preleti 50 poligona
            self.action.emit(act)
