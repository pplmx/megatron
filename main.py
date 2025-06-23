#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML页面转PDF合并工具 (纯Playwright版 - 动态页面尺寸 - 内存优化 - 目录支持)
- 自动检测HTML内容尺寸，避免裁剪
- 全程在内存中操作PDF，性能更佳
- 支持直接传入目录，自动按文件名排序处理
"""
import os
import io
from typing import List, Dict, Union
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 尝试导入必要的库
try:
    from playwright.sync_api import sync_playwright, Browser
    from pypdf import PdfWriter
except ImportError:
    logger.error("必需的库未安装。请运行: pip install playwright pypdf")
    logger.error("并且不要忘记初始化Playwright: playwright install")
    exit(1)

# --- 配置区 ---
# 现在可以指定一个目录，或者一个手动排序的文件列表
# 示例1：指定目录
HTML_SOURCE = "docs/pdf_html"

# 示例2：手动指定文件列表 (如果需要特定顺序)
# HTML_SOURCE = [
#     "docs/pdf_html/00_title_slide.html",
#     "docs/pdf_html/01_agenda.html",
#     # ...
# ]

OUTPUT_PDF_FILE = "final_document_from_directory.pdf"

class HTMLToPDFConverter:
    """使用Playwright将多个HTML文件合并为一个PDF，自动适应内容尺寸，并在内存中处理。"""

    def __init__(self):
        logger.info("Playwright PDF转换器已初始化 (内存优化/目录支持模式)。")

    def merge_html_to_pdf(self, html_source: Union[str, List[str]], output_pdf: str):
        """
        将多个HTML文件按顺序转换并合并为一个PDF。

        Args:
            html_source: 可以是一个存放HTML文件的目录路径(str)，或一个文件路径列表(List[str])。
            output_pdf: 输出PDF文件的路径。
        """
        # --- 核心修改点：处理输入源 ---
        if isinstance(html_source, str) and os.path.isdir(html_source):
            logger.info(f"检测到输入为目录: '{html_source}'. 正在扫描HTML文件...")
            try:
                # 筛选出html文件并按文件名排序
                filenames = sorted([
                    f for f in os.listdir(html_source)
                    if f.lower().endswith(('.html', '.htm'))
                ])
                if not filenames:
                    raise FileNotFoundError(f"在目录 '{html_source}' 中没有找到任何HTML文件。")
                # 构建完整路径列表
                html_files = [os.path.join(html_source, f) for f in filenames]
                logger.info(f"找到 {len(html_files)} 个文件，将按字母顺序处理。")
            except FileNotFoundError as e:
                logger.error(e)
                raise
        elif isinstance(html_source, list):
            logger.info("检测到输入为手动指定的文件列表。")
            html_files = html_source
        else:
            raise TypeError("输入源 'html_source' 必须是目录路径(str)或文件列表(list)。")

        # --- 后续逻辑保持不变 ---
        if not html_files:
            raise ValueError("最终要处理的HTML文件列表为空。")

        for html_file in html_files:
            if not os.path.exists(html_file):
                raise FileNotFoundError(f"HTML文件不存在，请检查路径: {html_file}")

        logger.info(f"开始转换 {len(html_files)} 个HTML文件:")
        for i, html_file in enumerate(html_files, 1):
            logger.info(f"  {i:02d}. {os.path.basename(html_file)}")

        pdf_bytes_list = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                logger.info("Chromium浏览器实例已启动。")

                for html_file in html_files:
                    pdf_bytes = self._convert_single_html_to_bytes(browser, html_file)
                    pdf_bytes_list.append(pdf_bytes)

                browser.close()
                logger.info("Chromium浏览器实例已关闭。")

            self._merge_pdfs_from_bytes(pdf_bytes_list, output_pdf)

        except Exception as e:
            logger.error(f"在转换过程中发生严重错误: {e}", exc_info=True)
            raise

    def _convert_single_html_to_bytes(self, browser: Browser, html_path: str) -> bytes:
        """转换单个HTML到PDF字节流，并返回。"""
        page = browser.new_page()
        absolute_path = os.path.abspath(html_path)
        page.goto(f"file:///{absolute_path}")
        page.wait_for_load_state('networkidle')

        # 在测量尺寸之前，强制移除 html 和 body 的内外边距
        page.add_style_tag(content="""
                html, body {
                    margin: 0 !important;
                    padding: 0 !important;
                }
            """)

        # 现在进行测量，此时的尺寸将是不含任何外边距的纯内容尺寸
        dimensions: Dict[str, float] = page.evaluate('''() => {
            return {
                width: Math.ceil(document.documentElement.scrollWidth),
                height: Math.ceil(document.documentElement.scrollHeight)
            }
        }''')

        page_width = f"{dimensions['width']}px"
        page_height = f"{dimensions['height']}px"

        logger.info(f"  -> 正在渲染 {os.path.basename(html_path)} | 检测到尺寸: {page_width} x {page_height}")

        pdf_content = page.pdf(
            width=page_width,
            height=page_height,
            print_background=True,
            page_ranges="1"
        )
        page.close()
        return pdf_content

    def _merge_pdfs_from_bytes(self, pdf_bytes_list: List[bytes], output_path: str):
        """从字节流列表合并PDF。"""
        merger = PdfWriter()
        logger.info("开始从内存中合并所有PDF页面...")

        for pdf_bytes in pdf_bytes_list:
            pdf_stream = io.BytesIO(pdf_bytes)
            merger.append(pdf_stream)

        with open(output_path, 'wb') as f:
            merger.write(f)
        merger.close()
        logger.info(f"成功合并 {len(pdf_bytes_list)} 个页面到: {output_path}")

def main():
    """主执行函数"""
    try:
        converter = HTMLToPDFConverter()
        converter.merge_html_to_pdf(HTML_SOURCE, OUTPUT_PDF_FILE)
        logger.info(f"🎉 全部任务完成！最终文件已保存为: {OUTPUT_PDF_FILE}")
    except (FileNotFoundError, ValueError, TypeError) as e:
        logger.error(f"执行失败: {e}")
    except Exception:
        logger.error("发生了未知错误，请检查上面的日志获取详细信息。")

if __name__ == "__main__":
    main()
