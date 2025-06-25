import argparse
import asyncio
import io
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from playwright.async_api import Browser, Page, async_playwright
from pypdf import PdfWriter

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class HTMLToPDFConverter:
    """高性能HTML转PDF转换器，支持并发处理和顺序保证"""

    def __init__(
        self,
        include_background: bool = True,
        max_workers: int = 4,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        timeout: int = 30000,  # 30秒超时
        wait_for: str = "networkidle",  # 等待策略
        force_viewport_size: bool = False,  # 是否强制使用viewport尺寸
    ):
        self.pdf_options = {
            "print_background": include_background,
            "page_ranges": "1",
        }
        self.max_workers = max_workers
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.timeout = timeout
        self.wait_for = wait_for
        self.force_viewport_size = force_viewport_size

        logger.info(
            f"转换器初始化完成 - 并发数: {self.max_workers}, "
            f"视口尺寸: {self.viewport_width}x{self.viewport_height}, "
            f"强制视口尺寸: {self.force_viewport_size}, "
            f"超时: {self.timeout}ms"
        )

    def convert(self, source: str | Path | list, output_path: str | Path) -> None:
        """转换HTML文件为PDF"""
        try:
            html_files = self._resolve_html_files(source)
            self._validate_files(html_files)

            if not html_files:
                raise ValueError("未找到有效的HTML文件")

            logger.info(f"开始转换 {len(html_files)} 个文件")

            # 异步转换所有文件
            pdf_results = asyncio.run(self._convert_all_files_async(html_files))

            # 处理转换结果
            successful_conversions = self._process_conversion_results(pdf_results, html_files)

            if not successful_conversions:
                raise ValueError("所有文件转换失败")

            # 按原始索引排序并合并PDF
            successful_conversions.sort(key=lambda x: x[0])
            pdf_bytes_list = [pdf_bytes for _, pdf_bytes in successful_conversions]

            self._merge_pdfs(pdf_bytes_list, Path(output_path))
            logger.info(f"✅ 转换完成: {output_path}")

        except Exception as e:
            logger.error(f"转换过程中发生错误: {e}")
            raise

    async def _convert_all_files_async(self, html_files: list[Path]) -> list[tuple[int, bytes] | Exception]:
        """异步转换所有HTML文件"""
        logger.info(f"启动异步转换，最大并发数: {self.max_workers}")

        async with async_playwright() as playwright:
            browser = await self._launch_browser(playwright)
            semaphore = asyncio.Semaphore(self.max_workers)

            try:
                tasks = [
                    self._convert_single_file_with_semaphore(browser, i, html_file, semaphore, len(html_files))
                    for i, html_file in enumerate(html_files)
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)
                return results

            finally:
                await browser.close()

    async def _launch_browser(self, playwright) -> Browser:
        """启动浏览器实例"""
        return await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
            ],
        )

    async def _convert_single_file_with_semaphore(
        self, browser: Browser, file_index: int, html_path: Path, semaphore: asyncio.Semaphore, total_files: int
    ) -> tuple[int, bytes]:
        """带信号量控制的单文件转换"""
        async with semaphore:
            return await self._convert_single_file(browser, file_index, html_path, total_files)

    async def _convert_single_file(
        self, browser: Browser, file_index: int, html_path: Path, total_files: int
    ) -> tuple[int, bytes]:
        """转换单个HTML文件为PDF"""
        log_prefix = f"[{file_index + 1:03d}/{total_files:03d}][{html_path.name}]"
        page = None

        try:
            logger.info(f"{log_prefix} 开始转换")

            page = await browser.new_page()
            await self._configure_page(page)

            # 导航到页面并等待加载
            await page.goto(html_path.as_uri(), timeout=self.timeout)
            await page.wait_for_load_state(self.wait_for, timeout=self.timeout)

            # 添加打印样式
            await self._add_print_styles(page)

            # 获取页面尺寸并生成PDF
            dimensions = await self._get_page_dimensions(page)
            logger.debug(f"{log_prefix} 页面尺寸: {dimensions['width']}x{dimensions['height']}")

            pdf_bytes = await self._generate_pdf(page, dimensions)

            logger.info(f"{log_prefix} 转换成功, PDF大小: {len(pdf_bytes):,} bytes")
            return file_index, pdf_bytes

        except Exception as e:
            logger.error(f"{log_prefix} 转换失败: {e}")
            raise
        finally:
            if page:
                await page.close()

    async def _configure_page(self, page: Page) -> None:
        """配置页面设置"""
        await page.set_viewport_size({"width": self.viewport_width, "height": self.viewport_height})

        # 设置超时
        page.set_default_timeout(self.timeout)

    async def _add_print_styles(self, page: Page) -> None:
        """添加打印样式"""
        await page.add_style_tag(
            content="""
            html, body {
                margin: 0 !important;
                padding: 0 !important;
                box-sizing: border-box;
            }
            * {
                -webkit-print-color-adjust: exact !important;
                color-adjust: exact !important;
            }
            @media print {
                body { overflow: visible !important; }
            }
        """
        )

    async def _get_page_dimensions(self, page: Page) -> dict:
        """获取页面实际尺寸"""
        return await page.evaluate("""() => ({
            width: Math.max(
                document.documentElement.scrollWidth,
                document.documentElement.offsetWidth,
                document.body.scrollWidth,
                document.body.offsetWidth
            ),
            height: Math.max(
                document.documentElement.scrollHeight,
                document.documentElement.offsetHeight,
                document.body.scrollHeight,
                document.body.offsetHeight
            )
        })""")

    async def _generate_pdf(self, page: Page, dimensions: dict) -> bytes:
        """生成PDF字节数据"""
        # 如果强制使用视口尺寸，则覆盖实际页面尺寸
        if self.force_viewport_size:
            pdf_width = self.viewport_width
            pdf_height = self.viewport_height
        else:
            pdf_width = dimensions["width"]
            pdf_height = dimensions["height"]

        pdf_options = {
            **self.pdf_options,
            "width": f"{pdf_width}px",
            "height": f"{pdf_height}px",
            "margin": {"top": "0", "right": "0", "bottom": "0", "left": "0"},
        }
        return await page.pdf(**pdf_options)

    def _process_conversion_results(
        self, results: list[tuple[int, bytes] | Exception], html_files: list[Path]
    ) -> list[tuple[int, bytes]]:
        """处理转换结果，分离成功和失败的转换"""
        successful_conversions = []
        failed_count = 0

        for i, result in enumerate(results):
            file_path = html_files[i]

            if isinstance(result, Exception):
                logger.error(f"文件 {file_path.name} (索引 {i}) 转换失败: {result}")
                failed_count += 1
            elif isinstance(result, tuple) and len(result) == 2:
                index, pdf_bytes = result
                if index != i:
                    logger.warning(f"索引不匹配: 期望 {i}, 实际 {index}, 文件: {file_path.name}")
                successful_conversions.append(result)
            else:
                logger.error(f"文件 {file_path.name} 返回了意外的结果类型: {type(result)}")
                failed_count += 1

        if failed_count > 0:
            logger.warning(f"有 {failed_count} 个文件转换失败")

        logger.info(f"成功转换 {len(successful_conversions)} 个文件")
        return successful_conversions

    def _resolve_html_files(self, source: str | Path | list) -> list[Path]:
        """解析HTML文件列表"""
        if isinstance(source, list):
            paths = [Path(f).resolve() for f in source]
            return [p for p in paths if p.suffix.lower() in {".html", ".htm"}]

        source_path = Path(source).resolve()

        if not source_path.exists():
            raise FileNotFoundError(f"路径不存在: {source_path}")

        if source_path.is_file():
            if source_path.suffix.lower() not in {".html", ".htm"}:
                raise ValueError(f"文件不是HTML格式: {source_path}")
            return [source_path]

        # 扫描目录中的HTML文件
        html_files = []
        for pattern in ["*.html", "*.htm"]:
            html_files.extend(source_path.glob(pattern))

        if not html_files:
            raise ValueError(f"目录中未找到HTML文件: {source_path}")

        return sorted(html_files, key=lambda x: x.name.lower())

    def _validate_files(self, html_files: list[Path]) -> None:
        """并发验证文件存在性"""
        if not html_files:
            return

        with ThreadPoolExecutor(max_workers=min(len(html_files), 10)) as executor:
            existence_checks = [executor.submit(self._check_file_exists, f) for f in html_files]
            missing_files = [html_files[i] for i, future in enumerate(existence_checks) if not future.result()]

        if missing_files:
            raise FileNotFoundError(f"以下文件不存在: {[str(f) for f in missing_files]}")

    @staticmethod
    def _check_file_exists(file_path: Path) -> bool:
        """检查文件是否存在且可读"""
        return file_path.exists() and file_path.is_file()

    def _merge_pdfs(self, pdf_bytes_list: list[bytes], output_path: Path) -> None:
        """合并PDF文件"""
        if not pdf_bytes_list:
            raise ValueError("没有PDF数据可供合并")

        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if len(pdf_bytes_list) == 1:
            output_path.write_bytes(pdf_bytes_list[0])
            logger.info("单个PDF文件直接保存")
            return

        writer = PdfWriter()
        try:
            for i, pdf_bytes in enumerate(pdf_bytes_list):
                try:
                    writer.append(io.BytesIO(pdf_bytes))
                except Exception as e:
                    logger.error(f"合并第 {i + 1} 个PDF时出错: {e}")
                    raise

            with output_path.open("wb") as f:
                writer.write(f)

            logger.info(f"成功合并 {len(pdf_bytes_list)} 个PDF页面")

        except Exception as e:
            logger.error(f"PDF合并失败: {e}")
            raise
        finally:
            writer.close()


