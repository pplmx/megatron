#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML页面转PDF合并工具
手动配置页面顺序，简单直接
"""

import os
from typing import List
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class HTMLToPDFConverter:
    """HTML转PDF转换器"""
    
    def __init__(self, method='weasyprint'):
        """
        初始化转换器
        
        Args:
            method: 转换方法 ('weasyprint', 'pdfkit', 'playwright')
        """
        self.method = method
        self._setup_converter()
    
    def _setup_converter(self):
        """设置转换器"""
        if self.method == 'weasyprint':
            try:
                from weasyprint import HTML, CSS
                self.HTML = HTML
                self.CSS = CSS
                logger.info("使用 WeasyPrint 转换器")
            except ImportError:
                raise ImportError("请安装 weasyprint: pip install weasyprint")
                
        elif self.method == 'pdfkit':
            try:
                import pdfkit
                from PyPDF2 import PdfMerger
                self.pdfkit = pdfkit
                self.PdfMerger = PdfMerger
                logger.info("使用 PDFKit 转换器")
            except ImportError:
                raise ImportError("请安装依赖: pip install pdfkit PyPDF2")
                
        elif self.method == 'playwright':
            try:
                from playwright.sync_api import sync_playwright
                from PyPDF2 import PdfMerger
                self.sync_playwright = sync_playwright
                self.PdfMerger = PdfMerger
                logger.info("使用 Playwright 转换器")
            except ImportError:
                raise ImportError("请安装依赖: pip install playwright PyPDF2")
    
    def merge_html_to_pdf(self, html_files: List[str], output_pdf: str) -> str:
        """
        将多个HTML文件按顺序转换并合并为一个PDF
        
        Args:
            html_files: HTML文件路径列表（按顺序排列）
            output_pdf: 输出PDF文件路径
            
        Returns:
            合并后的PDF文件路径
        """
        if not html_files:
            raise ValueError("HTML文件列表不能为空")
        
        # 检查文件是否存在
        for html_file in html_files:
            if not os.path.exists(html_file):
                raise FileNotFoundError(f"HTML文件不存在: {html_file}")
        
        logger.info(f"开始转换 {len(html_files)} 个HTML文件:")
        for i, html_file in enumerate(html_files, 1):
            logger.info(f"  {i:02d}. {html_file}")
        
        if self.method == 'weasyprint':
            return self._merge_with_weasyprint(html_files, output_pdf)
        else:
            return self._merge_with_separate_conversion(html_files, output_pdf)
    
    def _merge_with_weasyprint(self, html_files: List[str], output_pdf: str) -> str:
        """使用WeasyPrint直接合并多个HTML"""
        try:
            # 创建合并的HTML内容
            combined_html = "<html><head><meta charset='utf-8'></head><body>"
            
            for i, html_file in enumerate(html_files):
                with open(html_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 提取body内容
                    body_start = content.find('<body')
                    if body_start != -1:
                        body_start = content.find('>', body_start) + 1
                        body_end = content.rfind('</body>')
                        if body_end != -1:
                            body_content = content[body_start:body_end]
                        else:
                            body_content = content[body_start:]
                    else:
                        body_content = content
                
                # 添加分页符（除了第一页）
                if i > 0:
                    combined_html += '<div style="page-break-before: always;"></div>'
                
                combined_html += body_content
            
            combined_html += "</body></html>"
            
            # 转换为PDF
            css_style = self.CSS(string='''
                @page {
                    margin: 1cm;
                    size: A4;
                }
                body {
                    font-family: "DejaVu Sans", sans-serif;
                }
            ''')
            
            html_doc = self.HTML(string=combined_html)
            html_doc.write_pdf(output_pdf, stylesheets=[css_style])
            
            logger.info(f"已合并 {len(html_files)} 个HTML文件到: {output_pdf}")
            return output_pdf
            
        except Exception as e:
            logger.error(f"WeasyPrint合并失败: {e}")
            raise
    
    def _merge_with_separate_conversion(self, html_files: List[str], output_pdf: str) -> str:
        """先单独转换后合并的方式"""
        try:
            import tempfile
            import shutil
            
            # 创建临时目录
            temp_dir = tempfile.mkdtemp()
            pdf_files = []
            
            try:
                # 转换每个HTML文件
                for i, html_file in enumerate(html_files):
                    temp_pdf = os.path.join(temp_dir, f"temp_{i:03d}.pdf")
                    self._convert_single_html(html_file, temp_pdf)
                    pdf_files.append(temp_pdf)
                
                # 合并PDF文件
                merger = self.PdfMerger()
                for pdf_file in pdf_files:
                    merger.append(pdf_file)
                
                merger.write(output_pdf)
                merger.close()
                
                logger.info(f"已合并 {len(html_files)} 个HTML文件到: {output_pdf}")
                return output_pdf
                
            finally:
                # 清理临时目录
                shutil.rmtree(temp_dir)
            
        except Exception as e:
            logger.error(f"合并失败: {e}")
            raise
    
    def _convert_single_html(self, html_path: str, output_path: str) -> str:
        """转换单个HTML文件为PDF"""
        if self.method == 'pdfkit':
            options = {
                'page-size': 'A4',
                'margin-top': '1cm',
                'margin-right': '1cm',
                'margin-bottom': '1cm',
                'margin-left': '1cm',
                'encoding': "UTF-8",
                'no-outline': None,
                'enable-local-file-access': None
            }
            self.pdfkit.from_file(html_path, output_path, options=options)
            
        elif self.method == 'playwright':
            with self.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"file://{os.path.abspath(html_path)}")
                page.pdf(
                    path=output_path,
                    format='A4',
                    margin={
                        'top': '1cm',
                        'right': '1cm',
                        'bottom': '1cm',
                        'left': '1cm'
                    }
                )
                browser.close()
        
        logger.info(f"已转换: {html_path} -> {output_path}")
        return output_path


def main():
    """使用示例"""
    converter = HTMLToPDFConverter(method='weasyprint')  # 推荐使用weasyprint
    
    # 手动配置HTML文件顺序
    html_files = [
        "agenda.html",        # 封面
        "agenda.html",        # 封面
    ]
    
    try:
        converter.merge_html_to_pdf(html_files, "output.pdf")
        print("PDF合并完成!")
    except Exception as e:
        print(f"转换失败: {e}")


if __name__ == "__main__":
    main()
