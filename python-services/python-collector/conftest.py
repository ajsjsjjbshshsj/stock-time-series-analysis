"""
测试配置 — 在 import app 模块之前设置必要的环境变量。

pytest 自动加载此文件（位于 tests 目录的上级）。
"""

import os

# 为单元测试设置虚拟环境变量（不会连接真实数据库）
os.environ.setdefault('DB_PASSWORD', 'test_password')
os.environ.setdefault('TUSHARE_TOKEN', 'test_token_for_unit_tests')
os.environ.setdefault('DB_HOST', 'localhost')
os.environ.setdefault('DB_PORT', '3306')
os.environ.setdefault('DB_NAME', 'test_stock_analysis')
os.environ.setdefault('DB_USER', 'test_user')
