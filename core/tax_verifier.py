from datetime import datetime
from pathlib import Path
import re
from typing import Any, Iterable, Optional, Tuple

from PIL import Image

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Locator, Page, sync_playwright
except ImportError:
    PlaywrightError = Exception
    Locator = Any
    Page = Any
    sync_playwright = None

from core.models import InvoiceData


VERIFY_URL = "https://inv-veri.chinatax.gov.cn/"
CAPTCHA_ERROR_PATTERNS = [
    re.compile(r"验证码.*(错误|不正确|有误|失败|失效|超时|过期|不能为空|重新输入)"),
    re.compile(r"(错误|不正确|有误|失败|失效|超时|过期).*验证码"),
    re.compile(r"校验码.*(错误|不正确|有误|失败|失效|超时|过期|不能为空|重新输入)"),
    re.compile(r"(错误|不正确|有误|失败|失效|超时|过期).*校验码"),
]


class CaptchaValidationError(RuntimeError):
    """官网明确提示验证码错误时抛出，UI 可停在验证码步骤让用户重新输入。"""


class TaxVerifier:
    """使用 Playwright 驱动官方查验平台。

    合规边界：
    - 只自动打开网页和填写用户提供的发票字段。
    - 验证码只截图显示给用户，由用户人工输入。
    - 刷新验证码只在用户点击界面按钮，或官网明确提示验证码错误/失效后触发一次。
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir = self.output_dir / "debug"
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        self.playwright = None
        self.browser = None
        self.context = None
        self.page: Optional[Page] = None
        self.last_pdf_warning = ""

    def start(self) -> None:
        """启动浏览器。第一阶段使用 headless=False，方便人工输入和调试。"""
        if self.page:
            return
        if sync_playwright is None:
            raise RuntimeError("未安装 Playwright，请先执行 pip install -r requirements.txt。")
        self.playwright = sync_playwright().start()
        # self.browser = self.playwright.chromium.launch(headless=False)
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            viewport={"width": 1366, "height": 900},
            accept_downloads=True,
            ignore_https_errors=True,
        )
        self.page = self.context.new_page()

    def open_site(self) -> None:
        """打开全国增值税发票查验平台。"""
        page = self._require_page()
        try:
            page.goto(VERIFY_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
        except Exception as exc:
            self._save_debug_artifacts("open_site_failed")
            raise RuntimeError(f"打开查验平台失败：{exc}") from exc

    def fill_form(self, invoice: InvoiceData) -> None:
        """根据发票字段自动填写查验表单。"""
        missing_required = []
        if not invoice.invoice_number.strip():
            missing_required.append("发票号码")
        if not invoice.invoice_date.strip():
            missing_required.append("开票日期")
        if not invoice.total_amount.strip():
            missing_required.append("价税合计")
        if missing_required:
            raise ValueError("请先填写必填查验字段：" + "、".join(missing_required))

        failed_fields = []

        if invoice.invoice_code.strip():
            if not self._fill_by_candidates(
                invoice.invoice_code.strip(),
                selectors=[
                    "#fpdm",
                    "input[name='fpdm']",
                    "input[id*='fpdm']",
                    "input[name*='fpdm']",
                ],
                labels=["发票代码"],
            ):
                failed_fields.append("发票代码")

        required_fill_items = [
            (
                "发票号码",
                invoice.invoice_number.strip(),
                ["#fphm", "input[name='fphm']", "input[id*='fphm']", "input[name*='fphm']"],
                ["发票号码", "发票号"],
            ),
            (
                "开票日期",
                self._date_for_site(invoice.invoice_date),
                ["#kprq", "input[name='kprq']", "input[id*='kprq']", "input[name*='kprq']"],
                ["开票日期"],
            ),
            (
                "价税合计",
                self._amount_for_site(invoice.total_amount),
                ["#kjje", "input[name='kjje']", "input[id*='kjje']", "input[name*='kjje']"],
                ["价税合计", "合计金额", "小写", "金额"],
            ),
        ]

        for field_name, value, selectors, labels in required_fill_items:
            if not self._fill_by_candidates(value, selectors=selectors, labels=labels):
                failed_fields.append(field_name)

        if failed_fields:
            self._save_debug_artifacts("fill_form_failed")
            raise RuntimeError(
                "以下字段未能自动定位或填写："
                + "、".join(failed_fields)
                + "。已保存 debug 截图和 HTML，可人工检查页面 DOM。"
            )

        self._require_page().wait_for_timeout(500)

    def capture_captcha(self) -> str:
        """兼容旧调用：返回验证码合成截图路径。"""
        _code_path, _hint_path, combined_path = self.capture_captcha_images()
        return combined_path

    def capture_captcha_images(self) -> Tuple[str, str, str]:
        """分别截图验证码图片和颜色提示文字，快速模式。

        优先使用元素截图，避免 full_page 整页截图带来的明显延迟；元素截图失败时，
        再使用当前 viewport 截图并按元素坐标裁剪。只在极端情况下保存 debug。
        """
        page = self._require_page()
        captcha_code_path = self.output_dir / "captcha_code.png"
        captcha_hint_path = self.output_dir / "captcha_hint.png"
        combined_path = self.output_dir / "captcha.png"
        viewport_path = self.output_dir / "captcha_viewport.png"

        try:
            captcha = self._find_captcha_element()
            if captcha is None:
                page.wait_for_timeout(150)
                captcha = self._find_captcha_element()
            if captcha is None:
                self._save_debug_artifacts("captcha_not_found")
                raise RuntimeError("未能定位验证码图片。")

            # 先滚动到验证码附近，避免 viewport 裁剪不到。
            try:
                captcha.scroll_into_view_if_needed(timeout=800)
            except Exception:
                pass

            # 1) 最快路径：直接截验证码元素。
            try:
                captcha.screenshot(path=str(captcha_code_path), timeout=2000)
            except Exception:
                # 2) 兜底：截当前 viewport，而不是 full_page，再用 getBoundingClientRect 裁剪。
                box = self._element_viewport_box(captcha, padding=4)
                if box is None:
                    raise RuntimeError("验证码元素尺寸异常。")
                page.screenshot(path=str(viewport_path), full_page=False, timeout=5000)
                self._crop_image(viewport_path, captcha_code_path, box)

            hint = self._find_captcha_hint_element()
            if hint is not None:
                try:
                    hint.scroll_into_view_if_needed(timeout=500)
                except Exception:
                    pass
                try:
                    hint.screenshot(path=str(captcha_hint_path), timeout=1500)
                except Exception:
                    hint_box = self._element_viewport_box(hint, padding=4)
                    if hint_box is not None:
                        page.screenshot(path=str(viewport_path), full_page=False, timeout=5000)
                        self._crop_image(viewport_path, captcha_hint_path, hint_box)
                    else:
                        self._make_text_placeholder(captcha_hint_path, "请查看网页中的验证码颜色提示")
            else:
                self._make_text_placeholder(captcha_hint_path, "请查看网页中的验证码颜色提示")

            self._combine_captcha_images(captcha_code_path, captcha_hint_path, combined_path)
            return (
                str(captcha_code_path.resolve()),
                str(captcha_hint_path.resolve()),
                str(combined_path.resolve()),
            )
        except Exception as exc:
            try:
                page.screenshot(path=str(self.output_dir / "captcha_debug.png"), full_page=False, timeout=5000)
            except Exception:
                pass
            raise RuntimeError(f"验证码截图失败：{exc}") from exc

    def refresh_captcha(self) -> str:
        """兼容旧调用：刷新后返回验证码合成截图路径。"""
        _code_path, _hint_path, combined_path = self.refresh_captcha_images()
        return combined_path

    def refresh_captcha_images(self) -> Tuple[str, str, str]:
        """刷新验证码并重新截图，快速模式。

        只在检测到官网错误弹窗仍存在时才关闭弹窗；点击验证码优先使用 JS click，
        减少 Playwright 真实点击等待元素稳定带来的延迟。
        """
        page = self._require_page()

        if self._is_popup_overlay_visible() or self._get_popup_text_fast():
            self._close_web_error_popup()
            page.wait_for_timeout(80)

        if self._is_popup_overlay_visible():
            self._save_debug_artifacts("refresh_blocked_by_popup")
            raise RuntimeError("官网错误弹窗仍未关闭，无法刷新验证码。")

        captcha = self._find_captcha_element()
        if captcha is None:
            self._save_debug_artifacts("refresh_captcha_not_found")
            raise RuntimeError("未能定位验证码图片，无法刷新。")

        try:
            old_src = ""
            try:
                old_src = captcha.evaluate("(el) => el.getAttribute('src') || ''")
            except Exception:
                pass

            # JS click 不依赖浏览器是否在前台，也不会因为元素稳定性等待太久。
            try:
                captcha.evaluate("(el) => el.click()")
            except Exception:
                captcha.click(timeout=500, force=True)

            # 短等待验证码刷新。官网一般会立刻替换 base64 src，不需要等太久。
            if old_src:
                try:
                    page.wait_for_function(
                        """(oldSrc) => {
                            const el = document.querySelector('#yzm_img, #yzmImg, #captchaImg, img[id*=\"yzm\"], img[src*=\"yzm\"], img[src*=\"captcha\"]');
                            return el && (el.getAttribute('src') || '') && (el.getAttribute('src') || '') !== oldSrc;
                        }""",
                        arg=old_src,
                        timeout=600,
                    )
                except Exception:
                    page.wait_for_timeout(250)
            else:
                page.wait_for_timeout(350)

            return self.capture_captcha_images()
        except Exception as exc:
            self._save_debug_artifacts("refresh_captcha_failed")
            raise RuntimeError(f"刷新验证码失败：{exc}") from exc

    def recover_after_captcha_error(self) -> Tuple[str, str, str]:
        """验证码错误/失效后的自动恢复：快速关弹窗 -> 刷新验证码 -> 重新截图。"""
        page = self._require_page()
        self._close_web_error_popup()
        page.wait_for_timeout(80)

        if self._is_popup_overlay_visible():
            self._save_debug_artifacts("captcha_error_popup_still_visible")
            raise RuntimeError("官网验证码错误提示框仍未关闭，已保存 debug 文件。")

        return self.refresh_captcha_images()

    def submit(self, captcha_text: str) -> None:
        """填入人工输入的验证码并点击查验按钮，快速模式。"""
        if not captcha_text.strip():
            raise ValueError("请输入验证码。")

        page = self._require_page()
        dialog_messages = []

        def handle_dialog(dialog) -> None:
            try:
                dialog_messages.append(dialog.message)
                dialog.accept()
            except Exception:
                pass

        page.on("dialog", handle_dialog)
        try:
            filled = self._fill_captcha_input(captcha_text.strip())
            if not filled:
                self._save_debug_artifacts("captcha_input_not_found")
                raise RuntimeError("未能定位验证码输入框，已保存 debug 截图和 HTML。")

            # 很短等待，让网页脚本有机会把按钮切换为可提交状态。
            page.wait_for_timeout(80)
            if not self._click_check_button():
                self._save_debug_artifacts("submit_button_not_ready")
                raise RuntimeError(
                    "未能点击可用的查验按钮。请确认验证码和必填字段已通过网页校验。"
                )

            captcha_error = self._captcha_error_message(dialog_messages) or self._wait_for_submit_feedback(1600)
            if captcha_error:
                self._close_web_error_popup()
                raise CaptchaValidationError(captcha_error)

            # 极短兜底，防止错误弹窗稍晚出现。
            page.wait_for_timeout(180)
            captcha_error = self._detect_page_captcha_error_fast()
            if captcha_error:
                self._close_web_error_popup()
                raise CaptchaValidationError(captcha_error)
        finally:
            try:
                page.remove_listener("dialog", handle_dialog)
            except Exception:
                pass

    def save_result(self) -> tuple[str, str]:
        """保存官方查验结果截图和 PDF。PDF 失败时返回空 PDF 路径。"""
        page = self._require_page()
        screenshot_path = self.output_dir / "official_result.png"
        pdf_path = self.output_dir / "official_result.pdf"

        self._prepare_official_result_view_for_screenshot()
        page.screenshot(path=str(screenshot_path), full_page=False, timeout=8000)

        self.last_pdf_warning = ""
        try:
            page.pdf(path=str(pdf_path), print_background=True, format="A4", timeout=6000)
            pdf_result = str(pdf_path.resolve())
        except Exception as exc:
            self.last_pdf_warning = f"当前浏览器模式未能保存 PDF，仅保存截图：{exc}"
            pdf_result = ""

        return str(screenshot_path.resolve()), pdf_result

    def _prepare_official_result_view_for_screenshot(self) -> None:
        """把官方结果区域移动到当前窗口可见位置，再截 viewport。

        官网结果层经常是弹层/固定层，使用 full_page 截图容易截到空白遮罩或错位区域。
        这里不再用 DOM 文本做硬拦截，只做轻量定位和短暂等待，避免明明页面正常却被误判失败。
        """
        page = self._require_page()

        try:
            page.evaluate(
                """() => {
                    const keywords = [
                        '\\u53d1\\u7968\\u67e5\\u9a8c\\u660e\\u7ec6',
                        '\\u53d1\\u7968\\u53f7\\u7801',
                        '\\u5f00\\u7968\\u65e5\\u671f',
                        '\\u8d2d\\u4e70\\u65b9',
                        '\\u9500\\u552e\\u65b9',
                        '\\u4ef7\\u7a0e\\u5408\\u8ba1'
                    ];

                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && Number(style.opacity || '1') !== 0
                            && rect.width > 20
                            && rect.height > 20;
                    };

                    const preferredSelectors = [
                        '#popup_container',
                        '#popup_panel',
                        '.layui-layer',
                        '.layui-layer-content',
                        '.ui-dialog',
                        '.ui-dialog-content',
                        '.modal',
                        '.dialog'
                    ];

                    const candidates = [];
                    for (const selector of preferredSelectors) {
                        candidates.push(...Array.from(document.querySelectorAll(selector)));
                    }
                    candidates.push(...Array.from(document.querySelectorAll('body *')));

                    let best = null;
                    let bestScore = -1;
                    for (const el of candidates) {
                        if (!visible(el)) continue;
                        const text = (el.innerText || el.textContent || '').trim();
                        if (!text) continue;
                        const rect = el.getBoundingClientRect();
                        let score = keywords.reduce((sum, keyword) => sum + (text.includes(keyword) ? 10 : 0), 0);
                        score += Math.min(text.length / 300, 6);
                        if (rect.width > 600 && rect.height > 300) score += 3;
                        if (score > bestScore) {
                            bestScore = score;
                            best = el;
                        }
                    }

                    if (best) {
                        best.scrollIntoView({ block: 'start', inline: 'nearest' });
                        window.scrollBy(0, -20);
                    }
                }"""
            )
        except Exception:
            pass

        try:
            # headless/后台模式下官方结果层偶尔还没完成绘制，稍等 1 秒可避免截到空白。
            page.wait_for_timeout(1000)
        except Exception:
            pass

    def _wait_for_official_result_ready(self, timeout_ms: int = 20000) -> None:
        """等待官网查验结果内容真正渲染出来，避免保存到空白弹窗。"""
        page = self._require_page()
        end_time = datetime.now().timestamp() + timeout_ms / 1000.0

        while datetime.now().timestamp() < end_time:
            try:
                state = page.evaluate(
                    """() => {
                        const keywordGroups = [
                            ['发票号码', '开票日期'],
                            ['购买方', '销售方'],
                            ['价税合计', '税额', '金额'],
                            ['查验次数', '查验时间']
                        ];

                        const visibleText = (el) => {
                            if (!el) return '';
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            if (style.display === 'none'
                                || style.visibility === 'hidden'
                                || Number(style.opacity || '1') === 0
                                || rect.width <= 0
                                || rect.height <= 0) {
                                return '';
                            }
                            return (el.innerText || el.textContent || '').trim();
                        };

                        const containers = [
                            '#popup_container',
                            '#popup_panel',
                            '.layui-layer-content',
                            '.ui-dialog-content',
                            '.modal',
                            '.dialog',
                            'body'
                        ];

                        let bestText = '';
                        for (const selector of containers) {
                            for (const el of Array.from(document.querySelectorAll(selector))) {
                                const text = visibleText(el);
                                if (text.length > bestText.length) bestText = text;
                            }
                        }

                        const groupHits = keywordGroups.filter(group => group.some(k => bestText.includes(k))).length;
                        const hasInvoiceNumber = /发\\s*票\\s*号\\s*码\\s*[:：]?\\s*[A-Z0-9０-９]{8,}/.test(bestText);
                        const hasDate = /开\\s*票\\s*日\\s*期\\s*[:：]?\\s*\\d{4}/.test(bestText);
                        const hasAmount = /(价\\s*税\\s*合\\s*计|税\\s*额|金\\s*额)/.test(bestText) && /[¥￥]?\\s*\\d+\\.\\d{2}/.test(bestText);

                        return {
                            ready: groupHits >= 3 || (hasInvoiceNumber && hasDate) || (hasInvoiceNumber && hasAmount),
                            textLength: bestText.length,
                            groupHits,
                            hasInvoiceNumber,
                            hasDate,
                            hasAmount
                        };
                    }"""
                )
                if state and state.get("ready"):
                    page.wait_for_timeout(800)
                    return
            except Exception:
                pass

            page.wait_for_timeout(300)

        self._save_debug_artifacts("official_result_not_ready")
        raise RuntimeError("官方查验结果内容尚未加载完成，未保存空白截图；请稍后再点“保存官方结果”。")

    def get_page_text(self) -> str:
        """返回当前页面 body 文本，供官方字段解析器使用。"""
        page = self._require_page()
        try:
            return page.locator("body").inner_text(timeout=10000)
        except Exception as exc:
            self._save_debug_artifacts("get_page_text_failed")
            raise RuntimeError(f"读取官方页面文本失败：{exc}") from exc

    def close(self) -> None:
        """关闭浏览器资源。"""
        for resource in (self.context, self.browser):
            try:
                if resource:
                    resource.close()
            except Exception:
                pass

        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None

    def _require_page(self) -> Page:
        if not self.page:
            raise RuntimeError("浏览器尚未启动，请先点击“自动打开查验平台并填表”。")
        return self.page

    def _fill_by_candidates(self, value: str, selectors: Iterable[str], labels: Iterable[str]) -> bool:
        page = self._require_page()
        for selector in selectors:
            if self._try_fill_locator(page.locator(selector), value):
                return True

        for label in labels:
            try:
                if self._try_fill_locator(page.get_by_label(label), value):
                    return True
            except PlaywrightError:
                pass
            try:
                if self._try_fill_locator(page.get_by_placeholder(label), value):
                    return True
            except PlaywrightError:
                pass

            css_label = self._css_string(label)
            xpath_label = self._xpath_literal(label)
            candidate_selectors = [
                f"input[placeholder*={css_label}]",
                f"textarea[placeholder*={css_label}]",
                (
                    "xpath=//*[self::label or self::span or self::td or self::div or self::p]"
                    f"[contains(normalize-space(.), {xpath_label})]/following::input[1]"
                ),
            ]
            for selector in candidate_selectors:
                if self._try_fill_locator(page.locator(selector), value):
                    return True

        return False

    def _try_fill_locator(self, locator: Locator, value: str) -> bool:
        try:
            count = min(locator.count(), 5)
        except Exception:
            return False

        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=1000):
                    continue
                item.scroll_into_view_if_needed(timeout=3000)
                # 日期控件或网站脚本可能设置 readonly，移除后再填写更稳。
                item.evaluate(
                    "(el) => { el.removeAttribute('readonly'); el.removeAttribute('disabled'); }"
                )
                item.click(timeout=3000)
                try:
                    item.fill(value, timeout=3000)
                except Exception:
                    page = self._require_page()
                    page.keyboard.press("Control+A")
                    page.keyboard.type(value)

                # 官方页面会根据事件切换 #uncheckfp/#checkfp，fill 后主动派发常见事件。
                item.evaluate(
                    "(el) => {"
                    "el.dispatchEvent(new Event('input', { bubbles: true }));"
                    "el.dispatchEvent(new Event('change', { bubbles: true }));"
                    "el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));"
                    "el.dispatchEvent(new Event('blur', { bubbles: true }));"
                    "}"
                )
                return True
            except Exception:
                continue
        return False

    def _fill_captcha_input(self, value: str) -> bool:
        """快速填写验证码。

        优先用 JS 直接设置 value 并派发事件，避免键盘逐字输入和前台焦点造成延迟。
        失败时再退回真实键盘输入。
        """
        page = self._require_page()
        candidate_selectors = [
            "#yzm",
            "#captcha",
            "input[name='yzm']",
            "input[id*='yzm']",
            "input[name*='yzm']",
            "input[placeholder*='验证码']",
            "input[placeholder*='校验码']",
        ]

        for selector in candidate_selectors:
            try:
                ok = bool(page.evaluate(
                    """([selector, value]) => {
                        const elements = Array.from(document.querySelectorAll(selector));
                        for (const el of elements) {
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            if (style.display === 'none' || style.visibility === 'hidden' || rect.width <= 0 || rect.height <= 0) continue;
                            el.removeAttribute('readonly');
                            el.removeAttribute('disabled');
                            el.focus();
                            el.value = '';
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.value = value;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                            el.dispatchEvent(new Event('blur', { bubbles: true }));
                            return true;
                        }
                        return false;
                    }""",
                    [selector, value],
                ))
                if ok:
                    return True
            except Exception:
                pass

        # 兜底：真实键盘输入。
        for selector in candidate_selectors:
            locator = page.locator(selector)
            try:
                if locator.count() == 0:
                    continue
                item = locator.first
                if not item.is_visible(timeout=300):
                    continue
                item.scroll_into_view_if_needed(timeout=500)
                item.click(timeout=500, force=True)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(value, delay=5)
                item.evaluate(
                    "(el) => {"
                    "el.dispatchEvent(new Event('input', { bubbles: true }));"
                    "el.dispatchEvent(new Event('change', { bubbles: true }));"
                    "el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));"
                    "el.dispatchEvent(new Event('blur', { bubbles: true }));"
                    "}"
                )
                return True
            except Exception:
                continue
        return False

    def _click_check_button(self) -> bool:
        """快速点击真正提交按钮。

        优先用 JS click #checkfp，避免 Playwright 真实 click 等待元素稳定造成数秒延迟。
        """
        page = self._require_page()
        self._nudge_form_validation_events()

        try:
            clicked = bool(page.evaluate(
                """() => {
                    const candidates = [
                        '#checkfp',
                        'button.blue_button',
                        'input[type="button"][value*="查验"]',
                        'input[type="submit"][value*="查验"]',
                        'button'
                    ];
                    for (const selector of candidates) {
                        const elements = Array.from(document.querySelectorAll(selector));
                        for (const el of elements) {
                            const text = (el.innerText || el.value || el.textContent || '').replace(/\s+/g, '');
                            const id = el.id || '';
                            if (id === 'uncheckfp') continue;
                            if (id !== 'checkfp' && !/查验|查询|提交/.test(text)) continue;
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            const disabled = el.disabled || el.getAttribute('disabled') === 'true' || el.getAttribute('aria-disabled') === 'true';
                            if (disabled || style.display === 'none' || style.visibility === 'hidden' || rect.width <= 0 || rect.height <= 0) continue;
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }"""
            ))
            if clicked:
                return True
        except Exception:
            pass

        check_button = page.locator("#checkfp")
        try:
            if check_button.count() > 0 and self._try_click_locator(check_button):
                return True
        except Exception:
            pass

        return self._click_by_candidates(
            role_names=[re.compile(r"查\s*验|查询|提交")],
            selectors=[
                "button.blue_button:has-text('查 验')",
                "button.blue_button:has-text('查验')",
                "button:has-text('查询')",
                "input[type='button'][value*='查验']",
                "input[type='submit'][value*='查验']",
            ],
        )

    def _captcha_error_message(self, messages: Iterable[str]) -> str:
        for message in messages:
            if self._looks_like_captcha_error(message):
                return message.strip() or "验证码错误，请重新输入。"
        return ""

    def _get_popup_text_fast(self) -> str:
        """快速读取官网弹窗文字；不扫描 body，避免验证码错误检测很慢。"""
        page = self._require_page()
        try:
            text = page.evaluate(
                """() => {
                    const selectors = [
                        '#popup_panel',
                        '#popup_container',
                        '.layui-layer-content',
                        '.ui-dialog-content',
                        '.alert',
                        '.modal'
                    ];
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (!el) continue;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0 || rect.width <= 0 || rect.height <= 0) {
                            continue;
                        }
                        const value = (el.innerText || el.textContent || '').trim();
                        if (value) return value;
                    }
                    return '';
                }"""
            )
            return text or ""
        except Exception:
            return ""

    def _detect_page_captcha_error_fast(self) -> str:
        """快速判断当前可见弹窗是否为验证码错误提示。"""
        text = self._get_popup_text_fast()
        if self._looks_like_captcha_error(text):
            return text.strip() or "验证码错误，请重新输入。"
        return ""

    def _is_popup_overlay_visible(self) -> bool:
        """判断官网错误弹窗/遮罩是否仍在页面上且可见。"""
        page = self._require_page()
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const selectors = [
                            '#popup_overlay',
                            '#popup_container',
                            '#popup_panel',
                            '.layui-layer-shade',
                            '.layui-layer'
                        ];
                        return selectors.some(selector => {
                            const el = document.querySelector(selector);
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && Number(style.opacity || '1') !== 0
                                && rect.width > 0
                                && rect.height > 0;
                        });
                    }"""
                )
            )
        except Exception:
            return False

    def _wait_for_submit_feedback(self, timeout_ms: int = 1600) -> str:
        """提交后快速等待验证码错误弹窗或查验结果页出现。

        返回非空字符串表示验证码错误；返回空字符串表示未检测到验证码错误。
        """
        page = self._require_page()
        end_time = datetime.now().timestamp() + timeout_ms / 1000.0

        while datetime.now().timestamp() < end_time:
            captcha_error = self._detect_page_captcha_error_fast()
            if captcha_error:
                return captcha_error

            try:
                result_ready = bool(
                    page.evaluate(
                        """() => {
                            const body = document.body ? document.body.innerText : '';
                            return body.includes('查验结果')
                                || body.includes('发票查验明细')
                                || body.includes('销方名称')
                                || body.includes('购买方名称');
                        }"""
                    )
                )
                if result_ready:
                    return ""
            except Exception:
                pass

            page.wait_for_timeout(60)

        return self._detect_page_captcha_error_fast()

    def _detect_page_captcha_error(self) -> str:
        page = self._require_page()
        candidates = [
            ".layui-layer-content",
            ".ui-dialog-content",
            ".dialog",
            ".modal",
            ".alert",
            "body",
        ]
        for selector in candidates:
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 5)
            except Exception:
                continue
            for index in range(count):
                try:
                    item = locator.nth(index)
                    if selector != "body" and not item.is_visible(timeout=500):
                        continue
                    text = item.inner_text(timeout=800).strip()
                except Exception:
                    continue
                if self._looks_like_captcha_error(text):
                    return text or "验证码错误，请重新输入。"
        return ""

    def _close_web_error_popup(self) -> bool:
        """自动关闭官网验证码错误提示框。

        后期浏览器可能不在前台，所以优先使用 JS 直接点击 DOM 中的“确定/关闭”按钮。
        如果按钮点击后遮罩仍未消失，则兜底移除错误提示弹窗 DOM。
        该逻辑只关闭错误提示框，不识别、不绕过验证码。
        """
        page = self._require_page()

        # 1. 优先 JS 点击官网 popup 的确定按钮，避免普通 click 被遮罩拦截或前台状态影响。
        try:
            clicked = bool(
                page.evaluate(
                    """() => {
                        const candidates = [
                            '#popup_ok',
                            '#popup_cancel',
                            '#popup_panel input[type="button"]',
                            '#popup_panel button',
                            '#popup_panel a',
                            '#popup_container input[type="button"]',
                            '#popup_container button',
                            '#popup_container a',
                            'input[value="确定"]',
                            'input[value="关闭"]',
                            'input[value="OK"]'
                        ];

                        for (const selector of candidates) {
                            const elements = Array.from(document.querySelectorAll(selector));
                            for (const el of elements) {
                                const style = window.getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0 || rect.width <= 0 || rect.height <= 0) {
                                    continue;
                                }
                                el.click();
                                return true;
                            }
                        }

                        const all = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
                        for (const el of all) {
                            const text = (el.innerText || el.value || el.textContent || '').trim();
                            if (!['确定', '关闭', 'OK', 'ok'].includes(text)) continue;
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0 || rect.width <= 0 || rect.height <= 0) {
                                continue;
                            }
                            el.click();
                            return true;
                        }

                        return false;
                    }"""
                )
            )
            if clicked:
                page.wait_for_timeout(200)
                if not self._is_popup_overlay_visible():
                    return True
        except Exception:
            pass

        # 2. Playwright force click 兜底。
        close_selectors = [
            "#popup_ok",
            "#popup_cancel",
            "#popup_panel input[type='button']",
            "#popup_panel button",
            "#popup_panel a",
            "#popup_container input[type='button']",
            "#popup_container button",
            "#popup_container a",
            "input[value='确定']",
            "input[value='关闭']",
            "input[value='OK']",
            "button:has-text('确定')",
            "button:has-text('关闭')",
            "button:has-text('OK')",
            "a:has-text('确定')",
            "a:has-text('关闭')",
            ".layui-layer-btn0",
            ".layui-layer-close",
            ".ui-dialog-buttonpane button",
        ]

        for selector in close_selectors:
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 5)
                for index in range(count):
                    item = locator.nth(index)
                    if not item.is_visible(timeout=200):
                        continue
                    try:
                        item.click(timeout=500, force=True)
                        page.wait_for_timeout(200)
                        if not self._is_popup_overlay_visible():
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        # 3. Escape 兜底。
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            if not self._is_popup_overlay_visible():
                return True
        except Exception:
            pass

        # 4. 最后兜底：移除错误提示弹窗和遮罩。
        try:
            page.evaluate(
                """() => {
                    [
                        '#popup_overlay',
                        '#popup_container',
                        '#popup_panel',
                        '.layui-layer-shade',
                        '.layui-layer'
                    ].forEach(selector => {
                        document.querySelectorAll(selector).forEach(el => el.remove());
                    });
                    document.body.style.overflow = '';
                }"""
            )
            page.wait_for_timeout(200)
            return not self._is_popup_overlay_visible()
        except Exception:
            return False

    @staticmethod
    def _looks_like_captcha_error(text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "")
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in CAPTCHA_ERROR_PATTERNS)

    def _nudge_form_validation_events(self) -> None:
        """触发表单事件，帮助官网脚本更新按钮状态。快速版只等待很短时间。"""
        page = self._require_page()
        try:
            page.evaluate(
                """() => {
                    ['#fpdm', '#fphm', '#kprq', '#kjje', '#yzm'].forEach((selector) => {
                        const el = document.querySelector(selector);
                        if (!el) return;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                    });
                }"""
            )
            page.wait_for_timeout(80)
        except Exception:
            pass

    def _click_by_candidates(self, role_names: Iterable[re.Pattern], selectors: Iterable[str]) -> bool:
        page = self._require_page()
        for name in role_names:
            try:
                if self._try_click_locator(page.get_by_role("button", name=name)):
                    return True
            except Exception:
                pass

        for selector in selectors:
            if self._try_click_locator(page.locator(selector)):
                return True
        return False

    def _try_click_locator(self, locator: Locator) -> bool:
        try:
            count = min(locator.count(), 5)
        except Exception:
            return False

        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=300):
                    continue
                if self._is_disabled(item):
                    continue
                try:
                    item.evaluate("(el) => el.click()")
                    return True
                except Exception:
                    pass
                item.scroll_into_view_if_needed(timeout=500)
                item.click(timeout=800, force=True)
                return True
            except Exception:
                continue
        return False

    def _is_disabled(self, locator: Locator) -> bool:
        try:
            return bool(
                locator.evaluate(
                    "(el) => el.disabled || el.getAttribute('disabled') === 'true' || "
                    "el.getAttribute('aria-disabled') === 'true'"
                )
            )
        except Exception:
            return False

    def _find_captcha_element(self) -> Optional[Locator]:
        page = self._require_page()
        selectors = [
            "#yzm_img",
            "#yzmImg",
            "#captchaImg",
            "img[alt*='验证码']",
            "img[title*='验证码']",
            "img[src*='yzm']",
            "img[src*='captcha']",
            "img[id*='yzm']",
            "img[class*='yzm']",
            "canvas[id*='yzm']",
            "canvas[id*='captcha']",
            "xpath=//*[contains(normalize-space(.), '验证码')]/following::img[1]",
            "xpath=//*[contains(normalize-space(.), '验证码')]/following::canvas[1]",
        ]
        for selector in selectors:
            visible = self._first_visible(page.locator(selector))
            if visible is not None:
                return visible
        return None

    def _find_captcha_hint_element(self) -> Optional[Locator]:
        """寻找颜色提示文字区域。"""
        page = self._require_page()
        selectors = [
            "#yzminfo",
            "xpath=//*[contains(normalize-space(.), '验证码图片')]",
            "xpath=//*[contains(normalize-space(.), '红色文字')]",
            "xpath=//*[contains(normalize-space(.), '蓝色文字')]",
        ]
        for selector in selectors:
            visible = self._first_visible(page.locator(selector))
            if visible is not None:
                return visible
        return None

    def _combine_captcha_images(self, code_path: Path, hint_path: Path, output_path: Path) -> None:
        with Image.open(code_path) as code_image, Image.open(hint_path) as hint_image:
            code = code_image.convert("RGB")
            hint = hint_image.convert("RGB")
            padding = 12
            width = max(code.width, hint.width) + padding * 2
            height = code.height + hint.height + padding * 3
            canvas = Image.new("RGB", (width, height), "white")
            canvas.paste(hint, (padding, padding))
            canvas.paste(code, (padding, hint.height + padding * 2))
            canvas.save(output_path)

    def _element_viewport_box(self, locator: Optional[Locator], padding: int = 0) -> Optional[dict]:
        """返回元素在当前 viewport 截图坐标系里的矩形。"""
        if locator is None:
            return None
        try:
            box = locator.evaluate(
                """(el, padding) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const hidden = style.display === 'none'
                        || style.visibility === 'hidden'
                        || Number(style.opacity || '1') === 0;
                    if (hidden || rect.width < 2 || rect.height < 2) {
                        return null;
                    }
                    return {
                        x: Math.max(0, rect.left - padding),
                        y: Math.max(0, rect.top - padding),
                        width: rect.width + padding * 2,
                        height: rect.height + padding * 2
                    };
                }""",
                padding,
            )
        except Exception:
            return None
        if not box:
            return None
        if box.get("width", 0) < 2 or box.get("height", 0) < 2:
            return None
        return box

    def _element_page_box(self, locator: Optional[Locator], padding: int = 0) -> Optional[dict]:
        """返回元素在整页截图坐标系里的矩形，供整页截图后裁剪使用。"""
        if locator is None:
            return None
        try:
            box = locator.evaluate(
                """(el, padding) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const hidden = style.display === 'none'
                        || style.visibility === 'hidden'
                        || Number(style.opacity || '1') === 0;
                    if (hidden || rect.width < 2 || rect.height < 2) {
                        return null;
                    }
                    return {
                        x: Math.max(0, rect.left + window.scrollX - padding),
                        y: Math.max(0, rect.top + window.scrollY - padding),
                        width: rect.width + padding * 2,
                        height: rect.height + padding * 2
                    };
                }""",
                padding,
            )
        except Exception:
            return None
        if not box:
            return None
        if box.get("width", 0) < 2 or box.get("height", 0) < 2:
            return None
        return box

    def _crop_image(self, source_path: Path, output_path: Path, box: dict) -> None:
        """从整页截图中裁剪指定区域，避免元素截图因动态页面超时。"""
        with Image.open(source_path) as image:
            left = max(0, int(box["x"]))
            top = max(0, int(box["y"]))
            right = min(image.width, int(box["x"] + box["width"]))
            bottom = min(image.height, int(box["y"] + box["height"]))
            if right - left < 2 or bottom - top < 2:
                raise RuntimeError("验证码裁剪区域尺寸异常。")
            image.crop((left, top, right, bottom)).save(output_path)

    def _make_text_placeholder(self, output_path: Path, text: str) -> None:
        image = Image.new("RGB", (360, 48), "white")
        image.save(output_path)

    def _first_visible(self, locator: Locator) -> Optional[Locator]:
        try:
            count = min(locator.count(), 5)
        except Exception:
            return None
        for index in range(count):
            item = locator.nth(index)
            try:
                if item.is_visible(timeout=1000):
                    return item
            except Exception:
                continue
        return None

    def _save_debug_artifacts(self, prefix: str) -> None:
        if not self.page:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = self.debug_dir / f"{prefix}_{timestamp}.png"
        html_path = self.debug_dir / f"{prefix}_{timestamp}.html"
        try:
            self.page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass
        try:
            html_path.write_text(self.page.content(), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _date_for_site(value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        return digits if len(digits) == 8 else (value or "").strip()

    @staticmethod
    def _amount_for_site(value: str) -> str:
        text = (value or "").replace("￥", "").replace("¥", "").replace("元", "")
        text = re.sub(r"[,\s\u3000]", "", text)
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return match.group(0) if match else text

    @staticmethod
    def _css_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"
