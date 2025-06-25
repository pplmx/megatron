import argparse  # Added for command-line arguments
import asyncio
import io
import logging
import os  # Added for os.cpu_count()
from concurrent.futures import ThreadPoolExecutor  # ThreadPoolExecutor is still used for _validate_files
from pathlib import Path

from playwright.async_api import async_playwright
from pypdf import PdfWriter

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class HTMLToPDFConverter:
    """高性能HTML转PDF转换器，支持并发处理和顺序保证"""

    def __init__(
        self,
        include_background: bool = True,
        max_workers: int = 4,
        slide_width: int = 1280,
        slide_height: int = 720,
    ):
        self.pdf_options = {
            "print_background": include_background,
            "page_ranges": "1",  # Each HTML is one slide
        }
        self.max_workers = max_workers
        self.slide_width = slide_width
        self.slide_height = slide_height
        logger.info(
            f"转换器初始化完成，最大并发数: {self.max_workers}, 幻灯片尺寸: {self.slide_width}x{self.slide_height}"
        )

    def convert(self, source: str | Path | list, output_path: str | Path) -> None:
        """转换HTML文件为PDF"""
        html_files = self._resolve_html_files(source)
        self._validate_files(html_files)

        logger.info(f"开始转换 {len(html_files)} 个文件")

        # Run the main async conversion process
        # Pass html_files directly, they will be enumerated in the async method
        pdf_results_indexed = asyncio.run(self._convert_all_files_async(html_files))

        successful_conversions: list[tuple[int, bytes]] = []

        for i, result_item in enumerate(pdf_results_indexed):
            original_file_path = html_files[i]

            if isinstance(result_item, BaseException): # Check for BaseException first
                logger.error(f"Error converting file {original_file_path.name} (original index {i}): {result_item}")
            elif isinstance(result_item, tuple) and len(result_item) == 2 and isinstance(result_item[0], int) and isinstance(result_item[1], bytes):
                # This is the successful case: tuple[int, bytes]
                # We expect result_item[0] (the returned index) to match i (the enumerated index)
                if result_item[0] != i:
                    logger.warning(
                        f"Mismatch in expected original index ({i}) and returned index ({result_item[0]}) "
                        f"for file {original_file_path.name}. Trusting returned index for data consistency."
                    )
                successful_conversions.append(result_item)
            else:
                # This case handles any other unexpected type that might somehow appear,
                # though _convert_all_files_async is typed to return list[tuple[int, bytes] | BaseException].
                logger.error(
                    f"Unexpected result type for file {original_file_path.name} (original index {i}): {type(result_item)}"
                )

        if not successful_conversions:
            logger.error("所有文件转换失败或未返回有效PDF数据。")
            raise ValueError("所有文件转换失败。")

        # Sort by original index, which is the first element of the tuple
        successful_conversions.sort(key=lambda x: x[0])

        pdf_bytes_list: list[bytes] = [pdf_bytes for _, pdf_bytes in successful_conversions]

        # This check is technically redundant if successful_conversions processing is correct
        if not pdf_bytes_list:
            logger.error("没有从成功转换中提取出PDF字节列表。") # Should not happen if successful_conversions is not empty
            raise ValueError("没有成功的PDF转换结果可供合并。")

        self._merge_pdfs(pdf_bytes_list, Path(output_path))
        logger.info(f"✅ 转换完成: {output_path}")

    async def _convert_all_files_async(self, html_files: list[Path]) -> list[tuple[int, bytes] | BaseException]:
        """
        Asynchronously converts all HTML files using a single Playwright browser instance
        and an asyncio.Semaphore to limit concurrency.
        Returns a list of (original_index, pdf_bytes) or BaseException for each file.
        """
        logger.info(f"使用异步转换，最大并发Playwright任务数: {self.max_workers}")

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            semaphore = asyncio.Semaphore(self.max_workers)

            tasks = []
            for i, html_file in enumerate(html_files):
                tasks.append(self._convert_single_file_async_wrapper(browser, i, html_file, semaphore, len(html_files)))

            # return_exceptions=True allows all tasks to complete even if some fail
            results = await asyncio.gather(*tasks, return_exceptions=True)

            await browser.close()
            return results

    async def _convert_single_file_async_wrapper(
        self, browser, file_index: int, html_path: Path, semaphore: asyncio.Semaphore, total_files: int
    ) -> tuple[int, bytes]:
        """
        Wrapper to acquire semaphore for each file conversion.
        Converts a single HTML file to PDF bytes.
        Returns a tuple (file_index, pdf_bytes). Raises exception on failure.
        """
        async with semaphore:
            page = await browser.new_page()
            try:
                # Using a more unique identifier for logging if needed, combining index and name
                log_prefix = f"[idx:{file_index:03d}/{total_files:03d}][{html_path.name}]"
                logger.info(f"{log_prefix} 开始转换")

                # Use configured slide dimensions for viewport
                await page.set_viewport_size({"width": self.slide_width, "height": self.slide_height})

                await page.goto(html_path.as_uri())
                await page.wait_for_load_state("networkidle")

                await page.add_style_tag(
                    content="""
                    html, body { margin: 0 !important; padding: 0 !important; }
                    * { -webkit-print-color-adjust: exact !important; }
                """
                )

                # Use documentElement's scrollWidth/Height, which should reflect the .slide-container
                # if it's the main scrolling content, or the overall page size.
                dimensions = await page.evaluate("""() => ({
                    width: Math.ceil(document.documentElement.scrollWidth),
                    height: Math.ceil(document.documentElement.scrollHeight)
                })""")

                logger.info(f"{log_prefix} 页面尺寸: {dimensions['width']}x{dimensions['height']}")

                current_pdf_options = {
                    **self.pdf_options,  # Includes print_background and page_ranges="1"
                    "width": f"{dimensions['width']}px",
                    "height": f"{dimensions['height']}px",
                    "margin": {"top": "0px", "right": "0px", "bottom": "0px", "left": "0px"},
                }
                pdf_bytes = await page.pdf(**current_pdf_options)
                logger.info(f"{log_prefix} 转换成功, PDF大小: {len(pdf_bytes)} bytes")
                return file_index, pdf_bytes
            except Exception as e:
                logger.error(
                    f"{log_prefix} 转换失败: {e}", exc_info=False
                )  # exc_info=False to avoid huge logs in gather
                # Instead of raising, return None for bytes to allow gather to continue with other tasks
                # The main 'convert' method will handle logging and filtering these.
                # The Exception itself will be returned by gather if return_exceptions=True
                raise  # Re-raise so gather(return_exceptions=True) can capture it.
            finally:
                await page.close()
                logger.debug(f"{log_prefix} 页面已关闭")

    def _resolve_html_files(self, source: str | Path | list) -> list[Path]:
        """解析HTML文件列表"""
        if isinstance(source, list):
            return [Path(f).resolve() for f in source]

        source_path = Path(source).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"路径不存在: {source_path}")

        if source_path.is_file():
            return [source_path]

        html_files = sorted(f for f in source_path.iterdir() if f.suffix.lower() in {".html", ".htm"})
        if not html_files:
            raise ValueError(f"目录中未找到HTML文件: {source}")

        return html_files

    def _validate_files(self, html_files: list[Path]) -> None:
        """验证文件存在性"""
        with ThreadPoolExecutor(max_workers=min(len(html_files), 10)) as executor:
            futures = [executor.submit(lambda f: f.exists(), f) for f in html_files]
            missing_files = [html_files[i] for i, future in enumerate(futures) if not future.result()]

        if missing_files:
            raise FileNotFoundError(f"文件不存在: {missing_files}")

    # Removed old methods: _convert_sequential, _convert_concurrent, _convert_chunk,
    # _convert_indexed_files_async, _convert_indexed_file_async,
    # _convert_files_async, _convert_file_async

    def _merge_pdfs(self, pdf_bytes_list: list[bytes], output_path: Path) -> None:
        """合并PDF文件"""
        if not pdf_bytes_list:
            raise ValueError("无PDF数据")

        if len(pdf_bytes_list) == 1:
            output_path.write_bytes(pdf_bytes_list[0])
            return

        writer = PdfWriter()
        try:
            for pdf_bytes in pdf_bytes_list:
                writer.append(io.BytesIO(pdf_bytes))
            with output_path.open("wb") as f:
                writer.write(f)
            logger.info(f"合并完成，共 {len(pdf_bytes_list)} 页")
        finally:
            writer.close()


