import asyncio
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from playwright.async_api import async_playwright
from pypdf import PdfWriter

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class HTMLToPDFConverter:
    """高性能HTML文件转PDF转换器，支持并发处理和资源复用"""

    def __init__(self, include_background: bool = True, max_workers: int = 4):
        self.pdf_options = {
            "print_background": include_background,
            "page_ranges": "1",
        }
        self.max_workers = max_workers
        logger.info(f"HTML转PDF转换器已初始化，最大并发数: {max_workers}")

    def convert(self, source: str | Path | list, output_path: str | Path) -> None:
        """转换HTML文件为PDF"""
        html_files = self._resolve_html_files(source)
        self._validate_files(html_files)

        logger.info(f"开始转换 {len(html_files)} 个HTML文件")

        # 选择最优的处理策略
        if len(html_files) <= 2:
            pdf_bytes_list = self._convert_sequential(html_files)
        else:
            pdf_bytes_list = self._convert_concurrent(html_files)

        self._merge_pdfs_efficient(pdf_bytes_list, Path(output_path))
        logger.info(f"✅ 转换完成: {output_path}")

    def _resolve_html_files(self, source: str | Path | list) -> list[Path]:
        """解析HTML文件列表"""
        if isinstance(source, list):
            logger.info(f"使用用户指定的文件列表，共 {len(source)} 个文件")
            # FIX: Resolve each path in the list to be absolute
            return [Path(f).resolve() for f in source]

        # FIX: Resolve the source path itself to be absolute
        source_path = Path(source).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"路径不存在: {source_path}")

        if source_path.is_file():
            logger.info(f"处理单个文件: {source_path.name}")
            return [source_path]

        # Now that source_path is absolute, all paths from iterdir() will also be absolute
        html_files = sorted(f for f in source_path.iterdir() if f.suffix.lower() in {".html", ".htm"})

        if not html_files:
            raise ValueError(f"目录中未找到HTML文件: {source}")

        logger.info(f"找到 {len(html_files)} 个HTML文件")
        return html_files

    def _validate_files(self, html_files: list[Path]) -> None:
        """并发验证HTML文件是否存在"""
        with ThreadPoolExecutor(max_workers=min(len(html_files), 10)) as executor:
            futures = {executor.submit(lambda f: f.exists(), f): f for f in html_files}
            missing_files = [futures[future] for future in as_completed(futures) if not future.result()]

        if missing_files:
            raise FileNotFoundError(f"文件不存在: {missing_files}")

    def _convert_sequential(self, html_files: list[Path]) -> list[bytes]:
        """顺序转换（适用于小批量文件）"""
        logger.info("使用顺序转换模式")
        return asyncio.run(self._convert_all_files_async(html_files))

    def _convert_concurrent(self, html_files: list[Path]) -> list[bytes]:
        """并发转换（适用于大批量文件）"""
        logger.info(f"使用并发转换模式，工作线程数: {self.max_workers}")

        # 将文件分批，每个线程处理一批
        chunk_size = max(1, len(html_files) // self.max_workers)
        chunks = [html_files[i : i + chunk_size] for i in range(0, len(html_files), chunk_size)]

        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._convert_chunk, chunk, chunk_idx): chunk_idx
                for chunk_idx, chunk in enumerate(chunks)
            }

            # 按原始顺序收集结果
            chunk_results = {}
            for future in as_completed(futures):
                chunk_idx = futures[future]
                chunk_results[chunk_idx] = future.result()

            # 合并所有chunk的结果
            for i in range(len(chunks)):
                results.extend(chunk_results[i])

        return results

    def _convert_chunk(self, html_files: list[Path], chunk_idx: int) -> list[bytes]:
        """转换一个文件chunk"""
        logger.info(f"线程 {chunk_idx} 开始处理 {len(html_files)} 个文件")
        return asyncio.run(self._convert_all_files_async(html_files))

    async def _convert_all_files_async(self, html_files: list[Path]) -> list[bytes]:
        """异步转换所有文件"""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                # 使用信号量控制并发页面数量，避免资源耗尽
                semaphore = asyncio.Semaphore(3)
                tasks = [
                    self._convert_single_file_async(browser, html_file, semaphore, i + 1, len(html_files))
                    for i, html_file in enumerate(html_files)
                ]
                return await asyncio.gather(*tasks)
            finally:
                await browser.close()

    async def _convert_single_file_async(
        self, browser, html_path: Path, semaphore: asyncio.Semaphore, idx: int, total: int
    ) -> bytes:
        """异步转换单个HTML文件"""
        async with semaphore:
            page = await browser.new_page()
            try:
                # 使用线程ID来区分日志
                thread_id = threading.get_ident() % 1000
                logger.info(f"  [T{thread_id}] [{idx:02d}/{total}] {html_path.name}")

                # Since html_path is now absolute, as_uri() will work correctly.
                await page.goto(html_path.as_uri())
                await page.wait_for_load_state("networkidle")

                # 移除默认边距
                await page.add_style_tag(
                    content="""
                    html, body {
                        margin: 0 !important;
                        padding: 0 !important;
                    }
                """
                )

                # 获取页面尺寸
                dimensions = await page.evaluate("""() => ({
                    width: Math.ceil(document.documentElement.scrollWidth),
                    height: Math.ceil(document.documentElement.scrollHeight)
                })""")

                return await page.pdf(
                    width=f"{dimensions['width']}px", height=f"{dimensions['height']}px", **self.pdf_options
                )
            finally:
                await page.close()

    def _merge_pdfs_efficient(self, pdf_bytes_list: list[bytes], output_path: Path) -> None:
        """高效合并PDF文件"""
        if not pdf_bytes_list:
            raise ValueError("没有PDF数据需要合并")

        # 如果只有一个PDF，直接写入文件
        if len(pdf_bytes_list) == 1:
            output_path.write_bytes(pdf_bytes_list[0])
            logger.info("单个PDF文件直接写入")
            return

        # 多个PDF合并
        writer = PdfWriter()
        try:
            # 使用生成器表达式节省内存
            for pdf_bytes in pdf_bytes_list:
                writer.append(io.BytesIO(pdf_bytes))

            with output_path.open("wb") as f:
                writer.write(f)

            logger.info(f"成功合并 {len(pdf_bytes_list)} 个页面")
        finally:
            writer.close()


def convert_html_to_pdf(
    source: str | Path | list, output_path: str | Path, include_background: bool = True, max_workers: int = 4
) -> None:
    """便捷函数：将HTML文件转换为PDF

    Args:
        source: HTML文件来源（目录路径、文件路径或文件列表）
        output_path: 输出PDF文件路径
        include_background: 是否包含背景色和图片
        max_workers: 最大并发工作线程数
    """
    converter = HTMLToPDFConverter(include_background=include_background, max_workers=max_workers)
    converter.convert(source, output_path)


def main():
    html_source = "docs/pdf_html"  # 或者 ["file1.html", "file2.html"]
    output_pdf = "output.pdf"

    try:
        # 可以根据文件数量调整并发数
        convert_html_to_pdf(html_source, output_pdf, max_workers=6)
    except Exception as e:
        logger.error(f"转换失败: {e}", exc_info=True)  # exc_info=True gives more details
        raise


if __name__ == "__main__":
    main()
