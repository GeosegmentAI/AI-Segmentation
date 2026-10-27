#izgledi, paleta, stilovi (QSS), SVG ikonice i pomoćni widgeti.
from qgis.PyQt.QtCore import QByteArray, Qt
from qgis.PyQt.QtGui import QIcon, QPainter, QPixmap
from qgis.PyQt.QtSvg import QSvgRenderer
from qgis.PyQt.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                                 QWidget)

APP_NAME = "GeoSegment AI"
TOTAL_STEPS = 6

#paleta
PRIMARY = "#2563EB"
PRIMARY_2 = "#4F46E5"
PRIMARY_SOFT = "#EFF6FF"
SUCCESS= "#16A34A"
DANGER= "#DC2626"
WARNING= "#F59E0B"
ORANGE= "#EA580C"
TEXT= "#0F172A"
MUTED= "#64748B"
BORDER= "#E2E8F0"
BG= "#F8FAFC"

# ikonice (24×24, linijske)
ICONS = {
    "logo": '<path d="M12 2.5 20.5 7.3v9.4L12 21.5 3.5 16.7V7.3z"/>'
            '<path d="M8.2 9.6 12 7.8l3.9 2.3-.9 4.4-4.7 1.1-2.2-2.8z" fill="currentColor" fill-opacity=".25"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
    "browser": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/>'
               '<path d="M6.5 6.5h.01M9 6.5h.01"/><path d="m10 13 2 2 3.5-3.5"/>',
    "layers": '<path d="M12 3 2.5 8 12 13l9.5-5z"/><path d="m2.5 12.5 9.5 5 9.5-5"/><path d="m2.5 17 9.5 5 9.5-5"/>',
    "draw": '<path d="M5 18 3.5 7.5 11 3l8.5 5.5-2.5 11z"/><circle cx="3.5" cy="7.5" r="1.3" fill="currentColor"/>'
            '<circle cx="11" cy="3" r="1.3" fill="currentColor"/><circle cx="19.5" cy="8.5" r="1.3" fill="currentColor"/>',
    "sparkle": '<path d="M12 3c.6 3.9 2.1 5.4 6 6-3.9.6-5.4 2.1-6 6-.6-3.9-2.1-5.4-6-6 3.9-.6 5.4-2.1 6-6z"/>'
               '<path d="M19 15c.3 1.7.9 2.4 2.5 2.7-1.6.3-2.2 1-2.5 2.7-.3-1.7-.9-2.4-2.5-2.7 1.6-.3 2.2-1 2.5-2.7z"/>',
    "sliders": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
               '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "wand": '<path d="m4 20 10-10"/><path d="m14.5 3 1.2 2.8L18.5 7l-2.8 1.2L14.5 11l-1.2-2.8L10.5 7l2.8-1.2z"/>'
            '<path d="M19 13v3M17.5 14.5h3"/>',
    "shapes": '<rect x="5" y="5" width="14" height="14" rx="1.5"/><path d="M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
}


def svg_pixmap(name, color=PRIMARY, size=22):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" color="{color}">'
           f'{ICONS[name].replace("currentColor", color)}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    dpr = 2  # oštro i na ekranima visoke rezolucije
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def app_icon():
    return QIcon(svg_pixmap("logo", PRIMARY, 32))


