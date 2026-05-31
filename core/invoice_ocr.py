from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image

from core.file_loader import load_invoice_file
from core.models import InvoiceData


OCR_TARGET_WIDTH_USER = 1800
OCR_TARGET_WIDTH_OFFICIAL = 1600
OCR_JPEG_QUALITY = 85


USER_INVOICE_PROMPT = """你是一个发票 OCR 信息抽取助手。
请从图片中提取所有可见发票字段，并以 JSON 格式输出。
尽量保留图片上的原始中文字段名作为 JSON key，不要强行改成英文字段名。
不要只输出摘要字段，不要把多个字段合并成“购买方信息”或“销售方信息”，请尽量逐项输出每个格子里的字段和值。

重点提取：
发票号码、开票日期、购买方名称、购买方税号、销售方名称、销售方税号、不含税金额、税额、价税合计、备注、项目明细。

如果是检测、认证、纺织、服装、贸易、物流、咨询或服务类发票，请重点保留：
项目名称、服务名称、检测费、认证费、咨询服务费、技术服务费、纺织品、服装、面料、辅料、规格型号、单位、数量、单价、金额、税率、税额。

如果是机动车销售统一发票，只需重点提取：
发票号码、开票日期、购买方名称、购买方税号、销售方名称、销售方税号、不含税金额、税额、价税合计、备注。
其他车辆字段如车辆类型、厂牌型号、发动机号码、车辆识别代号等可以保留在 raw_text 中，不要求单独结构化。

同一个字段名只能输出一次，不要重复输出相同 JSON key。
只输出 JSON，不要解释，不要 Markdown。"""


OFFICIAL_RESULT_PROMPT = """你是一个官方发票查验结果 OCR 信息抽取助手。
请从图片中的国家税务总局发票查验结果页面提取所有可见发票字段，并以 JSON 格式输出。
尽量保留页面上的原始中文字段名作为 JSON key。
请重点提取发票号码、开票日期、购买方信息、销售方信息、不含税金额、税额、价税合计、备注和明细项目。
注意：开票日期不是查验时间。
同一个字段名只能输出一次，不要重复输出相同 JSON key。
只输出 JSON，不要解释，不要 Markdown。"""


FIELD_ALIASES = {
    "invoice_code": [
        "invoice_code",
        "发票代码",
        "机打发票代码",
    ],
    "invoice_number": [
        "invoice_number",
        "发票号码",
        "发票号",
        "电子发票号码",
        "数电票号码",
        "机打发票号码",
    ],
    "invoice_date": [
        "invoice_date",
        "开票日期",
        "开具日期",
        "填开日期",
    ],
    "amount_without_tax": [
        "amount_without_tax",
        "不含税金额",
        "金额不含税",
        "不含税价",
        "金额",
        "合计金额",
        "价款",
        "税前金额",
    ],
    "tax_amount": [
        "tax_amount",
        "发票税额",
        "税额",
        "增值税税额",
        "税额合计",
        "合计税额",
    ],
    "total_amount": [
        "total_amount",
        "价税合计",
        "发票金额",
        "小写金额",
        "价税合计小写",
        "合计含税金额",
        "含税金额",
        "总金额",
        "小写",
        "价税合计（小写）",
        "价税合计(小写)",
    ],
    "buyer_name": [
        "buyer_name",
        "购买方名称",
        "购方名称",
        "受票方名称",
        "买方名称",
        "购买方信息",
        "客户名称",
        "委托方名称",
        "付款方名称",
    ],
    "buyer_tax_id": [
        "buyer_tax_id",
        "购买方税号",
        "购方税号",
        "受票方税号",
        "买方税号",
        "购买方纳税人识别号",
        "受票方纳税人识别号",
        "统一社会信用代码/纳税人识别号",
        "统一社会信用代码/纳税人识别号/身份证号码",
        "纳税人识别号/身份证号码",
        "客户税号",
        "委托方税号",
    ],
    "seller_name": [
        "seller_name",
        "销售方名称",
        "销方名称",
        "销货单位名称",
        "销售单位名称",
        "销售方信息",
        "开票单位名称",
        "收款单位名称",
        "服务方名称",
        "检测机构名称",
        "认证机构名称",
    ],
    "seller_tax_id": [
        "seller_tax_id",
        "销售方税号",
        "销方税号",
        "销售方纳税人识别号",
        "销方纳税人识别号",
        "销售单位纳税人识别号",
        "开票单位税号",
        "服务方税号",
        "检测机构税号",
        "认证机构税号",
    ],
    "remark": [
        "remark",
        "备注",
        "备注栏",
        "说明",
    ],
    "raw_text": [
        "raw_text",
        "全文",
        "原始文本",
    ],
}


