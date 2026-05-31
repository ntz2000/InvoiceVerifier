import base64
import json
import re
import time
from pathlib import Path

from PIL import Image, ImageOps
from openai import OpenAI


# =========================
# 配置区
# =========================

API_KEY = "your API_KEY"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-vl-ocr-latest"

IMAGE_PATH = r"your image path"

OCR_TARGET_WIDTH = 2000
OCR_JPEG_QUALITY = 85

TEMP_IMAGE_PATH = "ocr_demo_compressed.jpg"


PROMPT = """你是一个发票 OCR 信息抽取助手。
请从图片中提取所有可见发票字段，并以 JSON 格式输出。
尽量保留图片上的原始中文字段名作为 JSON key，不要强行改成英文字段名。
不要只输出摘要字段，不要把多个字段合并成“购买方信息”或“销售方信息”，请尽量逐项输出每个格子里的字段和值。

重点提取：
发票号码、开票日期、购买方名称、购买方税号、销售方名称、销售方税号、不含税金额、税额、价税合计、备注。

如果是机动车销售统一发票，还必须提取：
购买方名称、统一社会信用代码/纳税人识别号/身份证号码、车辆类型、厂牌型号、合格证号、发动机号码、车辆识别代号/车架号码、销售单位名称、电话、纳税人识别号、账号、地址、开户银行、增值税税率、增值税税额、主管税务机关及代码、不含税价、完税凭证号码、吨位、限乘人数、开票人、备注。

只输出 JSON，不要解释，不要 Markdown。"""


# =========================
# 图片压缩
# =========================

def compress_image(input_path: str, output_path: str, target_width: int = 2000, quality: int = 85) -> str:
    image = Image.open(input_path)
    image = ImageOps.exif_transpose(image)

    if image.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", image.size, "white")
        bg.paste(image, mask=image.split()[-1])
        image = bg
    else:
        image = image.convert("RGB")

    old_w, old_h = image.size

    if old_w != target_width:
        scale = target_width / old_w
        target_height = int(old_h * scale)
        image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)

    image.save(output_path, format="JPEG", quality=quality, optimize=True)
    return output_path


def image_to_data_url(image_path: str) -> str:
    with open(image_path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{image_base64}"


# =========================
# 解析模型输出
# =========================

def parse_json_or_markdown(text: str) -> dict:
    text = text.strip()

    # 1. 优先解析 ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # 2. 解析纯 JSON
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass

    # 3. 兜底解析 Markdown / 普通 key-value
    # 支持：
    # - **发票号码**: 263...
    # 发票号码：263...
    data = {}
    lines = text.splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        line = re.sub(r"^[-*]\s*", "", line)
        line = line.replace("**", "")

        if ":" in line:
            key, value = line.split(":", 1)
        elif "：" in line:
            key, value = line.split("：", 1)
        else:
            continue

        key = key.strip()
        value = value.strip().strip("，,。")
        if key and value:
            data[key] = value

    if data:
        data["raw_text"] = text

    return data


# =========================
# 字段映射
# =========================

FIELD_ALIASES = {
    "invoice_code": ["invoice_code", "发票代码", "机打发票代码"],
    "invoice_number": ["invoice_number", "发票号码", "发票号", "机打发票号码"],
    "invoice_date": ["invoice_date", "开票日期"],

    "amount_without_tax": [
        "amount_without_tax", "不含税金额", "金额不含税", "不含税价", "不含税价小写"
    ],
    "tax_amount": [
        "tax_amount", "发票税额", "税额", "增值税税额", "税额合计", "增值税税额"
    ],
    "total_amount": [
        "total_amount", "价税合计", "发票金额", "小写金额", "价税合计小写"
    ],

    "buyer_name": [
        "buyer_name", "购买方名称", "购方名称", "受票方名称", "买方名称", "购买方信息"
    ],
    "buyer_tax_id": [
        "buyer_tax_id", "购买方税号", "购方税号", "受票方税号",
        "购买方纳税人识别号", "受票方纳税人识别号",
        "统一社会信用代码/纳税人识别号/身份证号码"
    ],

    "seller_name": [
        "seller_name", "销售方名称", "销方名称", "销货单位名称",
        "销售单位名称", "销售方信息"
    ],
    "seller_tax_id": [
        "seller_tax_id", "销售方税号", "销方税号",
        "销售方纳税人识别号", "销方纳税人识别号",
        "纳税人识别号"
    ],

    "remark": ["remark", "备注"],
}


def find_value_recursive(obj, aliases):
    """递归查找中文字段名。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in aliases and value not in (None, ""):
                return value

        for value in obj.values():
            found = find_value_recursive(value, aliases)
            if found not in (None, ""):
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = find_value_recursive(item, aliases)
            if found not in (None, ""):
                return found

    return ""


def normalize_date(value: str) -> str:
    value = str(value or "").strip()
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return value


def normalize_money(value: str) -> str:
    text = str(value or "").replace("￥", "").replace("¥", "").replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return m.group(0) if m else ""


def map_to_invoice_fields(data: dict) -> dict:
    result = {}

    for field, aliases in FIELD_ALIASES.items():
        value = find_value_recursive(data, aliases)
        result[field] = "" if value is None else str(value).strip()

    # 日期归一化
    result["invoice_date"] = normalize_date(result["invoice_date"])

    # 金额归一化
    for key in ["amount_without_tax", "tax_amount", "total_amount"]:
        result[key] = normalize_money(result[key])

    # 特殊补救：如果模型输出“发票金额”，一般就是价税合计
    if not result["total_amount"]:
        value = find_value_recursive(data, ["发票金额", "价税合计", "小写金额"])
        result["total_amount"] = normalize_money(value)

    # raw_text 保存完整模型结果
    result["raw_text"] = json.dumps(data, ensure_ascii=False, indent=2)

    return result


# =========================
# OCR 调用
# =========================

def call_qwen_ocr(image_path: str) -> str:
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    data_url = image_to_data_url(image_path)

    start_time = time.time()

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        temperature=0,
    )

    elapsed = time.time() - start_time
    print(f"\nOCR 耗时：{elapsed:.2f} 秒\n")

    return resp.choices[0].message.content


def main():
    print("原图：", IMAGE_PATH)

    compressed_path = compress_image(
        IMAGE_PATH,
        TEMP_IMAGE_PATH,
        target_width=OCR_TARGET_WIDTH,
        quality=OCR_JPEG_QUALITY,
    )

    with Image.open(compressed_path) as img:
        print(f"压缩图：{compressed_path}")
        print(f"压缩尺寸：{img.width} x {img.height}")
        print(f"压缩大小：{Path(compressed_path).stat().st_size / 1024:.1f} KB")

    raw_output = call_qwen_ocr(compressed_path)

    print("========== RAW OUTPUT ==========")
    print(raw_output)

    parsed = parse_json_or_markdown(raw_output)

    print("\n========== PARSED DATA ==========")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))

    mapped = map_to_invoice_fields(parsed)

    print("\n========== MAPPED INVOICE DATA ==========")
    print(json.dumps(mapped, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()