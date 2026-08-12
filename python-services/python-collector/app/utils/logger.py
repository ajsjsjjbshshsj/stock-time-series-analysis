"""
采集服务统一日志

日志按模块名路由到独立文件：
    app.collectors.*  → logs/collector.log
    app.jobs.*        → logs/collector.log
    app.repositories.*→ logs/collector.log
    其他              → logs/collector.log

所有 logger 同时输出到控制台（INFO 级别）。
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import LOG_LEVEL, LOG_DIR, LOG_MAX_BYTES, LOG_BACKUP_COUNT

_initialized_loggers: dict = {}
_log_dir_created = False

_LOG_FMT = '%(asctime)s | %(name)-32s | %(levelname)-7s | %(message)s'
_DATE_FMT = '%Y-%m-%d %H:%M:%S'
_LEVEL = getattr(logging, LOG_LEVEL.upper(), logging.INFO)


def _ensure_log_dir():
    global _log_dir_created
    if not _log_dir_created and LOG_DIR:
        os.makedirs(LOG_DIR, exist_ok=True)
        _log_dir_created = True


def get_logger(name: str = 'collector', level: int | None = None) -> logging.Logger:
    """
    获取 logger，所有采集服务日志统一输出到 collector.log。

    Args:
        name: 模块名，通常 __name__
        level: 可选覆盖日志级别
    """
    if name in _initialized_loggers:
        return _initialized_loggers[name]

    _ensure_log_dir()

    logger = logging.getLogger(name)
    logger.setLevel(level or _LEVEL)
    logger.propagate = False

    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    # 控制台
    console = logging.StreamHandler()
    console.setLevel(level or _LEVEL)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 文件
    log_file = os.path.join(LOG_DIR, 'collector.log')
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
