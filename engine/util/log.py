"""日志: 按服务名区分前缀，同时输出到控制台与文件"""
import logging
import os
import sys
import time

_logger = logging.getLogger('pygame')
_logger.setLevel(logging.DEBUG)
_inited = False


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
        fh = logging.FileHandler(
            os.path.join(log_dir, "{}_{}.log".format(service_name, time.strftime('%Y%m%d'))),
            encoding='utf-8')
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
