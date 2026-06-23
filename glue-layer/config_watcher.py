# glue-layer/config_watcher.py
"""配置文件监听器 — 检测文件变化并热重载"""
import json
import copy
import os
from typing import Optional
from pathlib import Path

from config_model import AgentConfig, ConfigValidator


class ConfigWatcher:
    """监听配置文件变化，提供热重载"""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._last_mtime: float = 0
        self._config: Optional[AgentConfig] = None

    def check_for_updates(self) -> bool:
        """检查文件是否变化，变化则重新加载。返回是否发生了更新。"""
        try:
            mtime = os.path.getmtime(self.config_path)
            if mtime > self._last_mtime:
                new_config = self._load_and_validate()
                if new_config is not None:
                    self._config = new_config
                    self._last_mtime = mtime
                    print("[ConfigWatcher] 配置已更新，重新加载", flush=True)
                    return True
                else:
                    # 加载失败，更新 mtime 避免反复尝试损坏的文件
                    self._last_mtime = mtime
                    return False
        except FileNotFoundError:
            pass
        except PermissionError:
            print(f"[ConfigWatcher] 配置文件被占用，等待下一轮重试", flush=True)
        return False

    def get_config(self) -> Optional[AgentConfig]:
        """获取当前配置（引用）"""
        return self._config

    def get_config_snapshot(self) -> Optional[AgentConfig]:
        """获取当前配置的深拷贝（用于消息处理快照）"""
        if self._config is None:
            return None
        return copy.deepcopy(self._config)

    def _load_and_validate(self) -> Optional[AgentConfig]:
        """加载并校验配置，失败返回 None"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return ConfigValidator.validate(data)
        except json.JSONDecodeError as e:
            print(f"[ConfigWatcher] JSON 解析失败: {e}，保留旧配置", flush=True)
            return None
        except (KeyError, TypeError, ValueError) as e:
            print(f"[ConfigWatcher] 配置校验失败: {e}，保留旧配置", flush=True)
            return None