BUYER_CONTEXT_KEYS = [
    "购买方",
    "购买方信息",
    "购方",
    "购方信息",
    "受票方",
    "受票方信息",
    "买方",
    "买方信息",
    "客户",
    "客户信息",
    "委托方",
    "委托方信息",
    "付款方",
    "付款方信息",
]

SELLER_CONTEXT_KEYS = [
    "销售方",
    "销售方信息",
    "销方",
    "销方信息",
    "卖方",
    "卖方信息",
    "销货单位",
    "销售单位",
    "开票单位",
    "收款单位",
    "服务方",
    "检测机构",
    "认证机构",
]


class QwenOCRClient:
    """阿里云百炼 OpenAI 兼容接口封装，只做字段抽取，不判断发票真伪。"""

    def __init__(self):
        try:
            from config.api_config import (
                DASHSCOPE_API_KEY,
                DASHSCOPE_BASE_URL,
                QWEN_OCR_MODEL,
                QWEN_OCR_TEMPERATURE,
            )
        except Exception:
            DASHSCOPE_API_KEY = ""
            DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            QWEN_OCR_MODEL = "qwen-vl-ocr-latest"
            QWEN_OCR_TEMPERATURE = 0

        self.api_key = (DASHSCOPE_API_KEY or "").strip()
        self.base_url = DASHSCOPE_BASE_URL
        self.model = QWEN_OCR_MODEL
        self.temperature = QWEN_OCR_TEMPERATURE
        self.client = None
        self.init_error = ""

        if not self._has_valid_api_key():
            self.init_error = "DASHSCOPE_API_KEY 未配置，OCR 将返回空结果。"
            return

        try:
            from openai import OpenAI

            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        except Exception as exc:
            self.init_error = f"OpenAI SDK 初始化失败：{exc}"

    def extract_invoice_from_image(self, image_path: str, mode: str = "user_invoice") -> InvoiceData:
        """识别单张图片。每张图片只调用一次 qwen-vl-ocr-latest。"""
        source_path = Path(image_path)
        if not source_path.exists():
            raise FileNotFoundError(f"图片文件不存在：{image_path}")

        if self.client is None:
            if self.init_error:
                print(f"[Qwen OCR] {self.init_error}")
            return InvoiceData()

        prompt = OFFICIAL_RESULT_PROMPT if mode == "official_result" else USER_INVOICE_PROMPT
        processed_path = self._preprocess_image_for_ocr(source_path, mode)
        data_url = self._image_to_data_url(str(processed_path))
        return self._extract_with_prompt(data_url, prompt, str(processed_path), mode)

    def _preprocess_image_for_ocr(self, image_path: Path, mode: str) -> Path:
        """统一转 RGB、按场景缩放宽度并保存为 JPG，再用压缩图调用 OCR。"""
        target_width = OCR_TARGET_WIDTH_OFFICIAL if mode == "official_result" else OCR_TARGET_WIDTH_USER
        output_dir = Path("output") / "temp"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_stem = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", image_path.stem).strip("_") or "invoice"
        output_path = output_dir / f"qwen_ocr_{safe_stem}_{mode}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"

        try:
            with Image.open(image_path) as image:
                rgb_image = image.convert("RGB")
                if rgb_image.width <= 0 or rgb_image.height <= 0:
                    raise ValueError("图片尺寸无效")
                target_height = max(1, round(rgb_image.height * target_width / rgb_image.width))
                resized = rgb_image.resize((target_width, target_height), Image.LANCZOS)
                resized.save(output_path, format="JPEG", quality=OCR_JPEG_QUALITY, optimize=True)
        except Exception as exc:
            raise RuntimeError(f"OCR 图片预处理失败：{image_path}，原因：{exc}") from exc

        print(f"[Qwen OCR] 使用预处理图片调用 API：{output_path}")
        return output_path

    def _extract_with_prompt(
        self,
        data_url: str,
        prompt: str,
        image_path: str,
        mode: str,
    ) -> InvoiceData:
        """调用模型一次，然后做中文字段映射和本地正则补救。"""
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
                temperature=self.temperature,
            )
            response_text = completion.choices[0].message.content or ""
        except Exception as exc:
            print(f"[Qwen OCR] 调用失败：{exc}")
            return InvoiceData()

        self._print_raw_response_debug(image_path, mode, response_text)
        parsed = self._parse_model_response(response_text)
        self._print_parsed_json_debug(parsed)

        invoice = self._dict_to_invoice_data(parsed, response_text)
        if mode == "official_result":
            self._clean_official_result_invoice(invoice)

        self._print_recovered_invoice_debug(invoice)
        self._print_invoice_debug(invoice)
        return invoice

    def _image_to_data_url(self, image_path: str) -> str:
        """把预处理后的 JPG 转为 qwen-vl-ocr-latest 可读取的 data URL。"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在：{image_path}")

        suffix = path.suffix.lower()
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/jpeg")

        with path.open("rb") as file:
            encoded = base64.b64encode(file.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _parse_model_response(self, text: str) -> dict[str, Any]:
        """先解析 JSON；失败后兼容 Markdown 列表格式。"""
        parsed = self._parse_json_response(text)
        if parsed:
            return parsed

        markdown_data = self._parse_markdown_response(text)
        if markdown_data:
            return markdown_data

        debug_dir = Path("output") / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        raw_path = debug_dir / f"qwen_ocr_raw_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        raw_path.write_text(text or "", encoding="utf-8")
        print(f"[Qwen OCR] 模型返回无法结构化，原始内容已保存：{raw_path}")
        return {}

    def _parse_json_response(self, text: str) -> dict[str, Any]:
        """尽量从模型输出中解析 JSON。"""
        raw = (text or "").strip()
        if not raw:
            return {}

        candidates = [raw]
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if fence_match:
            candidates.insert(0, fence_match.group(1).strip())

        json_block = self._extract_json_block(raw)
        if json_block:
            candidates.append(json_block)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    return {"项目明细": parsed}
            except Exception:
                continue
        return {}

    def _parse_markdown_response(self, text: str) -> dict[str, Any]:
        """JSON 解析失败时，兼容 Markdown 列表：- **字段**: 值。"""
        result: dict[str, Any] = {}
        for line in (text or "").splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            cleaned = re.sub(r"^\s*[-*+]\s*", "", cleaned)
            cleaned = re.sub(r"^\s*\d+[.)、]\s*", "", cleaned)
            match = re.match(r"\**\s*([^:*：]+?)\s*\**\s*[:：]\s*(.+?)\s*$", cleaned)
            if not match:
                continue
            key = re.sub(r"[*`_]+", "", match.group(1)).strip()
            value = re.sub(r"[*`]+", "", match.group(2)).strip()
            if key and value:
                result[key] = value
        return result

    def _dict_to_invoice_data(self, data: dict[str, Any], response_text: str = "") -> InvoiceData:
        """把模型返回的中文 JSON 别名映射到 InvoiceData。"""
        if not isinstance(data, dict):
            data = {}

        invoice = InvoiceData(
            invoice_code=self._get_alias_value(data, FIELD_ALIASES["invoice_code"]),
            invoice_number=self._get_alias_value(data, FIELD_ALIASES["invoice_number"]),
            invoice_date=self._get_alias_value(data, FIELD_ALIASES["invoice_date"]),
            amount_without_tax=self._get_alias_value(
                data,
                [alias for alias in FIELD_ALIASES["amount_without_tax"] if alias not in {"金额", "合计金额"}],
            ),
            tax_amount=self._get_alias_value(
                data,
                [alias for alias in FIELD_ALIASES["tax_amount"] if alias != "税额"],
            ),
            total_amount=self._get_alias_value(data, FIELD_ALIASES["total_amount"]),
            buyer_name=self._get_alias_value(
                data,
                [alias for alias in FIELD_ALIASES["buyer_name"] if alias not in {"购买方信息"}],
            ),
            buyer_tax_id=self._get_contextual_alias_value(data, "buyer_tax_id")
            or self._get_alias_value(
                data,
                [
                    "buyer_tax_id",
                    "购买方税号",
                    "购方税号",
                    "受票方税号",
                    "买方税号",
                    "购买方纳税人识别号",
                    "受票方纳税人识别号",
                    "客户税号",
                    "委托方税号",
                ],
            ),
            seller_name=self._get_alias_value(
                data,
                [alias for alias in FIELD_ALIASES["seller_name"] if alias not in {"销售方信息"}],
            ),
            seller_tax_id=self._get_contextual_alias_value(data, "seller_tax_id")
            or self._get_alias_value(
                data,
                [
                    "seller_tax_id",
                    "销售方税号",
                    "销方税号",
                    "销售方纳税人识别号",
                    "销方纳税人识别号",
                    "销售单位纳税人识别号",
                    "开票单位税号",
                    "服务方税号",
                    "检测机构税号",
                    "认证机构税号",
                ],
            ),
            remark=self._get_alias_value(data, FIELD_ALIASES["remark"]),
            raw_text=self._get_alias_value(data, FIELD_ALIASES["raw_text"]),
        )

        invoice.buyer_name = invoice.buyer_name or self._get_contextual_alias_value(data, "buyer_name")
        invoice.seller_name = invoice.seller_name or self._get_contextual_alias_value(data, "seller_name")
        invoice.buyer_tax_id = invoice.buyer_tax_id or self._get_contextual_alias_value(data, "buyer_tax_id")
        invoice.seller_tax_id = invoice.seller_tax_id or self._get_contextual_alias_value(data, "seller_tax_id")

        self._apply_amount_special_rules(data, invoice)

        # “金额/税额/合计金额”容易出现在明细项目里，只作为顶层兜底。
        invoice.amount_without_tax = invoice.amount_without_tax or self._get_top_level_alias_value(data, ["金额", "合计金额"])
        invoice.tax_amount = invoice.tax_amount or self._get_top_level_alias_value(data, ["税额"])

        # 当前没有 line_items 字段，因此把完整 JSON 保留在 raw_text，方便后续结构化明细项目。
        if not invoice.raw_text:
            invoice.raw_text = json.dumps(data, ensure_ascii=False)

        self._recover_missing_fields(invoice, response_text)
        return invoice

    def _apply_amount_special_rules(self, data: dict[str, Any], invoice: InvoiceData) -> None:
        """金额字段特殊规则，避免把发票金额误当成不含税金额。"""
        without_tax = self._get_alias_value(data, ["不含税金额"])
        tax_amount = self._get_alias_value(data, ["税额"])
        total_amount = self._get_alias_value(data, ["价税合计"])
        if without_tax and tax_amount and total_amount:
            invoice.amount_without_tax = without_tax
            invoice.tax_amount = tax_amount
            invoice.total_amount = total_amount

        invoice_tax = self._get_alias_value(data, ["发票税额"])
        invoice_total = self._get_alias_value(data, ["发票金额"])
        if without_tax and invoice_tax and invoice_total:
            invoice.amount_without_tax = without_tax
            invoice.tax_amount = invoice_tax
            invoice.total_amount = invoice_total

    def _get_alias_value(self, data: Any, aliases: list[str]) -> str:
        """支持英文/中文 key、嵌套 dict/list；先精确匹配，再包含匹配。"""
        return self._find_value_recursive(data, aliases)

    def _find_value_recursive(self, data: Any, aliases: list[str]) -> str:
        alias_keys = {self._normalize_key(alias) for alias in aliases}

        def stringify(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value).strip()

        def search(obj: Any) -> str:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if self._normalize_key(str(key)) in alias_keys:
                        text = stringify(value)
                        if text:
                            return text
                for key, value in obj.items():
                    normalized_key = self._normalize_key(str(key))
                    if any(alias_key and alias_key in normalized_key for alias_key in alias_keys):
                        text = stringify(value)
                        if text:
                            return text
                for value in obj.values():
                    found = search(value)
                    if found:
                        return found
            elif isinstance(obj, list):
                for item in obj:
                    found = search(item)
                    if found:
                        return found
            return ""

        return search(data).strip()

    def _get_contextual_alias_value(self, data: Any, field_name: str) -> str:
        """根据购买方/销售方上下文提取名称和税号，避免双方税号串位。"""
        if field_name.startswith("buyer"):
            context_keys = BUYER_CONTEXT_KEYS
            value_aliases = (
                ["名称", "购买方名称", "购方名称", "受票方名称", "买方名称", "客户名称", "委托方名称", "付款方名称"]
                if field_name == "buyer_name"
                else ["税号", "纳税人识别号", "统一社会信用代码", "统一社会信用代码/纳税人识别号", "纳税人识别号/身份证号码"]
            )
        else:
            context_keys = SELLER_CONTEXT_KEYS
            value_aliases = (
                ["名称", "销售方名称", "销方名称", "销售单位名称", "开票单位名称", "服务方名称", "检测机构名称", "认证机构名称"]
                if field_name == "seller_name"
                else ["税号", "纳税人识别号", "统一社会信用代码", "统一社会信用代码/纳税人识别号"]
            )

        context_norms = {self._normalize_key(key) for key in context_keys}

        def search_context(obj: Any) -> str:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    normalized_key = self._normalize_key(str(key))
                    if normalized_key in context_norms or any(ctx in normalized_key for ctx in context_norms):
                        found = self._get_alias_value(value, value_aliases)
                        if found:
                            return found
                        if isinstance(value, str):
                            found = self._extract_labeled_value_from_text(value, value_aliases)
                            if found:
                                return found
                for value in obj.values():
                    found = search_context(value)
                    if found:
                        return found
            elif isinstance(obj, list):
                for item in obj:
                    found = search_context(item)
                    if found:
                        return found
            return ""

        return search_context(data).strip()

    def _get_top_level_alias_value(self, data: Any, aliases: list[str]) -> str:
        """只在顶层 JSON 取通用别名，避免递归命中明细项目金额。"""
        if not isinstance(data, dict):
            return ""
        alias_keys = {self._normalize_key(alias) for alias in aliases}
        for key, value in data.items():
            if self._normalize_key(str(key)) in alias_keys:
                if value is None:
                    return ""
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False)
                return str(value).strip()
        return ""

    def _recover_missing_fields(self, invoice: InvoiceData, response_text: str) -> None:
        """从 raw_text 和原始响应中补救模型顶层 JSON 漏填的字段。"""
        invoice_json = json.dumps(asdict(invoice), ensure_ascii=False)
        full_text = f"{invoice_json}\n{invoice.raw_text or ''}\n{response_text or ''}"
        compact_text = re.sub(r"[\s\u3000]+", "", full_text)

        if not invoice.invoice_number:
            invoice.invoice_number = self._extract_invoice_number(full_text, compact_text)

        if invoice.invoice_code and invoice.invoice_number:
            code = self._clean_code_value(invoice.invoice_code)
            number = self._clean_invoice_number(invoice.invoice_number)
            if code == number and len(number) >= 16:
                invoice.invoice_code = ""
                invoice.invoice_number = number

        opening_date = self._extract_opening_date(full_text, compact_text)
        if opening_date:
            invoice.invoice_date = opening_date
        elif invoice.invoice_date:
            invoice.invoice_date = self._format_date_value(invoice.invoice_date)

        if not invoice.total_amount or self._same_money(invoice.total_amount, invoice.amount_without_tax):
            total_amount = self._extract_total_amount(full_text, compact_text)
            if total_amount:
                invoice.total_amount = total_amount

        amount_tax_pair = self._extract_amount_tax_pair(full_text, compact_text)
        if amount_tax_pair:
            amount_without_tax, tax_amount = amount_tax_pair
            if not invoice.amount_without_tax:
                invoice.amount_without_tax = amount_without_tax
            if not invoice.tax_amount:
                invoice.tax_amount = tax_amount

        if not invoice.buyer_name:
            invoice.buyer_name = self._extract_party_name(full_text, "buyer")
        if not invoice.buyer_tax_id:
            invoice.buyer_tax_id = self._extract_party_tax_id(full_text, "buyer")
        if not invoice.seller_name:
            invoice.seller_name = self._extract_party_name(full_text, "seller")
        if not invoice.seller_tax_id:
            invoice.seller_tax_id = self._extract_party_tax_id(full_text, "seller")
        self._normalize_invoice_values(invoice)
        invoice.remark = self._clean_remark_for_compare(invoice.remark)

    def _clean_official_result_invoice(self, invoice: InvoiceData) -> None:
        """官方结果中开票日期只能来自“开票日期”，不能误用“查验时间”。"""
        text = f"{invoice.raw_text or ''}\n{json.dumps(asdict(invoice), ensure_ascii=False)}"
        compact_text = re.sub(r"[\s\u3000]+", "", text)
        opening_date = self._extract_opening_date(text, compact_text)
        check_dates = [
            self._format_date_value(value)
            for value in re.findall(
                r"查\s*验\s*(?:日期|时间)\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*(\d{4}\s*[年/\-.]\s*\d{1,2}\s*[月/\-.]\s*\d{1,2})",
                text,
            )
        ]

        current_date = self._format_date_value(invoice.invoice_date)
        if opening_date:
            invoice.invoice_date = opening_date
        elif current_date in check_dates:
            invoice.invoice_date = ""
        else:
            invoice.invoice_date = current_date

    def _extract_invoice_number(self, full_text: str, compact_text: str) -> str:
        patterns = [
            r"发\s*票\s*号\s*码\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9][A-Z0-9\s-]{7,39})",
            r"数\s*电\s*票\s*号\s*码\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9][A-Z0-9\s-]{7,39})",
            r"电\s*子\s*发\s*票\s*号\s*码\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9][A-Z0-9\s-]{7,39})",
            r"发\s*票\s*号\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9][A-Z0-9\s-]{7,39})",
            r"(?<!代码)号码\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9][A-Z0-9\s-]{7,39})",
        ]
        for text in (full_text, compact_text):
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    number = self._clean_invoice_number(match.group(1))
                    if 8 <= len(number) <= 30:
                        return number
        return ""

    def _extract_opening_date(self, full_text: str, compact_text: str) -> str:
        patterns = [
            r"(?:开\s*票|开\s*具|填\s*开)\s*日\s*期\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*(\d{4}\s*[年/\-.]\s*\d{1,2}\s*[月/\-.]\s*\d{1,2}\s*日?)",
            r"(?:开\s*票|开\s*具|填\s*开)\s*日\s*期\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*(\d{8})",
        ]
        for text in (full_text, compact_text):
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    date_value = self._format_date_value(match.group(1))
                    if date_value:
                        return date_value
        return ""

    def _extract_total_amount(self, full_text: str, compact_text: str) -> str:
        patterns = [
            r"价\s*税\s*合\s*计[\s\S]{0,120}?[（(]\s*小\s*写\s*[）)]\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)",
            r"[（(]\s*小\s*写\s*[）)]\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)",
            r"价\s*税\s*合\s*计\s*小\s*写\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)",
            r"发\s*票\s*金\s*额\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)",
        ]
        for text in (full_text, compact_text):
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    return self._clean_money_value(match.group(1))
        return ""

    def _extract_amount_tax_pair(self, full_text: str, compact_text: str) -> tuple[str, str] | None:
        line_texts = re.split(r"[\r\n]+", full_text)
        for line in line_texts:
            if not re.search(r"合\s*计", line):
                continue
            if re.search(r"价\s*税\s*合\s*计|小\s*写|大\s*写", line):
                continue
            numbers = re.findall(r"[¥￥]?\s*([+-]?\d[\d,]*(?:\.\d+)?)", line)
            if len(numbers) >= 2:
                return self._clean_money_value(numbers[-2]), self._clean_money_value(numbers[-1])

        pattern = r"(?<!价税)合\s*计\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)"
        match = re.search(pattern, compact_text)
        if match:
            return self._clean_money_value(match.group(1)), self._clean_money_value(match.group(2))
        return None

    def _extract_party_name(self, full_text: str, party: str) -> str:
        section = self._extract_party_section(full_text, party)
        if section:
            match = re.search(r"名\s*称\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([^\n\r,，;；]+)", section)
            if match:
                return self._clean_party_value(match.group(1))

        labels = (
            r"(?:购买方|购方|受票方|买方|客户|委托方|付款方)\s*名\s*称"
            if party == "buyer"
            else r"(?:销售方|销方|销货单位|销售单位|开票单位|收款单位|服务方|检测机构|认证机构)\s*名\s*称"
        )
        match = re.search(labels + r"\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([^\n\r,，;；]+)", full_text)
        return self._clean_party_value(match.group(1)) if match else ""

    def _extract_party_tax_id(self, full_text: str, party: str) -> str:
        section = self._extract_party_section(full_text, party)
        patterns = [
            r"统一社会信用代码\s*/?\s*纳税人识别号(?:\s*/?\s*身份证号码)?\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9]{10,30})",
            r"纳税人识别号(?:\s*/?\s*身份证号码)?\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9]{10,30})",
            r"税\s*号\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9]{10,30})",
        ]
        if section:
            for pattern in patterns:
                match = re.search(pattern, section, re.IGNORECASE)
                if match:
                    return self._clean_tax_id(match.group(1))

        party_label = (
            r"(?:购买方|购方|受票方|买方|客户|委托方|付款方)"
            if party == "buyer"
            else r"(?:销售方|销方|销货单位|销售单位|开票单位|收款单位|服务方|检测机构|认证机构)"
        )
        specific_patterns = [
            party_label + r"\s*(?:税号|纳税人识别号|统一社会信用代码)\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9]{10,30})",
            party_label + r"[\s\S]{0,180}?统一社会信用代码\s*/?\s*纳税人识别号(?:\s*/?\s*身份证号码)?\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([A-Z0-9]{10,30})",
        ]
        for pattern in specific_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                return self._clean_tax_id(match.group(1))
        return ""

    def _extract_party_section(self, full_text: str, party: str) -> str:
        if party == "buyer":
            start = r"(?:购买方信息|购买方|购方信息|购方|受票方信息|受票方|买方信息|买方|客户信息|客户|委托方信息|委托方|付款方信息|付款方)"
            end = r"(?:销售方信息|销售方|销方信息|销方|销售单位|开票单位|收款单位|服务方|检测机构|认证机构|项目名称|货物或应税劳务|合\s*计|价\s*税\s*合\s*计)"
        else:
            start = r"(?:销售方信息|销售方|销方信息|销方|销售单位|销货单位|开票单位|收款单位|服务方|检测机构|认证机构|卖方信息|卖方)"
            end = r"(?:项目名称|货物或应税劳务|合\s*计|价\s*税\s*合\s*计|备注|开票人)"
        match = re.search(start + r"([\s\S]{0,600}?)(?=" + end + r"|$)", full_text)
        return match.group(1) if match else ""

    def _extract_labeled_value_from_text(self, text: str, aliases: list[str]) -> str:
        for alias in aliases:
            pattern = re.escape(alias)
            match = re.search(pattern + r"\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([^\n\r,，;；]+)", text)
            if match:
                return self._clean_party_value(match.group(1))
        return ""

    def _extract_remark(self, full_text: str) -> str:
        match = re.search(r"备\s*注(?:栏)?\s*[\"'“”]?\s*[:：]?\s*[\"'“”]?\s*([\s\S]{0,300}?)(?=开票人|复核|收款人|销售方|$)", full_text)
        if not match:
            return ""
        remark = self._clean_party_value(match.group(1))
        return remark if remark not in {"", "无"} else ""

    def _normalize_invoice_values(self, invoice: InvoiceData) -> None:
        invoice.invoice_code = self._clean_code_value(invoice.invoice_code)
        invoice.invoice_number = self._clean_invoice_number(invoice.invoice_number)
        invoice.invoice_date = self._format_date_value(invoice.invoice_date)
        invoice.amount_without_tax = self._clean_money_value(invoice.amount_without_tax)
        invoice.tax_amount = self._clean_money_value(invoice.tax_amount)
        invoice.total_amount = self._clean_money_value(invoice.total_amount)
        invoice.buyer_tax_id = self._clean_tax_id(invoice.buyer_tax_id)
        invoice.seller_tax_id = self._clean_tax_id(invoice.seller_tax_id)

    @staticmethod
    def _clean_remark_for_compare(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""

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
            return ""
        if '\\"' in text or "\\n" in text:
            return ""
        if "{" in text or "}" in text:
            return ""
        if len(text) > 80 and any(keyword in text for keyword in bad_keywords):
            return ""

        return text.strip()

    @staticmethod
    def _normalize_key(value: str) -> str:
        return re.sub(r"[\s\u3000:：()（）\[\]【】_\-/\\]+", "", value or "").lower()

    @staticmethod
    def _clean_invoice_number(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    @staticmethod
    def _clean_code_value(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    @staticmethod
    def _clean_tax_id(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    @staticmethod
    def _clean_money_value(value: str) -> str:
        text = str(value or "").strip()
        text = text.replace(",", "").replace("￥", "").replace("¥", "").replace("人民币", "")
        match = re.search(r"[+-]?\d+(?:\.\d+)?", text)
        return match.group(0) if match else ""

    @staticmethod
    def _clean_party_value(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"^[\"'“”\s\u3000:：]+|[\"'“”\s\u3000,，;；]+$", "", text)
        return text

    @staticmethod
    def _same_money(left: str, right: str) -> bool:
        def normalize(value: str) -> str:
            text = str(value or "").replace(",", "").replace("￥", "").replace("¥", "").strip()
            match = re.search(r"[+-]?\d+(?:\.\d+)?", text)
            if not match:
                return ""
            try:
                return f"{float(match.group(0)):.2f}"
            except Exception:
                return match.group(0)

        left_value = normalize(left)
        right_value = normalize(right)
        return bool(left_value and right_value and left_value == right_value)

    @staticmethod
    def _format_date_value(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""

        digits = re.sub(r"\D", "", text)
        if len(digits) >= 8:
            year, month, day = digits[:4], digits[4:6], digits[6:8]
            return f"{year}-{month}-{day}"

        match = re.search(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})", text)
        if match:
            year, month, day = match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        return text

    @staticmethod
    def _extract_json_block(text: str) -> str:
        start_positions = [idx for idx, char in enumerate(text) if char in "{["]
        for start in start_positions:
            stack = []
            in_string = False
            escape = False
            for index in range(start, len(text)):
                char = text[index]
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char in "{[":
                    stack.append(char)
                elif char in "}]":
                    if not stack:
                        break
                    opening = stack.pop()
                    if (opening, char) not in {("{", "}"), ("[", "]")}:
                        break
                    if not stack:
                        return text[start : index + 1]
        return ""

    def _has_valid_api_key(self) -> bool:
        if not self.api_key:
            return False
        placeholders = ["在这里填", "你的", "api_key", "dashscope_api_key"]
        lower_key = self.api_key.lower()
        return not any(placeholder in lower_key for placeholder in placeholders)

    @staticmethod
    def _print_raw_response_debug(image_path: str, mode: str, response_text: str) -> None:
        print("\n========== Qwen OCR RAW RESPONSE ==========")
        print(f"image: {image_path}")
        print(f"mode: {mode}")
        print("model: qwen-vl-ocr-latest")
        print(response_text)

    @staticmethod
    def _print_parsed_json_debug(parsed: dict[str, Any]) -> None:
        print("========== Qwen OCR PARSED JSON ==========")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))

    @staticmethod
    def _print_recovered_invoice_debug(invoice: InvoiceData) -> None:
        print("========== Qwen OCR RECOVERED INVOICE DATA ==========")
        print(json.dumps(asdict(invoice), ensure_ascii=False, indent=2))
        print("=====================================================")

    @staticmethod
    def _print_invoice_debug(invoice: InvoiceData) -> None:
        print("========== Qwen OCR INVOICE DATA ==========")
        print(json.dumps(asdict(invoice), ensure_ascii=False, indent=2))
        print("==========================================\n")


class InvoiceOCR:
    """OCR 入口。PDF 会先转图片；无 API Key 时返回空数据供人工录入。"""

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def __init__(self):
        self.client = QwenOCRClient()

    def extract(self, file_path: str) -> InvoiceData:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"发票文件不存在：{file_path}")

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            image_paths = load_invoice_file(str(path))
        elif suffix in self.IMAGE_SUFFIXES:
            image_paths = [str(path)]
        else:
            raise ValueError(f"不支持的发票文件格式：{suffix}")

        invoices = [
            self.client.extract_invoice_from_image(image_path, mode="user_invoice")
            for image_path in image_paths
        ]
        return self._merge_invoices(invoices)

    @staticmethod
    def _merge_invoices(invoices: list[InvoiceData]) -> InvoiceData:
        merged = InvoiceData()
        raw_parts = []
        fields = [
            "invoice_code",
            "invoice_number",
            "invoice_date",
            "amount_without_tax",
            "tax_amount",
            "total_amount",
            "buyer_name",
            "buyer_tax_id",
            "seller_name",
            "seller_tax_id",
            "remark",
        ]

        for invoice in invoices:
            for field in fields:
                if not getattr(merged, field) and getattr(invoice, field):
                    setattr(merged, field, getattr(invoice, field))
            if invoice.raw_text:
                raw_parts.append(invoice.raw_text)

        merged.raw_text = "\n\n".join(raw_parts)
        return merged
