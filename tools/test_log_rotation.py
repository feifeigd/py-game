"""日志轮换验证: 按天切 + 按大小切 + 保留份数"""
import glob
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.util import log as log_mod


def list_files(log_dir, service):
    return sorted(os.path.basename(p) for p in
                  glob.glob(os.path.join(log_dir, service + '_*.log')))


def main():
    tmp = tempfile.mkdtemp(prefix='logrot_')
    try:
        # 1) 按大小切: maxBytes 设 500 字节, 每条日志约 60 字节
        h = log_mod.DailySizeRotatingHandler(tmp, 'svc', max_bytes=500, backup_count=100)
        fmt = log_mod.logging.Formatter('[%(levelname)s][svc] %(message)s')
        h.setFormatter(fmt)
        logger = log_mod.logging.getLogger('test_size')
        logger.setLevel(log_mod.logging.DEBUG)
        logger.addHandler(h)
        for i in range(30):
            logger.info('message %02d padding padding padding', i)
        files = list_files(tmp, 'svc')
        assert len(files) > 1, f'expected size rotation, got {files}'
        seqs = [f for f in files if '.log' != f[-4:] or '.' in f[:-4].split('_')[1]]
        print('size rotation ok, files:', files)

        # 2) 按天切: 把 handler 的日期改成昨天, 下一条日志应换基准文件
        logger.removeHandler(h)
        h.close()
        for f in glob.glob(os.path.join(tmp, 'svc_*')):
            os.remove(f)
        h2 = log_mod.DailySizeRotatingHandler(tmp, 'svc', max_bytes=0, backup_count=100)
        h2.setFormatter(fmt)
        logger.addHandler(h2)
        # 模拟进程昨天启动、一直在写昨天的文件, 跨天后第一条日志应切到今天
        yesterday = time.strftime('%Y%m%d', time.localtime(time.time() - 86400))
        h2._date = yesterday
        h2.baseFilename = os.path.join(tmp, f'svc_{yesterday}.log')
        with open(h2.baseFilename, 'w') as fp:
            fp.write('before midnight\n')
        logger.info('after midnight')
        files = list_files(tmp, 'svc')
        assert len(files) == 2, f'expected 2 daily files, got {files}'
        today = time.strftime('%Y%m%d')
        assert f'svc_{today}.log' in files, files
        assert f'svc_{yesterday}.log' in files, files
        print('daily rotation ok, files:', files)

        # 3) 保留份数: backupCount=3, 造 6 个文件, 应只留 3 个最新的
        logger.removeHandler(h2)
        h2.close()
        for f in glob.glob(os.path.join(tmp, 'svc_*')):
            os.remove(f)
        h3 = log_mod.DailySizeRotatingHandler(tmp, 'svc', max_bytes=0, backup_count=3)
        for i in range(6):
            name = os.path.join(tmp, 'svc_202611{:02d}.log'.format(10 + i))
            with open(name, 'w') as fp:
                fp.write('old %d\n' % i)
            os.utime(name, (time.time() - (6 - i) * 100,) * 2)
        h3._cleanup()
        files = list_files(tmp, 'svc')
        assert len(files) == 3, 'expected 3 files kept, got %s' % files
        assert 'svc_20261115.log' in files and 'svc_20261110.log' not in files, files
        print('backup count ok, files:', files)

        # 4) init() 全流程: 从 config 读取 maxBytes/backupCount
        log_mod._inited = False
        log_mod._logger.handlers.clear()
        log_mod.init('master', {'log': {'path': tmp, 'isPrint': False,
                                        'maxBytes': 1024, 'backupCount': 5}})
        log_mod.info('hello rotation')
        for hd in log_mod._logger.handlers:
            hd.flush()
        files = list_files(tmp, 'master')
        assert files, 'no master log file created'
        print('init() ok, files:', files)

        print('ALL OK')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
