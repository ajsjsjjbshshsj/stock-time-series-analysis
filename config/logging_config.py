"""
统一日志配置模块

提供 get_logger(name) 工厂函数，按模块名自动路由到独立日志文件：
    data_loader.*   → logs/collector.log
    data_processor.* → logs/processor.log
    analysis.*      → logs/model.log
    database.*      → logs/database.log
    visualization.* → logs/dashboard.log
    main / 其他     → logs/model.log

所有 logger 同时输出到控制台（INFO 级别）。
文件 handler 使用 RotatingFileHandler 按大小滚动。

向后兼容：模块顶层保留 ``logger`` 变量供旧代码 ``from config.logging_config import logger`` 使用。
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from config.settings import (
    LOG_LEVEL,
    LOG_DIR,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
)

# ── 模块 → 日志文件映射 ──────────────────────────────────────

_MODULE_LOG_MAP = {
    'data_loader':    'collector.log',
    'data_processor': 'processor.log',
    'analysis':       'model.log',
    'database':       'database.log',
    'visualization':  'dashboard.log',
    'main':           'model.log',
}

# ── 内部缓存 ─────────────────────────────────────────────────

_initialized_loggers: dict[str, logging.Logger] = {}
_log_dir_created = False

_LOG_FMT = '%(asctime)s | %(name)-28s | %(levelname)-7s | %(message)s'
_DATE_FMT = '%Y-%m-%d %H:%M:%S'
_LEVEL = getattr(logging, LOG_LEVEL.upper(), logging.INFO)


def _ensure_log_dir():
    """确保日志目录存在（只执行一次）"""
    global _log_dir_created
    if not _log_dir_created and LOG_DIR:
        os.makedirs(LOG_DIR, exist_ok=True)
        _log_dir_created = True


def _resolve_log_file(module_name: str) -> str:
    """根据模块 __name__ 确定日志文件名"""
    top = module_name.split('.')[0]
    filename = _MODULE_LOG_MAP.get(top, 'model.log')
    return os.path.join(LOG_DIR, filename)


def get_logger(name: str = 'main', level: int | None = None) -> logging.Logger:
    """
    获取按模块路由的 logger。

    Args:
        name:  通常传入 ``__name__``，如 ``data_loader.collector``
        level: 可选覆盖日志级别

    Returns:
        配置好的 Logger 实例（带控制台 + 文件双输出）
    """
    if name in _initialized_loggers:
        return _initialized_loggers[name]

    _ensure_log_dir()

    logger = logging.getLogger(name)
    logger.setLevel(level or _LEVEL)
    logger.propagate = False  # 防止重复输出到 root logger

    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    # ── 控制台 handler ──
    console = logging.StreamHandler()
    console.setLevel(level or _LEVEL)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # ── 文件 handler（RotatingFileHandler）──
    log_file = _resolve_log_file(name)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8',
    )
    file_handler.setLevel(level or _LEVEL)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _initialized_loggers[name] = logger
    return logger


# ── 向后兼容：旧代码 ``from config.logging_config import logger`` ──

logger = get_logger('main')
