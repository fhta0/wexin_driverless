# glue-layer/test_integration.py
"""glue-layer ↔ wx-cli mock 集成测试

使用 unittest.mock 模拟 wx-cli 子进程和 SightFlow HTTP API，
测试完整的消息处理流程。
"""
import json
import os
import sys
import time
import pytest
import subprocess
from collections import deque
from unittest.mock import patch, MagicMock
from typing import Optional

# 确保可以 import glue-layer 模块
sys.path.insert(0, os.path.dirname(__file__))

from config_model import AgentConfig, IdentityConfig, GroupsConfig, AIConfig, AdvancedConfig, GroupEntry


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_config(
    wxid: str = 'wxid_self',
    names: list = None,
    monitor_groups: list = None,
) -> AgentConfig:
    """创建测试用 AgentConfig"""
    if names is None:
        names = ['测试助手']
    if monitor_groups is None:
        monitor_groups = [GroupEntry(room_id='12345@chatroom', name='测试群')]
    return AgentConfig(
        version=1,
        identity=IdentityConfig(wxid=wxid, names=names),
        groups=GroupsConfig(monitor=monitor_groups),
        ai=AIConfig(),
        advanced=AdvancedConfig(wx_cli_path='wx')
    )


class MockConfigWatcher:
    """模拟 ConfigWatcher，直接返回预设 config"""
    def __init__(self, config: AgentConfig):
        self._config = config

    def get_config(self) -> Optional[AgentConfig]:
        return self._config

    def get_config_snapshot(self) -> Optional[AgentConfig]:
        return self._config

    def check_for_updates(self) -> bool:
        return False


class MockHTTPResponse:
    """模拟 requests 响应"""
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


def make_mock_requests(
    autopilot_enabled: bool = True,
    reply_text: Optional[str] = '你好，我是自动回复',
    send_ok: bool = True,
):
    """构造 mock requests.get/post，模拟 SightFlow API"""
    call_log = []

    def mock_get(url, **kwargs):
        call_log.append(('GET', url, kwargs))
        if '/skill/autopilot' in url:
            return MockHTTPResponse(200, {'enabled': autopilot_enabled})
        return MockHTTPResponse(404, {'error': 'not_found'})

    def mock_post(url, **kwargs):
        call_log.append(('POST', url, kwargs))
        if '/skill/generate-reply' in url:
            if reply_text is None:
                return MockHTTPResponse(200, {'ok': False, 'error': 'skip'})
            return MockHTTPResponse(200, {'ok': True, 'reply': reply_text})
        if '/skill/send-message' in url:
            return MockHTTPResponse(200, {'ok': send_ok, 'elapsed_ms': 100})
        if '/skill/log' in url:
            return MockHTTPResponse(200, {'ok': True})
        if '/skill/alert' in url:
            return MockHTTPResponse(200, {'ok': True})
        return MockHTTPResponse(404, {'error': 'not_found'})

    return mock_get, mock_post, call_log


def make_subprocess_mock(messages: list = None, contacts: list = None):
    """构造 mock subprocess.run，模拟 wx-cli 输出"""
    if messages is None:
        messages = []
    if contacts is None:
        contacts = []

    def mock_subprocess_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0

        if 'new-messages' in cmd:
            data = {
                'messages': messages,
                'new_state': {'cursor': 'test123'},
                'meta': {'status': 'ok', 'unknown_shards': 0}
            }
            result.stdout = json.dumps(data, ensure_ascii=False)
            result.stderr = ''
        elif 'contacts' in cmd:
            result.stdout = json.dumps(contacts, ensure_ascii=False)
            result.stderr = ''
        else:
            result.stdout = '{}'
            result.stderr = ''

        return result

    return mock_subprocess_run


# ─── 测试用例 ─────────────────────────────────────────────────────────────────

def _make_agent(config, sent_messages_list):
    """创建一个配置好的 WeChatAgent 测试实例"""
    from main import WeChatAgent
    agent = WeChatAgent.__new__(WeChatAgent)
    agent._config_watcher = MockConfigWatcher(config)
    agent.mapper = MagicMock()
    agent.mapper.refresh.return_value = True
    agent.mapper.get_group_name.return_value = '测试群'
    agent._processed_keys = set()
    agent._send_times = deque(maxlen=3)
    agent._last_reply_ts = {}
    agent._start_timestamp = 0
    agent._last_send_time = {}
    agent._new_messages_state = None
    agent.message_queue = MagicMock()
    agent.message_queue.enqueue = lambda t, r, c: sent_messages_list.append((t, r, c))
    return agent


def test_full_private_message_flow():
    """私聊消息完整流: new-messages → AI 生成 → 入队 → 发送"""
    config = make_config()
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    # 模拟 wx new-messages 返回 1 条私聊消息
    mock_sub = make_subprocess_mock(messages=[
        {
            'username': 'wxid_alice',
            'sender': 'wxid_alice',
            'content': '你好',
            'type': '文本',
            'chat_type': 'private',
            'chat': 'Alice',
            'timestamp': int(time.time())
        }
    ])
    mock_get, mock_post, call_log = make_mock_requests(reply_text='收到，已自动回复')

    with patch('main.subprocess.run', side_effect=mock_sub), \
         patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        # 1. 获取新消息
        messages = agent._get_new_messages()
        assert len(messages) == 1
        assert messages[0]['content'] == '你好'

        # 2. 处理消息
        agent._process_new_message(messages[0])

        # 3. 验证 AI 被调用
        ai_calls = [c for c in call_log if 'generate-reply' in c[1]]
        assert len(ai_calls) == 1

        # 4. 验证消息入队
        assert len(sent_messages) == 1
        assert sent_messages[0][0] == 'Alice'
        assert '收到，已自动回复' in sent_messages[0][1]


