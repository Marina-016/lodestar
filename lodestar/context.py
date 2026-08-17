"""Workspace：每个运行会话持有 DB 连接与配置，供 Agent / Tools 共享。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from lodestar.config import Config
from lodestar.memory.db import open_db


@dataclass
class Workspace:
    config: Config
    conn = None

    def __post_init__(self):
        self.config.ensure_dirs()
        self.conn = open_db(self.config.db_path)

    def new_task_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
