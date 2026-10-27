import json
import os
import tempfile

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea,
                       QgsFeature, QgsFeatureRequest, QgsFillSymbol, QgsGeometry, QgsJsonUtils, QgsMapLayerProxyModel, QgsPointXY, QgsProcessingAlgRunnerTask,
                       QgsProcessingContext, QgsProcessingFeedback, QgsProject, QgsRasterLayer, QgsSpatialIndex, QgsTask,
                       QgsUnitTypes, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QFont
from qgis.PyQt.QtGui import QDoubleValidator
from osgeo import ogr
from qgis.PyQt.QtCore import QPoint, QRect, QTimer
from qgis.PyQt.QtWidgets import (QFileDialog, QSizePolicy, QCheckBox, QComboBox, QDockWidget, QSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QProgressBar, QPushButton, QSlider, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout,
                                 QWidget)

from .api import Api, ApiError, AuthError, pricing_url
from .auth import LoginServer
from .map_tools import ClickTool, LineDrawTool, PolygonDrawTool, ReviewTool
from .styles import VIEWS, class_colors, result_labels, set_box_labels, style_results
from .pipeline import DetectTask, clip_raster, compress_rgb
from .tiling import Grid, snap_to_raster, tile_windows
from .theme import (APP_NAME, DANGER, QSS, SUCCESS, button, centered, hero, label, repolish, screen,
                    step_header, svg_pixmap)

ESRI_NAME = "Esri World Imagery"
ESRI_URI = ("type=xyz&zmin=0&zmax=19&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D")

LOGIN, WAITING, MODE, RASTER, DRAW, PROMPT, RESULTS, CORRECT, SHAPES, DET_PROMPT, DET_RESULTS = range(11)

# preciznost: pozicija slidera -> veličina tile-a (piksela rastera po jednoj slici modela)
PRECISION = [
    (2048, "Less — faster, best for large objects (fields, forests, lakes)"),
    (1024, "Balanced"),
    (512, "More — slower, best for small objects (cars, single trees)"),
]
BASEMAP_RES_M = 0.5  # rezolucija renderovanja podloge (m/px)
# izvoz: naziv -> (OGR drajver, ekstenzija, opcije sloja)
EXPORTS = {
    "GeoPackage (.gpkg)": ("GPKG", "gpkg", []),
    "Shapefile (.shp)": ("ESRI Shapefile", "shp", []),
    "AutoCAD DXF (.dxf)": ("DXF", "dxf", []),
    "CSV with WKT geometry (.csv)": ("CSV", "csv", ["GEOMETRY=AS_WKT"]),
}
MAX_EXAMPLES = 6      # toliko isečaka staje u traku sa primerima na serveru
GREEN, RED = SUCCESS, DANGER


WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
try:  # QGIS ≥ 3.24
    from qgis.core import Qgis
    CAP_FLAT, JOIN_MITER = Qgis.EndCapStyle.Flat, Qgis.JoinStyle.Miter
except (ImportError, AttributeError):
    CAP_FLAT, JOIN_MITER = QgsGeometry.CapFlat, QgsGeometry.JoinStyleMiter


def to_crs(geom, src, dst):
    g = QgsGeometry(geom)
    g.transform(QgsCoordinateTransform(src, dst, QgsProject.instance()))
    return g


def from_geojson(geometry):
    """GeoJSON geometrija (dict) -> QgsGeometry."""
    return QgsGeometry.fromWkt(ogr.CreateGeometryFromJson(json.dumps(geometry)).ExportToWkt())


def largest_part(g):
    parts = g.asGeometryCollection()
    return max(parts, key=lambda p: p.area()) if len(parts) > 1 else g


def regularize(g, square=True):
    """Organski oblik iz modela -> pravilan: bez rupa i mrvica, prave ivice, (opciono) pravi uglovi."""
    g = largest_part(g.makeValid().removeInteriorRings())
    if g.isEmpty():
        return g
    g = g.simplify(0.04 * g.area() ** 0.5)  # tolerancija srazmerna veličini objekta
    if square:
        sq = g.orthogonalize(1e-8, 1000, 20)
        if not sq.isEmpty():
            g = sq
    return g.makeValid()


def resolve_overlaps(geoms, scores):
    """Bez preklapanja: objekat sa većim skorom zadržava sporni deo, ostali dobijaju samo ostatak.
    geoms/scores: {fid: ...}; vraća {fid: nova geometrija} samo za one koje su se promenile."""
    index, final, changed = QgsSpatialIndex(), {}, {}
    for fid in sorted(geoms, key=lambda f: -(scores.get(f) or 0)):
        g = geoms[fid]
        if g.isEmpty():
            continue
        orig = g
        for other in index.intersects(g.boundingBox()):
            if g.intersects(final[other]):
                g = g.difference(final[other])
        if g is not orig:
            g = largest_part(g.makeValid()) if not g.isEmpty() else g
            changed[fid] = g
        final[fid] = g
        if not g.isEmpty():
            index.addFeature(fid, g.boundingBox())
    return changed


MAX_PROMPTS = 5


def parse_prompts(text):
    """\"house, tree\" -> [\"house\", \"tree\"]: razdvaja zarezom, bez praznih i duplikata, najviše MAX_PROMPTS."""
    out = []
    for p in text.split(","):
        p = p.strip()
        if p and p.lower() not in (q.lower() for q in out):
            out.append(p)
    return out[:MAX_PROMPTS]


def short(text, n=40):
    return text if len(text) <= n else text[:n - 1] + "…"


def note(text=""):
    return label(text, center=True)


