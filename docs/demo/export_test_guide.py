"""Optional documentation tool: export the Markdown test guide with ReportLab."""

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


def export():
    root = Path(__file__).parent
    font, bold = "Helvetica", "Helvetica-Bold"
    fonts = Path("/usr/share/fonts/truetype/dejavu")
    if (fonts / "DejaVuSans.ttf").exists():
        pdfmetrics.registerFont(TTFont("GuideSans", str(fonts / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("GuideBold", str(fonts / "DejaVuSans-Bold.ttf")))
        font, bold = "GuideSans", "GuideBold"
    navy, teal = colors.HexColor("#102F43"), colors.HexColor("#007F83")
    normal = ParagraphStyle(
        "Body", fontName=font, fontSize=9.7, leading=14.7, spaceAfter=7, textColor=navy
    )
    heading = ParagraphStyle(
        "Heading",
        parent=normal,
        fontName=bold,
        fontSize=15,
        leading=20,
        spaceBefore=16,
        spaceAfter=10,
        keepWithNext=True,
    )
    case = ParagraphStyle(
        "Question", parent=normal, fontName=bold, spaceBefore=9, keepWithNext=True
    )
    source = ParagraphStyle("Source", parent=normal, fontSize=8.4, textColor=teal)
    subtitle = ParagraphStyle("Title", parent=heading, fontSize=28, leading=35)
    content = [
        Spacer(1, 80),
        Paragraph("NORTHSTAR WORKSPACE", subtitle),
        Paragraph("ResolveAI evaluation and test guide", heading),
        Paragraph(
            "60 exercises with expected answers. Keep this guide separate from the uploaded company knowledge base.",
            normal,
        ),
        Paragraph(
            "Fictional company. Updated for the unified ResolveAI dashboard and PostgreSQL/pgvector workflow. 13 November 2026.",
            normal,
        ),
        PageBreak(),
    ]
    for line in (
        (root / "Northstar_Workspace_Test_Guide.md")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        line = line.strip()
        if not line or line.startswith("# "):
            continue
        style = (
            heading
            if line.startswith("## ")
            else (
                case
                if re.match(r"^[KUF]\d\d\.", line)
                else source if line.startswith("Source:") else normal
            )
        )
        line = re.sub(r"^#{2,3}\s+", "", line)
        content.append(Paragraph(escape(line), style))

    def page(canvas, document):
        canvas.saveState()
        canvas.setFillColor(teal)
        canvas.setFont(font, 8)
        canvas.drawString(51, A4[1] - 31, "NORTHSTAR WORKSPACE / RESOLVEAI TEST GUIDE")
        canvas.drawString(
            51,
            27,
            "Fictional demonstration data | Expected answers stay outside the knowledge base",
        )
        canvas.drawRightString(A4[0] - 51, 27, str(document.page))
        canvas.restoreState()

    destination = root / "Northstar_Workspace_Test_Guide.pdf"
    document = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        leftMargin=51,
        rightMargin=51,
        topMargin=61,
        bottomMargin=49,
        title="Northstar Workspace ResolveAI Test Guide",
        author="Northstar Workspace (fictional)",
    )
    document.build(content, onFirstPage=page, onLaterPages=page)
    print(f"Generated {destination.name}")


if __name__ == "__main__":
    export()