def convert_html_to_pdf(
    source: str | Path | list,
    output_path: str | Path,
    include_background: bool = True,
    max_workers: int = 4,
    slide_width: int = 1280,
    slide_height: int = 720,
) -> None:
    """HTML转PDF便捷函数"""
    converter = HTMLToPDFConverter(
        include_background=include_background,
        max_workers=max_workers,
        slide_width=slide_width,
        slide_height=slide_height,
    )
    converter.convert(source, output_path)


def main():
    parser = argparse.ArgumentParser(description="Convert HTML files to a single PDF document.")
    parser.add_argument(
        "source",
        type=str,
        help="Source HTML file or directory containing HTML files.",
    )
    parser.add_argument(
        "output",
        type=str,
        help="Output PDF file path.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=os.cpu_count() or 1,  # Default to number of CPUs, or 1 if not determinable
        help="Maximum number of concurrent conversion tasks. Default is CPU count.",
    )
    # Changed include_background to store_false for a more typical flag behavior
    parser.add_argument(
        "--no-background",
        action="store_false",
        dest="include_background",  # This makes args.include_background True by default
        help="Disable printing of background graphics. Default is to include background.",
    )
    parser.add_argument(
        "--slide-width",
        type=int,
        default=1280,
        help="Target width for slides in pixels (used for viewport). Default is 1280.",
    )
    parser.add_argument(
        "--slide-height",
        type=int,
        default=720,
        help="Target height for slides in pixels (used for viewport). Default is 720.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("调试模式已启用")

    try:
        convert_html_to_pdf(
            source=args.source,
            output_path=args.output,
            include_background=args.include_background,
            max_workers=args.max_workers,
            slide_width=args.slide_width,
            slide_height=args.slide_height,
        )
    except Exception as e:
        logger.error(f"转换失败: {e}", exc_info=args.debug)  # Show full traceback only in debug
        # Consider exiting with a non-zero status code for scriptability
        # import sys
        # sys.exit(1)
        raise  # Re-raise for now, or handle more gracefully


if __name__ == "__main__":
    main()