# stilovi
QSS = f"""
#GeoSegRoot {{ background: {BG}; }}
/* osnovni tekst (tip-selektori imaju najmanju prednost -> #Naslovi ispod ih nadjačavaju) */
QLabel {{ color: {TEXT}; font-size: 12px; }}
QCheckBox, QLineEdit, QSpinBox, QComboBox, QGroupBox {{ font-size: 12px; }}
#GeoSegRoot QScrollArea, #GeoSegRoot QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

/* zaglavlje */
#Header {{ background: white; border: 1px solid {BORDER}; border-radius: 12px; }}
#Brand {{ font-size: 13px; font-weight: 800; color: {TEXT}; }}
#User {{ font-size: 11px; color: {MUTED}; }}

/* naslov koraka */
#StepIcon {{ background: {PRIMARY_SOFT}; border-radius: 12px; }}
#StepKicker {{ color: {PRIMARY}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
#StepTitle {{ font-size: 15px; font-weight: 700; color: {TEXT}; }}
#StepSub {{ color: {MUTED}; font-size: 11px; }}
#HeroTitle {{ font-size: 20px; font-weight: 800; color: {TEXT}; }}
#HeroSub {{ color: {MUTED}; font-size: 12px; }}

/* tekst */
#Muted {{ color: {MUTED}; font-size: 11px; }}
#Error {{ color: {DANGER}; }}
#Count {{ font-size: 22px; font-weight: 800; color: {TEXT}; }}
#Status {{ color: {TEXT}; font-size: 11px; }}

/* kartice */
QGroupBox {{
    background: white; border: 1px solid {BORDER}; border-radius: 10px;
    margin-top: 16px; padding: 12px 10px 10px 10px; font-weight: 700; color: {TEXT};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #334155; }}

/* dugmad */
QPushButton {{
    min-height: 30px; border-radius: 8px; padding: 4px 12px; font-weight: 600; font-size: 12px;
    border: 1px solid #CBD5E1; background: white; color: {TEXT};
}}
QPushButton:hover {{ background: #F1F5F9; border-color: #94A3B8; }}
QPushButton:disabled {{ color: #94A3B8; background: #F1F5F9; border-color: {BORDER}; }}
QPushButton[variant="primary"] {{
    min-height: 38px; border: none; color: white; font-size: 13px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PRIMARY}, stop:1 {PRIMARY_2});
}}
QPushButton[variant="primary"]:hover {{ background: #1D4ED8; }}
QPushButton[variant="primary"]:disabled {{ background: #BFDBFE; color: white; }}
QPushButton[variant="success"] {{ border: none; color: white; background: {SUCCESS}; }}
QPushButton[variant="success"]:hover {{ background: #15803D; }}
QPushButton[variant="upgrade"] {{
    min-height: 38px; border: none; color: white; font-size: 13px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {WARNING}, stop:1 {ORANGE});
}}
QPushButton[variant="ghost"] {{
    min-height: 22px; border: none; background: transparent; color: {MUTED}; padding: 2px 6px;
}}
QPushButton[variant="ghost"]:hover {{ color: {TEXT}; }}
QPushButton[variant="link"] {{
    min-height: 22px; border: none; background: transparent; color: {ORANGE}; font-weight: 800; padding: 2px 6px;
}}
QPushButton[variant="link"]:hover {{ color: #C2410C; }}
QPushButton[variant="danger"] {{ border: none; color: white; background: {DANGER}; }}
QPushButton[variant="danger"]:hover {{ background: #B91C1C; }}
QPushButton[variant="active"] {{ background: {PRIMARY}; border: none; color: white; }}
QPushButton[variant="active"]:hover {{ background: #1D4ED8; }}
QPushButton[variant="toggle"]:checked {{ background: {SUCCESS}; border: none; color: white; }}
QPushButton[variant="toggle-danger"]:checked {{ background: {DANGER}; border: none; color: white; }}
QPushButton[variant="toggle-primary"]:checked {{ background: {PRIMARY}; border: none; color: white; }}

/* polja */
QLineEdit, QSpinBox, QComboBox {{
    border: 1px solid #CBD5E1; border-radius: 8px; padding: 5px 8px; background: white;
    color: {TEXT}; min-height: 22px; selection-background-color: {PRIMARY};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {PRIMARY}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}

/* klizači */
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: white; border: 2px solid {PRIMARY}; width: 14px; height: 14px; margin: -6px 0; border-radius: 9px;
}}

/* progress */
QProgressBar {{
    border: none; background: {BORDER}; border-radius: 7px; text-align: center;
    color: {TEXT}; font-size: 10px; font-weight: 600; min-height: 14px; max-height: 14px;
}}
QProgressBar::chunk {{
    border-radius: 7px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PRIMARY}, stop:1 {PRIMARY_2});
}}
QProgressBar[level="warn"]::chunk {{ background: {WARNING}; }}
QProgressBar[level="full"]::chunk {{ background: {DANGER}; }}

/* tabovi */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: {BORDER}; color: #475569; padding: 6px 18px; border-radius: 8px;
    margin-right: 4px; font-weight: 700; font-size: 12px;
}}
QTabBar::tab:selected {{ background: {PRIMARY}; color: white; }}

QCheckBox {{ color: {TEXT}; spacing: 6px; }}
"""


# pomoćni widgeti

def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


def button(text, variant=None, slot=None, checkable=False):
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        (b.toggled if checkable else b.clicked).connect(slot)
    return b


def label(text="", role=None, center=False):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if role:
        lbl.setObjectName(role)
    if center:
        lbl.setAlignment(Qt.AlignCenter)
    return lbl

# Ikonica u obojenom kvadratu + 'STEP n OF 6' + naslov + podnaslov.
def step_header(icon, step, title, subtitle=""):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(2, 2, 2, 6)
    h.setSpacing(12)
    badge = QLabel()
    badge.setObjectName("StepIcon")
    badge.setFixedSize(44, 44)
    badge.setAlignment(Qt.AlignCenter)
    badge.setPixmap(svg_pixmap(icon, PRIMARY, 24))
    h.addWidget(badge, 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(1)
    if step:
        kicker = label(f"STEP {step} OF {TOTAL_STEPS}", "StepKicker")
        kicker.setWordWrap(False)
        col.addWidget(kicker)
    col.addWidget(label(title, "StepTitle"))
    if subtitle:
        col.addWidget(label(subtitle, "StepSub"))
    h.addLayout(col, 1)
    return w

#  Veliki logo sa nazivom (prijava).

def hero(title, subtitle):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setSpacing(6)
    logo = QLabel()
    logo.setPixmap(svg_pixmap("logo", PRIMARY, 64))
    logo.setAlignment(Qt.AlignCenter)
    v.addWidget(logo)
    v.addWidget(label(title, "HeroTitle", center=True))
    v.addWidget(label(subtitle, "HeroSub", center=True))
    return w

# Panel: naslov koraka, sadržaj koji se skroluje i dugmad fiksirana dole.

def screen(header, body, footer=()):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    if header is not None:
        lay.addWidget(header)
    inner = QWidget()
    iv = QVBoxLayout(inner)
    iv.setContentsMargins(0, 0, 4, 0)
    iv.setSpacing(8)
    for x in body:
        iv.addWidget(x) if isinstance(x, QWidget) else iv.addLayout(x)
    iv.addStretch()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # sadržaj prati širinu panela
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)
    for x in footer:
        lay.addWidget(x) if isinstance(x, QWidget) else lay.addLayout(x)
    return w


#Kratki paneli (prijava, čekanje): sadržaj vertikalno centriran.
def centered(*widgets):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setSpacing(10)
    lay.addStretch()
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch()
    return w
