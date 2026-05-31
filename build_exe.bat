@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo InvoiceVerifier Onefile Build
echo Use local chrome-win64, no browser download
echo ============================================

cd /d "%~dp0"

REM 是否打包后删除临时目录
REM 0 = 保留 .venv_pack，下次打包更快
REM 1 = 打包后删除 .venv_pack，目录更干净但下次更慢
set "CLEAN_VENV=0"

echo.
echo [1/9] 检查 Python...
python --version
if errorlevel 1 (
    echo 未检测到 Python。
    pause
    exit /b 1
)

echo.
echo [2/9] 检查根目录 chrome-win64...
if not exist "chrome-win64\chrome.exe" (
    echo 未找到：
    echo %CD%\chrome-win64\chrome.exe
    echo.
    echo 请确认你已经把 chrome-win64.zip 解压到项目根目录。
    pause
    exit /b 1
)

echo.
echo [3/9] 创建干净虚拟环境 .venv_pack...
if not exist ".venv_pack\Scripts\python.exe" (
    python -m venv .venv_pack
)

echo.
echo [4/9] 激活虚拟环境并安装最小依赖...
call ".venv_pack\Scripts\activate.bat"

python -m pip install --upgrade pip
python -m pip install PyQt5 playwright openpyxl PyMuPDF Pillow openai requests pyinstaller

echo.
echo [5/9] 准备 Playwright 本地浏览器目录...
if exist ms-playwright rmdir /s /q ms-playwright

mkdir "ms-playwright"
mkdir "ms-playwright\chromium-1223"
mkdir "ms-playwright\chromium_headless_shell-1223"

echo 正在复制 chrome-win64 到 chromium-1223...
xcopy /E /I /Y "chrome-win64" "ms-playwright\chromium-1223\chrome-win64" >nul

echo 正在创建 headless shell 兼容目录...
xcopy /E /I /Y "chrome-win64" "ms-playwright\chromium_headless_shell-1223\chrome-headless-shell-win64" >nul

if not exist "ms-playwright\chromium_headless_shell-1223\chrome-headless-shell-win64\chrome-headless-shell.exe" (
    copy /Y "ms-playwright\chromium_headless_shell-1223\chrome-headless-shell-win64\chrome.exe" "ms-playwright\chromium_headless_shell-1223\chrome-headless-shell-win64\chrome-headless-shell.exe" >nul
)

if not exist "ms-playwright\chromium_headless_shell-1223\chrome-headless-shell-win64\chrome-headless-shell.exe" (
    echo 创建 Playwright headless shell 目录失败。
    pause
    exit /b 1
)

echo.
echo [6/9] 生成 PyInstaller runtime hook...
if exist pyi_rth_playwright.py del /q pyi_rth_playwright.py

> pyi_rth_playwright.py echo import os
>> pyi_rth_playwright.py echo import sys
>> pyi_rth_playwright.py echo.
>> pyi_rth_playwright.py echo base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
>> pyi_rth_playwright.py echo browser_dir = os.path.join(base_dir, "ms-playwright")
>> pyi_rth_playwright.py echo if os.path.isdir(browser_dir):
>> pyi_rth_playwright.py echo     os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_dir

echo.
echo [7/9] 清理旧构建文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist release rmdir /s /q release
if exist InvoiceVerifier.spec del /q InvoiceVerifier.spec

mkdir release

echo.
echo [8/9] 开始打包单文件 exe...
pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name InvoiceVerifier ^
  --runtime-hook pyi_rth_playwright.py ^
  --add-data "ms-playwright;ms-playwright" ^
  --add-data "config;config" ^
  --collect-all playwright ^
  --hidden-import=fitz ^
  --hidden-import=PIL ^
  --hidden-import=openai ^
  --hidden-import=httpx ^
  --hidden-import=pydantic ^
  --hidden-import=playwright ^
  --hidden-import=playwright.sync_api ^
  --exclude-module pandas ^
  --exclude-module numpy ^
  --exclude-module scipy ^
  --exclude-module matplotlib ^
  --exclude-module plotly ^
  --exclude-module bokeh ^
  --exclude-module panel ^
  --exclude-module dask ^
  --exclude-module xarray ^
  --exclude-module cftime ^
  --exclude-module sklearn ^
  --exclude-module torch ^
  --exclude-module tensorflow ^
  --exclude-module cv2 ^
  --exclude-module IPython ^
  --exclude-module jupyter ^
  --exclude-module notebook ^
  main.py

if errorlevel 1 (
    echo.
    echo 打包失败，请查看上方错误。
    pause
    exit /b 1
)

echo.
echo [9/9] 移动 exe 并清理临时文件...
copy /Y "dist\InvoiceVerifier.exe" "release\InvoiceVerifier.exe" >nul

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist InvoiceVerifier.spec del /q InvoiceVerifier.spec
if exist pyi_rth_playwright.py del /q pyi_rth_playwright.py

REM ms-playwright 是由 chrome-win64 临时生成的，已经被打进 exe，可以删除
if exist ms-playwright rmdir /s /q ms-playwright

if "%CLEAN_VENV%"=="1" (
    if exist .venv_pack rmdir /s /q .venv_pack
)

echo.
echo ============================================
echo 打包完成！
echo 最终单文件 exe：
echo %CD%\release\InvoiceVerifier.exe
echo ============================================
echo.
echo 说明：
echo 1. 交付时只需要 release\InvoiceVerifier.exe。
echo 2. exe 内已包含 chrome-win64 浏览器文件。
echo 3. 首次启动会比较慢，因为 onefile 会解压运行。
echo 4. .venv_pack 默认保留，下次打包更快。
echo 5. 如果想打包后也删除 .venv_pack，把脚本顶部 CLEAN_VENV 改成 1。
echo 6. 如果 config\api_config.py 写死了 API Key，修改 Key 后需要重新打包。
echo.

pause