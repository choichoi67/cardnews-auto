from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def exclusive_runtime(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('같은 작업 공간의 서버 또는 봇 수신기가 이미 실행 중입니다. 기존 작업 종료 후 전환하세요.') from None
        yield
    finally:
        handle.close()
