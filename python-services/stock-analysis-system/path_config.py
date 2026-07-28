"""
添加项目根目录到Python路径
确保所有子模块可以正确导入
"""
import sys
import os

# 获取项目根目录(StockAnalysisSystem)
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
