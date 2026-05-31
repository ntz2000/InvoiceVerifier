from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
import unicodedata
from typing import Callable, List, Optional, Tuple

from core.models import CompareRow, InvoiceData


FIELD_SPECS: List[Tuple[str, str, str]] = [
    ("发票代码", "invoice_code", "text"),
    ("发票号码", "invoice_number", "text"),
    ("开票日期", "invoice_date", "date"),
    ("购买方名称", "buyer_name", "text"),
    ("购买方税号", "buyer_tax_id", "tax_id"),
    ("销售方名称", "seller_name", "text"),
    ("销售方税号", "seller_tax_id", "tax_id"),
    ("金额不含税", "amount_without_tax", "money"),
    ("税额", "tax_amount", "money"),
    ("价税合计", "total_amount", "money"),
    ("备注", "remark", "text"),
]

REQUIRED_FIELD_SPECS: List[Tuple[str, str]] = [
    ("发票号码", "invoice_number"),
    ("开票日期", "invoice_date"),
    ("价税合计", "total_amount"),
]


def _is_empty(value: str) -> bool:
    return not _normalize_text(value)


def _normalize_text(value: str) -> str:
    """文本归一化：统一全半角，去掉所有空白字符。"""
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[\s\u3000]+", "", value)


def _normalize_tax_id(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").upper()
    candidates = re.findall(r"[A-Z0-9]{10,30}", text)
    if candidates:
        return max(candidates, key=len)
    return re.sub(r"[^A-Z0-9]", "", text)


def _normalize_date(value: str) -> Optional[str]:
    """日期归一化：2025年08月22日、2025-08-22、20250822 都归成 YYYYMMDD。"""
    text = unicodedata.normalize("NFKC", value or "").strip()
    if not text:
        return None

    labeled_match = re.search(r"(?:开票|开具|填开)日期\D*(\d{4})\D*(\d{1,2})\D*(\d{1,2})", text)
    if labeled_match:
        year, month, day = labeled_match.groups()
        return f"{int(year):04d}{int(month):02d}{int(day):02d}"

    match = re.search(r"(\d{4})\D*(\d{1,2})\D*(\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}{int(month):02d}{int(day):02d}"

    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        return digits

    return None


def _normalize_money(value: str) -> Optional[str]:
    """金额归一化：去掉人民币符号和千分位，统一为两位小数。"""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("￥", "").replace("¥", "").replace("元", "")
    text = re.sub(r"[,\s\u3000]", "", text)
    if not text:
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None

    try:
        money = Decimal(match.group()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return format(money, "f")


def _money_decimal(value: str) -> Optional[Decimal]:
    normalized = _normalize_money(value)
    if normalized is None:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _normalizer(kind: str) -> Callable[[str], Optional[str]]:
    if kind == "date":
        return _normalize_date
    if kind == "money":
        return _normalize_money
    if kind == "tax_id":
        return lambda value: _normalize_tax_id(value)
    return lambda value: _normalize_text(value)


def _missing_message(user_value: str, official_value: str) -> str:
    user_missing = _is_empty(user_value)
    official_missing = _is_empty(official_value)
    if user_missing and official_missing:
        return "双方均无数据，待核验。"
    if user_missing:
        return "用户上传发票字段无数据，待核验。"
    if official_missing:
        return "官方查验字段无数据，待核验。"
    return "字段缺失或格式无法识别，待核验。"


def _looks_like_raw_json(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    bad_keywords = [
        "发票号码",
        "开票日期",
        "购买方",
        "销售方",
        "价税合计",
        "invoice_number",
        "invoice_date",
        "amount_without_tax",
        "total_amount",
    ]
    if text.startswith("{") or text.startswith("["):
        return True
    if '\\"' in text or "\\n" in text:
        return True
    if "{" in text or "}" in text:
        return True
    if len(text) > 80 and any(keyword in text for keyword in bad_keywords):
        return True
    return False


def _clean_field_before_compare(field_name: str, value: str) -> str:
    text = str(value or "").strip()
    if field_name == "remark" and _looks_like_raw_json(text):
        return ""
    if field_name in {"buyer_name", "seller_name"}:
        return _clean_party_name_before_compare(field_name, text)
    if field_name in {"buyer_tax_id", "seller_tax_id"}:
        return _extract_tax_id_for_compare(text)
    if field_name in {"invoice_code", "invoice_number"}:
        return _extract_identifier_for_compare(field_name, text)
    if field_name == "invoice_date":
        return _extract_date_text_for_compare(text)
    if field_name in {"amount_without_tax", "tax_amount", "total_amount"}:
        return _extract_money_text_for_compare(field_name, text)
    return text


def _clean_party_name_before_compare(field_name: str, value: str) -> str:
    text = _extract_json_alias_value(field_name, value) or str(value or "").strip()
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\\n", "\n").replace("\\r", "\n").replace("\\t", " ")
    text = re.sub(r"[\"'“”]+", "", text).strip()

    # OCR 偶尔会把“名称 + 税号/地址/电话”等格子串在一起；名称比对只取名称部分。
    stop_labels = [
        "统一社会信用代码",
        "纳税人识别号",
        "税号",
        "地址",
        "电话",
        "开户行",
        "账号",
        "银行",
        "购买方",
        "销售方",
        "项目名称",
        "规格型号",
        "金额",
        "税率",
        "税额",
    ]
    for label in stop_labels:
        index = text.find(label)
        if index > 0:
            text = text[:index]

    text = re.sub(
        r"^(?:名称|购买方名称|购方名称|受票方名称|买方名称|销售方名称|销方名称|销售单位名称|销货单位名称)\s*[:：]?\s*",
        "",
        text,
    )
    lines = [line.strip(" :：，,;；") for line in text.splitlines() if line.strip(" :：，,;；")]
    if lines:
        text = lines[0]
    return text.strip(" :：，,;；")


def _extract_tax_id_for_compare(value: str) -> str:
    text = _extract_json_alias_value("tax_id", value) or str(value or "")
    text = unicodedata.normalize("NFKC", text).upper()
    candidates = re.findall(r"[A-Z0-9]{10,30}", text)
    compact_text = re.sub(r"[^A-Z0-9]", "", text)
    if not candidates and 10 <= len(compact_text) <= 30:
        return compact_text
    if not candidates:
        return ""

    # 纳税人识别号一般是整段里最长的字母数字串，优先避开纯数字金额/日期。
    candidates = [candidate for candidate in candidates if not re.fullmatch(r"\d{8}", candidate)]
    return max(candidates, key=len) if candidates else ""


def _extract_identifier_for_compare(field_name: str, value: str) -> str:
    text = _extract_json_alias_value(field_name, value) or str(value or "")
    text = unicodedata.normalize("NFKC", text).upper()
    if not text.strip():
        return ""

    label_patterns = (
        [r"发票\s*代码\s*[:：]?\s*([A-Z0-9]{8,30})", r"机打发票代码\s*[:：]?\s*([A-Z0-9]{8,30})"]
        if field_name == "invoice_code"
        else [
            r"发票\s*号码\s*[:：]?\s*([A-Z0-9]{8,30})",
            r"发票\s*号\s*[:：]?\s*([A-Z0-9]{8,30})",
            r"数电票号码\s*[:：]?\s*([A-Z0-9]{8,30})",
        ]
    )
    for pattern in label_patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"[^A-Z0-9]", "", match.group(1))

    candidates = re.findall(r"[A-Z0-9]{8,30}", text)
    return max(candidates, key=len) if candidates else re.sub(r"[^A-Z0-9]", "", text)


def _extract_date_text_for_compare(value: str) -> str:
    text = _extract_json_alias_value("invoice_date", value) or str(value or "")
    text = unicodedata.normalize("NFKC", text)
    match = re.search(r"(?:开票|开具|填开)日期\D*(\d{4}\D*\d{1,2}\D*\d{1,2})", text)
    if match:
        return match.group(1)
    return text.strip()


def _extract_money_text_for_compare(field_name: str, value: str) -> str:
    text = _extract_json_alias_value(field_name, value) or str(value or "")
    text = unicodedata.normalize("NFKC", text)
    if not text.strip():
        return ""

    label_map = {
        "amount_without_tax": [r"不含税金额", r"金额不含税", r"合计金额", r"金额"],
        "tax_amount": [r"发票税额", r"税额合计", r"合计税额", r"税额"],
        "total_amount": [r"价税合计(?:\s*[（(]\s*小写\s*[）)])?", r"发票金额", r"小写金额", r"含税金额", r"总金额"],
    }
    money_pattern = r"[¥￥]?\s*(-?\d[\d,]*(?:\.\d+)?)"
    for label in label_map.get(field_name, []):
        match = re.search(label + r"[\s\S]{0,30}?" + money_pattern, text)
        if match:
            return match.group(1)
    return text.strip()


def _extract_json_alias_value(field_name: str, value: str) -> str:
    text = str(value or "").strip()
    if not (text.startswith("{") or text.startswith("[")):
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return ""

    alias_map = {
        "buyer_name": ["buyer_name", "购买方名称", "购方名称", "受票方名称", "买方名称", "名称"],
        "seller_name": ["seller_name", "销售方名称", "销方名称", "销售单位名称", "销货单位名称", "名称"],
        "tax_id": ["税号", "纳税人识别号", "统一社会信用代码", "统一社会信用代码/纳税人识别号"],
        "buyer_tax_id": ["buyer_tax_id", "购买方税号", "受票方税号", "购买方纳税人识别号"],
        "seller_tax_id": ["seller_tax_id", "销售方税号", "销方税号", "销售方纳税人识别号"],
        "invoice_code": ["invoice_code", "发票代码"],
        "invoice_number": ["invoice_number", "发票号码", "发票号"],
        "invoice_date": ["invoice_date", "开票日期", "开具日期", "填开日期"],
        "amount_without_tax": ["amount_without_tax", "不含税金额", "金额不含税"],
        "tax_amount": ["tax_amount", "发票税额", "税额"],
        "total_amount": ["total_amount", "价税合计", "发票金额"],
    }
    aliases = alias_map.get(field_name, [])
    normalized_aliases = {_normalize_compare_key(alias) for alias in aliases}

    def search(obj) -> str:
        if isinstance(obj, dict):
            for key, item in obj.items():
                if _normalize_compare_key(str(key)) in normalized_aliases:
                    return json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
            for item in obj.values():
                found = search(item)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = search(item)
                if found:
                    return found
        return ""

    return search(parsed).strip()


def _normalize_compare_key(value: str) -> str:
    return re.sub(r"[\s\u3000:：()（）\[\]【】_\-/\\]+", "", value or "").lower()


def _compare_remark(user_value: str, official_value: str) -> CompareRow:
    if _is_empty(user_value) and _is_empty(official_value):
        return CompareRow("备注", user_value, official_value, True, "双方均无有效备注。")
    if _is_empty(user_value):
        return CompareRow("备注", user_value, official_value, False, "用户备注未识别或为空，待核验；请人工确认。")
    if _is_empty(official_value):
        return CompareRow("备注", user_value, official_value, False, "官方备注未识别或为空，待核验；请人工确认。")

    normalized_user = _normalize_text(user_value)
    normalized_official = _normalize_text(official_value)
    if normalized_user == normalized_official:
        return CompareRow("备注", user_value, official_value, True, "一致")
    return CompareRow(
        "备注",
        user_value,
        official_value,
        False,
        f"备注不一致：用户值归一化为 {normalized_user}，官方值归一化为 {normalized_official}。",
    )


def compare_invoices(user: InvoiceData, official: InvoiceData) -> List[CompareRow]:
    """逐字段比对用户上传发票和官方查验结果。"""
    rows: List[CompareRow] = []

    for field_name, attr_name, kind in FIELD_SPECS:
        user_value = _clean_field_before_compare(attr_name, getattr(user, attr_name, "") or "")
        official_value = _clean_field_before_compare(attr_name, getattr(official, attr_name, "") or "")

        if attr_name == "invoice_code" and _is_empty(user_value) and _is_empty(official_value):
            rows.append(
                CompareRow(
                    field_name=field_name,
                    user_value=user_value,
                    official_value=official_value,
                    is_match=True,
                    message="双方均为空，电子发票/数电票可无发票代码。",
                )
            )
            continue

        if attr_name == "remark":
            rows.append(_compare_remark(user_value, official_value))
            continue

        if _is_empty(user_value) or _is_empty(official_value):
            rows.append(
                CompareRow(
                    field_name=field_name,
                    user_value=user_value,
                    official_value=official_value,
                    is_match=False,
                    message=_missing_message(user_value, official_value),
                )
            )
            continue

        normalizer = _normalizer(kind)
        normalized_user = normalizer(user_value)
        normalized_official = normalizer(official_value)

        if not normalized_user or not normalized_official:
            rows.append(
                CompareRow(
                    field_name=field_name,
                    user_value=user_value,
                    official_value=official_value,
                    is_match=False,
                    message="字段格式无法归一化，待核验。",
                )
            )
            continue

        if normalized_user == normalized_official:
            rows.append(
                CompareRow(
                    field_name=field_name,
                    user_value=user_value,
                    official_value=official_value,
                    is_match=True,
                    message="一致",
                )
            )
        else:
            rows.append(
                CompareRow(
                    field_name=field_name,
                    user_value=user_value,
                    official_value=official_value,
                    is_match=False,
                    message=f"不一致：用户值归一化为 {normalized_user}，官方值归一化为 {normalized_official}。",
                )
            )

    rows.extend(_extra_review_rows(user, official))
    return rows


def _extra_review_rows(user: InvoiceData, official: InvoiceData) -> List[CompareRow]:
    """追加复核辅助信息，让报告能看出金额关系和关键字段完整性。"""
    return [
        _required_fields_row(user, official),
        _money_delta_row("不含税金额差额", user.amount_without_tax, official.amount_without_tax),
        _money_delta_row("税额差额", user.tax_amount, official.tax_amount),
        _money_delta_row("价税合计差额", user.total_amount, official.total_amount),
        _amount_equation_row("金额勾稽（用户发票）", user, "user"),
        _amount_equation_row("金额勾稽（官方查验）", official, "official"),
    ]


def _required_fields_row(user: InvoiceData, official: InvoiceData) -> CompareRow:
    user_missing = [
        label for label, attr_name in REQUIRED_FIELD_SPECS if _is_empty(getattr(user, attr_name, ""))
    ]
    official_missing = [
        label for label, attr_name in REQUIRED_FIELD_SPECS if _is_empty(getattr(official, attr_name, ""))
    ]

    user_value = "完整" if not user_missing else "缺少：" + "、".join(user_missing)
    official_value = "完整" if not official_missing else "缺少：" + "、".join(official_missing)
    is_match = not user_missing and not official_missing
    if is_match:
        message = "发票号码、开票日期、价税合计均已取得。"
    else:
        parts = []
        if user_missing:
            parts.append("用户上传发票缺少 " + "、".join(user_missing))
        if official_missing:
            parts.append("官方查验结果缺少 " + "、".join(official_missing))
        message = "关键查验字段不完整，待核验：" + "；".join(parts) + "。"

    return CompareRow(
        field_name="关键查验字段完整性",
        user_value=user_value,
        official_value=official_value,
        is_match=is_match,
        message=message,
    )


def _money_delta_row(field_name: str, user_value: str, official_value: str) -> CompareRow:
    money_field = {
        "不含税金额差额": "amount_without_tax",
        "税额差额": "tax_amount",
        "价税合计差额": "total_amount",
    }.get(field_name, "")
    if money_field:
        user_value = _clean_field_before_compare(money_field, user_value)
        official_value = _clean_field_before_compare(money_field, official_value)

    user_money = _money_decimal(user_value)
    official_money = _money_decimal(official_value)
    if user_money is None or official_money is None:
        return CompareRow(
            field_name=field_name,
            user_value=user_value,
            official_value=official_value,
            is_match=False,
            message=_missing_message(user_value, official_value),
        )

    delta = (official_money - user_money).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    is_match = delta == Decimal("0.00")
    if is_match:
        message = "差额为 0.00。"
    else:
        message = f"差额不为 0：官方值 - 用户值 = {format(delta, 'f')}。"
    return CompareRow(field_name, user_value, official_value, is_match, message)


def _amount_equation_row(field_name: str, invoice: InvoiceData, side: str) -> CompareRow:
    amount_without_tax_text = _clean_field_before_compare("amount_without_tax", invoice.amount_without_tax)
    tax_amount_text = _clean_field_before_compare("tax_amount", invoice.tax_amount)
    total_amount_text = _clean_field_before_compare("total_amount", invoice.total_amount)

    amount_without_tax = _money_decimal(amount_without_tax_text)
    tax_amount = _money_decimal(tax_amount_text)
    total_amount = _money_decimal(total_amount_text)
    value_text = (
        f"不含税金额={amount_without_tax_text or '空'}；"
        f"税额={tax_amount_text or '空'}；"
        f"价税合计={total_amount_text or '空'}"
    )
    user_value = value_text if side == "user" else ""
    official_value = value_text if side == "official" else ""

    if amount_without_tax is None or tax_amount is None or total_amount is None:
        return CompareRow(
            field_name=field_name,
            user_value=user_value,
            official_value=official_value,
            is_match=False,
            message="金额勾稽所需字段不完整，待核验。",
        )

    calculated_total = (amount_without_tax + tax_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    normalized_total = total_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    is_match = calculated_total == normalized_total
    if is_match:
        message = f"金额勾稽一致：不含税金额 + 税额 = {format(calculated_total, 'f')}。"
    else:
        message = (
            "金额勾稽不一致："
            f"不含税金额 + 税额 = {format(calculated_total, 'f')}，"
            f"价税合计 = {format(normalized_total, 'f')}。"
        )
    return CompareRow(field_name, user_value, official_value, is_match, message)