class SamSegDock(QDockWidget):
    def __init__(self, iface):
        super().__init__(APP_NAME)
        self.setObjectName("SamSegDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.api = Api()
        self.me = None
        self.raster = None
        self.roi = None  # QgsGeometry u CRS-u mape
        self.prev_tool = None
        self.draw_tool = PolygonDrawTool(self.canvas)
        self.draw_tool.finished.connect(self.on_zone_drawn)
        self.grid_band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)  # linije podele unutar zone
        self.grid_band.setStrokeColor(QColor(0, 220, 255))
        self.grid_band.setLineStyle(Qt.DashLine)
        self.grid_band.setWidth(1)
        self._task = None
        self.result_layer = None
        self.result_labels = []
        self.result_total = 0
        self.job_id = None
        self.prompt = ""        # prva klasa: labela ručno dodatih poligona
        self.prompt_title = ""  # svi promptovi spojeni, za imena slojeva i fajlova
        self.kind = "segmentation"  # "segmentation" | "detection"
        self.det_layer, self.det_labels, self.det_total, self.det_title = None, [], 0, ""
        self.mode = None       # None | "edit" | "add"
        self.busy = False
        self.edit_fid = None   # poligon koji se ispravlja
        self.edit_points = []  # [(QgsPointXY u CRS-u mape, 1|0)]
        self.preview = None    # predlog nove geometrije (CRS sloja)
        self.last_add = None   # poslednji dodat objekat: {"fid", "geom" (CRS sloja), "points"}
        self.manual_on = False    # otvorena ručna sesija (edit bafer sloja)
        self.manual_tool = None   # None | "move" | "delete" | "merge" | "split"
        self.merge_first = None
        self.line_tool = LineDrawTool(self.canvas)
        self.line_tool.finished.connect(self.on_split_line)
        self.review_tool = ReviewTool(self.canvas)
        self.review_tool.action.connect(self.review_action)
        self.review_tool.deactivated.connect(self.stop_review)  # korisnik uzeo drugi QGIS alat
        self.reviewing = False
        self.review_fids, self.review_i, self.review_removed = [], 0, 0  # redosled: od najslabijeg skora
        self.examples = []     # [{"geom": QgsGeometry (CRS mape), "positive": bool, "band", "pix"}]
        self.example_kind = None  # None | True (primer) | False (look-alike koji ne želimo)
        self.example_tool = PolygonDrawTool(self.canvas)
        self.example_tool.finished.connect(self.on_example_drawn)
        self.click_tool = ClickTool(self.canvas)
        self.click_tool.clicked.connect(self.on_map_click)
        self.sel_band = self._band(QgsWkbTypes.PolygonGeometry, QColor(255, 215, 0), QColor(0, 0, 0, 0), 2)
        self.preview_band = self._band(QgsWkbTypes.PolygonGeometry, QColor(0, 200, 83), QColor(0, 200, 83, 70), 2)
        self.pos_band = self._band(QgsWkbTypes.PointGeometry, QColor(0, 200, 83), QColor(0, 200, 83), 2)
        self.neg_band = self._band(QgsWkbTypes.PointGeometry, QColor(229, 57, 53), QColor(229, 57, 53), 2)

        self.login = LoginServer(self)
        self.login.token_received.connect(self.on_token)
        self.login.failed.connect(lambda msg: self.show_login(msg))

        root = QWidget()
        root.setObjectName("GeoSegRoot")
        root.setStyleSheet(QSS)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)
        lay.addWidget(self._build_header())
        self.pages = QStackedWidget()
        lay.addWidget(self.pages)
        for build in (self._page_login, self._page_waiting, self._page_mode, self._page_raster, self._page_draw,
                      self._page_prompt, self._page_results, self._page_correct, self._page_shapes,
                      self._page_det_prompt, self._page_det_results):
            self.pages.addWidget(build())

        # View detection: stalno dole, na svim koracima posle detekcije
        self.view_box = QGroupBox("View detection")
        self.view_box.setToolTip("How detected polygons are drawn on the map.")
        self.view_cb = QComboBox()
        self.view_cb.addItems(VIEWS)
        self.view_cb.currentTextChanged.connect(
            lambda v: self.result_layer and style_results(self.result_layer, v, self.result_labels))
        QVBoxLayout(self.view_box).addWidget(self.view_cb)
        lay.addWidget(self.view_box)
        self.pages.currentChanged.connect(self.on_page_changed)
        self.on_page_changed(self.pages.currentIndex())
        self.setWidget(root)

        self.refresh_session()

    #  izgled

    def _band(self, kind, stroke, fill, width):
        b = QgsRubberBand(self.canvas, kind)
        b.setStrokeColor(stroke)
        b.setFillColor(fill)
        b.setWidth(width)
        if kind == QgsWkbTypes.PointGeometry:
            b.setIcon(QgsRubberBand.ICON_CIRCLE)
            b.setIconSize(10)
        return b

    def _build_header(self):
        self.header = QWidget()
        self.header.setObjectName("Header")
        v = QVBoxLayout(self.header)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(svg_pixmap("logo", "#2563EB", 22))
        names = QVBoxLayout()
        names.setSpacing(0)
        brand = label(APP_NAME, "Brand")
        brand.setWordWrap(False)
        names.addWidget(brand)
        self.user_lbl = label("", "User")
        names.addWidget(self.user_lbl)
        row.addWidget(logo)
        row.addLayout(names, 1)
        row.addWidget(button("Upgrade to Pro", "link", self.open_pricing))
        row.addWidget(button("Sign out", "ghost", self.logout))
        v.addLayout(row)

        self.quota_bar = QProgressBar()  # potrošnja: 0 -> kvota (km²)
        v.addWidget(self.quota_bar)
        return self.header

    def update_quota(self):
        used, quota = self.me["km2_used"], self.me["km2_quota"]
        self.quota_bar.setRange(0, max(1, int(quota * 1000)))
        self.quota_bar.setValue(min(int(used * 1000), int(quota * 1000)))
        self.quota_bar.setFormat(f"{used:.2f} / {quota:g} km² used")
        self.quota_bar.setProperty("level", "ok" if used < 0.8 * quota else "warn" if used < quota else "full")
        repolish(self.quota_bar)

    def open_pricing(self, *_):
        QDesktopServices.openUrl(QUrl(pricing_url()))

    def _page_login(self):
        self.login_msg = label("", "Error", center=True)
        return centered(
            hero(APP_NAME, "AI segmentation of rasters and basemaps.\nDraw a zone, describe it, get polygons."),
            button("Sign in / Sign up", "primary", self.start_login),
            self.login_msg)

    def _page_waiting(self):
        return centered(
            step_header("browser", None, "Waiting for you in the browser",
                        "Sign in or create an account there — you'll be brought back here automatically."),
            button("Open browser again", None, self.start_login),
            button("Cancel", "ghost", lambda: self.show_login()))

    def _page_mode(self):
        def card(title, text, kind):
            box = QGroupBox(title)
            v = QVBoxLayout(box)
            v.addWidget(label(text, "Muted"))
            v.addWidget(button(title, "primary", lambda: self.choose_mode(kind)))
            return box
        return screen(
            step_header("sparkle", None, "What do you want to do?", "Pick a mode. You can switch at any time."),
            [card("Segmentation", "Precise outlines (polygons) of objects. Describe or show what to find, "
                                  "then correct, reshape and export.", "segmentation"),
             card("Detection", "Find and count objects by text. Draws a box around each one, with a "
                               "confidence score.", "detection")])

    def _page_raster(self):
        self.raster_cb = QgsMapLayerComboBox()
        self.raster_cb.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.raster_cb.setAdditionalItems([ESRI_NAME])  # uvek dostupno, podrazumevano kad nema rastera
        box = QGroupBox("Select a raster")
        QVBoxLayout(box).addWidget(self.raster_cb)

        self.no_quota = QWidget()  # prikazuje se kad nema preostalih km²
        nq = QVBoxLayout(self.no_quota)
        nq.setContentsMargins(0, 4, 0, 0)
        self.no_quota_lbl = label("", "Muted", center=True)
        nq.addWidget(self.no_quota_lbl)
        nq.addWidget(button("Upgrade to Pro", "upgrade", self.open_pricing))
        return screen(
            step_header("layers", 1, "Choose imagery", "Use a raster from your project, or Esri World Imagery."),
            [box, self.no_quota],
            [button("Next", "primary", self.start_segmentation),
             button("Change mode", "ghost", self.show_mode_page)])

    def _page_draw(self):
        self.zone_lbl = label("", "Status", center=True)
        self.next_btn = button("Next", "primary", self.on_next)
        self.next_btn.setEnabled(False)
        self.upgrade_btn = button("Upgrade to Pro", "upgrade", self.open_pricing)
        self.upgrade_btn.hide()
        tips = QGroupBox("How to draw")
        QVBoxLayout(tips).addWidget(label(
            "• Click on the map to add points\n"
            "• Double-click, or click an existing point, to finish\n"
            "• Backspace removes the last point, Esc starts over", "Muted"))
        row = QHBoxLayout()
        row.addWidget(button("Back", "ghost", self.back_to_raster))
        row.addStretch()
        row.addWidget(button("Redraw", None, self.redraw_zone))
        return screen(
            step_header("draw", 2, "Draw your zone", "Outline the area you want to segment."),
            [tips, self.zone_lbl, self.upgrade_btn],
            [self.next_btn, row])

    def _page_prompt(self):
        text_box = QGroupBox("Textual describe")
        self.prompt_edit = QLineEdit()
        self.prompt_edit.setPlaceholderText('e.g. "house", or several: "house, tree, swimming pool"')
        self.prompt_edit.setToolTip(f"Separate with commas to detect up to {MAX_PROMPTS} things in one run.")
        self.prompt_edit.setMaxLength(200)
        QVBoxLayout(text_box).addWidget(self.prompt_edit)

        self.second_box = QGroupBox("Show what it looks like")
        sv = QVBoxLayout(self.second_box)
        brow = QVBoxLayout()  # jedno ispod drugog: pun tekst i u uskom panelu
        brow.setSpacing(6)
        self.draw_ex_btn = button("Drawing", "toggle", lambda on: self.set_example_kind(True if on else None), True)
        self.exclude_btn = button("Exclude a look-alike", "toggle-danger",
                                  lambda on: self.set_example_kind(False if on else None), True)
        brow.addWidget(self.draw_ex_btn)
        brow.addWidget(self.exclude_btn)
        sv.addLayout(brow)
        self.example_hint = label("Click points around one object, then double-click to close.", "Muted", True)
        sv.addWidget(self.example_hint)
        self.thumbs = QHBoxLayout()
        self.thumbs.setSpacing(6)
        thumbs_w = QWidget()
        thumbs_w.setLayout(self.thumbs)
        sv.addWidget(thumbs_w)

        precision_box = QGroupBox("Precision")
        pv = QVBoxLayout(precision_box)
        self.precision = QSlider(Qt.Horizontal)
        self.precision.setRange(0, len(PRECISION) - 1)
        self.precision.setValue(1)
        self.precision.setCursor(Qt.PointingHandCursor)
        self.precision_lbl = label(PRECISION[1][1], "Muted", True)
        self.precision.valueChanged.connect(self.update_grid)
        self.tiles_lbl = label("", center=True)
        ends = QHBoxLayout()
        ends.addWidget(label("Less", "Muted"))
        ends.addStretch()
        ends.addWidget(label("More", "Muted"))
        pv.addWidget(self.precision)
        pv.addLayout(ends)
        pv.addWidget(self.precision_lbl)
        pv.addWidget(self.tiles_lbl)
        self.merge_cb = QCheckBox("Merge touching objects")
        self.merge_cb.setChecked(True)
        self.merge_cb.setToolTip("Joins pieces of the same object (e.g. a roof split in two). "
                                 "Turn off when counting objects that touch, like row houses.")
        pv.addWidget(self.merge_cb)

        self.detect_btn = button("Detect", "primary", self.detect)
        self.detect_lbl = label("", "Status", center=True)
        self.detect_bar = QProgressBar()
        self.detect_bar.setTextVisible(True)
        self.detect_bar.setFormat("%p%")
        self.detect_bar.setAlignment(Qt.AlignCenter)
        self.detect_bar.hide()
        self.found_lbl = label("", "Status", center=True)
        self._seg_ui = dict(prompt_edit=self.prompt_edit, precision=self.precision, precision_lbl=self.precision_lbl,
                            tiles_lbl=self.tiles_lbl, merge_cb=self.merge_cb, detect_btn=self.detect_btn,
                            detect_lbl=self.detect_lbl, detect_bar=self.detect_bar, found_lbl=self.found_lbl)
        return screen(
            step_header("sparkle", 3, "Describe or show", "Type what to find, draw a few examples — or both."),
            [text_box, self.second_box, precision_box],
            [self.detect_lbl, self.detect_bar, self.found_lbl, self.detect_btn,
             button("Back", "ghost", self.back_to_draw)])

    def _page_results(self):
        self.count_lbl = label("", "Count", center=True)

        conf_box = QGroupBox("Confidence")
        cv = QVBoxLayout(conf_box)
        self.conf = QSlider(Qt.Horizontal)
        self.conf.setRange(10, 90)
        self.conf.setValue(50)
        self.conf.setCursor(Qt.PointingHandCursor)
        self.conf_lbl = label("", "Muted", center=True)
        self.conf.valueChanged.connect(self.apply_filters)
        cv.addWidget(self.conf)
        cv.addWidget(self.conf_lbl)

        size_box = QGroupBox("Size (m²)")
        form = QFormLayout(size_box)
        self.min_area = QLineEdit("15")
        self.max_area = QLineEdit()
        self.max_area.setPlaceholderText("No limit")
        for w in (self.min_area, self.max_area):
            v = QDoubleValidator(0, 1e9, 1, w)
            v.setNotation(QDoubleValidator.StandardNotation)
            w.setValidator(v)
            w.textChanged.connect(self.apply_filters)
        form.addRow("Min", self.min_area)
        form.addRow("Max", self.max_area)
        self.legend_lbl = label("", "Muted", center=True)
        self._seg_ui["conf"] = self.conf
        return screen(
            step_header("sliders", 4, "Review results", "Filter out weak or wrongly sized detections."),
            [self.count_lbl, label("objects visible", "Muted", True), self.legend_lbl, conf_box, size_box],
            [button("Next (correction)", "primary", lambda: self.pages.setCurrentIndex(CORRECT)),
             button("New detection", "ghost", self.new_detection)])

    def _page_correct(self):
        # AI: ispravka postojećeg poligona
        edit_box = QGroupBox("Edit polygon")
        ev = QVBoxLayout(edit_box)
        ev.addWidget(label("Click the polygon, then click the spot where the AI missed. "
                           "Right-click marks a spot that doesn't belong.", "Muted"))
        self.edit_btn = button("Edit a polygon", "toggle-primary",
                               lambda on: self.set_mode("edit" if on else None), True)
        ev.addWidget(self.edit_btn)
        row = QHBoxLayout()
        self.save_btn = button("Save", "success", self.save_edit)
        self.undo_btn = button("Undo", None, self.undo_edit)
        row.addWidget(self.save_btn)
        row.addWidget(self.undo_btn)
        ev.addLayout(row)

        # --- AI: dodavanje objekta koji fali
        add_box = QGroupBox("Add missing polygon")
        av = QVBoxLayout(add_box)
        av.addWidget(label("One click detection per object. Click near it again to extend it.", "Muted"))
        self.add_btn = button("Point at it on the map", None, lambda: self.set_mode("add"))
        self.stop_add_btn = button("Stop adding", "active", lambda: self.set_mode(None))
        av.addWidget(self.add_btn)
        av.addWidget(self.stop_add_btn)

        self.regular_cb = QCheckBox("Regular shapes (buildings)")
        self.regular_cb.setChecked(True)
        self.regular_cb.setToolTip("AI corrections come back straightened, with right angles and no holes. "
                                   "Turn off for trees, fields and other natural shapes.")
        self.ai_status = label("", "Status", center=True)
        ai = QWidget()
        al = QVBoxLayout(ai)
        al.setContentsMargins(0, 6, 0, 0)
        al.addWidget(edit_box)
        al.addWidget(add_box)
        al.addWidget(self.regular_cb)
        al.addWidget(self.ai_status)
        al.addStretch()

        # pomeranje tačaka, brisanje, spajanje, deljenje
        manual = QWidget()
        mv = QVBoxLayout(manual)
        mv.setContentsMargins(0, 6, 0, 0)
        tools_box = QGroupBox("Edit by hand")
        bv = QVBoxLayout(tools_box)
        self.manual_btns = {}
        grid = QHBoxLayout()
        col1, col2 = QVBoxLayout(), QVBoxLayout()
        for i, (tool, text) in enumerate((("move", "Move points"), ("delete", "Delete"),
                                          ("merge", "Merge"), ("split", "Split"))):
            b = button(text, "toggle-primary", lambda on, t=tool: self.set_manual_tool(t if on else None), True)
            self.manual_btns[tool] = b
            (col1 if i % 2 == 0 else col2).addWidget(b)
        grid.addLayout(col1)
        grid.addLayout(col2)
        bv.addLayout(grid)
        self.manual_hint = label("Pick a tool.", "Muted", center=True)
        bv.addWidget(self.manual_hint)
        mrow = QHBoxLayout()
        self.manual_save = button("Save", "success", lambda: self.finish_manual(save=True))
        self.manual_undo = button("Undo", None, self.undo_manual)
        self.manual_cancel = button("Cancel", "ghost", lambda: self.finish_manual(save=False))
        for b in (self.manual_save, self.manual_undo, self.manual_cancel):
            mrow.addWidget(b)
            b.hide()
        bv.addLayout(mrow)
        mv.addWidget(tools_box)
        mv.addStretch()

        # Review: poligon po poligon, od najslabijeg skora
        review = QWidget()
        rv = QVBoxLayout(review)
        rv.setContentsMargins(0, 6, 0, 0)
        rbox = QGroupBox("Quick review")
        rb = QVBoxLayout(rbox)
        rb.addWidget(label("Goes through polygons from the least to the most confident. "
                           "Stop when the rest look fine.", "Muted"))
        self.review_btn = button("Start review", "primary", self.start_review)
        rb.addWidget(self.review_btn)
        self.review_info = label("", "Status", center=True)
        rb.addWidget(self.review_info)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        self.review_btns = [
            button("Keep (A)", "success", lambda: self.review_action("keep")),
            button("Remove (D)", "danger", lambda: self.review_action("remove")),
            button("← Back", None, lambda: self.review_action("back")),
            button("Edit (E)", None, lambda: self.review_action("edit")),
            button("Stop (Esc)", "ghost", lambda: self.review_action("stop")),
        ]
        for i, b in enumerate(self.review_btns):
            (row1 if i < 2 else row2).addWidget(b)
            b.hide()
        rb.addLayout(row1)
        rb.addLayout(row2)
        self.review_keys = label("Keys work while the map has focus: A / → keep · D / Del remove · "
                                 "← back · E edit · Esc stop", "Muted", center=True)
        self.review_keys.hide()
        rb.addWidget(self.review_keys)
        rv.addWidget(rbox)
        rv.addStretch()

        tabs = QTabWidget()
        tabs.addTab(ai, "AI")
        tabs.addTab(manual, "Manual")
        tabs.addTab(review, "Review")
        tabs.currentChanged.connect(lambda _: (self.stop_review(), self.set_mode(None), self.finish_manual(save=True)))
        self.correct_tabs = tabs
        w = screen(
            step_header("wand", 5, "Correct", "Fix what the AI missed — with clicks or by hand."),
            [tabs],
            [button("Next (shapes)", "primary", self.enter_shapes), button("Back", "ghost", self.leave_correct)])
        self.set_mode(None)
        return w

    def _page_shapes(self):
        box = QGroupBox("Shapes")
        form = QFormLayout(box)
        form.setVerticalSpacing(10)
        self.right_angles = QCheckBox("Right angles")
        self.fill_holes = QCheckBox("Fill holes")
        self.fill_holes.setToolTip("Fills holes inside polygons and removes small detached specks.")
        self.grow = QSpinBox()
        self.grow.setRange(-20, 20)
        self.grow.setSuffix(" px")
        self.points = QSlider(Qt.Horizontal)
        self.points.setRange(0, 100)
        self.points.setCursor(Qt.PointingHandCursor)
        self.points_lbl = label("0%", "Muted")
        prow = QHBoxLayout()
        prow.addWidget(self.points)
        prow.addWidget(self.points_lbl)
        form.addRow(self.right_angles)
        form.addRow(self.fill_holes)
        form.addRow("Grow / shrink", self.grow)
        form.addRow("Points", prow)

        # debounce: preračun tek kad korisnik stane
        self.shape_timer = QTimer(self)
        self.shape_timer.setSingleShot(True)
        self.shape_timer.setInterval(250)
        self.shape_timer.timeout.connect(self.apply_shapes)
        self.right_angles.toggled.connect(self.shape_timer.start)
        self.fill_holes.toggled.connect(self.shape_timer.start)
        self.grow.valueChanged.connect(self.shape_timer.start)
        self.points.valueChanged.connect(lambda v: (self.points_lbl.setText(f"{v}%"), self.shape_timer.start()))
        export_box = QGroupBox("Export")
        ev = QVBoxLayout(export_box)
        self.export_cb = QComboBox()
        self.export_cb.addItems(list(EXPORTS))
        ev.addWidget(self.export_cb)
        ev.addWidget(label("Exports the polygons you see (filters and shapes applied).", "Muted"))
        return screen(
            step_header("shapes", 6, "Refine shapes", "Straighten, clean and resize all polygons, then export."),
            [box, export_box],
            [button("Export…", "primary", self.export_results), button("Back", "ghost", self.leave_shapes)])

    #  detekcija (okviri)

    def _page_det_prompt(self):
        text_box = QGroupBox("What to detect")
        edit = QLineEdit()
        edit.setPlaceholderText('e.g. "house", or several: "house, tree, swimming pool"')
        edit.setToolTip(f"Separate with commas to detect up to {MAX_PROMPTS} things in one run.")
        edit.setMaxLength(200)
        QVBoxLayout(text_box).addWidget(edit)

        precision_box = QGroupBox("Precision")
        pv = QVBoxLayout(precision_box)
        precision = QSlider(Qt.Horizontal)
        precision.setRange(0, len(PRECISION) - 1)
        precision.setValue(1)
        precision.setCursor(Qt.PointingHandCursor)
        precision_lbl = label(PRECISION[1][1], "Muted", True)
        precision.valueChanged.connect(self.update_grid)
        tiles_lbl = label("", center=True)
        ends = QHBoxLayout()
        ends.addWidget(label("Less", "Muted"))
        ends.addStretch()
        ends.addWidget(label("More", "Muted"))
        pv.addWidget(precision)
        pv.addLayout(ends)
        pv.addWidget(precision_lbl)
        pv.addWidget(tiles_lbl)

        detect_btn = button("Detect", "primary", self.detect)
        detect_lbl = label("", "Status", center=True)
        detect_bar = QProgressBar()
        detect_bar.setTextVisible(True)
        detect_bar.setFormat("%p%")
        detect_bar.setAlignment(Qt.AlignCenter)
        detect_bar.hide()
        found_lbl = label("", "Status", center=True)
        self._det_ui = dict(prompt_edit=edit, precision=precision, precision_lbl=precision_lbl, tiles_lbl=tiles_lbl,
                            merge_cb=QCheckBox(), detect_btn=detect_btn, detect_lbl=detect_lbl,
                            detect_bar=detect_bar, found_lbl=found_lbl)  # merge_cb: nevidljiv, uvek isključen
        return screen(
            step_header("sparkle", 3, "Detect objects", "Type what to find. Each object gets a box."),
            [text_box, precision_box],
            [detect_lbl, detect_bar, found_lbl, detect_btn, button("Back", "ghost", self.back_to_draw)])

    def _page_det_results(self):
        self.d_count_lbl = label("", "Count", center=True)
        self.d_legend_lbl = label("", "Muted", center=True)
        conf_box = QGroupBox("Confidence")
        cv = QVBoxLayout(conf_box)
        self.d_conf = QSlider(Qt.Horizontal)
        self.d_conf.setRange(10, 90)
        self.d_conf.setValue(50)
        self.d_conf.setCursor(Qt.PointingHandCursor)
        self.d_conf_lbl = label("", "Muted", center=True)
        self.d_conf.valueChanged.connect(self.apply_det_filters)
        cv.addWidget(self.d_conf)
        cv.addWidget(self.d_conf_lbl)
        self._det_ui["conf"] = self.d_conf
        self.d_labels_cb = QCheckBox("Show class and confidence on the map")
        self.d_labels_cb.setChecked(True)
        self.d_labels_cb.toggled.connect(self.apply_det_labels)
        export_box = QGroupBox("Export")
        ev = QVBoxLayout(export_box)
        self.d_export_cb = QComboBox()
        self.d_export_cb.addItems(list(EXPORTS))
        ev.addWidget(self.d_export_cb)
        ev.addWidget(label("Exports the boxes you see (confidence filter applied).", "Muted"))
        return screen(
            step_header("sliders", 4, "Detection results", "Raise the confidence to drop weak detections."),
            [self.d_count_lbl, label("objects visible", "Muted", True), self.d_legend_lbl, conf_box,
             self.d_labels_cb, export_box],
            [button("Export…", "primary", self.export_results),
             button("New detection", "ghost", self.new_detection_boxes),
             button("Change mode", "ghost", self.show_mode_page)])

    def use_ui(self, kind):
        """Isti kod za crtanje/slanje radi za oba moda: samo se prebace widgeti aktivne stranice."""
        for name, w in (self._det_ui if kind == "detection" else self._seg_ui).items():
            setattr(self, name, w)

    def show_mode_page(self):
        self.stop_drawing()
        self.grid_band.reset(QgsWkbTypes.LineGeometry)
        self.pages.setCurrentIndex(MODE)

    def choose_mode(self, kind):
        self.kind = kind
        self.use_ui(kind)
        self.show_raster_page()

    def apply_det_filters(self, *_):
        c = self.d_conf.value()
        self.d_conf_lbl.setText(f"≥ {c}%")
        lyr = self.det_layer
        if lyr is None:
            return
        lyr.setSubsetString(f'"score" >= {c / 100:.2f}')
        lyr.triggerRepaint()
        self.d_count_lbl.setText(f"{lyr.featureCount()} of {self.det_total}")
        self.update_legend(lyr, self.det_labels, self.d_legend_lbl)

    def apply_det_labels(self, *_):
        if self.det_layer is not None:
            set_box_labels(self.det_layer, self.d_labels_cb.isChecked())

    def on_det_removed(self):
        self.det_layer = None  # korisnik je obrisao sloj iz projekta

    def new_detection_boxes(self):
        self.det_layer = None  # prethodni rezultat ostaje u projektu sa svojim filterom
        self.pages.setCurrentIndex(DET_PROMPT)
        self.update_grid()

    def on_boxes_detected(self, names):
        self.end_detect_ui()
        title = ", ".join(names)
        lyr = QgsVectorLayer(f"{self._task.result_path}|layername=segments", f"GeoDetect: {short(title)}", "ogr")
        self.refresh_quota()
        self.update_grid()
        if not lyr.isValid() or lyr.featureCount() == 0:
            return self.detect_lbl.setText(f"<span style='color:{RED}'>Nothing found. Try another description "
                                           "or change the precision.</span>")
        self.det_labels = result_labels(lyr)
        style_results(lyr, "Outline", self.det_labels)  # okviri: samo kontura, boja po klasi
        QgsProject.instance().addMapLayer(lyr)
        lyr.willBeDeleted.connect(self.on_det_removed)
        self.det_layer, self.det_total, self.det_title = lyr, lyr.featureCount(), title
        self.detect_lbl.setText("")
        self.grid_band.reset(QgsWkbTypes.LineGeometry)
        self.pages.setCurrentIndex(DET_RESULTS)
        self.apply_det_filters()
        self.apply_det_labels()

    # prijava

    def refresh_session(self):
        if not self.api.token:
            return self.show_login()
        try:
            self.me = self.api.me()
            if self.me["km2_quota"] == 0:  # nov nalog -> pokušaj da preuzme besplatni paket
                self.api.claim_free()
                self.me = self.api.me()
        except AuthError:
            self.api.set_token("")
            return self.show_login("Your session has expired, please sign in again.")
        except ApiError as e:
            return self.show_login(str(e))
        self.user_lbl.setText(f"<b>{self.me['email']}</b>")
        self.update_quota()
        self.header.show()
        self.show_mode_page()
        self.warmup()

    def show_login(self, msg=""):
        self.login.stop()
        self.header.hide()
        self.login_msg.setText(msg)
        self.pages.setCurrentIndex(LOGIN)

    def start_login(self):
        try:
            url = self.login.start()
        except RuntimeError as e:
            return self.show_login(str(e))
        QDesktopServices.openUrl(QUrl(url))
        self.pages.setCurrentIndex(WAITING)

    def on_token(self, token):
        self.api.set_token(token)
        self.refresh_session()
        self.raise_()  # vrati panel u fokus posle browsera

    def logout(self):
        self.finish_manual(save=True)
        self.clear_examples()
        self.set_mode(None)
        self.stop_drawing()
        self.api.set_token("")
        self.me = None
        self.show_login()

    def warmup(self):
        # GPU se pali u pozadini dok korisnik bira raster i crta zonu."""
        self._task = QgsTask.fromFunction("GeoSegment: starting model", lambda task: self.api.warmup())
        QgsApplication.taskManager().addTask(self._task)

    #  raster

    def show_raster_page(self):
        out = self.me["km2_left"] <= 0
        self.no_quota.setVisible(out)
        if out:
            self.no_quota_lbl.setText(
                "Your free trial has been used." if self.me["km2_quota"] > 0 else
                "The free trial has already been used on this computer.")
        if self.raster_cb.currentLayer() is None:
            self.raster_cb.setCurrentIndex(self.raster_cb.count() - 1)  # Esri World Imagery
        self.pages.setCurrentIndex(RASTER)

    def esri_layer(self):
        for lyr in QgsProject.instance().mapLayersByName(ESRI_NAME):
            if isinstance(lyr, QgsRasterLayer):
                return lyr
        lyr = QgsRasterLayer(ESRI_URI, ESRI_NAME, "wms")
        if not lyr.isValid():
            return None
        QgsProject.instance().addMapLayer(lyr, False)
        QgsProject.instance().layerTreeRoot().addLayer(lyr)  # na dno liste slojeva
        return lyr

    def start_segmentation(self):
        if self.raster is not None:
            try:
                self.raster.willBeDeleted.disconnect(self.on_raster_removed)
            except (TypeError, RuntimeError):
                pass
        self.raster = self.raster_cb.currentLayer() or self.esri_layer()
        if self.raster is None:
            return self.iface.messageBar().pushWarning(APP_NAME, "Could not load Esri World Imagery")
        self.raster.willBeDeleted.connect(self.on_raster_removed)
        self.redraw_zone()
        self.pages.setCurrentIndex(DRAW)

    def on_raster_removed(self):
        # sloj se briše iz projekta: ne smemo da zadržimo referencu na njega
        self.raster = None
        self.roi = None
        self.stop_drawing()
        self.pages.setCurrentIndex(RASTER)
        self.iface.messageBar().pushWarning(APP_NAME, "The selected raster was removed from the project.")

    def back_to_raster(self):
        self.stop_drawing()
        self.pages.setCurrentIndex(RASTER)

    # zona (ROI)

    def redraw_zone(self):
        self.clear_examples()  # primeri važe samo za zonu u kojoj su nacrtani
        self.grid_band.reset(QgsWkbTypes.LineGeometry)
        self.roi = None
        self.zone_lbl.setText("")
        self.next_btn.setEnabled(False)
        self.upgrade_btn.hide()
        self.draw_tool.reset()
        self.activate_tool(self.draw_tool)
        self.canvas.setFocus()  # da Backspace/Esc stignu do alata

    def activate_tool(self, tool):
        if self.canvas.mapTool() not in (self.draw_tool, self.click_tool, self.example_tool, self.line_tool, self.review_tool):
            self.prev_tool = self.canvas.mapTool()  # korisnikov alat, vraćamo ga posle
        self.canvas.setMapTool(tool)

    def stop_drawing(self):
        self.grid_band.reset(QgsWkbTypes.LineGeometry)
        self.draw_tool.reset()
        self.release_tool()

    def release_tool(self):
        # Vrati korisnikov prethodni alat; nacrtana zona ostaje vidljiva
        tool = self.canvas.mapTool()
        if tool in (self.draw_tool, self.click_tool, self.example_tool, self.line_tool, self.review_tool):
            if self.prev_tool:
                self.canvas.setMapTool(self.prev_tool)
            else:
                self.canvas.unsetMapTool(tool)

    def on_zone_drawn(self, geom):
        if self.raster is None:
            return
        if not geom.isGeosValid():
            self.zone_lbl.setText("<span style='color:#DC2626'>The zone crosses itself. Please redraw it.</span>")
            return

        project = QgsProject.instance()
        map_crs = self.canvas.mapSettings().destinationCrs()
        if self.raster.name() != ESRI_NAME:
            tr = QgsCoordinateTransform(self.raster.crs(), map_crs, project)
            if not QgsGeometry.fromRect(tr.transformBoundingBox(self.raster.extent())).intersects(geom):
                self.zone_lbl.setText("<span style='color:#DC2626'>The zone is outside the selected raster.</span>")
                return

        da = QgsDistanceArea()
        da.setSourceCrs(map_crs, project.transformContext())
        da.setEllipsoid(project.ellipsoid() if project.ellipsoid() != "NONE" else "WGS84")
        km2 = da.convertAreaMeasurement(da.measureArea(geom), QgsUnitTypes.AreaSquareKilometers)

        left = self.me["km2_left"]
        if km2 > left:
            self.zone_lbl.setText(f"<span style='color:#DC2626'>Zone: {km2:.3f} km² — you have only "
                                  f"{left:.2f} km² left.</span>")
            self.upgrade_btn.show()
            return
        self.roi = geom
        self.zone_lbl.setText(f"Zone: <b>{km2:.3f} km²</b> (you have {left:.2f} km² left)")
        self.next_btn.setEnabled(True)

    def on_next(self):
        self.release_tool()  # zona ostaje na mapi, korisnik može da pomera mapu
        self.pages.setCurrentIndex(DET_PROMPT if self.kind == "detection" else PROMPT)
        self.update_grid()
        self.prompt_edit.setFocus()

    # primeri ("Show what it looks like")

    def set_example_kind(self, kind):
       #True = crtaj primer, False = crtaj look-alike koji ne želimo, None = ne crtaj.
        self.example_kind = kind
        for btn, on, text, color in ((self.draw_ex_btn, kind is True, "Drawing (click to stop)", GREEN),
                                     (self.exclude_btn, kind is False, "Excluding (click to stop)", RED)):
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.blockSignals(False)
            btn.setText(text if on else ("Drawing" if btn is self.draw_ex_btn else "Exclude a look-alike"))
        self.example_tool.reset()
        if kind is None:
            if self.canvas.mapTool() is self.example_tool:
                self.release_tool()
        else:
            self.activate_tool(self.example_tool)
            self.canvas.setFocus()

    def on_example_drawn(self, geom):
        msg = None
        if not geom.isGeosValid():
            msg = "The shape crosses itself, draw it again."
        elif not self.roi.contains(geom):
            msg = "Draw the example inside your zone."
        elif len(self.examples) >= MAX_EXAMPLES:
            msg = f"You can add up to {MAX_EXAMPLES} examples."
        if msg:
            self.detect_lbl.setText(f"<span style='color:{RED}'>{msg}</span>")
            return self.example_tool.reset()

        pix = self.grab_thumbnail(geom)  # pre reseta, dok se oblik još vidi na mapi
        band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        band.setStrokeColor(QColor(GREEN if self.example_kind else RED))
        band.setFillColor(QColor(0, 0, 0, 0))
        band.setWidth(2)
        band.setToGeometry(geom, self.canvas.mapSettings().destinationCrs())
        self.examples.append({"geom": geom, "positive": self.example_kind, "band": band, "pix": pix})
        self.example_tool.reset()  # spremno za sledeći primer
        self.detect_lbl.setText("")
        self.refresh_thumbs()

    def grab_thumbnail(self, geom):
        # Isečak sa mape oko primera (kvadrat, sa malo okoline).
        m2p, b = self.canvas.getCoordinateTransform(), geom.boundingBox()
        a, c = m2p.transform(b.xMinimum(), b.yMaximum()), m2p.transform(b.xMaximum(), b.yMinimum())
        cx, cy = (a.x() + c.x()) / 2, (a.y() + c.y()) / 2
        half = max(abs(c.x() - a.x()), abs(c.y() - a.y())) / 2 + 8
        rect = QRect(QPoint(int(cx - half), int(cy - half)), QPoint(int(cx + half), int(cy + half)))
        return self.canvas.grab(rect.intersected(self.canvas.rect()))

    def refresh_thumbs(self):
        while self.thumbs.count():
            w = self.thumbs.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, ex in enumerate(self.examples, 1):
            box = QWidget()
            box.setFixedSize(58, 58)
            img = QLabel(box)
            img.setGeometry(0, 0, 58, 58)
            img.setPixmap(ex["pix"].scaled(54, 54, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            img.setStyleSheet(f"border:2px solid {GREEN if ex['positive'] else RED}")
            num = QLabel(str(i), box)
            num.setGeometry(3, 3, 14, 14)
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet("background:rgba(0,0,0,160);color:white;font-size:9px;border-radius:7px")
            rm = QToolButton(box)
            rm.setText("×")
            rm.setGeometry(41, 3, 14, 14)
            rm.setStyleSheet("background:rgba(0,0,0,160);color:white;border:0;border-radius:7px;font-size:10px")
            rm.clicked.connect(lambda _, e=ex: self.remove_example(e))
            self.thumbs.addWidget(box)
        self.thumbs.addStretch()

    def remove_example(self, ex):
        self.canvas.scene().removeItem(ex["band"])
        self.examples.remove(ex)
        self.refresh_thumbs()

    def clear_examples(self):
        self.set_example_kind(None)
        for ex in self.examples:
            self.canvas.scene().removeItem(ex["band"])
        self.examples = []
        self.refresh_thumbs()

    def back_to_draw(self):
        self.set_example_kind(None)
        self.grid_band.reset(QgsWkbTypes.LineGeometry)
        self.pages.setCurrentIndex(DRAW)

    #  mreža tile-ova

    def grid(self):
        # Slika koja će otići na server za trenutnu zonu
        project = QgsProject.instance()
        map_crs = self.canvas.mapSettings().destinationCrs()
        if self.raster.providerType() == "gdal":
            roi = QgsGeometry(self.roi)
            roi.transform(QgsCoordinateTransform(map_crs, self.raster.crs(), project))
            px, py = self.raster.rasterUnitsPerPixelX(), self.raster.rasterUnitsPerPixelY()
            box = roi.boundingBox()
            box.grow(max(px, py) * 2)
            box = box.intersect(self.raster.extent())
            x0, y0, w, h = snap_to_raster(box, self.raster.extent(), px, py)
            return Grid(self.raster.crs(), x0, y0, px, py, w, h)
        box = self.roi.boundingBox()
        upp = (BASEMAP_RES_M / 111320 if map_crs.isGeographic() else
               BASEMAP_RES_M * QgsUnitTypes.fromUnitToUnitFactor(QgsUnitTypes.DistanceMeters, map_crs.mapUnits()))
        return Grid(map_crs, box.xMinimum(), box.yMaximum(), upp, upp,
                    max(1, round(box.width() / upp)), max(1, round(box.height() / upp)))

    def update_grid(self, *_):
        #Isprekidane linije podele na tile-ove, samo unutar zone; menja se sa sliderom preciznosti.
        v = self.precision.value()
        self.precision_lbl.setText(PRECISION[v][1])
        self.grid_band.reset(QgsWkbTypes.LineGeometry)
        if self.roi is None or self.raster is None:
            return

        g = self.grid()
        roi = QgsGeometry(self.roi)
        roi.transform(QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), g.crs,
                                             QgsProject.instance()))
        n, cols, rows = 0, set(), set()
        for (c, r, w, h), (cc, cr, _, _) in tile_windows(g.width, g.height, PRECISION[v][0]):
            if roi.intersects(QgsGeometry.fromRect(g.rect(c, r, w, h))):  # isto pravilo kao na serveru
                n += 1
            if cc:
                cols.add(cc)  # granice između core zona = linije podele
            if cr:
                rows.add(cr)

        b = g.bbox()
        lines = ([[QgsPointXY(g.x0 + c * g.px, b.yMinimum()), QgsPointXY(g.x0 + c * g.px, b.yMaximum())] for c in cols] +
                 [[QgsPointXY(b.xMinimum(), g.y0 - r * g.py), QgsPointXY(b.xMaximum(), g.y0 - r * g.py)] for r in rows])
        for pts in lines:
            part = QgsGeometry.fromPolylineXY(pts).intersection(roi)  # seci linije po zoni
            if not part.isEmpty():
                self.grid_band.addGeometry(part, g.crs, False)
        self.grid_band.updatePosition()
        self.grid_band.update()

        too_many = n > self.me.get("max_tiles", 1000)
        self.tiles_lbl.setText("<span style='color:#DC2626'>Zone is too large for this precision. "
                               "Lower the precision or draw a smaller zone.</span>" if too_many else "")
        self.detect_btn.setEnabled(not too_many)

    def detect(self):
        prompts = parse_prompts(self.prompt_edit.text())
        prompt = prompts[0] if prompts else ""
        positives = [e for e in self.examples if e["positive"]]
        if not prompts and not positives:
            hint = ("Enter what to detect." if self.kind == "detection"
                    else "Describe what to detect, or draw an example of it.")
            return self.detect_lbl.setText(f"<span style='color:{RED}'>{hint}</span>")
        if any(len(p) > 60 for p in prompts):  # isti limit kao na serveru, po jednom opisu
            return self.detect_lbl.setText(f"<span style='color:{RED}'>Each description can have at most "
                                           "60 characters.</span>")
        if len(prompts) > 1 and positives:
            return self.detect_lbl.setText(f"<span style='color:{RED}'>Examples work with a single description. "
                                           "Use one description, or remove the examples.</span>")
        self.set_example_kind(None)

        project = QgsProject.instance()
        map_crs = self.canvas.mapSettings().destinationCrs()
        roi_wgs = QgsGeometry(self.roi)
        roi_wgs.transform(QgsCoordinateTransform(map_crs, QgsCoordinateReferenceSystem("EPSG:4326"), project))
        to_wgs = QgsCoordinateTransform(map_crs, WGS84, project)
        examples = []
        for e in self.examples:
            g = QgsGeometry(e["geom"])
            g.transform(to_wgs)
            examples.append({"label": (prompt or "object") if e["positive"] else "background",
                             "geometry": json.loads(g.asJson())})
        body = {"source": "upload", "roi": json.loads(roi_wgs.asJson()),
                # sa primerima: SAM 3.1 dobija i opis i primere zajedno
                "mode": "examples" if positives else "text", "prompts": prompts,
                "examples": examples if positives else [],
                "min_score": 0.1, "tile_px": PRECISION[self.precision.value()][0],
                "merge": self.merge_cb.isChecked(),
                "output": "boxes" if self.kind == "detection" else "polygons"}
        self.detect_btn.setEnabled(False)

        g = self.grid()
        self.pending_px = g.px  # za Grow/shrink u pikselima
        b = g.bbox()
        if self.raster.providerType() == "gdal":
            # lokalni fajl: iseci samo obuhvat zone, poravnat na piksele (ista mreža kao na ekranu)
            bbox, source = (b.xMinimum(), b.yMinimum(), b.xMaximum(), b.yMaximum()), self.raster.source()
            self.start_detect(lambda d: clip_raster(source, bbox, os.path.join(d, "zone.tif")), body)
        else:
            # podloga (XYZ/WMS): prvo renderuj zonu u GeoTIFF, pa isto kao lokalni raster
            body["source"] = "basemap"
            self.render_basemap(body, g)

    def render_basemap(self, body, g):
        box = g.bbox()
        self._render_out = os.path.join(tempfile.mkdtemp(prefix="sam_seg_"), "basemap.tif")
        params = {
            "EXTENT": f"{box.xMinimum()},{box.xMaximum()},{box.yMinimum()},{box.yMaximum()} [{g.crs.authid()}]",
            "EXTENT_BUFFER": 0, "TILE_SIZE": 1024, "MAP_UNITS_PER_PIXEL": g.px,
            "MAKE_BACKGROUND_TRANSPARENT": False, "LAYERS": [self.raster.id()], "OUTPUT": self._render_out,
        }
        alg = QgsApplication.processingRegistry().createAlgorithmById("native:rasterize")
        self._ctx, self._feedback = QgsProcessingContext(), QgsProcessingFeedback()
        self._ctx.setProject(QgsProject.instance())
        self._render_task = QgsProcessingAlgRunnerTask(alg, params, self._ctx, self._feedback)
        bbox = (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
        self._render_task.executed.connect(lambda ok, res: self.on_rendered(ok, res, body, bbox))
        self.detect_lbl.setText("Rendering basemap…")
        QgsApplication.taskManager().addTask(self._render_task)

    def on_rendered(self, ok, results, body, bbox):
        if not ok:
            return self.detect_failed("Could not render the basemap")
        rendered = results["OUTPUT"]
        self.start_detect(lambda d: compress_rgb(rendered, bbox, os.path.join(d, "zone.tif")), body)

    def start_detect(self, prepare, body):
        names = body["prompts"] or ["look-alike objects"]
        self.start_live_layer(", ".join(names))
        self._task = DetectTask(self.api, prepare, body)
        self._task.status.connect(self.detect_lbl.setText)
        self._task.progress.connect(self.on_progress)
        self._task.features.connect(self.on_live_features)
        self._task.taskCompleted.connect(lambda: self.on_detected(names))
        self._task.taskTerminated.connect(lambda: self.detect_failed(self._task.error or "Cancelled"))
        QgsApplication.taskManager().addTask(self._task)

    #  prikaz uživo

    def start_live_layer(self, prompt):
        """Privremeni sloj koji se puni tile po tile; posle ga menja konačni rezultat."""
        self.remove_live_layer()
        self.live = QgsVectorLayer("Polygon?crs=EPSG:4326&field=label:string&field=score:double",
                                   f"GeoSegment: {short(prompt)} (detecting…)", "memory")
        self.live.renderer().setSymbol(QgsFillSymbol.createSimple(
            {"style": "no", "outline_color": "255,0,255", "outline_width": "0.7"} if self.kind == "detection" else
            {"color": "255,0,255,40", "outline_color": "255,0,255", "outline_width": "0.5"}))
        self.live.setSubsetString(f'"score" >= {self.conf.value() / 100:.2f}')  # isti prag kao rezultat
        QgsProject.instance().addMapLayer(self.live)
        self.live_fields = self.live.fields()
        self.detect_bar.setValue(0)
        self.detect_bar.show()
        self.found_lbl.setText("")

    def on_progress(self, done, total, found):
        self.detect_bar.setRange(0, total)
        self.detect_bar.setValue(done)
        self.found_lbl.setText(f"<b>{found}</b> objects found so far")

    def on_live_features(self, features):
        if self.live is None:
            return
        fc = json.dumps({"type": "FeatureCollection", "features": features})
        self.live.dataProvider().addFeatures(QgsJsonUtils.stringToFeatureList(fc, self.live_fields))
        self.live.triggerRepaint()

    def remove_live_layer(self):
        lyr, self.live = getattr(self, "live", None), None
        if lyr is not None:
            try:
                QgsProject.instance().removeMapLayer(lyr.id())
            except RuntimeError:
                pass  # korisnik ga je već obrisao

    def end_detect_ui(self):
        self.remove_live_layer()
        self.detect_bar.hide()
        self.found_lbl.setText("")

    def on_detected(self, names):
        if self.kind == "detection":
            return self.on_boxes_detected(names)
        title = ", ".join(names)
        self.end_detect_ui()
        lyr = QgsVectorLayer(f"{self._task.result_path}|layername=segments", f"GeoSegment: {short(title)}", "ogr")
        if not lyr.isValid() or lyr.renderer() is None or lyr.featureCount() == 0:
            self.refresh_quota()
            self.update_grid()
            return self.detect_lbl.setText(
                f"<span style='color:{RED}'>No objects found. Try another description, add examples "
                "or change the precision.</span>")
        self.result_labels = result_labels(lyr)
        style_results(lyr, self.view_cb.currentText(), self.result_labels)
        QgsProject.instance().addMapLayer(lyr)
        self.job_id, self.prompt, self.prompt_title = self._task.job_id, names[0], title
        self.result_px = self.pending_px
        lyr.willBeDeleted.connect(self.on_result_removed)
        self.result_layer, self.result_total = lyr, lyr.featureCount()
        self.remove_layer_overlaps()
        self.detect_lbl.setText("")
        self.refresh_quota()
        self.update_grid()
        self.grid_band.reset(QgsWkbTypes.LineGeometry)  # rezultat je bitniji od mreže
        self.pages.setCurrentIndex(RESULTS)
        self.apply_filters()

    #  filtriranje rezultata

    def apply_filters(self, *_):
        """Filter na nivou izvora (SQL nad GPKG-om): mapa i brojač se menjaju odmah."""
        c = self.conf.value()
        self.conf_lbl.setText(f"≥ {c}%")
        if self.result_layer is None:
            return
        cond = [f'"score" >= {c / 100:.2f}']
        for field, op in ((self.min_area, ">="), (self.max_area, "<=")):
            try:
                cond.append(f'"area_m2" {op} {float(field.text().replace(",", "."))}')
            except ValueError:
                pass  # prazno polje = bez ograničenja
        self.result_layer.setSubsetString(" AND ".join(cond))
        self.result_layer.triggerRepaint()
        self.count_lbl.setText(f"{self.result_layer.featureCount()} of {self.result_total}")
        self.update_legend()

    def update_legend(self, lyr=None, labels=None, widget=None):
        # po klasi: broj vidljivih objekata (samo kad rezultat ima više labela)
        lyr = self.result_layer if lyr is None else lyr
        labels = self.result_labels if labels is None else labels
        widget = self.legend_lbl if widget is None else widget
        idx = lyr.fields().indexOf("label")
        counts = {}
        for f in lyr.getFeatures(QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry).setSubsetOfAttributes([idx])):
            counts[f["label"]] = counts.get(f["label"], 0) + 1
        if len(labels) < 2:
            return widget.setText("")
        colors = class_colors(labels)
        widget.setText("  ".join(
            f"<span style='color:rgb({c})'>■</span> {lb} <b>{counts.get(lb, 0)}</b>" for lb, c in colors.items()))

    def on_result_removed(self):
        self.result_layer = None  # korisnik je obrisao sloj iz projekta
        self.stop_review()
        self.review_fids = []
        self.set_mode(None)
        self.view_box.hide()

    def new_detection(self):
        self.stop_review()
        self.review_fids = []
        self.set_mode(None)
        self.finish_manual(save=True)
        self.result_layer = None
        self.view_box.hide()  # prethodni rezultat ostaje u projektu sa svojim filterom
        self.pages.setCurrentIndex(PROMPT)
        self.update_grid()

    #  korekcija (AI)

    def set_mode(self, mode):
        """None | "edit" | "add" — samo jedan režim klikanja po mapi u isto vreme."""
        if self.mode == "add" and self.result_layer is not None and self.result_layer.isEditable():
            self.result_layer.commitChanges()  # dodati objekti se čuvaju kad se završi dodavanje
        self.mode = mode
        self.last_add = None
        self.clear_edit()
        for w, on in ((self.edit_btn, mode == "edit"),):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        self.add_btn.setVisible(mode != "add")
        self.stop_add_btn.setVisible(mode == "add")
        if mode:
            self.activate_tool(self.click_tool)
            self.ai_status.setText("Click a detected polygon." if mode == "edit" else "Click an object on the map.")
        else:
            self.release_tool()
            self.ai_status.setText("")

    def leave_correct(self):
        self.stop_review()
        self.finish_manual(save=True)
        self.set_mode(None)
        self.pages.setCurrentIndex(RESULTS)

    def clear_edit(self):
        self.edit_fid, self.edit_points, self.preview = None, [], None
        for b in (self.sel_band, self.preview_band):
            b.reset(QgsWkbTypes.PolygonGeometry)
        for b in (self.pos_band, self.neg_band):
            b.reset(QgsWkbTypes.PointGeometry)
        self.save_btn.hide()
        self.undo_btn.hide()

    def on_map_click(self, pt, left):
        if self.manual_tool in ("delete", "merge") and self.result_layer is not None:
            if left:
                self.on_manual_click(pt)
            return
        if self.busy or self.result_layer is None or self.job_id is None:
            return
        if self.mode == "add":
            if not left:
                return
            last = self.last_add
            if last and self.near(last["geom"], pt):
                # drugi klik uz prethodni objekat -> isti objekat, dopuni ga (ne pravi nov)
                pts = last["points"] + [(pt, 1)]
                self.refine_async(last["geom"], pts, lambda g, s: self.on_add_merged(g, pts))
            else:
                self.refine_async(None, [(pt, 1)], lambda g, s: self.on_added(g, [(pt, 1)]))
        elif self.mode == "edit":
            if self.edit_fid is None:
                self.select_polygon(pt)
            else:
                self.edit_points.append((pt, 1 if left else 0))
                self.draw_points()
                self.refine_edit()

    def select_polygon(self, pt):
        # Poligon koji se ispravlja 
        f = self.feature_at(pt)
        if f is None:
            return self.ai_status.setText("Click on a detected polygon.")
        self.select_feature(f)

    def select_feature(self, f):
        self.edit_fid, self.edit_orig = f.id(), f.geometry()
        self.sel_band.setToGeometry(self.edit_orig, self.result_layer.crs())
        self.ai_status.setText("Now click where the AI missed (right-click: remove).")

    def draw_points(self):
        self.pos_band.reset(QgsWkbTypes.PointGeometry)
        self.neg_band.reset(QgsWkbTypes.PointGeometry)
        for pt, label in self.edit_points:
            (self.pos_band if label else self.neg_band).addPoint(pt)

    def refine_edit(self):
        if self.edit_points:
            self.refine_async(self.edit_orig, self.edit_points, self.on_preview)
        else:
            self.preview = None
            self.preview_band.reset(QgsWkbTypes.PolygonGeometry)
            self.save_btn.hide()
            self.undo_btn.hide()

    def refine_async(self, polygon, points, done):
        # Poziv /refine u pozadini;
        lyr, map_crs = self.result_layer, self.canvas.mapSettings().destinationCrs()
        poly = json.loads(to_crs(polygon, lyr.crs(), WGS84).asJson()) if polygon else None
        pts = []
        for pt, label in points:
            w = to_crs(QgsGeometry.fromPointXY(pt), map_crs, WGS84).asPoint()
            pts.append({"x": w.x(), "y": w.y(), "label": label})
        job_id, crs = self.job_id, lyr.crs()

        def finished(exc, result=None):
            self.busy = False
            if exc:
                return self.ai_status.setText(f"<span style='color:#DC2626'>{exc}</span>")
            done(to_crs(from_geojson(result["geometry"]), WGS84, crs), result["score"])

        self.busy = True
        self.ai_status.setText("Thinking…")
        self._refine_task = QgsTask.fromFunction("GeoSegment: correction", lambda task: self.api.refine(job_id, poly, pts),
                                                 on_finished=finished)
        QgsApplication.taskManager().addTask(self._refine_task)

    def shape_correction(self, geom, exclude_fid=None):
        # Rezultat /refine -> (pravilan oblik) -> isečen tako da ne prekriva susedne objekte
        if self.regular_cb.isChecked():
            geom = regularize(geom)
        req = QgsFeatureRequest().setFilterRect(geom.boundingBox())
        others = [f.geometry() for f in self.result_layer.getFeatures(req)
                  if f.id() != exclude_fid and f.geometry().intersects(geom)]
        if others:
            geom = geom.difference(QgsGeometry.unaryUnion(others)).makeValid()
            geom = largest_part(geom) if not geom.isEmpty() else geom
        return geom

    def on_preview(self, geom, score):
        # samo pozitivni klikovi = proširi postojeći objekat (original + novi deo)
        if all(label == 1 for _, label in self.edit_points):
            geom = self.edit_orig.combine(geom)
        geom = self.shape_correction(geom, exclude_fid=self.edit_fid)
        self.preview = geom
        self.preview_band.setToGeometry(geom, self.result_layer.crs())
        self.save_btn.show()
        self.undo_btn.show()
        self.ai_status.setText("Keep clicking to refine, then Save.")

    def undo_edit(self):
        if self.busy or not self.edit_points:
            return
        self.edit_points.pop()
        self.draw_points()
        self.refine_edit()

    def area_m2(self, geom):
        da = QgsDistanceArea()
        da.setSourceCrs(self.result_layer.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid("WGS84")
        return round(da.convertAreaMeasurement(da.measureArea(geom), QgsUnitTypes.AreaSquareMeters), 1)

    def save_edit(self):
        if self.busy or self.preview is None:
            return
        lyr = self.result_layer
        lyr.startEditing()
        lyr.changeGeometry(self.edit_fid, self.preview)
        lyr.changeAttributeValue(self.edit_fid, lyr.fields().indexOf("area_m2"), self.area_m2(self.preview))
        lyr.commitChanges()
        self.clear_edit()
        self.apply_filters()
        self.ai_status.setText("Saved. Open the Review tab to continue the review." if self.review_pending()
                               else "Saved. Click another polygon to edit.")

    def near(self, geom, pt, px=25):
        # Da li je klik na objektu ili do px piksela ekrana od njega
        g = to_crs(geom, self.result_layer.crs(), self.canvas.mapSettings().destinationCrs())
        return g.distance(QgsGeometry.fromPointXY(pt)) <= self.canvas.mapUnitsPerPixel() * px

    def on_added(self, geom, points):
        geom = self.shape_correction(geom)
        if geom.isEmpty():
            return self.ai_status.setText("That spot is already covered by a polygon.")
        lyr = self.result_layer
        f = QgsFeature(lyr.fields())
        f.setGeometry(geom)
        f["label"], f["score"], f["area_m2"] = self.prompt, 1.0, self.area_m2(geom)  # ručno dodat = siguran
        lyr.startEditing()  # snima se na "Stop adding" (tako isti objekat može da se dopuni)
        lyr.addFeature(f)
        self.last_add = {"fid": f.id(), "geom": geom, "points": points}
        self.result_total += 1
        self.apply_filters()
        self.ai_status.setText("Added. Click near it to extend it, elsewhere for a new object.")

    def on_add_merged(self, geom, points):
        last, lyr = self.last_add, self.result_layer
        merged = self.shape_correction(geom.combine(last["geom"]), exclude_fid=last["fid"])  # jedan objekat
        lyr.changeGeometry(last["fid"], merged)
        lyr.changeAttributeValue(last["fid"], lyr.fields().indexOf("area_m2"), self.area_m2(merged))
        self.last_add = {"fid": last["fid"], "geom": merged, "points": points}
        lyr.triggerRepaint()
        self.ai_status.setText("Extended. Click near it again, or elsewhere for a new object.")

    # korekcija (Manual)

    MANUAL_HINTS = {
        "move": "Drag points on polygon edges. Double-click an edge to add a point, Delete removes a selected point.",
        "delete": "Click a polygon to delete it.",
        "merge": "Click two polygons to merge them into one.",
        "split": "Draw a line across a polygon, double-click (or right-click) to split it.",
    }

    def set_manual_tool(self, tool):
        # Ručni alati rade u jednoj sesiji (edit bafer): Save upisuje sve, Cancel poništava sve.
        lyr = self.result_layer
        for t, b in self.manual_btns.items():
            b.blockSignals(True)
            b.setChecked(t == tool)
            b.blockSignals(False)
        if self.manual_tool == "move" and tool != "move":
            self.iface.actionPan().trigger()
        self.merge_first = None
        self.sel_band.reset(QgsWkbTypes.PolygonGeometry)
        self.line_tool.reset()
        self.manual_tool = tool
        if tool is None or lyr is None:
            self.release_tool()
            self.manual_hint.setText("Pick a tool.")
            return
        if not self.manual_on:
            self.set_mode(None)
            lyr.startEditing()
            self.manual_on = True
            for b in (self.manual_save, self.manual_undo, self.manual_cancel):
                b.show()
        self.manual_hint.setText(self.MANUAL_HINTS[tool])
        if tool == "move":
            self.iface.setActiveLayer(lyr)
            self.iface.actionVertexTool().trigger()  # QGIS alat za pomeranje temena
        elif tool == "split":
            self.activate_tool(self.line_tool)
        else:
            self.activate_tool(self.click_tool)

    def feature_at(self, pt):
        #Najmanji vidljivi poligon ispod klika (ili None).
        lyr, map_crs = self.result_layer, self.canvas.mapSettings().destinationCrs()
        p = to_crs(QgsGeometry.fromPointXY(pt), map_crs, lyr.crs())
        tol = self.canvas.mapUnitsPerPixel() * 3
        rect = to_crs(QgsGeometry.fromPointXY(pt).buffer(tol, 4), map_crs, lyr.crs()).boundingBox()
        hits = [f for f in lyr.getFeatures(QgsFeatureRequest().setFilterRect(rect)) if f.geometry().intersects(p)]
        return min(hits, key=lambda f: f.geometry().area()) if hits else None

    def on_manual_click(self, pt):
        lyr, f = self.result_layer, self.feature_at(pt)
        if f is None:
            return self.manual_hint.setText("Click on a polygon.")
        if self.manual_tool == "delete":
            lyr.deleteFeature(f.id())
            self.manual_hint.setText("Deleted. Click another polygon, or Save.")
        elif self.manual_tool == "merge":
            if self.merge_first is None or self.merge_first.id() == f.id():
                self.merge_first = f
                self.sel_band.setToGeometry(f.geometry(), lyr.crs())
                return self.manual_hint.setText("Now click the polygon to merge it with.")
            a = self.merge_first
            merged = a.geometry().combine(f.geometry()).makeValid()
            lyr.changeGeometry(a.id(), merged)
            lyr.changeAttributeValue(a.id(), lyr.fields().indexOf("score"), max(a["score"] or 0, f["score"] or 0))
            lyr.deleteFeature(f.id())
            self.merge_first = None
            self.sel_band.reset(QgsWkbTypes.PolygonGeometry)
            self.manual_hint.setText("Merged. Click two more polygons, or Save.")
        lyr.triggerRepaint()

    def on_split_line(self, line):
        lyr = self.result_layer
        line = to_crs(line, self.canvas.mapSettings().destinationCrs(), lyr.crs())
        n = 0
        for f in list(lyr.getFeatures(QgsFeatureRequest().setFilterRect(line.boundingBox()))):
            g = f.geometry()
            if not g.intersects(line):
                continue
            _, parts, _ = g.splitGeometry(line.asPolyline(), False)  # g postaje prvi deo, parts su ostali
            if not parts:
                continue
            lyr.changeGeometry(f.id(), g)
            fid_idx = lyr.fields().indexOf("fid")
            for part in parts:
                nf = QgsFeature(f)
                if fid_idx >= 0:
                    nf.setAttribute(fid_idx, None)  # GPKG izlaže fid kao kolonu: kopija bi ponovila isti ključ
                nf.setGeometry(part)
                lyr.addFeature(nf)
            n += 1
        lyr.triggerRepaint()
        self.manual_hint.setText(f"Split {n} polygon{'s' if n != 1 else ''}. Draw another line, or Save."
                                 if n else "The line must cross a polygon completely.")

    def undo_manual(self):
        lyr = self.result_layer
        if lyr is not None and lyr.isEditable():
            lyr.undoStack().undo()
            lyr.triggerRepaint()

    def finish_manual(self, save):
        lyr = self.result_layer
        tool_was_move = self.manual_tool == "move"
        if self.manual_on and lyr is not None and lyr.isEditable():
            if save:
                buf, idx = lyr.editBuffer(), lyr.fields().indexOf("area_m2")
                if buf:
                    for fid, g in buf.changedGeometries().items():
                        lyr.changeAttributeValue(fid, idx, self.area_m2(g))
                    for fid, f in buf.addedFeatures().items():
                        lyr.changeAttributeValue(fid, idx, self.area_m2(f.geometry()))
                    self.result_total += len(buf.addedFeatures()) - len(buf.deletedFeatureIds())
                lyr.commitChanges()
            else:
                lyr.rollBack()
        self.manual_on = False
        for t, b in self.manual_btns.items():
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        self.manual_tool = None
        self.merge_first = None
        self.sel_band.reset(QgsWkbTypes.PolygonGeometry)
        self.line_tool.reset()
        self.release_tool()
        if tool_was_move:
            self.iface.actionPan().trigger()
        for b in (self.manual_save, self.manual_undo, self.manual_cancel):
            b.hide()
        self.manual_hint.setText("Pick a tool.")
        if lyr is not None:
            self.apply_filters()

    def remove_layer_overlaps(self):
        # Server rešava preklapanja unutar tile-a; ovde se čiste i preostala (granice tile-ova i sl.).
        lyr = self.result_layer
        feats = {f.id(): f for f in lyr.getFeatures()}
        changed = {}
        for lb in {f["label"] for f in feats.values()}:  # klase se ne seku međusobno (kuća i drvo smeju da se preklapaju)
            group = {i: f for i, f in feats.items() if f["label"] == lb}
            changed.update(resolve_overlaps({i: f.geometry() for i, f in group.items()},
                                            {i: f["score"] for i, f in group.items()}))
        if not changed:
            return
        idx = lyr.fields().indexOf("area_m2")
        lyr.startEditing()
        for fid, g in changed.items():
            lyr.changeGeometry(fid, g)
            lyr.changeAttributeValue(fid, idx, 0.0 if g.isEmpty() else self.area_m2(g))
        lyr.commitChanges()

    # brzi pregled

    def review_pending(self):
        return self.review_i < len(self.review_fids)

    def start_review(self):
        lyr = self.result_layer
        if lyr is None:
            return
        self.set_mode(None)
        self.finish_manual(save=True)
        if not self.review_pending():  # nov pregled; inače nastavak (npr. posle Edit)
            feats = sorted(lyr.getFeatures(), key=lambda f: f["score"] or 0)  # samo vidljivi (filteri važe)
            self.review_fids, self.review_i, self.review_removed = [f.id() for f in feats], 0, 0
        if not self.review_fids:
            return self.review_info.setText("No polygons to review.")
        lyr.startEditing()  # brisanja idu u edit bafer -> Back ih vraća preko undo
        self.reviewing = True
        self.activate_tool(self.review_tool)
        self.set_review_ui(True)
        self.show_review()

    def show_review(self):
        # Prvi postojeći poligon od trenutne pozicije: zumiraj na njega i obeleži ga.
        lyr = self.result_layer
        while self.review_pending():
            f = lyr.getFeature(self.review_fids[self.review_i])
            if f.isValid() and f.hasGeometry():  # obrisan ili sakriven filterom -> preskoči
                break
            self.review_i += 1
        else:
            return self.stop_review()
        g = f.geometry()
        rect = to_crs(g, lyr.crs(), self.canvas.mapSettings().destinationCrs()).boundingBox()
        rect.scale(4)  # objekat zauzima ~četvrtinu prikaza, ostalo je kontekst
        self.canvas.setExtent(rect)
        if self.canvas.scale() < 400:  # sitni objekti: ne zumiraj ispod rezolucije podloge
            self.canvas.zoomScale(400)
        self.canvas.refresh()
        self.sel_band.setToGeometry(g, lyr.crs())
        self.review_info.setText(f"<b>{self.review_i + 1} / {len(self.review_fids)}</b> · "
                                 f"confidence {round((f['score'] or 0) * 100)}% · {f['area_m2'] or 0:.0f} m²")
        self.canvas.setFocus()  # tasteri idu mapi i posle klika na dugme

    def review_action(self, action):
        if not self.reviewing:
            return
        if action == "stop":
            return self.stop_review()
        if action == "back":
            return self.review_back()
        fid = self.review_fids[self.review_i]
        if action == "edit":
            return self.review_edit(fid)
        if action == "remove":
            self.result_layer.deleteFeature(fid)
        self.review_i += 1
        self.show_review()

    def review_back(self):
        lyr = self.result_layer
        deleted = lyr.editBuffer().deletedFeatureIds() if lyr.editBuffer() else []
        for i in range(self.review_i - 1, -1, -1):
            fid = self.review_fids[i]
            if fid in deleted:
                # pregled ide redom, pa je poslednja komanda u undo steku baš brisanje ovog poligona
                lyr.undoStack().undo()
                break
            if lyr.getFeature(fid).isValid():
                break
        else:
            return self.canvas.setFocus()  # već smo na prvom
        self.review_i = i
        self.show_review()

    def review_edit(self, fid):
        # Prekida pregled (odluke se čuvaju) i otvara AI ispravku baš ovog poligona.
        self.stop_review()
        f = self.result_layer.getFeature(fid)
        self.correct_tabs.setCurrentIndex(0)
        self.set_mode("edit")
        self.select_feature(f)

    def stop_review(self):
        if not self.reviewing:
            return
        self.reviewing = False  # pre release_tool: deactivated signal opet zove stop_review
        lyr = self.result_layer
        if lyr is not None and lyr.isEditable():
            buf = lyr.editBuffer()
            removed = len(buf.deletedFeatureIds()) if buf else 0
            lyr.commitChanges()
            self.result_total -= removed
            self.review_removed += removed
        self.sel_band.reset(QgsWkbTypes.PolygonGeometry)
        self.release_tool()
        self.set_review_ui(False)
        if lyr is not None:
            self.apply_filters()
        if self.review_pending():
            self.review_info.setText(f"Paused at {self.review_i + 1} / {len(self.review_fids)} · "
                                     f"{self.review_removed} removed so far.")
        else:
            self.review_info.setText(f"Review finished · {self.review_removed} removed.")
            self.review_fids, self.review_i = [], 0

    def set_review_ui(self, on):
        for b in self.review_btns:
            b.setVisible(on)
        self.review_keys.setVisible(on)
        self.review_btn.setVisible(not on)
        self.review_btn.setText("Continue review" if self.review_pending() else "Start review")

    # oblici

    def on_page_changed(self, index):
        self.view_box.setVisible(index in (RESULTS, CORRECT, SHAPES) and self.result_layer is not None)

    def enter_shapes(self):
        # Polazne geometrije se pamte; svaka promena opcija računa se iznova od njih (bez gomilanja).
        self.stop_review()
        self.set_mode(None)
        self.finish_manual(save=True)
        lyr = self.result_layer
        if lyr is None:
            return
        feats = list(lyr.getFeatures())
        self.shape_base = {f.id(): f.geometry() for f in feats}
        self.shape_scores = {f.id(): f["score"] for f in feats}
        for w in (self.right_angles, self.fill_holes, self.grow, self.points):
            w.blockSignals(True)
        self.right_angles.setChecked(False)
        self.fill_holes.setChecked(False)
        self.grow.setValue(0)
        self.points.setValue(0)
        self.points_lbl.setText("0%")
        for w in (self.right_angles, self.fill_holes, self.grow, self.points):
            w.blockSignals(False)
        lyr.startEditing()  # promene su u edit baferu dok se ne napusti korak
        self.pages.setCurrentIndex(SHAPES)

    def shaped(self, g):
        g = g.makeValid()  # SAM poligoni su često blago neispravni -> negativan buffer bi vratio prazno
        if self.fill_holes.isChecked():
            g = g.removeInteriorRings()
            parts = g.asGeometryCollection()
            if len(parts) > 1:  # sitne odvojene mrlje uz objekat
                big = max(p.area() for p in parts)
                g = QgsGeometry.collectGeometry([p for p in parts if p.area() >= 0.1 * big])
        pct = self.points.value()
        if pct:
            # tolerancija srazmerna veličini objekta -> mali i veliki poligoni se uprošćavaju podjednako
            g = g.simplify(pct / 100 * 0.15 * g.area() ** 0.5)
        if self.right_angles.isChecked():
            g = g.orthogonalize(1e-8, 1000, 20)
        d = self.grow.value() * self.result_px
        if d:
            g = g.buffer(d, 2, CAP_FLAT, JOIN_MITER, 2)  # miter čuva uglove; d < 0 = shrink
        return g.makeValid()

    def apply_shapes(self):
        lyr = self.result_layer
        if lyr is None or not lyr.isEditable():
            return
        area_idx = lyr.fields().indexOf("area_m2")
        shaped = {fid: self.shaped(QgsGeometry(base)) for fid, base in self.shape_base.items()}
        # grow širi svaki objekat posebno -> susedi bi se preklopili; jači skor zadržava sporni deo
        shaped.update(resolve_overlaps(shaped, self.shape_scores))
        for fid, g in shaped.items():
            # shrink može potpuno da "pojede" mrvicu: površina 0 -> sakriva je filter Min
            lyr.changeGeometry(fid, g)
            lyr.changeAttributeValue(fid, area_idx, 0.0 if g.isEmpty() else self.area_m2(g))
        lyr.triggerRepaint()
        self.count_lbl.setText(f"{lyr.featureCount()} of {self.result_total}")

    def export_results(self):
        det = self.kind == "detection"
        lyr = self.det_layer if det else self.result_layer
        if lyr is None:
            return
        cb, title = (self.d_export_cb, self.det_title) if det else (self.export_cb, self.prompt_title)
        driver, ext, layer_opts = EXPORTS[cb.currentText()]
        path, _ = QFileDialog.getSaveFileName(self, "Export boxes" if det else "Export polygons",
                                              f"geo{'detect' if det else 'segment'}_{short(title, 20)}.{ext}", f"*.{ext}")
        if not path:
            return
        if lyr.isEditable():  # trenutni oblici ulaze u izvoz
            self.shape_timer.stop()
            self.apply_shapes()
            lyr.commitChanges()
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName, opts.fileEncoding, opts.layerOptions = driver, "UTF-8", layer_opts
        opts.skipAttributeCreation = driver == "DXF"  # DXF prima samo geometriju, ne proizvoljne kolone
        err = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, path, QgsProject.instance().transformContext(), opts)
        if self.pages.currentIndex() == SHAPES:
            lyr.startEditing()  # nastavak podešavanja oblika posle izvoza
        if err[0] == QgsVectorFileWriter.NoError:
            self.iface.messageBar().pushSuccess(APP_NAME, f"Exported {lyr.featureCount()} {'boxes' if det else 'polygons'} to {path}")
        else:
            self.iface.messageBar().pushWarning(APP_NAME, f"Export failed: {err[1]}")

    def leave_shapes(self):
        self.shape_timer.stop()
        if self.result_layer is not None and self.result_layer.isEditable():
            self.apply_shapes()
            self.result_layer.commitChanges()
        self.apply_filters()
        self.pages.setCurrentIndex(CORRECT)

    def detect_failed(self, msg):
        self.end_detect_ui()
        self.detect_lbl.setText(f"<span style='color:#DC2626'>{msg}</span>")
        self.refresh_quota()
        self.update_grid()

    def refresh_quota(self):
        try:
            self.me = self.api.me()
            self.update_quota()
        except ApiError:
            pass

    #  gašenje

    def closeEvent(self, e):
        self.set_mode(None)
        self.stop_drawing()
        super().closeEvent(e)

    def cleanup(self):
        self.login.stop()
        if self.result_layer is not None and self.result_layer.isEditable():
            self.result_layer.commitChanges()  # ne ostavljaj nesačuvane oblike
        self.set_mode(None)
        self.stop_drawing()
