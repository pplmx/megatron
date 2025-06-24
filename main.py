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
    """高性能HTML转PDF转换器，支持并发处理和顺序保证"""

    def __init__(self, include_background: bool = True, max_workers: int = 4):
        self.pdf_options = {
            "print_background": include_background,
            "page_ranges": "1",
        }
        self.max_workers = max_workers
        logger.info(f"转换器初始化完成，并发数: {max_workers}")

    def convert(self, source: str | Path | list, output_path: str | Path) -> None:
        """转换HTML文件为PDF"""
        html_files = self._resolve_html_files(source)
        self._validate_files(html_files)

        logger.info(f"开始转换 {len(html_files)} 个文件")

        if len(html_files) <= 2:
            pdf_bytes_list = self._convert_sequential(html_files)
        else:
            pdf_bytes_list = self._convert_concurrent(html_files)

        self._merge_pdfs(pdf_bytes_list, Path(output_path))
        logger.info(f"✅ 转换完成: {output_path}")

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

    def _convert_sequential(self, html_files: list[Path]) -> list[bytes]:
        """顺序转换模式"""
        logger.info("使用顺序转换")
        return asyncio.run(self._convert_files_async(html_files))

    def _convert_concurrent(self, html_files: list[Path]) -> list[bytes]:
        """并发转换模式，保证输出顺序"""
        logger.info(f"使用并发转换，线程数: {self.max_workers}")

        # 为文件分配索引
        indexed_files = list(enumerate(html_files))

        # 分批处理
        chunk_size = max(1, len(html_files) // self.max_workers)
        chunks = [indexed_files[i : i + chunk_size] for i in range(0, len(indexed_files), chunk_size)]

        # 并发执行
        results_with_index = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self._convert_chunk, chunk) for chunk in chunks]

            for future in as_completed(futures):
                results_with_index.extend(future.result())

        # 按索引排序恢复原始顺序
        results_with_index.sort(key=lambda x: x[0])
        return [pdf_bytes for _, pdf_bytes in results_with_index]

    def _convert_chunk(self, indexed_files: list[tuple[int, Path]]) -> list[tuple[int, bytes]]:
        """转换文件块"""
        return asyncio.run(self._convert_indexed_files_async(indexed_files))

    async def _convert_indexed_files_async(self, indexed_files: list[tuple[int, Path]]) -> list[tuple[int, bytes]]:
        """异步转换带索引的文件"""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                semaphore = asyncio.Semaphore(3)
                tasks = [
                    self._convert_indexed_file_async(browser, index, html_file, semaphore, len(indexed_files))
                    for index, html_file in indexed_files
                ]
                return await asyncio.gather(*tasks)
            finally:
                await browser.close()

    async def _convert_indexed_file_async(
        self, browser, file_index: int, html_path: Path, semaphore: asyncio.Semaphore, total: int
    ) -> tuple[int, bytes]:
        """转换单个带索引的文件"""
        async with semaphore:
            page = await browser.new_page()
            try:
                thread_id = threading.get_ident() % 10000
                logger.info(f"  [T{thread_id:04d}] [idx:{file_index:03d}] {html_path.name}")

                await page.goto(html_path.as_uri())
                await page.wait_for_load_state("networkidle")

                await page.add_style_tag(content="html,body{margin:0!important;padding:0!important}")

                dimensions = await page.evaluate("""() => ({
                    width: Math.ceil(document.documentElement.scrollWidth),
                    height: Math.ceil(document.documentElement.scrollHeight)
                })""")

                pdf_bytes = await page.pdf(
                    width=f"{dimensions['width']}px", height=f"{dimensions['height']}px", **self.pdf_options
                )

                return (file_index, pdf_bytes)
            finally:
                await page.close()

    async def _convert_files_async(self, html_files: list[Path]) -> list[bytes]:
        """异步转换文件列表（顺序模式）"""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                semaphore = asyncio.Semaphore(3)
                tasks = [
                    self._convert_file_async(browser, html_file, semaphore, i, len(html_files))
                    for i, html_file in enumerate(html_files)
                ]
                return await asyncio.gather(*tasks)
            finally:
                await browser.close()

    async def _convert_file_async(
        self, browser, html_path: Path, semaphore: asyncio.Semaphore, idx: int, total: int
    ) -> bytes:
        """转换单个文件（顺序模式）"""
        async with semaphore:
            page = await browser.new_page()
            try:
                logger.info(f"  [{idx + 1:03d}/{total:03d}] {html_path.name}")

                await page.goto(html_path.as_uri())
                await page.wait_for_load_state("networkidle")

                await page.add_style_tag(content="html,body{margin:0!important;padding:0!important}")

                dimensions = await page.evaluate("""() => ({
                    width: Math.ceil(document.documentElement.scrollWidth),
                    height: Math.ceil(document.documentElement.scrollHeight)
                })""")

                return await page.pdf(
                    width=f"{dimensions['width']}px", height=f"{dimensions['height']}px", **self.pdf_options
                )
            finally:
                await page.close()

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
    source: str | Path | list, output_path: str | Path, include_background: bool = True, max_workers: int = 4
) -> None:
    """HTML转PDF便捷函数"""
    converter = HTMLToPDFConverter(include_background=include_background, max_workers=max_workers)
    converter.convert(source, output_path)


def main():
    html_source = "docs/pdf_html"
    output_pdf = "output.pdf"

    try:
        convert_html_to_pdf(html_source, output_pdf, max_workers=6)
    except Exception as e:
        logger.error(f"转换失败: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
