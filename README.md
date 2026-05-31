InvoiceVerifier
<p align="center">
  <strong>电子发票自动查验与一致性比对工具</strong>
</p>
<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue">
  <img alt="PyQt5" src="https://img.shields.io/badge/GUI-PyQt5-green">
  <img alt="Playwright" src="https://img.shields.io/badge/Browser-Playwright-orange">
  <img alt="OCR" src="https://img.shields.io/badge/OCR-Qwen--VL-purple">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-lightgrey">
</p>
InvoiceVerifier 是一个基于 Python 的电子发票自动查验与一致性比对工具。  
项目提供 PyQt5 桌面界面，支持导入 PDF / PNG / JPG / JPEG / WEBP 等格式的发票文件，并可调用 Qwen-VL OCR 自动提取发票字段。随后，程序通过 Playwright 打开国家税务总局全国增值税发票查验平台，辅助完成发票查验、官方结果保存、字段一致性比对和 Excel 报告生成。
> 本项目适用于发票批量核验、报销材料预审、财务审核辅助、发票字段一致性检查等场景。  
> 本项目是审核辅助工具，不替代官方平台、财务人员或税务人员的最终判断。
---
目录
功能特点
项目截图
项目结构
环境要求
安装依赖
API 配置
运行方式
使用流程
输出目录
一致性比对说明
打包为 EXE
安全与合规说明
常见问题
Roadmap
License
Disclaimer
---
功能特点
模块	功能
文件导入	支持 PDF、PNG、JPG、JPEG、WEBP 格式发票
剪贴板输入	支持粘贴截图、图片路径、PDF 路径
OCR 识别	支持 Qwen-VL OCR 自动提取发票关键字段
人工修正	OCR 失败或字段不准时，可在界面中手动填写
官方查验	通过 Playwright 打开官方发票查验平台并自动填表
验证码处理	验证码由用户人工输入，不破解、不自动识别
结果保存	支持保存官方查验结果截图和 PDF
字段比对	对用户发票字段与官方查验字段进行一致性检查
报告生成	自动生成 Excel 审核报告，并高亮异常字段
复核拼图	生成发票原图与官方查验结果拼图，方便人工复核
EXE 打包	支持使用 PyInstaller 打包为 Windows 单文件程序
---
项目截图
如果需要展示项目效果，可以在仓库中新建 `docs/images/` 目录，并放入截图：
```text
docs/images/main_window.png
docs/images/report_example.png
docs/images/review_image.png
```
然后在 README 中启用如下内容：
```markdown
![Main Window](docs/images/main_window.png)
![Report Example](docs/images/report_example.png)
```
---
项目结构
```text
.
├── app/
│   ├── __init__.py
│   └── main_window.py              # PyQt5 主界面
├── config/
│   ├── __init__.py
│   └── api_config.py               # OCR API 配置
├── core/
│   ├── __init__.py
│   ├── comparator.py               # 字段一致性比对
│   ├── file_loader.py              # PDF / 图片读取与转换
│   ├── invoice_ocr.py              # 发票 OCR 识别
│   ├── models.py                   # 数据结构定义
│   ├── official_parser.py          # 官方查验结果解析
│   ├── report_generator.py         # Excel 报告生成
│   └── tax_verifier.py             # 官方平台自动化查验
├── build_exe.bat                   # Windows 打包脚本
├── main.py                         # 程序入口
├── ocr_demo.py                     # OCR 独立测试脚本
├── requirements.txt                # Python 依赖
└── README.md
```
---
环境要求
推荐环境如下：
环境	版本或说明
Python	3.10+
系统	Windows 10 / Windows 11
GUI	PyQt5
浏览器自动化	Playwright + Chromium
OCR	阿里云百炼 DashScope / Qwen-VL OCR
网络	需要访问官方发票查验平台
依赖示例：
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
安装依赖
1. 克隆项目
```bash
git clone https://github.com/your-name/InvoiceVerifier.git
cd InvoiceVerifier
```
2. 创建虚拟环境
```bash
python -m venv .venv
.venv\Scripts\activate
```
3. 安装 Python 依赖
```bash
pip install -r requirements.txt
```
4. 安装 Playwright Chromium
```bash
python -m playwright install chromium
```
---
API 配置
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
开源安全建议
不要将真实 API Key 上传到公开仓库。推荐做法是：
```text
config/api_config.example.py   # 上传 GitHub
config/api_config.py           # 本地使用，加入 .gitignore
```
`.gitignore` 中加入：
```gitignore
config/api_config.py
.env
*.env
```
如果没有配置 API Key，程序仍然可以运行，但 OCR 自动识别不可用，需要用户手动填写发票字段。
---
运行方式
在项目根目录执行：
```bash
python main.py
```
程序启动后会打开桌面界面：
```text
InvoiceVerifier - 电子发票自动查验与一致性比对工具
```
---
使用流程
总体流程
```text
导入发票
   ↓
OCR 自动识别字段
   ↓
人工确认或修正字段
   ↓
打开官方查验平台并自动填表
   ↓
用户人工输入验证码
   ↓
提交查验并保存官方结果
   ↓
识别官方查验结果
   ↓
字段一致性比对
   ↓
生成 Excel 审核报告
```
---
1. 导入发票
支持以下方式：
点击按钮选择发票文件
在粘贴区域粘贴发票截图
在粘贴区域粘贴 PDF 或图片文件路径
支持格式：
```text
.pdf
.png
.jpg
.jpeg
.webp
```
---
2. OCR 自动识别字段
点击：
```text
自动识别字段
```
程序会尝试提取以下字段：
字段类型	示例字段
基础信息	发票代码、发票号码、开票日期
金额信息	不含税金额、税额、价税合计
购买方	购买方名称、购买方税号
销售方	销售方名称、销售方税号
其他	备注等补充信息
OCR 结果会显示在界面中，用户可以人工确认和修改。
---
3. 打开查验平台并填表
点击：
```text
自动打开查验平台并填表
```
程序会使用 Playwright 打开国家税务总局全国增值税发票查验平台，并根据识别字段自动填写查验表单。
常见查验字段包括：
发票号码
开票日期
价税合计
发票代码，部分票种需要
---
4. 人工输入验证码
官方平台验证码必须由用户人工输入。
本项目不会：
自动识别验证码
破解验证码
绕过官方查验流程
程序只会辅助截图显示验证码区域和提示信息，用户根据官方页面提示输入验证码。
---
5. 保存官方结果
查验完成后，可以保存：
官方查验结果截图
官方查验结果 PDF，若当前浏览器模式不支持 PDF 保存，则只保存截图
---
6. 生成比对报告
程序会对用户上传发票字段和官方查验字段进行比对，并生成 Excel 报告。
报告会标记：
缺失字段
不一致字段
疑似异常字段
需要人工复核的字段
---
输出目录
程序运行后会在项目根目录生成 `output/` 目录：
```text
output/
├── captcha.png                    # 验证码提示区域截图
├── official_result.png             # 官方查验结果截图
├── official_result.pdf             # 官方查验结果 PDF
├── clipboard/                      # 剪贴板截图保存目录
├── debug/                          # 调试截图、HTML、OCR 原始响应
├── reports/                        # Excel 比对报告
├── review/                         # 发票与官方结果拼图
└── temp/                           # PDF 转图片等临时文件
```
> `output/` 目录可能包含发票图片、查验截图、报告和敏感信息。  
> 上传 GitHub 前请务必删除或加入 `.gitignore`。
---
一致性比对说明
比对逻辑位于：
```text
core/comparator.py
```
系统会对用户上传发票字段和官方查验字段进行一致性检查。
常见检查项：
类型	检查内容
票据基础信息	发票代码、发票号码、开票日期
金额信息	不含税金额、税额、价税合计
购买方信息	购买方名称、购买方税号
销售方信息	销售方名称、销售方税号
其他信息	备注、异常提示等
报告中的结论由规则比对生成，不由大模型直接判断。
---
OCR Demo
可以单独运行 OCR 测试脚本：
```bash
python ocr_demo.py
```
使用前请在 `ocr_demo.py` 中设置图片路径：
```python
IMAGE_PATH = r"your image path"
```
并确保 `config/api_config.py` 中已配置有效 API Key。
---
打包为 EXE
项目提供 Windows 打包脚本：
```text
build_exe.bat
```
打包前需要准备本地 Chromium 目录：
```text
chrome-win64/chrome.exe
```
然后执行：
```bat
build_exe.bat
```
打包完成后会生成：
```text
release/InvoiceVerifier.exe
```
说明：
打包脚本会创建 `.venv_pack` 虚拟环境
打包脚本会安装最小依赖
打包脚本会将本地 Chromium 文件打入程序
生成结果为单文件 EXE
首次启动较慢，因为 onefile 模式会先解压运行
如果修改了 API Key 或配置文件，需要重新打包
---
安全与合规说明
本项目遵循以下原则：
不绕过官方网站验证流程
不破解验证码
不自动识别验证码
不保存或输出完整 API Key
OCR 仅用于发票字段和官方结果字段抽取
最终审核结论应以官方平台和人工复核为准
上传公开仓库前，请确认没有包含：
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
推荐 `.gitignore`：
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
config/api_config.py

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
---
当前限制
OCR 识别效果受图片清晰度、旋转角度、遮挡和扫描质量影响
OCR 调用依赖阿里云百炼接口，网络异常或 API Key 无效时需要人工录入
官方查验平台页面结构变化可能导致自动填表逻辑需要调整
验证码必须人工输入，程序不会自动识别验证码
官方结果字段默认基于结果截图 OCR 提取，必要时仍建议人工复核
本项目目前主要面向 Windows 桌面环境
---
常见问题
<details>
<summary><strong>没有 API Key 能不能用？</strong></summary>
可以。没有 API Key 时，OCR 自动识别不可用，但仍可以手动填写发票字段，并继续进行官方查验和报告生成。
</details>
<details>
<summary><strong>程序会不会破解验证码？</strong></summary>
不会。程序只显示验证码和提示区域截图，验证码必须由用户人工输入。
</details>
<details>
<summary><strong>为什么查验平台打不开？</strong></summary>
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
</details>
<details>
<summary><strong>为什么 OCR 结果不准确？</strong></summary>
可能原因包括：
发票截图不清晰
图片分辨率过低
PDF 转图片质量较差
发票版式特殊
发票字段被遮挡
模型返回内容不稳定
建议在界面中人工修正 OCR 结果后再查验。
</details>
<details>
<summary><strong>生成的报告在哪里？</strong></summary>
默认位于：
```text
output/reports/
```
</details>
<details>
<summary><strong>打包后为什么首次启动慢？</strong></summary>
因为 PyInstaller onefile 模式会先把程序和浏览器文件解压到临时目录，首次启动需要等待一段时间。
</details>
---
Roadmap
[ ] 支持更多发票类型和版式
[ ] 支持批量发票队列处理
[ ] 支持更稳定的官方结果字段解析
[ ] 支持报告模板自定义
[ ] 支持自动归档发票、截图和报告
[ ] 支持配置文件模板化
[ ] 支持更完善的异常日志和运行状态提示
[ ] 支持更多 OCR 模型后端
---
License
This project is released for learning, research, and internal auditing assistance purposes.
Recommended license: MIT License.
---
Disclaimer
InvoiceVerifier is an independent open-source tool and is not affiliated with, endorsed by, or sponsored by the State Taxation Administration or any invoice verification platform.
This project is designed only as an auxiliary tool for invoice verification and consistency checking. The final verification result should be based on the official platform and manual review. Users are responsible for complying with all applicable laws, regulations, platform terms of use, and data privacy requirements.
