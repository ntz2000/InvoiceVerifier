import re
import unicodedata

from core.models import InvoiceData


def _clean_value(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    value = re.sub(r"[ \t\u3000]+", " ", value)
    return value.strip(" ：:")


def _clean_code_value(value: str) -> str:
    value = _clean_value(value)
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def _clean_amount(value: str) -> str:
    value = _clean_value(value)
    value = value.replace("￥", "").replace("¥", "").replace("元", "")
    value = re.sub(r"[,\s\u3000]", "", value)
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return match.group(0) if match else ""


def _extract_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _clean_value(match.group(1))
    return ""


def _extract_code(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if match:
            return _clean_code_value(match.group(1))
    return ""


def _extract_amount(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if match:
            return _clean_amount(match.group(1))
    return ""


def _line_after_label(text: str, label: str) -> str:
    pattern = rf"{label}\s*[:：]?\s*([^\r\n]+)"
    return _extract_first(text, [pattern])


def parse_official_text(text: str) -> InvoiceData:
    """从官方查验页面文本中提取字段。

    官方结果页有普通发票和机动车销售统一发票等不同版式。这里优先覆盖常见 DOM 文本：
    发票号码、开票日期、购买方名称、统一社会信用代码/纳税人识别号、
    销售方/销货单位名称、纳税人识别号、价税合计、增值税税额、不含税价。
    """
    raw_text = text or ""
    normalized_text = unicodedata.normalize("NFKC", raw_text)
    compact_text = re.sub(r"[ \t\u3000]+", " ", normalized_text)
    no_space_text = re.sub(r"[\s\u3000]+", "", normalized_text)

    invoice_code = _extract_code(
        compact_text,
        [
            r"发\s*票\s*代\s*码\s*[:：]?\s*([A-Z0-9０-９\s]{6,30})",
            r"发票代码\s+([A-Z0-9０-９\s]{6,30})",
        ],
    )
    if not invoice_code:
        invoice_code = _extract_code(no_space_text, [r"发票代码[:：]?([A-Z0-9]{6,30})"])

    invoice_number = _extract_code(
        compact_text,
        [
            r"发\s*票\s*号\s*码\s*[:：]?\s*([A-Z0-9０-９\s]{8,40})",
            r"发票号码\s*[:：]?\s*([A-Z0-9０-９\s]{8,40})",
            r"发票号\s*[:：]?\s*([A-Z0-9０-９\s]{8,40})",
        ],
    )
    if not invoice_number:
        invoice_number = _extract_code(
            no_space_text,
            [
                r"发票号码[:：]?([A-Z0-9]{8,40})",
                r"发票号[:：]?([A-Z0-9]{8,40})",
            ],
        )
    invoice_date = _extract_first(
        compact_text,
        [
            r"开票日期\s*[:：]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
            r"开票日期\s*[:：]?\s*(\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2})",
            r"开票日期\s*[:：]?\s*(\d{8})",
        ],
    )

    buyer_name = _extract_first(
        compact_text,
        [
            r"购买方名称\s*[:：]?\s*([^\r\n]+?)(?:\s{2,}|统一社会信用代码|$)",
            r"购方名称\s*[:：]?\s*([^\r\n]+)",
        ],
    )
    buyer_tax_id = _extract_first(
        compact_text,
        [
            r"统一社会信用代码/纳税人识\s*别号/身份证号码\s*([A-Z0-9]{8,30})",
            r"统一社会信用代码\s*/?\s*纳税人识别号\s*[:：]?\s*([A-Z0-9]{8,30})",
            r"购买方(?:纳税人识别号|税号|统一社会信用代码)\s*[:：]?\s*([A-Z0-9]{8,30})",
            r"购方(?:纳税人识别号|税号|统一社会信用代码)\s*[:：]?\s*([A-Z0-9]{8,30})",
        ],
    )

    seller_name = _extract_first(
        compact_text,
        [
            r"销货单位名称\s*[:：]?\s*([^\r\n]+?)(?:\s{2,}|电话|纳税人识别号|$)",
            r"销售方名称\s*[:：]?\s*([^\r\n]+)",
            r"销方名称\s*[:：]?\s*([^\r\n]+)",
        ],
    )
    seller_tax_id = _extract_first(
        compact_text,
        [
            r"销货单位名称[\s\S]{0,220}?纳税人识别号\s*[:：]?\s*([A-Z0-9]{8,30})",
            r"销售方(?:纳税人识别号|税号|统一社会信用代码)\s*[:：]?\s*([A-Z0-9]{8,30})",
            r"销方(?:纳税人识别号|税号|统一社会信用代码)\s*[:：]?\s*([A-Z0-9]{8,30})",
        ],
    )

    amount_without_tax = _extract_amount(
        compact_text,
        [
            r"不\s*含\s*税\s*价[\s\S]{0,40}?(?:小写)?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
            r"金额不含税\s*[:：]?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
            r"不含税金额\s*[:：]?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
            r"开具金额(?:不含税)?\s*[:：]?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
            r"合计金额\s*[:：]?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
        ],
    )
    tax_amount = _extract_amount(
        compact_text,
        [
            r"增\s*值\s*税\s*税\s*额[\s\S]{0,40}?([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
            r"税额合计\s*[:：]?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
            r"税额\s*[:：]?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
        ],
    )
    total_amount = _extract_amount(
        compact_text,
        [
            r"价\s*税\s*合\s*计[\s\S]{0,120}?小写\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
            r"价税合计(?:小写)?\s*[:：]?\s*([￥¥]?\s*[\d,]+(?:\.\d{1,4})?)",
        ],
    )

    remark = _line_after_label(compact_text, "备注")

    return InvoiceData(
        invoice_code=invoice_code,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        amount_without_tax=amount_without_tax,
        tax_amount=tax_amount,
        total_amount=total_amount,
        buyer_name=buyer_name,
        buyer_tax_id=buyer_tax_id,
        seller_name=seller_name,
        seller_tax_id=seller_tax_id,
        remark=remark,
        raw_text=raw_text,
    )
