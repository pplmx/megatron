import io
import logging
from pathlib import Path

from playwright.sync_api import Browser, sync_playwright
from pypdf import PdfWriter

# 配置日志格式
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class HTMLToPDFConverter:
    """
    使用Playwright将HTML文件转换并合并为PDF的转换器

    支持两种输入模式：
    1. 传入目录路径：自动扫描目录下的HTML文件，按文件名字母顺序处理
    2. 传入文件列表：按用户指定的顺序处理文件
    """

    def __init__(self):
        # PDF生成选项配置
        self.pdf_options = {
            "print_background": True,  # 包含背景颜色和图片
            "page_ranges": "1",  # 只导出第一页（避免分页）
        }
        logger.info("HTML转PDF转换器已初始化")

    def convert(self, source: str | list[str], output_path: str) -> None:
        """
        将HTML文件转换为PDF

        Args:
            source: HTML文件来源，支持以下格式：
                   - 目录路径 (str): 扫描目录下所有HTML文件，按文件名排序
                   - 文件列表 (list[str]): 按列表中的顺序处理文件
            output_path: 输出PDF文件的完整路径
        """
        # 解析并获取待处理的HTML文件列表
        html_files = self._resolve_html_files(source)
        # 验证所有文件是否存在
        self._validate_files(html_files)

        logger.info(f"开始转换 {len(html_files)} 个HTML文件")

        # 存储每个HTML文件转换后的PDF字节数据
        pdf_bytes_list = []

        # 使用Playwright启动浏览器进行转换
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                # 逐个转换HTML文件
                for i, html_file in enumerate(html_files, 1):
                    logger.info(f"  [{i:02d}/{len(html_files)}] {Path(html_file).name}")
                    pdf_bytes = self._convert_single_file(browser, html_file)
                    pdf_bytes_list.append(pdf_bytes)
            finally:
                # 确保浏览器正确关闭
                browser.close()

        # 将所有PDF页面合并为单个文件
        self._merge_pdfs(pdf_bytes_list, output_path)
        logger.info(f"✅ 转换完成: {output_path}")

    def _resolve_html_files(self, source: str | list[str]) -> list[str]:
        """
        解析HTML文件列表

        处理逻辑：
        - 如果是列表：直接返回（按用户指定顺序）
        - 如果是单个文件：返回包含该文件的列表
        - 如果是目录：扫描并按文件名排序返回HTML文件列表
        """
        # 情况1：用户提供文件列表，按用户指定顺序处理
        if isinstance(source, list):
            logger.info(f"使用用户指定的文件列表，共 {len(source)} 个文件")
            return source

        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"路径不存在: {source}")

        # 情况2：单个文件
        if source_path.is_file():
            logger.info(f"处理单个文件: {source_path.name}")
            return [str(source_path)]

        # 情况3：目录，按文件名字母顺序自动排序
        logger.info(f"扫描目录: {source}")
        html_files = sorted(str(f) for f in source_path.iterdir() if f.suffix.lower() in {".html", ".htm"})

        if not html_files:
            raise ValueError(f"目录中未找到HTML文件: {source}")

        logger.info(f"找到 {len(html_files)} 个HTML文件，将按文件名顺序处理")
        return html_files

    def _validate_files(self, html_files: list[str]) -> None:
        """
        验证HTML文件是否存在

        Args:
            html_files: HTML文件路径列表

        Raises:
            FileNotFoundError: 当任何文件不存在时抛出异常
        """
        for html_file in html_files:
            if not Path(html_file).exists():
                raise FileNotFoundError(f"HTML文件不存在: {html_file}")

    def _convert_single_file(self, browser: Browser, html_path: str) -> bytes:
        """
        转换单个HTML文件为PDF字节数据

        Args:
            browser: Playwright浏览器实例
            html_path: HTML文件路径

        Returns:
            PDF文件的字节数据
        """
        page = browser.new_page()
        try:
            # 加载HTML文件（使用file://协议）
            file_url = f"file:///{Path(html_path).resolve()}"
            page.goto(file_url)
            # 等待页面完全加载（包括异步资源）
            page.wait_for_load_state("networkidle")

            # 移除页面默认边距，确保内容完整显示
            page.add_style_tag(
                content="""
                html, body {
                    margin: 0 !important;
                    padding: 0 !important;
                }
            """
            )

            # 测量页面内容的实际尺寸
            dimensions = page.evaluate("""() => ({
                width: Math.ceil(document.documentElement.scrollWidth),
                height: Math.ceil(document.documentElement.scrollHeight)
            })""")

            # 根据内容尺寸生成PDF，确保不会裁切内容
            return page.pdf(width=f"{dimensions['width']}px", height=f"{dimensions['height']}px", **self.pdf_options)
        finally:
            # 关闭页面释放资源
            page.close()

    def _merge_pdfs(self, pdf_bytes_list: list[bytes], output_path: str) -> None:
        """
        将多个PDF字节流合并为单个PDF文件

        Args:
            pdf_bytes_list: PDF字节数据列表
            output_path: 输出文件路径
        """
        writer = PdfWriter()

        # 将每个PDF的字节数据添加到合并器中
        for pdf_bytes in pdf_bytes_list:
            pdf_stream = io.BytesIO(pdf_bytes)
            writer.append(pdf_stream)

        # 写入最终的PDF文件
        with open(output_path, "wb") as f:
            writer.write(f)

        # 清理资源
        writer.close()
        logger.info(f"成功合并 {len(pdf_bytes_list)} 个页面")


def main():
    """
    主程序入口

    使用示例：
    1. 处理目录（按文件名排序）:
       html_source = "docs/pdf_html"

    2. 指定文件顺序（按用户定义顺序）:
       html_source = [
           "docs/title.html",
           "docs/chapter1.html",
           "docs/chapter2.html"
       ]
    """
    html_source = "docs/pdf_html"
    output_pdf = "output.pdf"

    # ================== 执行转换 ==================
    try:
        converter = HTMLToPDFConverter()
        converter.convert(html_source, output_pdf)
    except Exception as e:
        logger.error(f"转换失败: {e}")
        raise


if __name__ == "__main__":
    main()
