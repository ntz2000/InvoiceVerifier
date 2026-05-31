from pathlib import Path
from typing import List


SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}


def _project_output_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "output"


def load_invoice_file(path: str) -> List[str]:
    """加载发票文件。

    图片直接返回绝对路径；PDF 使用 PyMuPDF 按页转成 PNG，保存到 output/temp/。
    PyMuPDF 只在处理 PDF 时导入，这样纯图片 OCR 不会因为本机暂未安装 fitz 而影响启动。
    """
    if not path:
        raise ValueError("请选择发票文件。")

    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"发票文件不存在：{source_path}")
    if not source_path.is_file():
        raise ValueError(f"选择的路径不是文件：{source_path}")

    suffix = source_path.suffix.lower()

    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        # Path.resolve 可以正确保留中文路径，后续 OCR 或预览直接使用绝对路径更稳。
        return [str(source_path.resolve())]

    if suffix not in SUPPORTED_PDF_EXTENSIONS:
        raise ValueError("仅支持 PDF、PNG、JPG、JPEG、WEBP 格式的发票文件。")

    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("处理 PDF 需要安装 PyMuPDF，请先执行 pip install -r requirements.txt。") from exc

    temp_dir = _project_output_dir() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    image_paths: List[str] = []
    document = None
    try:
        document = fitz.open(str(source_path))
        if document.page_count == 0:
            raise ValueError("PDF 文件没有可转换的页面。")

        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            # 使用 4 倍缩放，右上角发票号码通常很小，较高分辨率能明显提升 OCR 稳定性。
            pixmap = page.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False)
            output_path = temp_dir / f"{source_path.stem}_page_{page_index + 1}.png"
            pixmap.save(str(output_path))
            image_paths.append(str(output_path.resolve()))
    except Exception as exc:
        raise RuntimeError(f"PDF 转图片失败：{exc}") from exc
    finally:
        if document is not None:
            document.close()

    return image_paths
