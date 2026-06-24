# glue-layer/test_main.py
"""glue-layer 核心逻辑单元测试"""
import pytest
import time
from collections import deque


# ─── _make_msg_key tests ─────────────────────────────────────────────────────

def test_make_msg_key_basic():
    """消息 key 由 timestamp + sender + content hash 组成"""
    from main import WeChatAgent

    agent = WeChatAgent.__new__(WeChatAgent)

    msg = {
        'timestamp': 1700000000,
        'sender': 'wxid_abc',
        'content': '你好世界'
    }
    key = agent._make_msg_key(msg)
    assert '1700000000' in key
    assert 'wxid_abc' in key
    # Same message should produce same key
    assert agent._make_msg_key(msg) == key


def test_make_msg_key_truncates_long_content():
    """消息 key 只取前 50 字符"""
    from main import WeChatAgent
    agent = WeChatAgent.__new__(WeChatAgent)

    msg1 = {'timestamp': 100, 'sender': 's', 'content': 'a' * 50}
    msg2 = {'timestamp': 100, 'sender': 's', 'content': 'a' * 100}
    assert agent._make_msg_key(msg1) == agent._make_msg_key(msg2)


def test_make_msg_key_different_senders():
    """不同 sender 产生不同 key"""
    from main import WeChatAgent
    agent = WeChatAgent.__new__(WeChatAgent)

    msg1 = {'timestamp': 100, 'sender': 'alice', 'content': 'hi'}
    msg2 = {'timestamp': 100, 'sender': 'bob', 'content': 'hi'}
    assert agent._make_msg_key(msg1) != agent._make_msg_key(msg2)


# ─── _is_mentioned tests ─────────────────────────────────────────────────────

def test_is_mentioned_with_matching_name():
    """@昵称 检测正常"""
    from main import WeChatAgent
    from config_model import AgentConfig, IdentityConfig, GroupsConfig, AIConfig

    agent = WeChatAgent.__new__(WeChatAgent)
    config = AgentConfig(
        version=1,
        identity=IdentityConfig(wxid='wxid_self', names=['小明']),
        groups=GroupsConfig(),
        ai=AIConfig()
    )

    assert agent._is_mentioned('你好 @小明', config) is True
    assert agent._is_mentioned('你好 @小红', config) is False


def test_is_mentioned_empty_names_returns_false():
    """空 names 列表应返回 False（不回复，防止刷屏）"""
    from main import WeChatAgent
    from config_model import AgentConfig, IdentityConfig, GroupsConfig, AIConfig

    agent = WeChatAgent.__new__(WeChatAgent)
    config = AgentConfig(
        version=1,
        identity=IdentityConfig(wxid='wxid_self', names=[]),
        groups=GroupsConfig(),
        ai=AIConfig()
    )

    assert agent._is_mentioned('你好 @任何人', config) is False


def test_is_mentioned_multiple_names():
    """多个昵称，任一匹配即可"""
    from main import WeChatAgent
    from config_model import AgentConfig, IdentityConfig, GroupsConfig, AIConfig

    agent = WeChatAgent.__new__(WeChatAgent)
    config = AgentConfig(
        version=1,
        identity=IdentityConfig(wxid='wxid_self', names=['小明', 'Ming', '明明']),
        groups=GroupsConfig(),
        ai=AIConfig()
    )

    assert agent._is_mentioned('@Ming 你好', config) is True
    assert agent._is_mentioned('@明明 在吗', config) is True
    assert agent._is_mentioned('@小红 你好', config) is False


# ─── Rate limiting tests ─────────────────────────────────────────────────────

def test_rate_limit_global():
    """全局限速：3 条/分钟"""
    from main import MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS

    assert MAX_MESSAGES_PER_MINUTE == 3
    assert MESSAGE_WINDOW_SECONDS == 60


def test_rate_limit_allows_under_limit():
    """未达限速时应允许发送"""
    from main import WeChatAgent, MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS

    agent = WeChatAgent.__new__(WeChatAgent)
    agent._send_times = deque(maxlen=MAX_MESSAGES_PER_MINUTE)

    now = time.time()
    agent._send_times.append(now - 30)
    agent._send_times.append(now - 10)

    # Simulate rate limit check from _process_message
    while agent._send_times and agent._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
        agent._send_times.popleft()

    assert len(agent._send_times) < MAX_MESSAGES_PER_MINUTE


def test_rate_limit_blocks_at_limit():
    """达到限速时应阻止发送"""
    from main import WeChatAgent, MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS

    agent = WeChatAgent.__new__(WeChatAgent)
    agent._send_times = deque(maxlen=MAX_MESSAGES_PER_MINUTE)

    now = time.time()
    agent._send_times.append(now - 30)
    agent._send_times.append(now - 20)
    agent._send_times.append(now - 10)

    while agent._send_times and agent._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
        agent._send_times.popleft()

    assert len(agent._send_times) >= MAX_MESSAGES_PER_MINUTE


def test_rate_limit_allows_after_window():
    """窗口期过后应允许发送"""
    from main import WeChatAgent, MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS

    agent = WeChatAgent.__new__(WeChatAgent)
    agent._send_times = deque(maxlen=MAX_MESSAGES_PER_MINUTE)

    now = time.time()
    agent._send_times.append(now - 120)
    agent._send_times.append(now - 90)
    agent._send_times.append(now - 70)

    while agent._send_times and agent._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
        agent._send_times.popleft()

    assert len(agent._send_times) == 0


# ─── Echo quiet period tests ─────────────────────────────────────────────────

def test_echo_quiet_seconds_constant():
    """Echo 静默期常量检查"""
    from main import ECHO_QUIET_SECONDS
    assert ECHO_QUIET_SECONDS == 15


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
