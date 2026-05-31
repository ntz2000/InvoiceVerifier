InvoiceVerifier
InvoiceVerifier 是一个基于 Python 的电子发票自动查验与一致性比对工具。项目提供 PyQt5 桌面界面，支持上传 PDF、PNG、JPG、JPEG、WEBP 等格式的电子发票，也支持从剪贴板粘贴发票截图或文件路径。系统可调用 Qwen-VL OCR 自动提取发票字段，并通过 Playwright 打开国家税务总局全国增值税发票查验平台，辅助完成发票查验、官方结果保存、字段一致性比对和 Excel 报告生成。
本项目适用于发票批量核验、报销材料预审、财务审核辅助、发票字段一致性检查等场景。
---
1. 功能特点
支持 PDF、PNG、JPG、JPEG、WEBP 格式发票文件
支持从剪贴板粘贴发票截图、PDF 或图片文件路径
支持 Qwen-VL OCR 自动识别发票字段
支持 OCR 失败或未配置 API Key 时人工填写字段
支持自动打开国家税务总局全国增值税发票查验平台
支持自动填写发票号码、开票日期、价税合计等查验字段
支持人工输入验证码，程序不识别、不破解验证码
支持保存官方查验结果截图和 PDF
支持使用 Qwen OCR 识别官方查验结果截图
支持用户发票字段与官方查验字段一致性比对
支持生成 Excel 审核报告
支持高亮显示缺失字段、不一致字段和疑似异常字段
支持生成发票原图与官方查验结果拼图，方便人工复核
支持 PyInstaller 打包为 Windows 单文件 exe
---
2. 适用场景
InvoiceVerifier 主要适合以下场景：
企业财务发票审核
报销材料预审
发票真实性辅助查验
发票字段 OCR 抽取
发票上传件与官方查验结果一致性比对
财务人员人工复核前的初步筛查
自动生成发票核验 Excel 报告
注意：本项目是审核辅助工具，不替代财务人员、税务人员或官方平台的最终判断。
---
3. 项目结构
```text
.
├── app/
│   ├── __init__.py
│   └── main_window.py
├── config/
│   ├── __init__.py
│   └── api_config.py
├── core/
│   ├── __init__.py
│   ├── comparator.py
│   ├── file_loader.py
│   ├── invoice_ocr.py
│   ├── models.py
│   ├── official_parser.py
│   ├── report_generator.py
│   └── tax_verifier.py
├── build_exe.bat
├── main.py
├── ocr_demo.py
├── requirements.txt
└── README.md
```
路径	说明
`main.py`	程序入口，启动 PyQt5 桌面应用
`app/main_window.py`	主界面逻辑，包括文件选择、字段展示、查验流程、报告生成等
`core/invoice_ocr.py`	发票 OCR 识别逻辑，调用 Qwen-VL OCR 并解析发票字段
`core/tax_verifier.py`	使用 Playwright 打开官方查验平台、填表、截图和保存结果
`core/comparator.py`	用户发票字段与官方查验字段的一致性比对规则
`core/report_generator.py`	Excel 审核报告生成
`core/file_loader.py`	PDF、图片等文件读取与转换
`core/models.py`	发票数据结构和比对结果数据结构
`core/official_parser.py`	官方查验文本解析辅助逻辑
`config/api_config.py`	Qwen OCR API 配置
`ocr_demo.py`	OCR 功能独立测试脚本
`build_exe.bat`	Windows 单文件 exe 打包脚本
---
4. 环境要求
推荐环境：
Python 3.10+
Windows 10 / Windows 11
Chrome / Chromium 运行环境
可访问国家税务总局全国增值税发票查验平台
可选：阿里云百炼 DashScope API Key，用于 Qwen-VL OCR
Python 依赖见 `requirements.txt`：
```text
PyQt5
playwright
openpyxl
PyMuPDF
Pillow
requests
openai
```
---
5. 安装依赖
在项目根目录执行：
```bash
pip install -r requirements.txt
python -m playwright install chromium
```
如果你使用虚拟环境，推荐：
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```
---
6. OCR API 配置
项目使用阿里云百炼 OpenAI 兼容接口调用 `qwen-vl-ocr-latest` 进行发票字段识别。
打开：
```text
config/api_config.py
```
填写：
```python
DASHSCOPE_API_KEY = "your API_KEY"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_OCR_MODEL = "qwen-vl-ocr-latest"
QWEN_OCR_TEMPERATURE = 0
```
建议不要把真实 API Key 上传到公开仓库。如果要开源，推荐将真实配置文件加入 `.gitignore`，并提供 `api_config.example.py` 作为模板。
如果未配置 API Key，或者 OCR 调用失败，程序仍然可以运行，用户可以在界面中手动填写或修正发票字段。
---
7. 运行方式
在项目根目录执行：
```bash
python main.py
```
启动后会打开桌面界面：
```text
InvoiceVerifier - 电子发票自动查验与一致性比对工具
```
---
8. 使用流程
8.1 选择或粘贴发票
用户可以通过以下方式导入发票：
点击“选择发票文件”
在粘贴框中粘贴发票截图
在粘贴框中粘贴 PDF 或图片文件路径
拖入或粘贴支持格式的文件
支持格式包括：
```text
.pdf
.png
.jpg
.jpeg
.webp
```
---
8.2 自动识别字段
如果已配置 Qwen OCR API Key，可以点击：
```text
自动识别字段
```
程序会尝试提取以下字段：
发票代码
发票号码
开票日期
不含税金额
税额
价税合计
购买方名称
购买方税号
销售方名称
销售方税号
备注
OCR 结果会显示在界面中，用户可以人工确认和修改。
---
8.3 打开查验平台并填表
点击：
```text
自动打开查验平台并填表
```
程序会使用 Playwright 打开国家税务总局全国增值税发票查验平台：
```text
https://inv-veri.chinatax.gov.cn/
```
程序会根据用户发票字段自动填写查验表单。
当前查验流程中通常需要以下字段：
发票号码
开票日期
价税合计
发票代码，部分票种需要
---
8.4 人工输入验证码
官方平台验证码必须由用户人工输入。
本项目不会调用 OCR 识别验证码，也不会破解验证码。程序只会截图显示验证码区域和验证码提示，用户根据官方页面提示输入验证码文字。
---
8.5 提交查验并保存官方结果
输入验证码后，点击：
```text
提交查验 / 继续
```
查验完成后，可以保存官方结果：
```text
保存官方结果
```
程序会保存：
官方查验结果截图
官方查验结果 PDF，若当前浏览器模式不支持 PDF 保存，则只保存截图
---
8.6 识别官方结果并生成报告
程序可以使用 Qwen OCR 识别官方结果截图，提取官方查验字段。
随后点击：
```text
生成比对报告
```
程序会比较用户上传发票字段和官方查验字段，并生成 Excel 报告。
不一致、缺失或疑似异常字段会在报告中高亮显示。
---
8.7 查看复核拼图
点击：
```text
显示发票与官方结果拼图
```
程序会把用户上传的发票图像和官方查验结果截图合并成一张复核图片，方便人工对照检查。
---
9. 输出目录
程序运行后会在项目根目录生成 `output/` 目录。
常见输出包括：
```text
output/
├── captcha.png
├── official_result.png
├── official_result.pdf
├── clipboard/
├── debug/
├── reports/
├── review/
└── temp/
```
路径	说明
`output/temp/`	PDF 转图片等临时文件
`output/captcha.png`	验证码提示区域截图
`output/official_result.png`	官方查验结果截图
`output/official_result.pdf`	官方查验结果 PDF
`output/debug/`	调试截图、HTML、OCR 原始响应等
`output/reports/`	Excel 比对报告
`output/clipboard/`	剪贴板截图保存目录
`output/review/`	发票与官方结果拼图
---
10. 一致性比对说明
比对逻辑位于：
```text
core/comparator.py
```
系统会对用户发票字段和官方查验字段进行一致性检查。常见检查内容包括：
发票号码是否一致
发票代码是否一致
开票日期是否一致
价税合计是否一致
不含税金额是否一致
税额是否一致
购买方名称是否一致
购买方税号是否一致
销售方名称是否一致
销售方税号是否一致
备注等补充字段是否存在异常
报告中的结论由规则比对生成，不由大模型直接判断。
---
11. OCR Demo
可以单独运行 OCR 测试脚本：
```bash
python ocr_demo.py
```
使用前请在 `ocr_demo.py` 中设置图片路径，例如：
```python
IMAGE_PATH = r"your image path"
```
并确保 `config/api_config.py` 中已配置有效 API Key。
---
12. 打包为 exe
项目提供 Windows 打包脚本：
```text
build_exe.bat
```
打包前需要准备本地 `chrome-win64` 目录，并确保：
```text
chrome-win64\chrome.exe
```
存在于项目根目录。
然后运行：
```bat
build_exe.bat
```
打包完成后会生成：
```text
release\InvoiceVerifier.exe
```
说明：
打包脚本会创建 `.venv_pack` 虚拟环境
打包脚本会安装最小依赖
打包脚本会把本地 Chromium 文件打入 exe
生成结果为单文件 exe
首次启动会较慢，因为 onefile 会先解压运行
如果修改了 `config/api_config.py` 中的 API Key，需要重新打包
---
13. 安全与合规说明
本项目遵循以下原则：
不绕过官方网站验证流程
不破解验证码
不自动识别验证码
不保存或输出完整 API Key
OCR 仅用于发票字段和官方结果字段抽取
最终审核结论由规则比对和人工复核共同确认
上传公开仓库前请删除真实 API Key、缓存文件、输出文件和发票图片
建议公开仓库中不要包含：
```text
__pycache__/
*.pyc
.env
output/
logs/
*.xlsx
*.pdf
真实发票图片
真实 API Key
```
---
14. 建议的 .gitignore
推荐在项目根目录添加：
```gitignore
# Python cache
__pycache__/
*.py[cod]
*.pyo

