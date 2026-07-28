"""
采集服务配置

优先从环境变量 / .env 文件加载，缺失必填项时给出明确提示。
支持从服务自身目录或项目根目录加载 .env。
"""

import os
import sys

from dotenv import load_dotenv

# ── 加载 .env ────────────────────────────────────────────────
# 尝试多个路径：服务根目录 → 项目根目录
# __file__ = .../services/python-collector/app/config.py
_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))           # .../app/
_SERVICE_ROOT = os.path.abspath(os.path.join(_CONFIG_DIR, '..'))   # .../python-collector/
_PROJECT_ROOT = os.path.abspath(os.path.join(_CONFIG_DIR, '..', '..', '..'))  # .../StockAnalysisSystem/

for env_path in [
    os.path.join(_SERVICE_ROOT, '.env'),
    os.path.join(_PROJECT_ROOT, '.env'),
]:
    if os.path.isfile(env_path):
        load_dotenv(env_path)
        break


# ── 敏感配置校验 ─────────────────────────────────────────────

def _require_env(key: str) -> str:
    """读取必须存在的环境变量，缺失时抛出明确提示。"""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"[配置缺失] 环境变量 {key} 未设置。"
            f"请在 .env 文件中配置，参考 .env.example"
        )
    return value


def _optional_env(key: str, default: str) -> str:
    return os.getenv(key, default)


# ── 脱敏工具 ─────────────────────────────────────────────────

def mask_secret(value: str, visible: int = 4) -> str:
    """将敏感值仅保留前 N 位，其余用 * 替换。"""
    if not value or len(value) <= visible:
        return '****'
    return value[:visible] + '*' * (len(value) - visible)


# ── 数据库配置 ───────────────────────────────────────────────

DB_PASSWORD = _require_env('DB_PASSWORD')

DATABASE_CONFIG = {
    'host': _optional_env('DB_HOST', 'localhost'),
    'port': _optional_env('DB_PORT', '3306'),
    'user': _optional_env('DB_USER', 'root'),
    'password': DB_PASSWORD,
    'database': _optional_env('DB_NAME', 'stock_analysis'),
    'charset': 'utf8mb4',
    'connect_timeout': 10,
    'pool_timeout': 30,
    'pool_pre_ping': True,
    'pool_recycle': 3600,
}


# ── 数据源配置 ───────────────────────────────────────────────

TUSHARE_TOKEN = _require_env('TUSHARE_TOKEN')

# 采集数据源选择：tushare / akshare
COLLECTOR_SOURCE = _optional_env('COLLECTOR_SOURCE', 'tushare')


# ── 采集参数 ─────────────────────────────────────────────────

COLLECTION_CONFIG = {
    'default_start_date': _optional_env('COLLECTION_START_DATE', '20220101'),
    'batch_size': int(_optional_env('COLLECTION_BATCH_SIZE', '100')),
    'retry_times': int(_optional_env('COLLECTION_RETRY_TIMES', '3')),
    'retry_delay': float(_optional_env('COLLECTION_RETRY_DELAY', '1.0')),
    'request_interval': float(_optional_env('COLLECTION_REQUEST_INTERVAL', '0.5')),
    'max_retry_count': int(_optional_env('COLLECTION_MAX_RETRY_COUNT', '5')),
}


# ── 日志配置 ─────────────────────────────────────────────────

LOG_LEVEL = _optional_env('LOG_LEVEL', 'INFO')
LOG_DIR = _optional_env('COLLECTOR_LOG_DIR', 'logs')
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5


# ── 安全输出 ─────────────────────────────────────────────────

def safe_config_repr() -> str:
    """返回配置摘要字符串，敏感字段已脱敏。"""
    return (
        "DATABASE: host=%s, port=%s, user=%s, password=%s, database=%s | "
        "SOURCE: %s | TUSHARE_TOKEN: %s"
        % (
            DATABASE_CONFIG['host'],
            DATABASE_CONFIG['port'],
            DATABASE_CONFIG['user'],
            mask_secret(DB_PASSWORD),
            DATABASE_CONFIG['database'],
            COLLECTOR_SOURCE,
            mask_secret(TUSHARE_TOKEN),
        )
    )
