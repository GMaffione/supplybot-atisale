"""
Converte manuale_supply_chain.md in manuale_supply_chain.pdf (la knowledge base della RAG).

Il Markdown resta la fonte da modificare; il PDF si rigenera con:
    python genera_manuale_pdf.py
Gestisce il sottoinsieme di Markdown usato nel manuale: titoli #/##/###,
paragrafi, elenchi puntati e numerati, tabelle a barre verticali, citazioni (>)
e grassetto (**testo**).
"""

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (ListFlowable, ListItem, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

QUI = Path(__file__).parent
SORGENTE = QUI / "manuale_supply_chain.md"
OUTPUT = QUI / "manuale_supply_chain.pdf"

# Font DejaVu: copre accenti italiani e simbolo dell'euro. Se manca, si usa Helvetica.
FONT, FONT_B = "Helvetica", "Helvetica-Bold"
for base in ["/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts"]:
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", f"{base}/DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", f"{base}/DejaVuSans-Bold.ttf"))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")
        FONT, FONT_B = "DejaVu", "DejaVu-Bold"
        break
    except Exception:
        continue

BLU = colors.HexColor("#1f4e79")
GRIGIO = colors.HexColor("#5f6b7a")

stili = {
    "h1": ParagraphStyle("h1", fontName=FONT_B, fontSize=20, leading=25, textColor=BLU, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName=FONT_B, fontSize=14, leading=18, textColor=BLU, spaceBefore=14, spaceAfter=6, keepWithNext=1),
    "h3": ParagraphStyle("h3", fontName=FONT_B, fontSize=11.5, leading=15, textColor=BLU, spaceBefore=8, spaceAfter=4, keepWithNext=1),
    "p": ParagraphStyle("p", fontName=FONT, fontSize=10, leading=14.5, spaceAfter=6, alignment=TA_LEFT),
    "sub": ParagraphStyle("sub", fontName=FONT, fontSize=9.5, leading=13, textColor=GRIGIO, spaceAfter=10),
    "avviso": ParagraphStyle("avviso", fontName=FONT, fontSize=9, leading=12.5, textColor=colors.HexColor("#7a4b00"),
                             backColor=colors.HexColor("#fff4e0"), borderPadding=8, spaceBefore=4, spaceAfter=14),
    "cella": ParagraphStyle("cella", fontName=FONT, fontSize=9, leading=12),
    "cella_h": ParagraphStyle("cella_h", fontName=FONT_B, fontSize=9, leading=12, textColor=colors.white),
}


def inline(testo):
    """Grassetto Markdown -> tag reportlab, con escape dei caratteri speciali."""
    testo = testo.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", testo)


def tabella(righe):
    celle = [[c.strip() for c in r.strip().strip("|").split("|")] for r in righe]
    celle = [r for r in celle if not all(re.fullmatch(r":?-+:?", c) for c in r)]
    dati = [[Paragraph(inline(c), stili["cella_h"] if i == 0 else stili["cella"]) for c in r]
            for i, r in enumerate(celle)]
    larghezza = A4[0] - 4 * cm
    t = Table(dati, colWidths=[larghezza / len(celle[0])] * len(celle[0]), repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLU),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f9")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d0da")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [t, Spacer(1, 8)]


def elenco(righe, numerato):
    voci = [ListItem(Paragraph(inline(re.sub(r"^(\d+\.|-)\s+", "", r.strip())), stili["p"]), leftIndent=12)
            for r in righe]
    return [ListFlowable(voci, bulletType="1" if numerato else "bullet", start="1" if numerato else None,
                         bulletFontName=FONT, bulletFontSize=9, leftIndent=16)]


def converti(md):
    storia, blocco, tipo = [], [], None

    def chiudi():
        nonlocal blocco, tipo
        if not blocco:
            return
        if tipo == "tabella":
            storia.extend(tabella(blocco))
        elif tipo in ("ul", "ol"):
            storia.extend(elenco(blocco, tipo == "ol"))
        elif tipo == "avviso":
            storia.append(Paragraph(inline(" ".join(b.lstrip("> ") for b in blocco)), stili["avviso"]))
        else:
            storia.append(Paragraph("<br/>".join(inline(b) for b in blocco), stili["p"]))
        blocco, tipo = [], None

    primo_paragrafo = False
    for riga in md.splitlines():
        s = riga.strip()
        nuovo = ("tabella" if s.startswith("|") else "ol" if re.match(r"^\d+\.\s", s)
                 else "ul" if s.startswith("- ") else "avviso" if s.startswith(">") else "p")
        if not s or s.startswith("#") or nuovo != tipo:
            chiudi()
        if not s:
            continue
        if s.startswith("### "):
            storia.append(Paragraph(inline(s[4:]), stili["h3"]))
        elif s.startswith("## "):
            storia.append(Paragraph(inline(s[3:]), stili["h2"]))
        elif s.startswith("# "):
            storia.append(Paragraph(inline(s[2:]), stili["h1"]))
            primo_paragrafo = True
        elif primo_paragrafo and nuovo == "p":
            storia.append(Paragraph(inline(s), stili["sub"]))
            primo_paragrafo = False
        else:
            blocco.append(s)
            tipo = nuovo
    chiudi()
    return storia


def pie_di_pagina(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(GRIGIO)
    canvas.drawString(2 * cm, 1.2 * cm, "Manuale Operativo Supply Chain – documento simulato a scopo didattico")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Pagina {doc.page}")
    canvas.restoreState()


if __name__ == "__main__":
    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm,
                            title="Manuale Operativo Supply Chain (simulato)", author="Progetto SupplyBot")
    doc.build(converti(SORGENTE.read_text(encoding="utf-8")), onFirstPage=pie_di_pagina, onLaterPages=pie_di_pagina)
    print(f"Scritto {OUTPUT.name}")
