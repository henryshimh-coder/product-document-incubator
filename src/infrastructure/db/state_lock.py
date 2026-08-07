"""project 级状态锁：应用运行期持共享锁，重置/恢复脚本持排他锁。

评审 T12 整改第二轮 Important 修复：此前只有重置脚本拿锁，应用运行时不持锁，
应用保持旧数据库连接时重置仍成功，旧连接与新连接看到两个不同数据库状态，
形成分裂。现在 `build_container` 在返回可用容器前持 `LOCK_SH`，`restore_snapshot`
持 `LOCK_EX|LOCK_NB`；任一方在运行时另一方 fail closed（应用 `APP_STATE_LOCKED`，
重置 `RESET_LOCKED`）。flock 随文件描述符关闭或进程退出自动释放。
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

STATE_LOCK_REL = Path("data/local_state/.reset.lock")


def acquire_shared(project_root: Path) -> int:
    """应用运行期持共享锁；重置进行（排他锁占用）时 fail closed。

    返回锁文件描述符，交由调用方在容器关闭或进程退出时释放。
    """
    lock_path = project_root / STATE_LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise RuntimeError(f"APP_STATE_LOCKED:{project_root}") from error
    return descriptor


def release(descriptor: int | None) -> None:
    """释放共享锁；重复释放安全（描述符已失效时静默忽略）。"""
    if descriptor is None:
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(descriptor)
    except OSError:
        pass
