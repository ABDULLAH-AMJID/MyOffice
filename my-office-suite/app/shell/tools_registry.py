"""Registry of the MyOffice tools (numbers 1-32 follow the product blueprint; 33-42 added in 1.2; 43-48 added in 1.3).

``handler`` is either ``"main:<method>"`` (a MainWindow method) or ``"package.module:callable"``
(called with the main window).  The Home page, the Tools gallery, the CLI tests and
TESTING.md are all generated from this single list.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    number: int
    id: str
    title: str
    category: str
    icon: str
    description: str
    handler: str

    @property
    def name(self) -> str:
        return self.title


CATEGORIES = ["Core Suite", "AI Tools", "PDF Power Tools", "Writer Add-ons", "Sheet Add-ons",
              "Photos & Media", "System & Cloud"]

TOOLS: list[Tool] = [
    Tool(1, "writer", "Writer", "Core Suite", "writer", "Word processor with DOCX, pagination, tables, styles", "main:new_writer"),
    Tool(2, "sheet", "Spreadsheets", "Core Suite", "sheet", "XLSX grid up to XFD with a formula engine", "main:new_sheet"),
    Tool(3, "slide", "Presentation", "Core Suite", "slide", "PPTX slides, shapes, images and animations", "main:new_slide"),
    Tool(4, "pdf", "PDF Viewer & Editor", "Core Suite", "pdf", "View, annotate and edit PDF files", "main:open_pdf"),
    Tool(5, "pdf_convert", "PDF Convert", "PDF Power Tools", "convert", "Office \u2194 PDF, images \u2192 PDF (built-in)", "app.pdf.toolbox:open_convert"),
    Tool(6, "pdf_merge", "Merge PDF", "PDF Power Tools", "merge_pdf", "Combine several PDFs into one", "app.pdf.toolbox:open_merge"),
    Tool(7, "pdf_split", "Split PDF", "PDF Power Tools", "split_pdf", "Split by ranges, every N pages or bookmarks", "app.pdf.toolbox:open_split"),
    Tool(8, "pdf_compress", "Compress PDF", "PDF Power Tools", "compress", "Shrink PDFs (Ghostscript / image recompression)", "app.pdf.toolbox:open_compress"),
    Tool(9, "pdf_ocr", "OCR", "PDF Power Tools", "ocr", "Searchable PDF / text from scans (Urdu + English)", "app.pdf.toolbox:open_ocr"),
    Tool(10, "pdf_sign", "Sign PDF", "PDF Power Tools", "sign", "Visible + cryptographic (PAdES) signatures", "app.pdf.toolbox:open_sign"),
    Tool(11, "pdf_protect", "Protect / Unlock", "PDF Power Tools", "lock", "AES-256 passwords and permissions", "app.pdf.toolbox:open_protect"),
    Tool(12, "pdf_watermark", "Watermark", "PDF Power Tools", "watermark", "Text or image watermark on pages", "app.pdf.toolbox:open_watermark"),
    Tool(13, "pdf_extract", "Extract", "PDF Power Tools", "extract", "Extract text, images, pages and tables", "app.pdf.toolbox:open_extract"),
    Tool(14, "pdf_edit", "Edit PDF", "PDF Power Tools", "edit", "Edit text, redact, add text/images/shapes", "main:edit_pdf"),
    Tool(15, "templates", "Templates", "Writer Add-ons", "templates", "Template gallery (.wpt/.ett/.dpt) with thumbnails", "main:show_templates"),
    Tool(16, "mail_merge", "Mail Merge", "Writer Add-ons", "mail_merge", "Letters from DOCX + CSV/XLSX data", "main:mail_merge"),
    Tool(17, "citation", "Reference / Citation", "Writer Add-ons", "quote", "Sources, citations and bibliography (APA, MLA...)", "main:citations"),
    Tool(18, "track_changes", "Track Changes & Comments", "Writer Add-ons", "track", "Revisions and comments (CRDT delta)", "main:track_changes"),
    Tool(19, "compare", "Compare Documents", "Writer Add-ons", "compare", "Redline two document versions (difflib)", "main:compare_documents"),
    Tool(20, "pivot", "Pivot Table", "Sheet Add-ons", "pivot", "Group-by summaries with pandas", "main:pivot_table"),
    Tool(21, "data_analysis", "Data Analysis", "Sheet Add-ons", "analysis", "Statistics, regression, histogram, goal seek", "main:data_analysis"),
    Tool(22, "macros", "Macros", "Sheet Add-ons", "macro", "Record and run Python macros (VBA alternative)", "main:macros"),
    Tool(23, "charts", "Charts", "Sheet Add-ons", "chart_bar", "Column, line, pie, scatter charts (matplotlib)", "main:charts"),
    Tool(24, "forms", "Forms / Survey", "Sheet Add-ons", "forms", "JSON-Schema forms, responses to a sheet", "app.forms.designer:open_forms"),
    Tool(25, "collab", "Real-time Collaboration", "System & Cloud", "collab", "Multi-file live LAN co-editing, QR code, per-file link & Team Chat", "main:collaborate"),
    Tool(26, "cloud_sync", "Cloud Sync", "System & Cloud", "cloud", "Two-way sync with Gmail, Google Drive, Router USB, S3 or Supabase", "app.tools.cloud_sync_dialog:open_dialog"),
    Tool(27, "backup", "Backup & Security Vault", "System & Cloud", "backup", "Versioned backups, Laptop-Locked Router/USB Vault & Gmail Backup", "app.tools.backup_dialog:open_dialog"),
    Tool(28, "addins", "Add-in Manager", "System & Cloud", "addins", "Enable, disable and install add-ins", "app.tools.addin_dialog:open_dialog"),
    Tool(29, "fonts", "Font Manager", "System & Cloud", "fonts", "Inspect, preview and install fonts (fontTools)", "app.tools.font_manager:open_dialog"),
    Tool(30, "repair", "File Repair", "System & Cloud", "repair", "Recover damaged DOCX/XLSX/PPTX/PDF files", "app.tools.repair_dialog:open_dialog"),
    Tool(31, "update", "Update Service", "System & Cloud", "update", "Check version.json and install updates", "app.tools.update_dialog:open_dialog"),
    Tool(32, "capture", "Screen Capture", "System & Cloud", "capture", "Capture screen, window or region", "main:screen_capture"),
    Tool(33, "photos", "MyOffice Photos", "Core Suite", "photos", "View and edit pictures: adjust, filters, retouch, draw", "main:open_photos"),
    Tool(34, "ai_settings", "MyOffice AI", "AI Tools", "ai", "AI writer, translate, formulas, slides, Chat PDF - choose a provider", "main:ai_settings"),
    Tool(35, "chat_pdf", "Chat with PDF", "AI Tools", "chat", "Summaries and answers with page references", "main:chat_pdf"),
    Tool(36, "translate", "Parallel Translate", "AI Tools", "translate", "Side-by-side translation of documents, slides and PDFs", "main:parallel_translate"),
    Tool(37, "scan_pdf", "Scan to PDF", "PDF Power Tools", "scan", "Scanner, camera or phone photos to a searchable PDF", "app.pdf.scan:scan_to_pdf"),
    Tool(38, "picture_text", "Picture to Text", "Photos & Media", "ocr_image", "Recognise text in photos and screenshots (Urdu, Hindi, English)", "app.tools.picture_text:open_dialog"),
    Tool(39, "screen_recorder", "Screen Recorder", "Photos & Media", "screen_record", "Record the screen with voice, camera and drawing (MP4)", "app.tools.screen_recorder:open_dialog"),
    Tool(40, "resume", "Resume Master", "Writer Add-ons", "resume", "A finished CV in one click - six designs, DOCX or PDF", "app.tools.resume:open_dialog"),
    Tool(41, "batch_rename", "Batch Rename", "System & Cloud", "rename", "Rename many files with patterns, preview and undo", "app.tools.batch_rename:open_dialog"),
    Tool(42, "versions", "Version History", "System & Cloud", "versions", "Every save is kept - open, compare or restore", "main:version_history"),
    Tool(43, "shortcuts", "Keyboard Shortcuts", "System & Cloud", "keyboard", "Customize & rebind shortcuts, detect conflicts, export/import profiles", "main:open_shortcuts_dialog"),
    Tool(44, "command_palette", "Command Palette", "System & Cloud", "search", "Instant fuzzy launcher across all commands, tools & recent files", "main:open_command_palette"),
    Tool(45, "vault", "Hardware Security Vault", "System & Cloud", "vault", "Laptop-bound AES-256-GCM encryption for Router USB & external drives", "main:open_hardware_vault"),
    Tool(46, "pdf_page_numbers", "Page Numbers", "PDF Power Tools", "list_num", "Stamp page numbers or Bates numbering across PDFs", "app.pdf.toolbox:open_page_numbers"),
    Tool(47, "pdf_stamp", "Stamp PDF", "PDF Power Tools", "stamp", "Apply commercial vector stamps (APPROVED, CONFIDENTIAL, PAID...)", "app.pdf.toolbox:open_stamp"),
    Tool(48, "pdf_flatten", "Flatten PDF", "PDF Power Tools", "layers", "Bake interactive forms, annotations and stamps into PDF pages", "app.pdf.toolbox:open_flatten"),
    Tool(49, "mobile_sync", "Mobile App Sync (PC \u2194 Android)", "System & Cloud", "smartphone", "Two-way Wi-Fi / Hotspot sync, QR pairing & Vault key share with MyOffice Android", "app.tools.mobile_sync_dialog:open_dialog"),
]

TOOLS_BY_ID = {t.id: t for t in TOOLS}
assert len(TOOLS) == 49 and len(TOOLS_BY_ID) == 49


def resolve(handler: str, main_window):
    """Return a zero-argument callable for ``handler``."""
    import importlib

    mod_name, _, attr = handler.partition(":")
    if mod_name == "main":
        return getattr(main_window, attr)
    fn = getattr(importlib.import_module(mod_name), attr)
    return lambda: fn(main_window)
