# glue-layer/test_config_model.py
import json
import pytest
from config_model import AgentConfig, ConfigValidator

class TestConfigValidator:
    def test_valid_config(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": [{"room_id": "test@chatroom", "name": "测试群"}]},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"},
            "advanced": {"wx_cli_path": "wx"}
        }
        config = ConfigValidator.validate(data)
        assert config.identity.wxid == "wxid_test"
        assert config.identity.names == ["测试"]
        assert len(config.groups.monitor) == 1
        assert config.ai.api_key_format == "plaintext"

    def test_missing_wxid_fails(self):
        data = {
            "version": 1,
            "identity": {"names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="wxid"):
            ConfigValidator.validate(data)

    def test_empty_names_fails(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": []},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="names"):
            ConfigValidator.validate(data)

    def test_invalid_api_key_format_fails(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "invalid", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="api_key_format"):
            ConfigValidator.validate(data)

    def test_unsupported_version_fails(self):
        data = {
            "version": 99,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="version"):
            ConfigValidator.validate(data)

    def test_defaults_filled(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        config = ConfigValidator.validate(data)
        assert config.advanced.wx_cli_path == "wx"

    def test_dpapi_format_accepted(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "encrypted-data", "api_key_format": "dpapi", "model": "gpt-4o-mini"}
        }
        config = ConfigValidator.validate(data)
        assert config.ai.api_key_format == "dpapi"
