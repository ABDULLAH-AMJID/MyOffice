"""Generate the bundled templates (resources/templates/<kind>/*.wpt|*.ett|*.dpt + PNG thumbnails + index.json).

Run:  QT_QPA_PLATFORM=offscreen python tools/make_templates.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = ROOT / "resources" / "templates"


# ============================================================================= Writer
def writer_doc():
    from app.writer.editor import WriterEditor

    ed = WriterEditor()
    return ed


def w_para(ed, text, style="Normal", align=None, bold=False, size=None, color=None, rtl=False):
    from PySide6.QtGui import QTextCharFormat

    from app.writer.model import apply_style

    c = ed.textCursor()
    if ed.document().characterCount() > 1:
        c.movePosition(QTextCursor.End)
        c.insertBlock()
    apply_style(c, style, 11)
    bf = c.blockFormat()
    bf.setAlignment(Qt.AlignLeft)
    if align == "center":
        bf.setAlignment(Qt.AlignHCenter)
    elif align == "right":
        bf.setAlignment(Qt.AlignRight)
    if rtl:
        bf.setLayoutDirection(Qt.RightToLeft)
        bf.setAlignment(Qt.AlignLeft)
    c.setBlockFormat(bf)
    f = QTextCharFormat(c.charFormat())
    if bold:
        f.setFontWeight(QFont.Bold)
    if size:
        f.setFontPointSize(size)
    if color:
        f.setForeground(QColor(color))
    if rtl:
        f.setFontFamilies(["Noto Nastaliq Urdu"])
    c.insertText(text, f)
    ed.setTextCursor(c)


def w_table(ed, rows, header=True, widths=None):
    from PySide6.QtGui import QTextCharFormat, QTextLength, QTextTableFormat

    c = ed.textCursor()
    c.movePosition(QTextCursor.End)
    c.insertBlock()
    tf = QTextTableFormat()
    tf.setBorder(1)
    tf.setCellPadding(4)
    tf.setCellSpacing(0)
    tf.setWidth(QTextLength(QTextLength.PercentageLength, 100))
    n = len(rows[0])
    tf.setColumnWidthConstraints([QTextLength(QTextLength.PercentageLength, w) for w in (widths or [100 / n] * n)])
    t = c.insertTable(len(rows), n, tf)
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = t.cellAt(i, j)
            cc = cell.firstCursorPosition()
            f = QTextCharFormat()
            if i == 0 and header:
                f.setFontWeight(QFont.Bold)
                cf = cell.format()
                cf.setBackground(QColor("#d9e2f3"))
                cell.setFormat(cf)
            cc.insertText(str(val), f)
    end = t.lastCursorPosition()
    end.movePosition(QTextCursor.NextBlock)
    ed.setTextCursor(end)


def save_writer(ed, name, title, category, desc, entries, hf=None):
    from app.writer.docx_io import save_docx
    from app.writer.model import HeaderFooter
    from app.writer.render import page_image

    folder = OUT / "writer"
    folder.mkdir(parents=True, exist_ok=True)
    if hf:
        ed.hf = HeaderFooter(**hf)
    path = folder / f"{name}.wpt"
    save_docx(str(path), ed.document(), ed.page, ed.hf, {}, ed.image_bytes, title=title, author="MyOffice")
    img = page_image(ed.document(), ed.page, ed.hf, 0, 300)
    img.save(str(folder / f"{name}.png"))
    entries.append({"file": path.name, "name": title, "category": category, "description": desc,
                    "thumbnail": f"{name}.png"})


def make_writer(entries):
    ed = writer_doc()
    w_para(ed, "Your Company Name", "Title")
    w_para(ed, "123 Main Street \u2022 Sahiwal, Punjab \u2022 +92 300 0000000 \u2022 info@company.pk", "Subtitle")
    w_para(ed, "15 October 2026")
    w_para(ed, "Mr. Recipient Name\nDesignation\nOrganisation\nAddress")
    w_para(ed, "Subject: Write the subject of your letter here", bold=True)
    w_para(ed, "Dear Sir/Madam,")
    w_para(ed, "Start your letter with a short introduction that explains why you are writing. Keep the first paragraph "
               "clear and to the point.")
    w_para(ed, "Use the second paragraph to give details, facts or background. Replace this sample text with your own "
               "words; all formatting is already set up.")
    w_para(ed, "Close with a call to action and thank the reader for their time.")
    w_para(ed, "Yours sincerely,")
    w_para(ed, "\n\nYour Name\nYour Title")
    save_writer(ed, "business_letter", "Business Letter", "Letters", "Formal letter with company heading", entries,
                {"footer": "Page {PAGE} of {NUMPAGES}", "footer_align": "center"})

    ed = writer_doc()
    w_para(ed, "AYESHA KHAN", "Title", align="center")
    w_para(ed, "Software Engineer \u2022 Lahore, Pakistan \u2022 ayesha@example.com \u2022 linkedin.com/in/ayesha", "Subtitle", align="center")
    w_para(ed, "Profile", "Heading 1")
    w_para(ed, "Results-driven engineer with 5 years of experience building desktop and web applications in Python and "
               "Qt. Passionate about Urdu localisation and accessible software.")
    w_para(ed, "Experience", "Heading 1")
    w_para(ed, "Senior Developer \u2014 TechSoft (2023\u2013present)", "Heading 3")
    w_para(ed, "Led a team of four building an office productivity suite; reduced load times by 60%.", "List Paragraph")
    w_para(ed, "Developer \u2014 DataWorks (2020\u20132023)", "Heading 3")
    w_para(ed, "Built reporting tools with pandas and openpyxl used by 200+ clients.", "List Paragraph")
    w_para(ed, "Education", "Heading 1")
    w_para(ed, "BS Computer Science \u2014 University of the Punjab (2016\u20132020)")
    w_para(ed, "Skills", "Heading 1")
    w_table(ed, [["Languages", "Frameworks", "Tools"], ["Python, C++, SQL", "Qt / PySide6, Django", "Git, Docker, Linux"]])
    save_writer(ed, "resume", "Resume / CV", "Personal", "Clean one-page CV", entries)

    ed = writer_doc()
    w_para(ed, "Meeting Minutes", "Title")
    w_table(ed, [["Date", "Time", "Location"], ["15 Oct 2026", "10:00\u201311:00", "Conference Room A"]])
    w_para(ed, "Attendees", "Heading 2")
    w_para(ed, "Name 1, Name 2, Name 3")
    w_para(ed, "Agenda", "Heading 2")
    for item in ("Review of previous action items", "Project status", "Budget", "Any other business"):
        w_para(ed, item, "List Paragraph")
    w_para(ed, "Decisions", "Heading 2")
    w_para(ed, "Record the decisions taken in the meeting here.")
    w_para(ed, "Action Items", "Heading 2")
    w_table(ed, [["Action", "Owner", "Due date", "Status"], ["", "", "", ""], ["", "", "", ""], ["", "", "", ""]],
            widths=[46, 18, 18, 18])
    save_writer(ed, "meeting_minutes", "Meeting Minutes", "Business", "Agenda, decisions and action items", entries)

    ed = writer_doc()
    w_para(ed, "INVOICE", "Title", align="right")
    w_para(ed, "Invoice #: INV-2026-001\nDate: 15 Oct 2026\nDue: 30 Oct 2026", align="right")
    w_para(ed, "Bill to:", bold=True)
    w_para(ed, "Client Name\nClient Address\nCity")
    w_table(ed, [["Description", "Qty", "Unit price", "Amount"], ["Consulting services", "10", "Rs 5,000", "Rs 50,000"],
                 ["Software licence", "1", "Rs 25,000", "Rs 25,000"], ["", "", "Total", "Rs 75,000"]],
            widths=[46, 12, 20, 22])
    w_para(ed, "Payment details: Bank name \u2022 Account title \u2022 IBAN PK00 XXXX 0000 0000 0000 0000")
    w_para(ed, "Thank you for your business!", align="center", bold=True, color="#2f5496")
    save_writer(ed, "invoice", "Invoice", "Business", "Professional invoice layout", entries)

    ed = writer_doc()
    w_para(ed, "\u0628\u062e\u062f\u0645\u062a \u062c\u0646\u0627\u0628 \u067e\u0631\u0646\u0633\u067e\u0644 \u0635\u0627\u062d\u0628", rtl=True, size=16)
    w_para(ed, "\u06af\u0648\u0631\u0646\u0645\u0646\u0679 \u06c1\u0627\u0626\u06cc \u0633\u06a9\u0648\u0644\u060c \u0633\u0627\u06c1\u06cc\u0648\u0627\u0644", rtl=True, size=14)
    w_para(ed, "\u0645\u0648\u0636\u0648\u0639: \u0686\u06be\u0679\u06cc \u06a9\u06cc \u062f\u0631\u062e\u0648\u0627\u0633\u062a", rtl=True, size=14, bold=True)
    w_para(ed, "\u062c\u0646\u0627\u0628 \u0639\u0627\u0644\u06cc!", rtl=True, size=14)
    w_para(ed, "\u0645\u0624\u062f\u0628\u0627\u0646\u06c1 \u06af\u0632\u0627\u0631\u0634 \u06c1\u06d2 \u06a9\u06c1 \u0645\u062c\u06be\u06d2 \u0636\u0631\u0648\u0631\u06cc \u06a9\u0627\u0645 \u06a9\u06cc \u0648\u062c\u06c1 \u0633\u06d2 \u062f\u0648 \u062f\u0646 \u06a9\u06cc \u0686\u06be\u0679\u06cc \u062f\u0631\u06a9\u0627\u0631 \u06c1\u06d2\u06d4 "
               "\u0644\u06c1\u0630\u0627 \u0645\u062c\u06be\u06d2 \u0686\u06be\u0679\u06cc \u0639\u0646\u0627\u06cc\u062a \u0641\u0631\u0645\u0627 \u06a9\u0631 \u0645\u0645\u0646\u0648\u0646 \u0641\u0631\u0645\u0627\u0626\u06cc\u06ba\u06d4", rtl=True, size=14)
    w_para(ed, "\u0622\u067e \u06a9\u0627 \u0641\u0631\u0645\u0627\u0646\u0628\u0631\u062f\u0627\u0631 \u0634\u0627\u06af\u0631\u062f\n\u0646\u0627\u0645: ____________\n\u062c\u0645\u0627\u0639\u062a: ________\n\u062a\u0627\u0631\u06cc\u062e: ________", rtl=True, size=14)
    save_writer(ed, "urdu_application", "\u062f\u0631\u062e\u0648\u0627\u0633\u062a (Urdu Application)", "Urdu",
                "Right-to-left Urdu application in Nastaliq", entries)

    ed = writer_doc()
    w_para(ed, "Annual Report 2026", "Title")
    w_para(ed, "Prepared by the Finance Department", "Subtitle")
    w_para(ed, "Executive Summary", "Heading 1")
    w_para(ed, "Summarise the year's key results here: revenue, growth, milestones and outlook.")
    w_para(ed, "Financial Highlights", "Heading 1")
    w_table(ed, [["Metric", "2025", "2026", "Change"], ["Revenue", "120 M", "150 M", "+25%"], ["Profit", "18 M", "24 M", "+33%"],
                 ["Customers", "1,200", "1,650", "+38%"]])
    w_para(ed, "Outlook", "Heading 1")
    w_para(ed, "Describe the plans and expectations for the coming year.")
    save_writer(ed, "annual_report", "Annual Report", "Reports", "Report with summary and highlights table", entries,
                {"header": "{TITLE}", "header_align": "right", "footer": "Page {PAGE}", "footer_align": "center"})


# ============================================================================= Sheet
def save_sheet(book, name, title, category, desc, entries):
    from app.sheet.printing import paginate, paint_page
    from app.sheet.xlsx_io import save_xlsx

    folder = OUT / "sheet"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.ett"
    save_xlsx(str(path), book)
    sh = book.sheets[0]
    img = QImage(300, 400, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.white)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    scale, pages = paginate(sh, 600, 800)
    rows, cols = pages[0]
    p.scale(0.5, 0.5)
    paint_page(p, book, sh, rows, cols, scale, 0, 0, True, {})
    p.end()
    img.save(str(folder / f"{name}.png"))
    entries.append({"file": path.name, "name": title, "category": category, "description": desc, "thumbnail": f"{name}.png"})


def make_sheets(entries):
    from app.sheet.model import Book, Border, ChartSpec, CondFormat

    def setup(book, sh, rows, start=1):
        for r, row in enumerate(rows, start=start):
            for c, v in enumerate(row, start=1):
                if v is not None and v != "":
                    book.set_input(sh, r, c, str(v))

    def style(book, sh, rng, **kw):
        r1, c1, r2, c2 = rng
        st = book.styled_states(sh, [(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)], lambda s: s.with_(**kw))
        book.apply_states(sh, st)

    hdr = dict(bold=True, fill="#1f4e79", color="#ffffff", halign="center")
    b = Book()
    sh = b.add_sheet("Budget")
    setup(b, sh, [["Monthly Budget", "", "", ""], ["", "", "", ""], ["Category", "Planned", "Actual", "Difference"],
                  ["Rent", 50000, 50000], ["Utilities", 12000, 13850], ["Groceries", 30000, 27400], ["Transport", 8000, 9100],
                  ["Education", 15000, 15000], ["Savings", 20000, 18000], ["Other", 5000, 3200]])
    for r in range(4, 11):
        b.set_input(sh, r, 4, f"=B{r}-C{r}")
    setup(b, sh, [["Total", "=SUM(B4:B10)", "=SUM(C4:C10)", "=SUM(D4:D10)"]], start=11)
    style(b, sh, (1, 1, 1, 1), bold=True, size=18.0, color="#1f4e79")
    style(b, sh, (3, 1, 3, 4), **hdr)
    style(b, sh, (4, 2, 11, 4), numfmt='"Rs" #,##0')
    style(b, sh, (11, 1, 11, 4), bold=True, border_top=Border("thin"), border_bottom=Border("double"))
    sh.col_widths.update({1: 150, 2: 110, 3: 110, 4: 110})
    sh.cond_formats.append(CondFormat((4, 4, 10, 4), "cell", "<", "0", fill="#ffc7ce", color="#9c0006"))
    sh.charts.append(ChartSpec("pie", "Budget!A3:C10", "Actual spending", has_header=True, anchor=(3, 6), size=(380, 260)))
    sh.freeze = (3, 0)
    save_sheet(b, "monthly_budget", "Monthly Budget", "Personal", "Planned vs actual with chart", entries)

    b = Book()
    sh = b.add_sheet("Invoice")
    setup(b, sh, [["INVOICE"], [""], ["Invoice #", "INV-001", "", "Date", "2026-10-15"], ["Customer", "Customer name"], [""],
                  ["Item", "Description", "Qty", "Unit price", "Amount"]])
    for r in range(7, 13):
        b.set_input(sh, r, 5, f'=IF(C{r}="","",C{r}*D{r})')
    setup(b, sh, [["A-100", "Product A", 2, 1500], ["B-200", "Service B", 5, 800]], start=7)
    setup(b, sh, [["", "", "", "Subtotal", "=SUM(E7:E12)"], ["", "", "", "Tax 17%", "=E14*0.17"],
                  ["", "", "", "Total", "=E14+E15"]], start=14)
    style(b, sh, (1, 1, 1, 1), bold=True, size=20.0, color="#1f4e79")
    style(b, sh, (6, 1, 6, 5), **hdr)
    style(b, sh, (7, 4, 16, 5), numfmt='"Rs" #,##0.00')
    style(b, sh, (16, 4, 16, 5), bold=True, border_top=Border("thin"), border_bottom=Border("double"))
    sh.col_widths.update({1: 80, 2: 200, 3: 60, 4: 100, 5: 110})
    save_sheet(b, "invoice", "Invoice", "Business", "Invoice with automatic totals and tax", entries)

    b = Book()
    sh = b.add_sheet("Attendance")
    days = [str(d) for d in range(1, 16)]
    setup(b, sh, [["Attendance \u2014 October 2026"], ["Name"] + days + ["Present", "%"]])
    names = ["Ali", "Sara", "Usman", "Fatima", "Bilal", "Ayesha"]
    import random

    random.seed(7)
    for i, n in enumerate(names, start=3):
        b.set_input(sh, i, 1, n)
        for d in range(15):
            b.set_input(sh, i, 2 + d, "P" if random.random() > 0.15 else "A")
        b.set_input(sh, i, 17, f'=COUNTIF(B{i}:P{i},"P")')
        b.set_input(sh, i, 18, f"=Q{i}/15")
    style(b, sh, (1, 1, 1, 1), bold=True, size=16.0, color="#1f4e79")
    style(b, sh, (2, 1, 2, 18), **hdr)
    style(b, sh, (3, 18, 8, 18), numfmt="0%")
    for c in range(2, 17):
        sh.col_widths[c] = 30
    sh.col_widths[1] = 110
    sh.cond_formats.append(CondFormat((3, 2, 8, 16), "text", "contains", "A", fill="#ffc7ce", color="#9c0006"))
    sh.cond_formats.append(CondFormat((3, 18, 8, 18), "databar", fill="#63be7b"))
    save_sheet(b, "attendance", "Attendance Sheet", "Education", "Monthly attendance with percentages", entries)

    b = Book()
    sh = b.add_sheet("Sales")
    setup(b, sh, [["Sales Dashboard 2026"], [""], ["Month", "North", "South", "East", "West", "Total"]])
    random.seed(3)
    for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=4):
        b.set_input(sh, i, 1, m)
        for c in range(2, 6):
            b.set_input(sh, i, c, str(random.randint(40, 120) * 1000))
        b.set_input(sh, i, 6, f"=SUM(B{i}:E{i})")
    setup(b, sh, [["Total", "=SUM(B4:B15)", "=SUM(C4:C15)", "=SUM(D4:D15)", "=SUM(E4:E15)", "=SUM(F4:F15)"]], start=16)
    style(b, sh, (1, 1, 1, 1), bold=True, size=18.0, color="#1f4e79")
    style(b, sh, (3, 1, 3, 6), **hdr)
    style(b, sh, (4, 2, 16, 6), numfmt="#,##0")
    style(b, sh, (16, 1, 16, 6), bold=True, border_top=Border("thin"), border_bottom=Border("double"))
    sh.charts.append(ChartSpec("line", "Sales!A3:E15", "Monthly sales by region", anchor=(3, 8), size=(480, 300)))
    sh.cond_formats.append(CondFormat((4, 6, 15, 6), "scale3"))
    save_sheet(b, "sales_dashboard", "Sales Dashboard", "Business", "Regional sales with line chart", entries)

    b = Book()
    sh = b.add_sheet("Grades")
    setup(b, sh, [["Gradebook"], ["Student", "Test 1", "Test 2", "Project", "Final", "Average", "Grade"]])
    for i, n in enumerate(names, start=3):
        b.set_input(sh, i, 1, n)
        for c in range(2, 6):
            b.set_input(sh, i, c, str(random.randint(55, 98)))
        b.set_input(sh, i, 6, f"=AVERAGE(B{i}:E{i})")
        b.set_input(sh, i, 7, f'=IFS(F{i}>=90,"A",F{i}>=80,"B",F{i}>=70,"C",F{i}>=60,"D",TRUE,"F")')
    style(b, sh, (1, 1, 1, 1), bold=True, size=18.0, color="#1f4e79")
    style(b, sh, (2, 1, 2, 7), **hdr)
    style(b, sh, (3, 6, 8, 6), numfmt="0.0")
    save_sheet(b, "gradebook", "Gradebook", "Education", "Averages and letter grades", entries)


# ============================================================================= Slides
def save_slides(pres, name, title, category, desc, entries):
    from app.slide.canvas import render_slide
    from app.slide.pptx_io import save_pptx

    folder = OUT / "slide"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.dpt"
    save_pptx(str(path), pres)
    render_slide(pres, pres.slides[0], 320).save(str(folder / f"{name}.png"))
    entries.append({"file": path.name, "name": title, "category": category, "description": desc, "thumbnail": f"{name}.png"})


def make_slides(entries):
    from app.slide.model import THEMES, Animation, Presentation, Shape, Theme, Transition, emu, make_slide

    def pres(theme):
        return Presentation(theme=Theme(**THEMES[theme].__dict__))

    p = pres("Midnight")
    s = make_slide(p, "title", "Startup Pitch", "Changing how Pakistan works with documents")
    s.transition = Transition("fade", 0.8)
    p.slides.append(s)
    for t, body in (("The Problem", "Office software is expensive\nPoor Urdu support\nNo offline PDF tools"),
                    ("Our Solution", "Free, open office suite\nNastaleeq-ready Writer\n32 built-in tools"),
                    ("Market", "220 million people\n3 million SMEs\nGrowing digital adoption"),
                    ("Business Model", "Free core product\nPaid cloud & support plans\nEnterprise deployment")):
        sl = make_slide(p, "title_content", t, body)
        sl.shapes[1].animation = Animation("fly_in", "click", 0.6, 0, "left", 0)
        sl.transition = Transition("push", 0.6, "left")
        p.slides.append(sl)
    end = make_slide(p, "section", "Thank You", "contact@myoffice.pk")
    p.slides.append(end)
    save_slides(p, "startup_pitch", "Startup Pitch", "Business", "Dark theme pitch deck with animations", entries)

    p = pres("Ocean")
    p.slides.append(make_slide(p, "title", "Project Status", "Week 42 \u2022 October 2026"))
    st = make_slide(p, "title_only", "Milestones", "")
    for i, (label, color) in enumerate((("Design \u2714", "#2a9d8f"), ("Build \u2714", "#2a9d8f"),
                                        ("Testing \u23f3", "#e9c46a"), ("Launch", "#adb5bd"))):
        sh = Shape("roundrect", emu(70 + i * 290), emu(260), emu(250), emu(110), fill=color)
        sh.set_text(label, size=22, bold=True, color="#ffffff", align="center")
        sh.valign = "middle"
        sh.animation = Animation("zoom", "after" if i else "click", 0.4, 0, "bottom", i)
        st.shapes.append(sh)
    p.slides.append(st)
    ch = make_slide(p, "title_only", "Budget vs Actual", "")
    c = Shape("chart", emu(120), emu(150), emu(1000), emu(500))
    c.chart = {"kind": "column", "title": "", "categories": ["Q1", "Q2", "Q3", "Q4"],
               "series": [["Budget", [100, 120, 130, 140]], ["Actual", [95, 125, 128, 0]]], "legend": True}
    ch.shapes.append(c)
    p.slides.append(ch)
    p.slides.append(make_slide(p, "title_content", "Risks & Next Steps", "Testing capacity\nVendor delays\nNext review: 22 Oct"))
    save_slides(p, "project_status", "Project Status", "Business", "Milestones, chart and next steps", entries)

    p = pres("Sunrise")
    p.slides.append(make_slide(p, "title", "Lecture Title", "Course name \u2022 Lecturer"))
    p.slides.append(make_slide(p, "title_content", "Learning Objectives", "Objective one\nObjective two\nObjective three"))
    two = make_slide(p, "two_content", "Compare & Contrast", "Point A\nPoint B\nPoint C")
    two.shapes[2].paragraphs[0].runs[0].text = "Point X"
    p.slides.append(two)
    p.slides.append(make_slide(p, "section", "Questions?", ""))
    save_slides(p, "lecture", "Lecture", "Education", "Warm theme for classes", entries)

    p = pres("Office")
    p.slides.append(make_slide(p, "title", "Presentation Title", "Subtitle"))
    p.slides.append(make_slide(p, "title_content", "Slide Title", "First point\nSecond point\nThird point"))
    save_slides(p, "minimal", "Minimal", "General", "Clean white starter deck", entries)


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from app.services import fonts

    fonts.load_bundled_fonts()
    fonts.install_substitutions()
    for kind, fn in (("writer", make_writer), ("sheet", make_sheets), ("slide", make_slides)):
        entries: list = []
        fn(entries)
        folder = OUT / kind
        with open(folder / "index.json", "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "templates": entries}, fh, ensure_ascii=False, indent=2)
        print(f"{kind}: {len(entries)} templates")
    del app


if __name__ == "__main__":
    main()
