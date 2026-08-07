#!/usr/bin/env python3
"""
Generate docs/HOW_IT_WORKS.pdf from docs/HOW_IT_WORKS.md using reportlab.

Supports the markdown subset used in the document: #/##/### headings,
--- separators, bullet & numbered lists, ``` code fences, |tables|,
**bold**, *italic*, and `inline code`.
"""
import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, Preformatted, HRFlowable, KeepTogether,
)
from reportlab.pdfbase.pdfmetrics import stringWidth

HERE = Path(__file__).resolve().parent
MD_PATH = HERE / "HOW_IT_WORKS.md"
PDF_PATH = HERE / "HOW_IT_WORKS.pdf"

ACCENT = HexColor("#16324f")
ACCENT_LIGHT = HexColor("#eef2f8")
CODE_BG = HexColor("#f5f5f5")
BODY = HexColor("#1d1d1d")
GRAY = HexColor("#6b6b6b")

PAGE_W, PAGE_H = A4
MARGIN = 2.0 * cm

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _style(name, **kw):
    base = dict(
        fontName="Helvetica",
        fontSize=9.6,
        leading=13.6,
        textColor=BODY,
        alignment=TA_JUSTIFY,
        spaceBefore=0,
        spaceAfter=5,
    )
    base.update(kw)
    return ParagraphStyle(name, **base)

S_TITLE = _style("title", fontName="Helvetica-Bold", fontSize=23, leading=28,
                 textColor=ACCENT, alignment=TA_LEFT, spaceAfter=2)
S_SUBTITLE = _style("subtitle", fontSize=10.5, leading=14, textColor=GRAY,
                    alignment=TA_LEFT, spaceAfter=6)
S_H1 = _style("h1", fontName="Helvetica-Bold", fontSize=15.5, leading=19,
              textColor=ACCENT, alignment=TA_LEFT, spaceBefore=2, spaceAfter=7)
S_H2 = _style("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15,
              textColor=ACCENT, alignment=TA_LEFT, spaceBefore=9, spaceAfter=4)
S_BODY = _style("body")
S_BULLET = _style("bullet", leftIndent=14, bulletIndent=4, spaceAfter=3)
S_CODEBLOCK = ParagraphStyle("codeblock", fontName="Courier", fontSize=7.8,
                             leading=10.2, textColor=HexColor("#24292e"))
S_CELL = ParagraphStyle("cell", fontSize=8.8, leading=12, textColor=BODY,
                        alignment=TA_LEFT)
S_CELLH = ParagraphStyle("cellh", fontName="Helvetica-Bold", fontSize=8.8,
                         leading=12, textColor=white, alignment=TA_LEFT)

# ---------------------------------------------------------------------------
# Inline markup: `code`, **bold**, *italic*
# ---------------------------------------------------------------------------

INLINE_RE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)")

def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _inline(text: str) -> str:
    def repl(m):
        tok = m.group(1)
        if tok.startswith("`"):
            return f'<font face="Courier" size="8.3">{_escape(tok[1:-1])}</font>'
        if tok.startswith("**"):
            return "<b>" + _escape(tok[2:-2]) + "</b>"
        return "<i>" + _escape(tok[1:-1]) + "</i>"
    return INLINE_RE.sub(repl, _escape(text))

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _split_rows(line: str):
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]

def _parse_blocks(lines):
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip()

        if line.strip() == "---":
            blocks.append(("rule", None))
            i += 1
            continue

        if line.startswith("```"):
            code = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                code.append(lines[i].rstrip())
                i += 1
            i += 1  # skip closing fence
            blocks.append(("code", "\n".join(code)))
            continue

        if not line.strip():
            i += 1
            continue

        if line.startswith("####"):
            blocks.append(("h3", _inline(line[5:].strip())))
            i += 1
            continue
        if line.startswith("###"):
            blocks.append(("h2", _inline(line[4:].strip())))
            i += 1
            continue
        if line.startswith("##"):
            blocks.append(("h1", _inline(line[3:].strip())))
            i += 1
            continue
        if line.startswith("# "):
            blocks.append(("title", _inline(line[2:].strip())))
            i += 1
            continue

        # table: row followed by separator row
        if line.startswith("|") and i + 1 < n and re.match(
                r"^\|\s*:?-{2,}\s*(\|\s*:?-{2,}\s*)*\|?\s*$", lines[i + 1]):
            header = _split_rows(line)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_rows(lines[i]))
                i += 1
            blocks.append(("table", (header, rows)))
            continue

        # bullet list (consecutive "- " lines)
        if line.startswith("- "):
            items = []
            while i < n and lines[i].startswith("- "):
                items.append(_inline(lines[i][2:].strip()))
                i += 1
            blocks.append(("bullets", items))
            continue

        # numbered list (consecutive "1. " lines)
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            items = []
            while i < n:
                m2 = re.match(r"^(\d+)\.\s+(.*)$", lines[i])
                if not m2:
                    break
                items.append(_inline(m2.group(2).strip()))
                i += 1
            blocks.append(("numbered", items))
            continue

        # plain paragraph (join continuation lines)
        para = [line]
        i += 1
        while i < n:
            nxt = lines[i].rstrip()
            if (not nxt or nxt.startswith(("#", "- ", "|", "```"))
                    or re.match(r"^(\d+)\.\s+", nxt) or nxt.strip() == "---"):
                break
            para.append(nxt)
            i += 1
        blocks.append(("para", _inline(" ".join(x.strip() for x in para))))
    return blocks

