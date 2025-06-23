#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML页面转PDF合并工具 (纯Playwright版)
- 不修改原始HTML的边距或样式
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
    from playwright.sync_api import sync_playwright, Page, Playwright
    from pypdf import PdfWriter
except ImportError:
    logger.error("必需的库未安装。请运行: pip install playwright pypdf")
    logger.error("并且不要忘记初始化Playwright: playwright install")
    # 退出程序，因为无法继续
    exit(1)


class HTMLToPDFConverter:
    """使用Playwright将多个HTML文件合并为一个PDF"""

    def __init__(self):
        logger.info("Playwright PDF转换器已初始化。")

    def merge_html_to_pdf(self, html_files: List[str], output_pdf: str) -> str:
        """
        将多个HTML文件按顺序转换并合并为一个PDF。

        Args:
            html_files (List[str]): 按顺序排列的HTML文件路径列表。
            output_pdf (str): 输出PDF文件的路径。

        Returns:
            str: 成功生成的PDF文件路径。
        """
        if not html_files:
            raise ValueError("HTML文件列表不能为空。")

        # 预先检查所有文件是否存在，避免中途失败
        for html_file in html_files:
            if not os.path.exists(html_file):
                raise FileNotFoundError(f"HTML文件不存在，请检查路径: {html_file}")

        logger.info(f"开始转换 {len(html_files)} 个HTML文件:")
        for i, html_file in enumerate(html_files, 1):
            logger.info(f"  {i:02d}. {os.path.basename(html_file)}")

        # 创建一个临时目录来存放中间生成的单页PDF
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

            # 第二步：合并所有临时PDF文件
            self._merge_pdfs(pdf_files, output_pdf)

            return output_pdf

        except Exception as e:
            logger.error(f"在转换过程中发生严重错误: {e}", exc_info=True)
            raise
        finally:
            # 第三步：清理临时文件和目录
            logger.info(f"正在清理临时文件目录: {temp_dir}")
            shutil.rmtree(temp_dir)

    def _convert_single_html(self, browser, html_path: str, output_path: str):
        """
        使用共享的浏览器实例转换单个HTML文件为PDF。
        """
        page = browser.new_page()
        absolute_path = os.path.abspath(html_path)

        # 使用 file:/// 协议以确保正确加载本地资源
        page.goto(f"file:///{absolute_path}")
        # 等待所有网络活动停止，确保页面（包括图片、字体）完全加载
        page.wait_for_load_state('networkidle')

        logger.info(f"  -> 正在渲染 {os.path.basename(html_path)} ...")

        # 生成PDF。不指定format, margin等参数，以尊重HTML/CSS中的设置。
        page.pdf(
            path=output_path,
            print_background=True  # 保留背景色和背景图片
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

    # --- 用户配置 ---

    # 1. 把您要转换的HTML文件按顺序放在这个列表里
    HTML_FILES_IN_ORDER = [
        "docs/agenda.html",
        "docs/agenda.html",
        # "docs/page3.html", # 可以继续添加更多文件
    ]

    # 2. 指定最终输出的PDF文件名
    OUTPUT_PDF_NAME = "final_document.pdf"

    # ------------------

    try:
        converter = HTMLToPDFConverter()
        converter.merge_html_to_pdf(HTML_FILES_IN_ORDER, OUTPUT_PDF_NAME)
        logger.info(f"🎉 全部任务完成！最终文件已保存为: {OUTPUT_PDF_NAME}")
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"执行失败: {e}")
    except Exception:
        # 上层函数已经记录了详细错误，这里只给一个通用提示
        logger.error("发生了未知错误，请检查上面的日志获取详细信息。")


if __name__ == "__main__":
    main()