def test_group_mention_triggers_reply():
    """群聊 @检测：消息包含 @昵称 → 触发回复"""
    config = make_config(names=['测试助手'])
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    mock_sub = make_subprocess_mock(messages=[
        {
            'username': '12345@chatroom',
            'sender': 'wxid_alice',
            'content': '@测试助手 帮我查一下',
            'type': '文本',
            'chat_type': 'group',
            'chat': '测试群',
            'timestamp': int(time.time())
        }
    ])
    mock_get, mock_post, call_log = make_mock_requests(reply_text='好的，正在查询')

    with patch('main.subprocess.run', side_effect=mock_sub), \
         patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        messages = agent._get_new_messages()
        assert len(messages) == 1

        agent._process_new_message(messages[0])

        # @检测通过，应该回复
        assert len(sent_messages) == 1
        assert sent_messages[0][0] == '测试群'


def test_group_no_mention_skipped():
    """群聊无 @不回复"""
    config = make_config(names=['测试助手'])
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    mock_sub = make_subprocess_mock(messages=[
        {
            'username': '12345@chatroom',
            'sender': 'wxid_alice',
            'content': '今天天气不错',
            'type': '文本',
            'chat_type': 'group',
            'chat': '测试群',
            'timestamp': int(time.time())
        }
    ])
    mock_get, mock_post, call_log = make_mock_requests(reply_text='不应该回复')

    with patch('main.subprocess.run', side_effect=mock_sub), \
         patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        messages = agent._get_new_messages()
        agent._process_new_message(messages[0])

        # 无 @，不应入队
        assert len(sent_messages) == 0
        ai_calls = [c for c in call_log if 'generate-reply' in c[1]]
        assert len(ai_calls) == 0


def test_echo_prevention():
    """发送后 15s 内同一联系人消息被跳过"""
    config = make_config()
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    # 模拟刚刚发送过消息
    agent._last_send_time['wxid_alice'] = time.time()

    msg = {
        'username': 'wxid_alice',
        'sender': 'wxid_alice',
        'content': '你好',
        'type': '文本',
        'chat_type': 'private',
        'chat': 'Alice',
        'timestamp': int(time.time())
    }

    mock_get, mock_post, call_log = make_mock_requests(reply_text='不应回复')

    with patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        agent._process_new_message(msg)

        assert len(sent_messages) == 0
        ai_calls = [c for c in call_log if 'generate-reply' in c[1]]
        assert len(ai_calls) == 0


def test_rate_limiting():
    """3 条/分钟限速：第 4 条消息被跳过"""
    config = make_config()
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    # 模拟已达限速
    now = time.time()
    agent._send_times.append(now - 30)
    agent._send_times.append(now - 20)
    agent._send_times.append(now - 10)

    msg = {
        'username': 'wxid_new',
        'sender': 'wxid_new',
        'content': '你好',
        'type': '文本',
        'chat_type': 'private',
        'chat': 'NewContact',
        'timestamp': int(time.time())
    }

    mock_get, mock_post, call_log = make_mock_requests(reply_text='不应回复')

    with patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        agent._process_new_message(msg)

        assert len(sent_messages) == 0
        ai_calls = [c for c in call_log if 'generate-reply' in c[1]]
        assert len(ai_calls) == 0


def test_autopilot_disabled_skips_all():
    """autopilot 关闭时，check_autopilot_enabled 返回 False"""
    mock_get, mock_post, call_log = make_mock_requests(autopilot_enabled=False)

    with patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        from main import check_autopilot_enabled
        result = check_autopilot_enabled()
        assert result is False


def test_self_message_skipped():
    """自己发送的消息被跳过"""
    config = make_config(wxid='wxid_self')
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    msg = {
        'username': 'wxid_alice',
        'sender': 'wxid_self',
        'content': '我自己发的',
        'type': '文本',
        'chat_type': 'private',
        'chat': 'Alice',
        'timestamp': int(time.time())
    }

    mock_get, mock_post, call_log = make_mock_requests(reply_text='不应回复')

    with patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        agent._process_new_message(msg)
        assert len(sent_messages) == 0


def test_system_message_skipped():
    """系统消息被跳过"""
    config = make_config()
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    msg = {
        'username': 'wxid_alice',
        'sender': 'wxid_alice',
        'content': '你已添加了对方为好友',
        'type': '系统',
        'chat_type': 'private',
        'chat': 'Alice',
        'timestamp': int(time.time())
    }

    mock_get, mock_post, call_log = make_mock_requests(reply_text='不应回复')

    with patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        agent._process_new_message(msg)
        assert len(sent_messages) == 0


def test_duplicate_message_skipped():
    """重复消息（相同 key）被跳过"""
    config = make_config()
    sent_messages = []
    agent = _make_agent(config, sent_messages)

    msg = {
        'username': 'wxid_alice',
        'sender': 'wxid_alice',
        'content': '你好',
        'type': '文本',
        'chat_type': 'private',
        'chat': 'Alice',
        'timestamp': 1700000000
    }

    mock_get, mock_post, call_log = make_mock_requests(reply_text='回复')

    with patch('main.requests.get', side_effect=mock_get), \
         patch('main.requests.post', side_effect=mock_post):
        agent._process_new_message(msg)
        assert len(sent_messages) == 1

        # 第二次处理相同消息
        agent._process_new_message(msg)
        assert len(sent_messages) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
