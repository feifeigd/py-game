"""日志: 按服务名区分前缀，同时输出到控制台与文件

文件按天 + 按大小双轮换，文件名形如:
    game1_20261123.log       当天当前写入的文件
    game1_20261123.1.log     当天因超大小切出的第 1 份
旧文件超过 backupCount 份时按修改时间从旧到新删除。
"""
import glob
import logging
import os
import sys
import time
from logging.handlers import BaseRotatingHandler

_logger = logging.getLogger('pygame')
_logger.setLevel(logging.DEBUG)
_inited = False

# 单文件默认 50MB，超过即切；保留最近 30 份
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 30


class DailySizeRotatingHandler(BaseRotatingHandler):
    """按天切换基准文件；当天内超过 maxBytes 时切出 .N 序号文件；总量超 backupCount 删最旧。"""

    def __init__(self, log_dir, service_name, max_bytes, backup_count):
        self._log_dir = log_dir
        self._service = service_name
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._date = self._today()
        super().__init__(self._base_name(), 'a', encoding='utf-8', delay=True)

    @staticmethod
    def _today():
        return time.strftime('%Y%m%d')

    def _base_name(self):
        return os.path.join(
            self._log_dir, f'{self._service}_{self._date}.log')

    def shouldRollover(self, record):
        if self._today() != self._date:
            return True
        if self._max_bytes > 0 and self.stream is not None:
            self.stream.seek(0, os.SEEK_END)
            if self.stream.tell() + len(self.format(record)) >= self._max_bytes:
                return True
        return False

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        today = self._today()
        if today != self._date:
            # 跨天: 换基准文件名，序号重新从 1 计
            self._date = today
            self.baseFilename = self._base_name()
        elif os.path.exists(self.baseFilename):
            # 同一天超大小: 当前文件改名带递增序号
            n = 1
            while os.path.exists(self._seq_name(n)):
                n += 1
            os.rename(self.baseFilename, self._seq_name(n))
        self._cleanup()
        if not self.delay:
            self.stream = self._open()

    def _seq_name(self, n):
        root, ext = os.path.splitext(self.baseFilename)
        return f'{root}.{n}{ext}'

    def _cleanup(self):
        if self._backup_count <= 0:
            return
        files = glob.glob(os.path.join(self._log_dir, self._service + '_*.log'))
        if len(files) <= self._backup_count:
            return
        files.sort(key=os.path.getmtime)
        for path in files[:len(files) - self._backup_count]:
            try:
                os.remove(path)
            except OSError:
                pass


def init(service_name, config):
    global _inited
    if _inited:
        return
    _inited = True
    log_config = (config or {}).get('log', {})
    level = getattr(logging, str(log_config.get('level', 'DEBUG')).upper(), logging.DEBUG)
    _logger.setLevel(level)

    fmt = logging.Formatter(
        '[%(asctime)s][%(levelname)s][' + service_name + '] %(message)s',
        '%Y-%m-%d %H:%M:%S')

    if log_config.get('isPrint', True):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        _logger.addHandler(sh)

    log_dir = log_config.get('path', './log')
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        fh = DailySizeRotatingHandler(
            log_dir, service_name,
            int(log_config.get('maxBytes', DEFAULT_MAX_BYTES)),
            int(log_config.get('backupCount', DEFAULT_BACKUP_COUNT)))
        fh.setFormatter(fmt)
        _logger.addHandler(fh)


def debug(msg, *args):
    _logger.debug(msg % args if args else msg)


def info(msg, *args):
    _logger.info(msg % args if args else msg)


def notice(msg, *args):
    _logger.info(msg % args if args else msg)


def warning(msg, *args):
    _logger.warning(msg % args if args else msg)


def error(msg, *args):
    _logger.error(msg % args if args else msg)


def exception(msg='', *args):
    _logger.exception(msg % args if args else msg)
