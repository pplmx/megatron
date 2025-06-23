#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML页面转PDF合并工具 (纯Playwright版 - 动态页面尺寸)
- 自动检测每个HTML内容的尺寸，避免裁剪
"""
import os
import shutil
import tempfile
from typing import List
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


class HTMLToPDFConverter:
    """使用Playwright将多个HTML文件合并为一个PDF，自动适应内容尺寸"""

    def __init__(self):
        logger.info("Playwright PDF转换器已初始化 (动态尺寸模式)。")

    def merge_html_to_pdf(self, html_files: List[str], output_pdf: str) -> str:
        """
        将多个HTML文件按顺序转换并合并为一个PDF。
        """
        if not html_files:
            raise ValueError("HTML文件列表不能为空。")

        for html_file in html_files:
            if not os.path.exists(html_file):
                raise FileNotFoundError(f"HTML文件不存在，请检查路径: {html_file}")

        logger.info(f"开始转换 {len(html_files)} 个HTML文件:")
        for i, html_file in enumerate(html_files, 1):
            logger.info(f"  {i:02d}. {os.path.basename(html_file)}")

        temp_dir = tempfile.mkdtemp()
        pdf_files = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                logger.info("Chromium浏览器实例已启动。")

                for i, html_file in enumerate(html_files):
                    temp_pdf_path = os.path.join(temp_dir, f"temp_{i:03d}.pdf")
                    self._convert_single_html(browser, html_file, temp_pdf_path)
                    pdf_files.append(temp_pdf_path)

                browser.close()
                logger.info("Chromium浏览器实例已关闭。")

            self._merge_pdfs(pdf_files, output_pdf)
            return output_pdf

        except Exception as e:
            logger.error(f"在转换过程中发生严重错误: {e}", exc_info=True)
            raise
        finally:
            logger.info(f"正在清理临时文件目录: {temp_dir}")
            shutil.rmtree(temp_dir)

    def _convert_single_html(self, browser: Browser, html_path: str, output_path: str):
        """
        转换单个HTML。先测量内容尺寸，再生成大小匹配的PDF。
        """
        page = browser.new_page()
        absolute_path = os.path.abspath(html_path)
        page.goto(f"file:///{absolute_path}")
        page.wait_for_load_state('networkidle')

        # --- 核心修改点 ---
        # 执行JS获取内容的完整渲染尺寸（像素）
        dimensions = page.evaluate('''() => {
            return {
                width: document.documentElement.scrollWidth,
                height: document.documentElement.scrollHeight
            }
        }''')

        page_width = f"{dimensions['width']}px"
        page_height = f"{dimensions['height']}px"

        logger.info(f"  -> 正在渲染 {os.path.basename(html_path)} | 检测到尺寸: {page_width} x {page_height}")

        # 使用测量出的尺寸生成PDF，确保内容完全容纳
        page.pdf(
            path=output_path,
            width=page_width,
            height=page_height,
            print_background=True,
            page_ranges="1" # 确保只生成一页，避免因微小误差产生空白页
        )

        page.close()

    def _merge_pdfs(self, pdf_files: List[str], output_path: str):
        """
        使用pypdf合并多个PDF文件。
        """
        merger = PdfWriter()
        logger.info("开始合并所有临时PDF文件...")

        for pdf_file in pdf_files:
            merger.append(pdf_file)

        with open(output_path, 'wb') as f:
            merger.write(f)
        merger.close()

        logger.info(f"成功合并 {len(pdf_files)} 个文件到: {output_path}")


def main():
    """主执行函数"""
    html_files = [
        "docs/pdf_html/00_title_slide.html",
        "docs/pdf_html/01_agenda.html",
        "docs/pdf_html/02_what_is_megatron.html",
        "docs/pdf_html/03_why_megatron.html",
        "docs/pdf_html/04_megatron_features.html",
        "docs/pdf_html/05_parallel_overview.html",
        "docs/pdf_html/06_data_parallelism.html",
        "docs/pdf_html/07_pipeline_parallelism.html",
        "docs/pdf_html/08_tensor_parallelism.html",
        "docs/pdf_html/09_sequence_parallelism.html",
        "docs/pdf_html/10_ptdp_strategy.html",
        "docs/pdf_html/11_tp_deep_dive.html",
        "docs/pdf_html/12_column_parallel.html",
        "docs/pdf_html/13_row_parallel.html",
        "docs/pdf_html/14_communication_patterns.html",
        "docs/pdf_html/15_attention_implementation.html",
        "docs/pdf_html/16_mlp_implementation.html",
        "docs/pdf_html/17_moe_implementation.html",
        "docs/pdf_html/18_performance_optimization.html",
        "docs/pdf_html/19_best_practices.html",
    ]
    output_dir = "final_document_autosized.pdf"

    try:
        converter = HTMLToPDFConverter()
        converter.merge_html_to_pdf(html_files, output_dir)
        logger.info(f"🎉 全部任务完成！最终文件已保存为: {output_dir}")
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"执行失败: {e}")
    except Exception:
        logger.error("发生了未知错误，请检查上面的日志获取详细信息。")


if __name__ == "__main__":
    main()