# ---------------------------------------------------------------------------
# Flowables
# ---------------------------------------------------------------------------

def _build_flowables(blocks):
    story = []
    first_h1 = True
    for kind, payload in blocks:
        if kind == "title":
            story.append(Paragraph(payload, S_TITLE))
            story.append(HRFlowable(width="100%", thickness=1.2,
                                    color=ACCENT, spaceBefore=4, spaceAfter=10))
        elif kind == "h1":
            if not first_h1:
                story.append(PageBreak())
            first_h1 = False
            story.append(Paragraph(payload, S_H1))
            story.append(HRFlowable(width="100%", thickness=0.8,
                                    color=ACCENT_LIGHT, spaceBefore=1, spaceAfter=8))
        elif kind == "h2":
            story.append(Paragraph(payload, S_H2))
        elif kind == "h3":
            story.append(Paragraph(payload, S_H2))
        elif kind == "para":
            story.append(Paragraph(payload, S_BODY))
        elif kind == "bullets":
            for it in payload:
                story.append(Paragraph(it, S_BULLET, bulletText="\u2022"))
        elif kind == "numbered":
            for idx, it in enumerate(payload, start=1):
                story.append(Paragraph(it, S_BULLET, bulletText=f"{idx}."))
        elif kind == "code":
            story.append(Spacer(1, 2))
            story.append(Preformatted(payload, S_CODEBLOCK))
            story.append(Spacer(1, 6))
        elif kind == "table":
            header, rows = payload
            data = [[Paragraph(_inline(h), S_CELLH) for h in header]]
            data += [[Paragraph(_inline(c), S_CELL) for c in row] for row in rows]
            widths = [4.0 * cm, PAGE_W - 2 * MARGIN - 4.0 * cm]
            t = Table(data, colWidths=widths, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [white, ACCENT_LIGHT]),
                ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#c3cbd8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(Spacer(1, 3))
            story.append(t)
            story.append(Spacer(1, 6))
        elif kind == "rule":
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.6,
                                    color=ACCENT_LIGHT, spaceBefore=2, spaceAfter=8))
    return story

# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------

def _on_page(canv, doc):
    canv.saveState()
    canv.setStrokeColor(ACCENT)
    canv.setLineWidth(0.8)
    canv.line(MARGIN, PAGE_H - 1.15 * cm, PAGE_W - MARGIN, PAGE_H - 1.15 * cm)
    canv.setFillColor(GRAY)
    canv.setFont("Helvetica", 7.5)
    canv.drawString(MARGIN, PAGE_H - 1.0 * cm, "Harpocrates \u2014 Steganography Framework: How It Works")
    canv.drawRightString(PAGE_W - MARGIN, PAGE_H - 1.0 * cm, f"{doc.page}")
    canv.setStrokeColor(HexColor("#d8dde5"))
    canv.setLineWidth(0.4)
    canv.line(MARGIN, 1.35 * cm, PAGE_W - MARGIN, 1.35 * cm)
    canv.setFont("Helvetica-Oblique", 7.5)
    canv.drawString(MARGIN, 1.1 * cm, "Generated from docs/HOW_IT_WORKS.md")
    canv.restoreState()

def main():
    text = MD_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    blocks = _parse_blocks(lines)
    story = _build_flowables(blocks)

    doc = BaseDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=1.7 * cm, bottomMargin=1.7 * cm,
        title="Harpocrates \u2014 Steganography Framework: How It Works",
        author="Harpocrates",
    )
    frame = Frame(MARGIN, 1.7 * cm, PAGE_W - 2 * MARGIN, PAGE_H - 3.4 * cm, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=_on_page)])
    doc.build(story)
    print(f"PDF written to {PDF_PATH} ({doc.page} pages)")

if __name__ == "__main__":
    sys.exit(main())