# Virtual environments
.venv/
venv/
env/
.venv_pack/

# Build artifacts
build/
dist/
release/
*.spec

# Playwright / local browser files
chrome-win64/
ms-playwright/

# Local secrets
.env
*.env

# Runtime output
output/
logs/
*.log

# Generated reports and temporary files
*.xlsx
*.pdf
*.tmp
*.part

# IDE
.vscode/
.idea/
```
如果你选择不上传真实配置文件，也可以额外加入：
```gitignore
config/api_config.py
```
然后提供：
```text
config/api_config.example.py
```
作为配置模板。
---
15. 当前限制
OCR 识别效果受发票图片清晰度、旋转角度、遮挡和扫描质量影响
OCR 调用依赖阿里云百炼接口，网络异常或 API Key 无效时需要人工录入
官方查验平台页面结构变化可能导致自动填表逻辑需要调整
验证码必须人工输入，程序不会自动识别验证码
官方结果字段默认基于结果截图 OCR 提取，必要时仍建议人工复核
本项目目前主要面向 Windows 桌面环境
---
16. 常见问题
16.1 没有 API Key 能不能用？
可以。没有 API Key 时，OCR 自动识别不可用，但仍可以手动填写发票字段，并继续进行官方查验和报告生成。
16.2 程序会不会破解验证码？
不会。程序只显示验证码和提示区域截图，验证码必须由用户人工输入。
16.3 为什么查验平台打不开？
可能原因包括：
网络无法访问官方平台
Playwright Chromium 未安装
官方平台临时维护
本地代理或安全软件拦截
可以先执行：
```bash
python -m playwright install chromium
```
然后重新运行程序。
16.4 为什么 OCR 结果不准确？
可能原因包括：
发票截图不清晰
图片分辨率过低
PDF 转图片质量较差
发票版式特殊
发票字段被遮挡
模型返回内容不稳定
建议在界面中人工修正 OCR 结果后再查验。
16.5 生成的报告在哪里？
默认位于：
```text
output/reports/
```
16.6 打包后为什么首次启动慢？
因为 PyInstaller onefile 模式会先把程序和浏览器文件解压到临时目录，首次启动需要等待一段时间。
---
17. Roadmap
[ ] 支持更多发票类型和版式
[ ] 支持更稳定的官方结果字段解析
[ ] 支持批量发票队列处理
[ ] 支持报告模板自定义
[ ] 支持自动归档发票、截图和报告
[ ] 支持配置文件模板化
[ ] 支持更完善的异常日志和运行状态提示
[ ] 支持更多 OCR 模型后端
---
18. License
This project is released for learning, research, and internal auditing assistance purposes.
Recommended license: MIT License.
---
19. Disclaimer
InvoiceVerifier is an independent open-source tool and is not affiliated with, endorsed by, or sponsored by the State Taxation Administration or any invoice verification platform.
This project is designed only as an auxiliary tool for invoice verification and consistency checking. The final verification result should be based on the official platform and manual review. Users are responsible for complying with all applicable laws, regulations, platform terms of use, and data privacy requirements.
