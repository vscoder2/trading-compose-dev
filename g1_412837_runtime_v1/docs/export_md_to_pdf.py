#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, Preformatted, SimpleDocTemplate, Spacer


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontSize=18,
            leading=22,
            spaceBefore=10,
            spaceAfter=8,
            textColor=colors.HexColor("#0f172a"),
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=14,
            leading=18,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#0f172a"),
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base["Heading3"],
            fontSize=12,
            leading=16,
            spaceBefore=8,
            spaceAfter=4,
            textColor=colors.HexColor("#0f172a"),
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontSize=9.6,
            leading=13,
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base["BodyText"],
            fontSize=9.6,
            leading=13,
            leftIndent=14,
            bulletIndent=4,
            spaceAfter=2,
        ),
        "mono": ParagraphStyle(
            "mono",
            parent=base["Code"],
            fontName="Courier",
            fontSize=8.2,
            leading=10.5,
            leftIndent=4,
            rightIndent=4,
            textColor=colors.HexColor("#0b1324"),
            backColor=colors.HexColor("#f8fafc"),
        ),
    }
    return styles


def render_markdown_to_pdf(md_path: Path, pdf_path: Path) -> None:
    styles = _styles()
    text = md_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    story = []
    in_code = False
    code_buf: list[str] = []
    table_buf: list[str] = []

    def flush_code():
        nonlocal code_buf
        if code_buf:
            story.append(Preformatted("\n".join(code_buf), styles["mono"]))
            story.append(Spacer(1, 0.08 * inch))
            code_buf = []

    def flush_table():
        nonlocal table_buf
        if table_buf:
            story.append(Preformatted("\n".join(table_buf), styles["mono"]))
            story.append(Spacer(1, 0.08 * inch))
            table_buf = []

    for raw in lines:
        line = raw.rstrip("\n")

        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_table()
                in_code = True
            continue

        if in_code:
            code_buf.append(line)
            continue

        if line.strip().startswith("|"):
            table_buf.append(line)
            continue
        else:
            flush_table()

        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 0.06 * inch))
            continue

        # Markdown image: ![alt](relative/or/abs/path.png)
        m = re.match(r"!\[(.*?)\]\((.*?)\)", stripped)
        if m:
            alt = m.group(1).strip()
            src = m.group(2).strip()
            img_path = (md_path.parent / src).resolve() if not Path(src).is_absolute() else Path(src)
            if img_path.exists():
                max_w = 6.8 * inch
                max_h = 4.8 * inch
                img = Image(str(img_path))
                iw, ih = img.imageWidth, img.imageHeight
                scale = min(max_w / iw, max_h / ih, 1.0)
                img.drawWidth = iw * scale
                img.drawHeight = ih * scale
                story.append(img)
                if alt:
                    story.append(Paragraph(alt, styles["body"]))
                story.append(Spacer(1, 0.08 * inch))
            else:
                story.append(Paragraph(f"[Missing image: {src}]", styles["body"]))
            continue

        if stripped.startswith("# "):
            story.append(Paragraph(stripped[2:].strip(), styles["h1"]))
            continue
        if stripped.startswith("## "):
            story.append(Paragraph(stripped[3:].strip(), styles["h2"]))
            continue
        if stripped.startswith("### "):
            story.append(Paragraph(stripped[4:].strip(), styles["h3"]))
            continue

        if stripped.startswith("- "):
            story.append(Paragraph(stripped[2:].strip(), styles["bullet"], bulletText="•"))
            continue

        if stripped[0].isdigit() and ". " in stripped[:5]:
            story.append(Paragraph(stripped, styles["body"]))
            continue

        # Simple markdown emphasis cleanup for PDF readability.
        text_line = stripped.replace("**", "").replace("`", "")
        story.append(Paragraph(text_line, styles["body"]))

    flush_code()
    flush_table()

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=md_path.stem,
        author="Codex",
    )
    doc.build(story)


def main() -> int:
    p = argparse.ArgumentParser(description="Render markdown to PDF using reportlab.")
    p.add_argument("--input", required=True, help="Path to markdown input.")
    p.add_argument("--output", required=True, help="Path to PDF output.")
    args = p.parse_args()

    md_path = Path(args.input).resolve()
    pdf_path = Path(args.output).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    render_markdown_to_pdf(md_path, pdf_path)
    print(f"Wrote: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
