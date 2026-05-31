from datetime import datetime
from pathlib import Path
import re
from typing import Dict, List
import sys
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from PyQt5.QtCore import QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.comparator import compare_invoices
from core.file_loader import load_invoice_file
from core.invoice_ocr import InvoiceOCR, QwenOCRClient
from core.models import CompareRow, InvoiceData
from core.report_generator import generate_excel_report
from core.tax_verifier import CaptchaValidationError, TaxVerifier


FIELD_DEFINITIONS = [
    ("invoice_code", "发票代码"),
    ("invoice_number", "发票号码"),
    ("invoice_date", "开票日期"),
    ("amount_without_tax", "开具金额不含税"),
    ("tax_amount", "税额"),
    ("total_amount", "价税合计"),
    ("buyer_name", "购买方名称"),
    ("buyer_tax_id", "购买方税号"),
    ("seller_name", "销售方名称"),
    ("seller_tax_id", "销售方税号"),
    ("remark", "备注"),
]

REQUIRED_VERIFY_FIELDS = {"invoice_number", "invoice_date", "total_amount"}
REQUIRED_VERIFY_FIELD_ORDER = ["invoice_number", "invoice_date", "total_amount"]
SUPPORTED_FILE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


class InvoicePasteTextEdit(QTextEdit):
    """接收截图、PDF/图片文件或文件路径的粘贴框。"""

    pasted_file_ready = pyqtSignal(str)

    def __init__(self, output_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.output_dir = output_dir
        self.setAcceptDrops(True)
        self.setPlaceholderText("在这里粘贴发票截图，或粘贴/拖入 PDF、PNG、JPG、JPEG、WEBP 文件，然后点击“确认粘贴并开始”。")
        self.setMinimumHeight(74)

    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        file_path = self._file_from_mime(source)
        if file_path:
            self._set_received_file(file_path)
            return

        pdf_path = self._save_pdf_from_mime(source)
        if pdf_path:
            self._set_received_file(pdf_path)
            return

        image_path = self._save_image_from_mime(source)
        if image_path:
            self._set_received_file(image_path)
            return

        super().insertFromMimeData(source)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._file_from_mime(event.mimeData()) or event.mimeData().hasImage():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._file_from_mime(event.mimeData()) or event.mimeData().hasImage():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        file_path = self._file_from_mime(event.mimeData())
        if file_path:
            self._set_received_file(file_path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _set_received_file(self, file_path: str) -> None:
        self.setPlainText(file_path)
        self.pasted_file_ready.emit(file_path)

    def _file_from_mime(self, mime_data) -> str:
        if mime_data.hasUrls():
            for url in mime_data.urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if self._is_supported_file(path):
                    return str(path)

        if mime_data.hasText():
            path = self._path_from_text(mime_data.text())
            if path:
                return str(path)
        return ""

    def _save_pdf_from_mime(self, mime_data) -> str:
        """保存直接粘贴进剪切板的 PDF 二进制内容。

        资源管理器复制 PDF 文件时通常走 URL 文件路径；这里兜底处理少数应用直接放入
        application/pdf 数据的情况。
        """
        for mime_format in ("application/pdf", "application/x-pdf"):
            if not mime_data.hasFormat(mime_format):
                continue
            payload = bytes(mime_data.data(mime_format))
            if not payload or not payload.startswith(b"%PDF"):
                continue
            clipboard_dir = self.output_dir / "clipboard"
            clipboard_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            target_path = clipboard_dir / f"pasted_invoice_{timestamp}.pdf"
            target_path.write_bytes(payload)
            return str(target_path)
        return ""

    def _save_image_from_mime(self, mime_data) -> str:
        if not mime_data.hasImage():
            return ""

        image_data = mime_data.imageData()
        if isinstance(image_data, QImage):
            image = image_data
        elif isinstance(image_data, QPixmap):
            image = image_data.toImage()
        else:
            image = QApplication.clipboard().image()

        if image.isNull():
            return ""

        clipboard_dir = self.output_dir / "clipboard"
        clipboard_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target_path = clipboard_dir / f"pasted_invoice_{timestamp}.png"
        if not image.save(str(target_path), "PNG"):
            raise RuntimeError("剪贴板图片保存失败，请重新截图后再粘贴。")
        return str(target_path)

    def _path_from_text(self, text: str) -> Path | None:
        for line in (text or "").splitlines():
            candidate = line.strip().strip('"').strip("'")
            if candidate.lower().startswith("file://"):
                parsed = urlparse(candidate)
                candidate = unquote(parsed.path or "")
                if parsed.netloc:
                    candidate = f"//{parsed.netloc}{candidate}"
                if re.match(r"^/[A-Za-z]:/", candidate):
                    candidate = candidate[1:]
            if not candidate:
                continue
            path = Path(candidate)
            if self._is_supported_file(path):
                return path
        return None

    @staticmethod
    def _is_supported_file(path: Path) -> bool:
        return path.exists() and path.is_file() and path.suffix.lower() in SUPPORTED_FILE_SUFFIXES


class ImagePreviewDialog(QDialog):
    """非模态图片预览窗口：整图适配窗口，避免打开后还要拖滚动条。"""

    def __init__(self, title: str, image_path: str, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.original_pixmap = pixmap
        self.setWindowTitle(title)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        self.path_label = QLabel(str(Path(image_path).resolve()))
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        self.path_label.setText(f"{Path(image_path).resolve()}\n按 Esc 关闭全屏预览。")

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(720, 520)
        self.image_label.setStyleSheet("QLabel { background: #f7f7f7; border: 1px solid #d0d0d0; }")

        layout = QVBoxLayout(self)
        layout.addWidget(self.path_label)
        layout.addWidget(self.image_label, stretch=1)

        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(1380, max(900, available.width() - 120)), min(900, max(650, available.height() - 120)))
        else:
            self.resize(1280, 860)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._update_scaled_image()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_scaled_image()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _update_scaled_image(self) -> None:
        if self.original_pixmap.isNull():
            return
        target_size = self.image_label.size()
        if target_size.width() < 20 or target_size.height() < 20:
            return
        scaled_pixmap = self.original_pixmap.scaled(
            target_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled_pixmap)


class MainWindow(QMainWindow):
    """主界面：文件选择、字段编辑、查验、比对报告都在这里串起来。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("InvoiceVerifier - 电子发票自动查验与一致性比对工具")
        self.resize(1720, 1080)
        self.setMinimumSize(1420, 900)
        app_font = QFont(self.font())
        app_font.setPointSize(max(app_font.pointSize() + 2, 11))
        self.setFont(app_font)

        if getattr(sys, "frozen", False):
            self.project_root = Path(sys.executable).resolve().parent
        else:
            self.project_root = Path(__file__).resolve().parents[1]
        self.output_dir = self.project_root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "debug").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "temp").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "reports").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "clipboard").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "review").mkdir(parents=True, exist_ok=True)

        self.settings = QSettings("InvoiceVerifier", "InvoiceVerifier")
        self.last_open_dir = self.settings.value("last_open_dir", str(Path.home()))
        if not self.last_open_dir or not Path(self.last_open_dir).exists():
            self.last_open_dir = str(Path.home())

        self.selected_file_path = ""
        self.pasted_invoice_path = ""
        self.official_result_png = ""
        self.review_image_path = ""
        self.pending_official_ocr = False
        self.auto_flow_active = False
        self.waiting_for_captcha = False
        self.ocr = InvoiceOCR()
        self.verifier: TaxVerifier | None = None
        self.compare_rows: List[CompareRow] = []
        self.image_preview_dialogs: List[QDialog] = []

        self.user_fields: Dict[str, QLineEdit] = {}
        self.official_fields: Dict[str, QLineEdit] = {}

        self._build_ui()
        self._apply_app_style()
        self._apply_status_style("info")

    def _build_ui(self) -> None:
        central_widget = QWidget()
        root_layout = QVBoxLayout(central_widget)

        root_layout.addWidget(self._build_file_group())
        root_layout.addWidget(self._build_status_group())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([640, 800])
        root_layout.addWidget(splitter, stretch=1)

        self.setCentralWidget(central_widget)

    def _apply_app_style(self) -> None:
        """轻量美化界面，不改变现有交互逻辑。"""
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f4f7fb;
                color: #1f2937;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d7e0ea;
                border-radius: 6px;
                margin-top: 12px;
                padding: 12px 10px 10px 10px;
                font-weight: 600;
                color: #244e73;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                background: #f4f7fb;
            }
            QLineEdit, QTextEdit {
                background: #ffffff;
                border: 1px solid #c8d3df;
                border-radius: 4px;
                padding: 5px 7px;
                selection-background-color: #2f80c9;
            }
            QTextEdit {
                color: #365169;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #2f80c9;
                background: #fbfdff;
            }
            QPushButton {
                background: #f8fbff;
                border: 1px solid #b8c9da;
                border-radius: 5px;
                padding: 6px 12px;
                color: #17324d;
            }
            QPushButton:hover {
                background: #e8f2ff;
                border-color: #77a8d8;
            }
            QPushButton:pressed {
                background: #d8eafb;
            }
            QPushButton:disabled {
                background: #eef2f6;
                color: #98a2ad;
                border-color: #d5dce3;
            }
            QScrollArea, QTableWidget {
                background: #ffffff;
                border: 1px solid #d7e0ea;
                border-radius: 4px;
            }
            QHeaderView::section {
                background: #eaf2fb;
                color: #17324d;
                border: 0;
                border-right: 1px solid #cbd8e5;
                border-bottom: 1px solid #cbd8e5;
                padding: 6px;
                font-weight: 600;
            }
            QTableWidget {
                gridline-color: #d8e0e8;
            }
            QLabel {
                background: transparent;
            }
            """
        )

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox("发票文件选择")
        layout = QVBoxLayout(group)
        button_row = QHBoxLayout()

        select_button = QPushButton("选择发票文件")
        select_button.clicked.connect(self.on_select_file)
        button_row.addWidget(select_button)

        self.recognize_button = QPushButton("自动识别字段")
        self.recognize_button.clicked.connect(self.on_recognize_user_invoice)
        button_row.addWidget(self.recognize_button)

        self.auto_flow_button = QPushButton("开始自动流程")
        self.auto_flow_button.clicked.connect(self.on_start_auto_flow)
        button_row.addWidget(self.auto_flow_button)

        self.file_path_label = QLabel("尚未选择文件")
        self.file_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_path_label.setWordWrap(True)
        button_row.addWidget(self.file_path_label, stretch=1)
        layout.addLayout(button_row)

        paste_row = QHBoxLayout()
        self.paste_box = InvoicePasteTextEdit(self.output_dir, self)
        self.paste_box.pasted_file_ready.connect(self.on_pasted_invoice_ready)
        paste_row.addWidget(self.paste_box, stretch=1)

        paste_confirm_button = QPushButton("确认粘贴并开始")
        paste_confirm_button.clicked.connect(self.on_confirm_pasted_invoice)
        paste_row.addWidget(paste_confirm_button)
        layout.addLayout(paste_row)

        return group

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("流程状态")
        layout = QVBoxLayout(group)

        self.status_label = QLabel("状态：等待选择发票文件。")
        status_font = QFont(self.font())
        status_font.setPointSize(max(status_font.pointSize() + 2, 12))
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_label.setMinimumHeight(64)
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._apply_status_style("info")
        layout.addWidget(self.status_label)
        return group

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self._build_user_fields_group(), stretch=2)
        layout.addWidget(self._build_verify_group(), stretch=1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self._build_official_fields_group(), stretch=1)
        layout.addWidget(self._build_report_group(), stretch=2)
        return panel

    def _build_user_fields_group(self) -> QGroupBox:
        group = QGroupBox("用户发票字段（可编辑）")
        form_container = QWidget()
        form_layout = QFormLayout(form_container)
        form_layout.setLabelAlignment(Qt.AlignRight)

        for field_name, label in FIELD_DEFINITIONS:
            edit = self._create_field_edit(field_name, label)
            self.user_fields[field_name] = edit
            form_layout.addRow(self._display_label(field_name, label), edit)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(form_container)

        layout = QVBoxLayout(group)
        layout.addWidget(scroll_area)
        return group

    def _build_verify_group(self) -> QGroupBox:
        group = QGroupBox("查验")
        layout = QGridLayout(group)

        self.open_fill_button = QPushButton("自动打开查验平台并填表")
        self.open_fill_button.clicked.connect(self.on_open_and_fill)
        layout.addWidget(self.open_fill_button, 0, 0, 1, 2)

        self.captcha_hint_label = QLabel("颜色提示截图")
        self.captcha_hint_label.setAlignment(Qt.AlignCenter)
        self.captcha_hint_label.setMinimumSize(300, 64)
        self.captcha_hint_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.captcha_hint_label.setStyleSheet("border: 1px solid #cccccc; background: #fafafa;")
        layout.addWidget(self.captcha_hint_label, 1, 0, 1, 2)

        self.captcha_code_label = QLabel("验证码图片截图")
        self.captcha_code_label.setAlignment(Qt.AlignCenter)
        self.captcha_code_label.setMinimumSize(180, 82)
        self.captcha_code_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.captcha_code_label.setStyleSheet("border: 1px solid #cccccc; background: #fafafa;")
        layout.addWidget(self.captcha_code_label, 2, 0, 1, 2)

        captcha_tip = QLabel("请按上方截图提示，只输入指定颜色的验证码文字。")
        captcha_tip.setWordWrap(True)
        layout.addWidget(captcha_tip, 3, 0, 1, 2)

        self.captcha_input = QLineEdit()
        self.captcha_input.setPlaceholderText("请输入验证码")
        self.captcha_input.returnPressed.connect(self.on_continue_after_captcha)
        layout.addWidget(QLabel("验证码"), 4, 0)
        layout.addWidget(self.captcha_input, 4, 1)

        refresh_button = QPushButton("刷新验证码")
        refresh_button.clicked.connect(self.on_refresh_captcha)
        layout.addWidget(refresh_button, 5, 0)

        self.submit_button = QPushButton("提交查验 / 继续")
        self.submit_button.clicked.connect(self.on_continue_after_captcha)
        layout.addWidget(self.submit_button, 5, 1)

        save_button = QPushButton("保存官方结果")
        save_button.clicked.connect(self.on_save_official_result)
        layout.addWidget(save_button, 6, 0, 1, 2)

        return group

    def _build_official_fields_group(self) -> QGroupBox:
        group = QGroupBox("官方查验字段（可编辑）")
        form_container = QWidget()
        form_layout = QFormLayout(form_container)
        form_layout.setLabelAlignment(Qt.AlignRight)

        for field_name, label in FIELD_DEFINITIONS:
            edit = self._create_field_edit(field_name, label)
            self.official_fields[field_name] = edit
            form_layout.addRow(self._display_label(field_name, label), edit)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(form_container)

        self.official_ocr_button = QPushButton("使用 Qwen OCR 识别官方结果截图")
        self.official_ocr_button.clicked.connect(self.on_ocr_official_result)

        layout = QVBoxLayout(group)
        layout.addWidget(scroll_area)
        layout.addWidget(self.official_ocr_button)
        return group

    def _build_report_group(self) -> QGroupBox:
        group = QGroupBox("报告")
        layout = QVBoxLayout(group)

        button_row = QHBoxLayout()
        generate_button = QPushButton("生成比对报告")
        generate_button.clicked.connect(self.on_generate_report)
        button_row.addWidget(generate_button)

        self.report_path_label = QLabel("尚未生成报告")
        self.report_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.report_path_label.setWordWrap(True)
        button_row.addWidget(self.report_path_label, stretch=1)
        layout.addLayout(button_row)

        image_button_row = QHBoxLayout()
        self.show_review_image_button = QPushButton("显示发票与官方结果拼图")
        self.show_review_image_button.clicked.connect(self.on_show_review_image)
        self.show_review_image_button.setEnabled(False)
        image_button_row.addWidget(self.show_review_image_button)
        image_button_row.addStretch(1)
        layout.addLayout(image_button_row)

        self.preview_table = QTableWidget(0, 5)
        self.preview_table.setHorizontalHeaderLabels(
            ["字段名", "用户上传发票值", "官方查验值", "是否一致", "差异说明"]
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setAlternatingRowColors(False)
        layout.addWidget(self.preview_table)

        return group

    def _create_field_edit(self, field_name: str, label: str) -> QLineEdit:
        edit = QLineEdit()
        if field_name == "invoice_date":
            edit.setPlaceholderText("YYYYMMDD 或 YYYY-MM-DD")
            edit.setToolTip("开票日期建议填写为 YYYYMMDD，例如 20250822。")
        else:
            edit.setPlaceholderText(label)
        return edit

    def _display_label(self, field_name: str, label: str) -> str:
        if field_name in REQUIRED_VERIFY_FIELDS:
            return f"* {label}"
        return label

    def on_select_file(self) -> None:
        start_dir = getattr(self, "last_open_dir", str(Path.home())) or str(Path.home())
        if not Path(start_dir).exists():
            start_dir = str(Path.home())

        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择发票文件",
                start_dir,
                "发票文件 (*.pdf *.png *.jpg *.jpeg *.webp);;所有文件 (*)",
            )
        except KeyboardInterrupt:
            self._set_status("状态：文件选择已中断，请重新点击“选择发票文件”。")
            return
        except Exception as exc:
            QMessageBox.warning(self, "选择文件失败", str(exc))
            return
        if not file_path:
            return

        try:
            self._use_invoice_file(file_path, status_prefix="状态：已选择发票文件")
        except Exception as exc:
            QMessageBox.warning(self, "文件无效", str(exc))

    def on_pasted_invoice_ready(self, file_path: str) -> None:
        self.pasted_invoice_path = file_path
        self.file_path_label.setText(f"已读取粘贴内容：{file_path}")
        self._set_status("状态：已读取粘贴的发票内容，点击“确认粘贴并开始”即可执行流程。")

    def on_confirm_pasted_invoice(self) -> None:
        try:
            file_path = self._file_from_paste_box_or_clipboard()
            if not file_path:
                QMessageBox.information(self, "提示", "请先在粘贴框中粘贴发票截图，或粘贴/拖入 PDF、图片文件。")
                return
            self._use_invoice_file(file_path, status_prefix="状态：已确认粘贴发票")
        except Exception as exc:
            QMessageBox.warning(self, "粘贴内容无效", str(exc))

    def _use_invoice_file(self, file_path: str, status_prefix: str) -> None:
        selected_path = Path(file_path)
        if not selected_path.exists() or not selected_path.is_file():
            raise ValueError(f"文件不存在或不可读取：\n{file_path}")

        suffix = selected_path.suffix.lower()
        if suffix not in SUPPORTED_FILE_SUFFIXES:
            raise ValueError("仅支持 PDF、PNG、JPG、JPEG、WEBP 格式。")

        self.last_open_dir = str(selected_path.parent)
        self.settings.setValue("last_open_dir", self.last_open_dir)
        self.selected_file_path = str(selected_path)
        self.pasted_invoice_path = str(selected_path)
        self.file_path_label.setText(str(selected_path))
        self._clear_invoice_fields(self.user_fields)
        self._clear_invoice_fields(self.official_fields)
        self.captcha_hint_label.setText("颜色提示截图")
        self.captcha_code_label.setText("验证码图片截图")
        self.captcha_input.clear()
        self.report_path_label.setText("尚未生成报告")
        self.preview_table.setRowCount(0)
        self.show_review_image_button.setEnabled(False)
        self.pending_official_ocr = False
        self.official_result_png = ""
        self.review_image_path = ""
        self._set_status(f"{status_prefix}，正在自动启动流程。")
        self.on_start_auto_flow()

    def _file_from_paste_box_or_clipboard(self) -> str:
        text_path = self.paste_box._path_from_text(self.paste_box.toPlainText()) if hasattr(self, "paste_box") else None
        if text_path:
            return str(text_path)

        if self.pasted_invoice_path and Path(self.pasted_invoice_path).exists():
            return self.pasted_invoice_path

        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        file_path = self.paste_box._file_from_mime(mime_data)
        if file_path:
            self.pasted_invoice_path = file_path
            return file_path

        pdf_path = self.paste_box._save_pdf_from_mime(mime_data)
        if pdf_path:
            self.pasted_invoice_path = pdf_path
            self.paste_box.setPlainText(pdf_path)
            return pdf_path

        image_path = self.paste_box._save_image_from_mime(mime_data)
        if image_path:
            self.pasted_invoice_path = image_path
            self.paste_box.setPlainText(image_path)
            return image_path
        return ""

    def on_recognize_user_invoice(self) -> None:
        if not self.selected_file_path:
            QMessageBox.information(self, "提示", "请先选择发票文件。")
            return

        try:
            self._recognize_user_invoice(show_messages=True)
        except Exception as exc:
            QMessageBox.warning(self, "OCR 识别失败", str(exc))

    def on_start_auto_flow(self) -> None:
        if not self.selected_file_path:
            self._set_status("状态：请先选择发票文件，再开始自动流程。")
            QMessageBox.information(self, "提示", "请先选择发票文件。")
            return

        self.auto_flow_active = True
        self.waiting_for_captcha = False
        self.auto_flow_button.setEnabled(False)

        try:
            self._set_status("步骤 1/7：正在识别上传发票字段。")
            invoice_data = self._recognize_user_invoice(show_messages=False)
            current_invoice = self._get_invoice_from_fields(self.user_fields)
            if not self._has_any_business_field(invoice_data) and not self._has_any_business_field(current_invoice):
                self._pause_auto_flow("自动流程暂停：OCR 未返回有效结果，请手动填写字段后继续。")
                QMessageBox.information(self, "需要人工处理", "OCR 未返回有效结果，请手动填写或检查 API Key。")
                return

            missing_required = self._missing_required_field_labels(current_invoice)
            if missing_required:
                message = "自动流程暂停：查验必填字段缺失：" + "、".join(missing_required) + "。请补充后重新开始自动流程。"
                self._pause_auto_flow(message)
                QMessageBox.warning(self, "需要人工补充", message)
                return

            self._set_status("步骤 2/7：正在打开查验平台并自动填表。")
            self._prepare_verification()
            self.waiting_for_captcha = True
            self._set_status("步骤 3/7：流程已暂停。请按截图输入指定颜色验证码，然后点击“提交查验 / 继续”。")
            self.captcha_input.setFocus()
        except Exception as exc:
            self._pause_auto_flow(f"自动流程暂停：{exc}")
            QMessageBox.warning(self, "自动流程暂停", f"{exc}\n\n可修正后使用保留的按钮继续当前环节。")

    def on_open_and_fill(self) -> None:
        try:
            self._prepare_verification()
            self.waiting_for_captcha = True
            self._set_status("状态：查验平台已打开并完成填表。流程已暂停，请输入验证码后点击“提交查验 / 继续”。")
            QMessageBox.information(self, "已打开", "查验平台已打开并完成自动填表，请按截图提示人工输入验证码。")
        except Exception as exc:
            self._set_status(f"状态：查验准备失败：{exc}")
            QMessageBox.warning(self, "查验准备失败", str(exc))

    def on_refresh_captcha(self) -> None:
        if not self.verifier:
            QMessageBox.information(self, "提示", "请先打开查验平台。")
            return
        try:
            self._set_status("状态：正在按用户请求刷新验证码。")
            captcha_paths = self.verifier.refresh_captcha_images()
            self._show_captcha_images(captcha_paths)
            self._set_status("状态：验证码已刷新。请按截图输入指定颜色验证码，然后点击“提交查验 / 继续”。")
        except Exception as exc:
            self._set_status(f"状态：刷新验证码失败：{exc}")
            QMessageBox.warning(self, "刷新验证码失败", str(exc))

    def on_continue_after_captcha(self) -> None:
        if self.waiting_for_captcha or self.auto_flow_active:
            self._continue_auto_flow_after_captcha()
            return
        self.on_submit_verification()

    def on_submit_verification(self) -> None:
        if not self.verifier:
            QMessageBox.information(self, "提示", "请先打开查验平台并填表。")
            return

        try:
            self._set_status("状态：正在提交查验。")
            self._submit_verification_internal()
            self._set_status("状态：查验已提交。请保存官方结果，系统会默认用官方截图 OCR 填充字段。")
        except CaptchaValidationError as exc:
            self._handle_captcha_error(str(exc))
        except Exception as exc:
            self._set_status(f"状态：提交查验失败：{exc}")
            QMessageBox.warning(self, "提交查验失败", str(exc))

    def on_save_official_result(self) -> None:
        if not self.verifier:
            QMessageBox.information(self, "提示", "请先完成查验页面打开。")
            return

        try:
            self._set_status("状态：正在保存官方结果截图和 PDF。")
            self._save_official_result_internal(run_official_ocr=True)
            if self.pending_official_ocr:
                self._set_status("状态：官方结果已保存并已尝试 OCR；仍有关键字段缺失，可手动编辑或重新 OCR。")
            else:
                self._set_status("状态：官方结果已保存，官方截图 OCR 已完成。")
        except Exception as exc:
            self._set_status(f"状态：保存官方结果失败：{exc}")
            QMessageBox.warning(self, "保存官方结果失败", str(exc))

    def on_ocr_official_result(self) -> None:
        if not self.official_result_png or not Path(self.official_result_png).exists():
            QMessageBox.information(self, "提示", "请先点击“保存官方结果”，生成官方结果截图。")
            return

        self._set_status("状态：正在使用 Qwen OCR 识别官方结果截图。")
        self._ocr_official_result_from_saved_image(show_success=True)

    def _ocr_official_result_from_saved_image(self, show_success: bool) -> None:
        try:
            self.official_ocr_button.setEnabled(False)
            self.official_ocr_button.setText("识别中...")
            QApplication.processEvents()

            client = QwenOCRClient()
            official_data = client.extract_invoice_from_image(
                self.official_result_png,
                mode="official_result",
            )
            self._clean_official_invoice_data(official_data)
            if self._has_any_business_field(official_data):
                self._set_invoice_to_fields(official_data, self.official_fields, only_non_empty=True)
                missing_keys = self._missing_required_field_labels(
                    self._get_invoice_from_fields(self.official_fields)
                )
                self.pending_official_ocr = bool(missing_keys)
                if missing_keys:
                    self._set_status("状态：官方结果 OCR 已填入可识别字段，但仍缺少：" + "、".join(missing_keys))
                else:
                    self._set_status("状态：官方结果 OCR 已填入关键字段。")
                if show_success:
                    QMessageBox.information(self, "识别完成", "已将官方结果截图 OCR 字段填入官方查验字段区域。")
            else:
                print("[Official OCR] 未返回有效结果，请手动填写或检查 API Key。")
                self.pending_official_ocr = True
                self._set_status("状态：官方结果 OCR 未返回有效字段，相关字段会在报告中标为待核验。")
                if show_success:
                    QMessageBox.information(
                        self,
                        "OCR 未返回有效结果",
                        "OCR 未返回有效结果，请手动填写或检查 API Key。",
                    )
        except Exception as exc:
            self.pending_official_ocr = True
            if show_success:
                self._set_status(f"状态：官方结果 OCR 失败：{exc}")
                QMessageBox.warning(self, "官方结果 OCR 失败", str(exc))
            else:
                self._set_status(f"状态：官方结果 OCR 失败，继续生成待核验结果：{exc}")
                print(f"[Official OCR] 自动识别失败：{exc}")
        finally:
            self.official_ocr_button.setEnabled(True)
            self.official_ocr_button.setText("使用 Qwen OCR 识别官方结果截图")

    def on_generate_report(self) -> None:
        try:
            self._set_status("状态：正在生成比对报告。")
            report_path = self._generate_report_internal()
            self._set_status(f"状态：报告已生成：{report_path}")
            QMessageBox.information(self, "报告已生成", f"比对报告已保存：\n{report_path}")
        except Exception as exc:
            self._set_status(f"状态：生成报告失败：{exc}")
            QMessageBox.warning(self, "生成报告失败", str(exc))

    def on_show_review_image(self) -> None:
        try:
            image_path = self._combined_review_image_path()
            self._show_image_dialog("人工核验拼图", image_path)
        except Exception as exc:
            QMessageBox.warning(self, "显示核验拼图失败", str(exc))

    def _set_status(self, text: str) -> None:
        self._apply_status_style(self._status_kind(text))
        self.status_label.setText(text)
        print(f"[UI Status] {text}")
        QApplication.processEvents()

    def _status_kind(self, text: str) -> str:
        if any(word in text for word in ("失败", "异常", "缺失", "错误")):
            return "warning"
        if "验证码" in text or "步骤 3/7" in text:
            return "captcha"
        if "暂停" in text:
            return "warning"
        if "完成" in text or "已生成" in text:
            return "success"
        return "info"

    def _apply_status_style(self, kind: str) -> None:
        styles = {
            "info": ("#eaf4ff", "#2f80c9", "#0f3157"),
            "captcha": ("#fff6d8", "#d59b00", "#5c4100"),
            "warning": ("#fff0f0", "#d9534f", "#7a1f1f"),
            "success": ("#eaf7e8", "#3a9b4a", "#1f5c2a"),
        }
        background, border, text_color = styles.get(kind, styles["info"])
        self.status_label.setStyleSheet(
            "QLabel {"
            f" background: {background};"
            f" border: 2px solid {border};"
            f" color: {text_color};"
            " padding: 12px;"
            " border-radius: 4px;"
            "}"
        )

    def _pause_auto_flow(self, message: str) -> None:
        self.auto_flow_active = False
        self.waiting_for_captcha = False
        self.auto_flow_button.setEnabled(True)
        self._set_status(message)

    def _recognize_user_invoice(self, show_messages: bool) -> InvoiceData:
        self.recognize_button.setEnabled(False)
        self.recognize_button.setText("识别中...")
        QApplication.processEvents()

        try:
            invoice_data = self.ocr.extract(self.selected_file_path)
            if self._has_any_business_field(invoice_data):
                self._set_invoice_to_fields(invoice_data, self.user_fields, only_non_empty=True)
                missing_required = self._missing_required_field_labels(self._get_invoice_from_fields(self.user_fields))
                if missing_required:
                    self._set_status("状态：OCR 已填入可识别字段，但仍缺少：" + "、".join(missing_required))
                    if show_messages:
                        QMessageBox.warning(
                            self,
                            "识别结果需补充",
                            "已将 OCR 识别结果填入用户发票字段区域，但 "
                            + "、".join(missing_required)
                            + " 未识别出来。请查看命令行里的完整 OCR 返回内容，并手动补充后再查验。",
                        )
                else:
                    self._set_status("状态：OCR 已识别出查验必填字段，请继续。")
                    if show_messages:
                        QMessageBox.information(self, "识别完成", "已将 OCR 识别结果填入用户发票字段区域，请人工确认。")
            else:
                self._set_status("状态：OCR 未返回有效结果，请手动填写或检查 API Key。")
                if show_messages:
                    QMessageBox.information(
                        self,
                        "OCR 未返回有效结果",
                        "OCR 未返回有效结果，请手动填写或检查 API Key。",
                    )
            return invoice_data
        finally:
            self.recognize_button.setEnabled(True)
            self.recognize_button.setText("自动识别字段")

    def _prepare_verification(self) -> None:
        invoice = self._get_invoice_from_fields(self.user_fields)
        missing_required = self._missing_required_field_labels(invoice)
        if missing_required:
            raise ValueError("查验必填字段缺失：" + "、".join(missing_required) + "。")

        if self.verifier is None:
            self.verifier = TaxVerifier(str(self.output_dir))
            self.verifier.start()

        self.verifier.open_site()
        self.verifier.fill_form(invoice)
        captcha_paths = self.verifier.capture_captcha_images()
        self._show_captcha_images(captcha_paths)
        self.captcha_input.clear()

    def _submit_verification_internal(self) -> None:
        if not self.verifier:
            raise RuntimeError("查验平台尚未打开。")

        self.verifier.submit(self.captcha_input.text())
        self.pending_official_ocr = True
        print("[Official Result] 查验已提交；官方字段将以结果截图 OCR 为准，不再解析 DOM 文本。")

    def _save_official_result_internal(self, run_official_ocr: bool) -> tuple[str, str]:
        if not self.verifier:
            raise RuntimeError("查验页面尚未打开。")

        screenshot_path, pdf_path = self.verifier.save_result()
        self.official_result_png = screenshot_path
        print(f"[Official Result] 截图已保存：{screenshot_path}")
        if pdf_path:
            print(f"[Official Result] PDF 已保存：{pdf_path}")
        elif self.verifier.last_pdf_warning:
            print(f"[Official Result] {self.verifier.last_pdf_warning}")

        if run_official_ocr:
            self._set_status("状态：正在使用 Qwen OCR 识别官方结果截图。")
            self._ocr_official_result_from_saved_image(show_success=False)
        return screenshot_path, pdf_path

    def _handle_captcha_error(self, message: str) -> None:
        """验证码错误后的 UI 恢复逻辑，快速版。

        成功自动关闭官网弹窗并刷新验证码后，不再弹 QMessageBox 阻塞用户，
        只更新状态栏并让用户直接重新输入。
        """
        self.auto_flow_active = True
        self.waiting_for_captcha = True
        self.auto_flow_button.setEnabled(False)
        self.captcha_input.clear()
        recovered = False
        recover_error = ""

        try:
            if self.verifier:
                self._set_status("步骤 3/7：验证码错误/失效，正在自动确认官网提示并刷新验证码。")
                captcha_paths = self.verifier.recover_after_captcha_error()
                self._show_captcha_images(captcha_paths)
                recovered = True
        except Exception as exc:
            recover_error = str(exc)
            print(f"[Captcha] 验证码错误后自动刷新失败：{exc}")

        if recovered:
            self._set_status("步骤 3/7：验证码错误/失效。已自动确认并刷新验证码，请直接重新输入后按 Enter 或点击“提交查验 / 继续”。")
            # 不弹模态 QMessageBox，避免阻塞下一次输入/提交。
        else:
            self._set_status("步骤 3/7：验证码错误/失效。自动刷新失败，请点击“刷新验证码”后重新输入。")
            QMessageBox.warning(
                self,
                "验证码刷新失败",
                "验证码错误/失效，但自动确认并刷新失败：\n"
                + (recover_error or message or "未知错误")
                + "\n\n请点击“刷新验证码”重试。",
            )

        self.captcha_input.setFocus()

    def _continue_auto_flow_after_captcha(self) -> None:
        if not self.verifier:
            self._pause_auto_flow("自动流程暂停：查验平台尚未打开，请先打开并填表。")
            QMessageBox.information(self, "提示", "请先打开查验平台并填表。")
            return

        if not self.captcha_input.text().strip():
            self.waiting_for_captcha = True
            self._set_status("步骤 3/7：流程仍暂停。验证码为空，请输入指定颜色验证码后点击“提交查验 / 继续”。")
            return

        self.auto_flow_active = True
        self.waiting_for_captcha = False
        self.auto_flow_button.setEnabled(False)

        try:
            self._set_status("步骤 4/7：正在提交查验。")
            self._submit_verification_internal()

            self._set_status("步骤 5/7：正在保存官方结果截图和 PDF。")
            self._save_official_result_internal(run_official_ocr=False)

            self._set_status("步骤 6/7：正在使用 Qwen OCR 识别官方结果截图。")
            self._ocr_official_result_from_saved_image(show_success=False)

            self._set_status("步骤 7/7：正在生成比对报告。")
            report_path = self._generate_report_internal()
            self.auto_flow_active = False
            self.auto_flow_button.setEnabled(True)
            self._set_status(f"自动流程完成：报告已生成：{report_path}")
        except CaptchaValidationError as exc:
            self._handle_captcha_error(str(exc))
        except Exception as exc:
            self._pause_auto_flow(f"自动流程暂停：{exc}")
            QMessageBox.warning(self, "自动流程暂停", f"{exc}\n\n可修正后使用保留的按钮继续当前环节。")

    def _generate_report_internal(self) -> Path:
        user_invoice = self._get_invoice_from_fields(self.user_fields)
        official_invoice = self._get_invoice_from_fields(self.official_fields)
        self.compare_rows = compare_invoices(user_invoice, official_invoice)
        self._update_preview_table(self.compare_rows)

        conclusion = self._build_conclusion(self.compare_rows)
        report_path = self._build_report_path(user_invoice.invoice_number)
        generate_excel_report(self.compare_rows, str(report_path), conclusion)
        self.report_path_label.setText(str(report_path))
        self.show_review_image_button.setEnabled(
            bool(self.selected_file_path and self.official_result_png and Path(self.official_result_png).exists())
        )
        return report_path

    def _get_invoice_from_fields(self, fields: Dict[str, QLineEdit]) -> InvoiceData:
        values = {field_name: edit.text().strip() for field_name, edit in fields.items()}
        return InvoiceData(**values)

    def _set_invoice_to_fields(
        self,
        invoice: InvoiceData,
        fields: Dict[str, QLineEdit],
        only_non_empty: bool = False,
    ) -> None:
        for field_name, _label in FIELD_DEFINITIONS:
            value = getattr(invoice, field_name, "") or ""
            if only_non_empty and not value.strip():
                continue
            fields[field_name].setText(value)

    def _clear_invoice_fields(self, fields: Dict[str, QLineEdit]) -> None:
        for edit in fields.values():
            edit.clear()

    def _has_any_business_field(self, invoice: InvoiceData) -> bool:
        return any((getattr(invoice, field_name, "") or "").strip() for field_name, _ in FIELD_DEFINITIONS)

    def _missing_required_field_labels(self, invoice: InvoiceData) -> List[str]:
        missing_labels = []
        label_map = dict(FIELD_DEFINITIONS)
        for field_name in REQUIRED_VERIFY_FIELD_ORDER:
            if not (getattr(invoice, field_name, "") or "").strip():
                missing_labels.append(label_map.get(field_name, field_name))
        return missing_labels

    def _clean_official_invoice_data(self, invoice: InvoiceData) -> None:
        """官方结果页常没有发票代码，避免 OCR 把发票号码误塞进发票代码。"""
        if invoice.invoice_code.strip() and invoice.invoice_code.strip() == invoice.invoice_number.strip():
            invoice.invoice_code = ""
        if invoice.invoice_code.strip() and not self.user_fields["invoice_code"].text().strip():
            # 用户原票没有发票代码时，官方 OCR 中像发票号码的长串不要当代码。
            if len(invoice.invoice_code.strip()) >= 16:
                invoice.invoice_code = ""

    def _show_captcha_images(self, captcha_paths) -> None:
        code_path, hint_path, _combined_path = captcha_paths
        self._show_image_in_label(self.captcha_hint_label, hint_path, "颜色提示截图加载失败")
        self._show_image_in_label(self.captcha_code_label, code_path, "验证码图片截图加载失败")

    def _show_image_in_label(self, label: QLabel, image_path: str, error_text: str) -> None:
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            label.setText(error_text)
            return
        scaled_pixmap = pixmap.scaled(
            label.width(),
            label.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        label.setPixmap(scaled_pixmap)

    def _input_preview_image_path(self) -> str:
        if not self.selected_file_path:
            raise ValueError("尚未选择发票文件。")
        image_paths = load_invoice_file(self.selected_file_path)
        if not image_paths:
            raise RuntimeError("未能生成输入发票预览图片。")
        return image_paths[0]

    def _combined_review_image_path(self) -> str:
        if not self.official_result_png or not Path(self.official_result_png).exists():
            raise ValueError("尚未保存官方结果截图。")

        input_image_path = self._input_preview_image_path()
        review_dir = self.output_dir / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = review_dir / f"invoice_review_pair_{timestamp}.png"
        self._make_review_pair_image(input_image_path, self.official_result_png, target_path)
        self.review_image_path = str(target_path)
        return str(target_path)

    def _make_review_pair_image(self, input_path: str, official_path: str, output_path: Path) -> None:
        """把上传发票和官方结果拼成一张图，方便人工并排核验。"""
        with Image.open(input_path) as input_image, Image.open(official_path) as official_image:
            left_image = ImageOps.exif_transpose(input_image).convert("RGB")
            right_image = ImageOps.exif_transpose(official_image).convert("RGB")

        right_image = self._crop_official_result_for_review(right_image)
        left_image = self._fit_review_image(left_image)
        right_image = self._fit_review_image(right_image)

        padding = 28
        title_height = 58
        title_gap = 10
        canvas_width = left_image.width + right_image.width + padding * 3
        canvas_height = max(left_image.height, right_image.height) + title_height + padding * 2 + title_gap
        canvas = Image.new("RGB", (canvas_width, canvas_height), "#f5f7fb")
        draw = ImageDraw.Draw(canvas)
        font = self._review_font(28)

        left_x = padding
        right_x = left_x + left_image.width + padding
        image_y = padding + title_height + title_gap

        draw.text((left_x, padding), "用户上传发票", fill="#17324d", font=font)
        draw.text((right_x, padding), "官方查验结果", fill="#17324d", font=font)

        canvas.paste(left_image, (left_x, image_y))
        canvas.paste(right_image, (right_x, image_y))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path)

    def _crop_official_result_for_review(self, image: Image.Image) -> Image.Image:
        """拼图预览时只保留官方查验明细内容，裁掉网页页头、页脚和灰色背景。

        官方结果通常在页面里以黑色边框的白色查验明细框呈现。这里仅影响人工核验
        拼图，不改动保存的官方原始截图，也不影响 OCR。
        """
        border_box = self._detect_official_result_border(image)
        if not border_box:
            return image

        left, top, right, bottom = border_box
        inset = 3
        left = min(max(left + inset, 0), image.width - 1)
        top = min(max(top + inset, 0), image.height - 1)
        right = max(min(right - inset, image.width), left + 1)
        bottom = max(min(bottom - inset, image.height), top + 1)
        return image.crop((left, top, right, bottom))

    @staticmethod
    def _detect_official_result_border(image: Image.Image) -> tuple[int, int, int, int] | None:
        """用深色边框线定位官方结果内容框，失败时返回 None。"""
        width, height = image.size
        if width < 200 or height < 160:
            return None

        pixels = image.load()

        def is_dark(x: int, y: int) -> bool:
            r, g, b = pixels[x, y][:3]
            return r < 55 and g < 55 and b < 55

        row_candidates: list[int] = []
        row_threshold = max(80, int(width * 0.35))
        for y in range(height):
            dark_count = 0
            for x in range(width):
                if is_dark(x, y):
                    dark_count += 1
            if dark_count >= row_threshold:
                row_candidates.append(y)

        col_candidates: list[int] = []
        col_threshold = max(80, int(height * 0.35))
        for x in range(width):
            dark_count = 0
            for y in range(height):
                if is_dark(x, y):
                    dark_count += 1
            if dark_count >= col_threshold:
                col_candidates.append(x)

        if not row_candidates or not col_candidates:
            return None

        top = row_candidates[0]
        bottom = row_candidates[-1] + 1
        left = col_candidates[0]
        right = col_candidates[-1] + 1

        crop_width = right - left
        crop_height = bottom - top
        if crop_width < width * 0.45 or crop_height < height * 0.35:
            return None
        if crop_width < 200 or crop_height < 160:
            return None

        return left, top, right, bottom

    @staticmethod
    def _fit_review_image(image: Image.Image) -> Image.Image:
        max_width = 1180
        max_height = 1600
        width, height = image.size
        scale = min(max_width / width, max_height / height, 1.0)
        if scale < 1.0:
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), resample)
        image = ImageEnhance.Sharpness(image).enhance(1.05)

        framed = Image.new("RGB", (image.width + 2, image.height + 2), "#cbd8e5")
        framed.paste(image, (1, 1))
        return framed

    @staticmethod
    def _review_font(size: int):
        font_candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for font_path in font_candidates:
            try:
                if Path(font_path).exists():
                    return ImageFont.truetype(font_path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _show_image_dialog(self, title: str, image_path: str) -> None:
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            raise RuntimeError(f"图片加载失败：{image_path}")

        dialog = ImagePreviewDialog(title, image_path, pixmap, self)
        self.image_preview_dialogs.append(dialog)
        dialog.finished.connect(lambda _result, preview=dialog: self._forget_image_preview(preview))
        dialog.showFullScreen()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_image_preview(self, dialog: QDialog) -> None:
        if dialog in self.image_preview_dialogs:
            self.image_preview_dialogs.remove(dialog)

    def _update_preview_table(self, rows: List[CompareRow]) -> None:
        self.preview_table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            values = [
                row.field_name,
                row.user_value,
                row.official_value,
                self._row_status_text(row),
                row.message,
            ]

            if row.is_match:
                background = QColor("#E2F0D9")
            elif self._is_uncertain_row(row):
                background = QColor("#FFF2CC")
            else:
                background = QColor("#F4CCCC")

            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(background)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.preview_table.setItem(row_index, column_index, item)

        self.preview_table.resizeRowsToContents()

    def _build_conclusion(self, rows: List[CompareRow]) -> str:
        if all(row.is_match for row in rows):
            return "一致"
        if any(not row.is_match and not self._is_uncertain_row(row) for row in rows):
            return "不一致/需人工复核"
        return "存在无数据字段，待核验"

    def _row_status_text(self, row: CompareRow) -> str:
        if row.is_match:
            return "一致"
        if self._is_uncertain_row(row):
            return "无数据/待核验"
        return "不一致"

    def _build_report_path(self, invoice_number: str) -> Path:
        reports_dir = self.output_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        safe_invoice_number = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", invoice_number.strip()) or "未填写发票号码"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return reports_dir / f"compare_report_{safe_invoice_number}_{timestamp}.xlsx"

    @staticmethod
    def _is_uncertain_row(row: CompareRow) -> bool:
        return any(keyword in row.message for keyword in ("缺失", "无数据", "无法判断", "复核", "不确定", "待核验", "人工核验"))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.verifier:
            self.verifier.close()
        event.accept()
