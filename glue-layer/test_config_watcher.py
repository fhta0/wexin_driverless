# glue-layer/test_config_watcher.py
import json
import os
import time
import pytest
import tempfile
from pathlib import Path
from config_watcher import ConfigWatcher
from config_model import ConfigValidator


def _write_config(path: Path, data: dict):
    """写入配置文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def _valid_config_data(**overrides):
    """生成有效的配置数据"""
    base = {
        "version": 1,
        "identity": {"wxid": "wxid_test", "names": ["测试"]},
        "groups": {"monitor": []},
        "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
    }
    base.update(overrides)
    return base


class TestConfigWatcher:
    def test_loads_config_on_first_check(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        changed = watcher.check_for_updates()

        assert changed is True
        assert watcher.get_config().identity.wxid == "wxid_test"

    def test_no_change_returns_false(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()  # 首次加载
        changed = watcher.check_for_updates()  # 第二次无变化

        assert changed is False

    def test_detects_file_modification(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()

        # 修改文件（确保 mtime 变化）
        time.sleep(0.1)
        _write_config(config_path, _valid_config_data(identity={"wxid": "wxid_new", "names": ["新名字"]}))
        changed = watcher.check_for_updates()

        assert changed is True
        assert watcher.get_config().identity.wxid == "wxid_new"

    def test_file_not_found_no_crash(self, tmp_path):
        config_path = tmp_path / 'nonexistent.json'
        watcher = ConfigWatcher(config_path)
        changed = watcher.check_for_updates()

        assert changed is False
        assert watcher.get_config() is None

    def test_json_error_preserves_old_config(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()
        old_wxid = watcher.get_config().identity.wxid

        # 写入非法 JSON
        time.sleep(0.1)
        config_path.write_text("not valid json{{{", encoding='utf-8')
        changed = watcher.check_for_updates()

        assert changed is False  # 加载失败，不视为变化
        assert watcher.get_config().identity.wxid == old_wxid  # 保留旧配置

    def test_validation_error_preserves_old_config(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()

        # 写入格式错误（缺少必填字段）
        time.sleep(0.1)
        _write_config(config_path, {"version": 1, "identity": {}})
        changed = watcher.check_for_updates()

        assert changed is False
        assert watcher.get_config().identity.wxid == "wxid_test"

    def test_deepcopy_snapshot(self, tmp_path):
        """验证 get_config_snapshot 返回深拷贝"""
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()

        snapshot = watcher.get_config_snapshot()
        original = watcher.get_config()

        # 修改 snapshot 不应影响源配置
        snapshot.identity.names.append("修改")
        assert "修改" not in original.identity.names
