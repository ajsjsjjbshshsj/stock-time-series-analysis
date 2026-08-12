# -*- coding: utf-8 -*-
"""
基线对账测试

覆盖：
    - 重复运行是否产生相同基线结果
    - 基线报告文件存在且格式正确
    - 关键指标快照不为空
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASELINE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'models', 'baseline')


class TestBaselineReport:
    """基线报告测试。"""

    def test_report_exists(self):
        report_path = os.path.join(BASELINE_DIR, 'baseline_report.json')
        if not os.path.exists(report_path):
            pytest.skip("基线报告不存在，请先运行 python scripts/build_baseline.py")

    def test_report_structure(self):
        report_path = os.path.join(BASELINE_DIR, 'baseline_report.json')
        if not os.path.exists(report_path):
            pytest.skip("基线报告不存在")

        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)

        # 必需字段
        assert 'version' in report
        assert 'created_at' in report
        assert 'data_stats' in report
        assert 'key_indicators_snapshot' in report
        assert 'stock_pool' in report

    def test_data_stats_valid(self):
        report_path = os.path.join(BASELINE_DIR, 'baseline_report.json')
        if not os.path.exists(report_path):
            pytest.skip("基线报告不存在")

        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)

        stats = report['data_stats']
        assert stats['raw_rows'] > 0
        assert stats['cleaned_rows'] > 0
        assert stats['cleaned_rows'] <= stats['raw_rows']
        assert stats['duplicates'] >= 0
        assert stats['stocks_with_features'] > 0

    def test_stock_pool_size(self):
        report_path = os.path.join(BASELINE_DIR, 'baseline_report.json')
        if not os.path.exists(report_path):
            pytest.skip("基线报告不存在")

        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)

        assert len(report['stock_pool']) == 20


class TestBaselineReproducibility:
    """基线可复现性测试。"""

    def test_panel_file_hash_consistent(self):
        """面板文件哈希应与报告中一致。"""
        report_path = os.path.join(BASELINE_DIR, 'baseline_report.json')
        panel_path = os.path.join(BASELINE_DIR, 'baseline_panel.parquet')

        if not os.path.exists(report_path) or not os.path.exists(panel_path):
            pytest.skip("基线文件不存在")

        import hashlib
        with open(panel_path, 'rb') as f:
            current_hash = hashlib.md5(f.read()).hexdigest()

        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)

        assert current_hash == report['panel_hash'], (
            "面板文件哈希不匹配！基线数据可能被意外修改。"
            "请重新运行 python scripts/build_baseline.py"
        )


class TestBaselineKeyIndicators:
    """关键指标快照测试。"""

    def test_indicators_not_empty(self):
        report_path = os.path.join(BASELINE_DIR, 'baseline_report.json')
        if not os.path.exists(report_path):
            pytest.skip("基线报告不存在")

        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)

        snapshot = report['key_indicators_snapshot']
        assert len(snapshot) > 0, "关键指标快照为空"

        # 至少有一个股票有 MA5 值
        has_ma5 = any(
            v.get('ma5') is not None
            for v in snapshot.values()
        )
        assert has_ma5, "所有股票的 MA5 均为 None"