def convert_html_to_pdf(
    source: str | Path | list,
    output_path: str | Path,
    include_background: bool = True,
    max_workers: int = 4,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    timeout: int = 30000,
    wait_for: str = "networkidle",
    force_viewport_size: bool = False,
) -> None:
    """HTML转PDF便捷函数

    Args:
        source: HTML文件路径、目录路径或文件路径列表
        output_path: 输出PDF文件路径
        include_background: 是否包含背景图形
        max_workers: 最大并发数
        viewport_width: 浏览器视口宽度（像素）
        viewport_height: 浏览器视口高度（像素）
        timeout: 页面加载超时时间（毫秒）
        wait_for: 等待策略 ('load', 'domcontentloaded', 'networkidle')
        force_viewport_size: 是否强制使用视口尺寸作为PDF尺寸（否则使用页面实际尺寸）
    """
    converter = HTMLToPDFConverter(
        include_background=include_background,
        max_workers=max_workers,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        timeout=timeout,
        wait_for=wait_for,
        force_viewport_size=force_viewport_size,
    )
    converter.convert(source, output_path)


def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(
        description="将HTML文件转换为单个PDF文档",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  %(prog)s slides/ output.pdf
  %(prog)s slides/ output.pdf --max-workers 8 --viewport-width 1920 --viewport-height 1080
  %(prog)s presentation.html output.pdf --no-background --force-viewport-size --debug
        """,
    )

    parser.add_argument("source", help="源HTML文件或包含HTML文件的目录路径")

    parser.add_argument("output", help="输出PDF文件路径")

    parser.add_argument(
        "--max-workers",
        type=int,
        default=min(os.cpu_count() or 1, 8),  # 限制最大值避免资源耗尽
        help="最大并发转换任务数（默认: CPU核心数，最大8）",
    )

    parser.add_argument(
        "--no-background", action="store_false", dest="include_background", help="禁用背景图形打印（默认包含背景）"
    )

    parser.add_argument("--viewport-width", type=int, default=1280, help="浏览器视口宽度（像素，默认: 1280）")

    parser.add_argument("--viewport-height", type=int, default=720, help="浏览器视口高度（像素，默认: 720）")

    parser.add_argument(
        "--force-viewport-size", action="store_true", help="强制使用视口尺寸作为PDF尺寸（否则使用页面实际尺寸）"
    )

    parser.add_argument("--timeout", type=int, default=30000, help="页面加载超时时间（毫秒，默认: 30000）")

    parser.add_argument(
        "--wait-for",
        choices=["load", "domcontentloaded", "networkidle"],
        default="networkidle",
        help="页面等待策略（默认: networkidle）",
    )

    parser.add_argument("--debug", action="store_true", help="启用调试日志")

    parser.add_argument("--version", action="version", version="HTML to PDF Converter 2.0")

    args = parser.parse_args()

    # 配置日志级别
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("调试模式已启用")

    # 参数验证
    if args.max_workers < 1:
        parser.error("--max-workers 必须大于0")

    if args.viewport_width < 100 or args.viewport_height < 100:
        parser.error("视口尺寸不能小于100像素")

    if args.timeout < 1000:
        parser.error("超时时间不能小于1000毫秒")

    try:
        convert_html_to_pdf(
            source=args.source,
            output_path=args.output,
            include_background=args.include_background,
            max_workers=args.max_workers,
            viewport_width=args.viewport_width,
            viewport_height=args.viewport_height,
            timeout=args.timeout,
            wait_for=args.wait_for,
            force_viewport_size=args.force_viewport_size,
        )

        logger.info("程序执行完成")

    except KeyboardInterrupt:
        logger.info("用户中断操作")
        sys.exit(1)
    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        if args.debug:
            logger.exception("详细错误信息:")
        sys.exit(1)


if __name__ == "__main__":
    main()
