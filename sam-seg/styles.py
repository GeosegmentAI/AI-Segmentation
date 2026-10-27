# Načini prikaza detektovanih poligona (padajući meni "View detection").
from qgis.core import (QgsFillSymbol, QgsPalLayerSettings, QgsProperty, QgsSymbolLayer, QgsTextBufferSettings,
                       QgsTextFormat, QgsVectorLayerSimpleLabeling)

VIEWS = ["Normal", "Outline", "Distinct", "Confidence"]
MAGENTA = "255,0,255"
# boje klasa kad rezultat ima više labela (prva klasa je magenta, kao kad je klasa jedna)
PALETTE = ["255,0,255", "0,170,255", "255,160,0", "0,200,90", "230,40,40", "140,90,255", "0,200,200", "160,160,0"]


def class_colors(labels):
    """{label: "r,g,b"} za sortirane labele; ciklično ako ih ima više od boja."""
    return {lb: PALETTE[i % len(PALETTE)] for i, lb in enumerate(sorted(labels))}


def _color_expr(colors):
    # izraz koji vraća "r,g,b" po labeli poligona (ostalo = magenta)
    parts = " ".join(f"WHEN \"label\" = '{lb.replace(chr(39), chr(39) * 2)}' THEN '{c}'" for lb, c in colors.items())
    return f"CASE {parts} ELSE '{MAGENTA}' END"


def result_labels(layer):
    idx = layer.fields().indexOf("label")
    return sorted(v for v in layer.uniqueValues(idx) if v not in (None, "")) if idx >= 0 else []


def style_results(layer, view, labels=None):
    # labels: klase zapamćene pri učitavanju (stabilne boje i kad filteri sakriju neku klasu)
    labels = labels if labels is not None else result_labels(layer)
    multi = len(labels) > 1
    base = _color_expr(class_colors(labels)) if multi else None

    def alpha(a):  # a je broj ili izraz
        return QgsProperty.fromExpression(f"set_color_part({base}, 'alpha', {a})")

    if view == "Outline":
        sym = QgsFillSymbol.createSimple({"style": "no", "outline_color": MAGENTA, "outline_width": "0.7"})
        if multi:
            sym.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, QgsProperty.fromExpression(base))
    elif view == "Distinct":
        # svaki poligon svoja boja (zlatni ugao po ID-u -> susedi se razlikuju)
        sym = QgsFillSymbol.createSimple({"color": f"{MAGENTA},150", "outline_color": "40,40,40",
                                          "outline_width": "0.3"})
        sym.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.PropertyFillColor,
            QgsProperty.fromExpression("color_hsva(($id * 137) % 360, 75, 95, 150)"))
    elif view == "Confidence":
        # jedna boja, jačina prati pouzdanost (10% -> bledo, 90% -> puno)
        sym = QgsFillSymbol.createSimple({"color": f"{MAGENTA},120", "outline_color": MAGENTA,
                                          "outline_width": "0.4"})
        a = 'scale_linear("score", 0.1, 0.9, 25, 230)'
        if multi:
            sym.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, alpha(a))
            sym.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, QgsProperty.fromExpression(base))
        else:
            sym.symbolLayer(0).setDataDefinedProperty(
                QgsSymbolLayer.PropertyFillColor,
                QgsProperty.fromExpression(f'color_rgba({MAGENTA}, {a})'))
    else:  # Normal
        sym = QgsFillSymbol.createSimple({"color": f"{MAGENTA},40", "outline_color": MAGENTA,
                                          "outline_width": "0.5"})
        if multi:
            sym.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, alpha(40))
            sym.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, QgsProperty.fromExpression(base))
    layer.renderer().setSymbol(sym)
    layer.triggerRepaint()


def set_box_labels(layer, on):
    """Natpis na okviru: klasa i pouzdanost (npr. "house 87%")."""
    if not on:
        layer.setLabelsEnabled(False)
        layer.triggerRepaint()
        return
    cfg = QgsPalLayerSettings()
    cfg.fieldName = "\"label\" || ' ' || round(\"score\" * 100) || '%'"
    cfg.isExpression = True
    fmt = QgsTextFormat()
    fmt.setSize(9)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1)
    fmt.setBuffer(buf)
    cfg.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(cfg))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
