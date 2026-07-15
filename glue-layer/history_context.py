# glue-layer/history_context.py
#
# 群聊历史上下文：为 AI 回复提供短期对话背景（Phase 1 - 滑动窗口）
#
# 在 _process_message 处理群聊 @消息时，拉取该群最近 N 条文本消息，
# 拼成"[最近对话]"前缀注入 context，让 AI 能理解"刚才说的那个""上午的方案"
# 这类回指性问题。
#
# 数据来源：wx-cli 的 `wx history <group_wxid> -n N --json --msg-type text`
#   - group_wxid 直接传 msg.username（带 @chatroom 后缀），wx-cli resolve_username
#     对 @chatroom 直查，无需走名字映射
#   - 返回结构：{chat, username, is_group, chat_type, count, messages, meta}
#   - 每条 message 含 timestamp/time/sender/content/type/sender_username/
#     sender_contact_display/sender_group_nickname
#
# 缓存：模块级 dict，key=username，value=(messages, fetch_ts)，TTL 300 秒
#   群聊高频 @ 场景下避免反复 spawn wx 进程。轮询循环单线程，无需加锁。
#
# 降级：任何异常（subprocess 失败/JSON 解析错误/超时）→ 记日志 + 返回空字符串，
#   不阻塞主流程。AI 退化为无历史模式，仍能用当前消息回复。
#
# Phase 2 计划：把硬编码常量（KB_HISTORY_WINDOW/KB_CONTEXT_TOKEN_LIMIT/KB_CACHE_TTL_SECONDS）
# 迁到 config_model.py 的 knowledge_base 配置节。

import os
import subprocess
import json
import time
from typing import List, Optional, Tuple

# Phase 1 硬编码常量（未来迁 config_model.knowledge_base）
KB_HISTORY_WINDOW = 30            # 拉最近多少条消息
KB_CONTEXT_TOKEN_LIMIT = 1500     # 注入 context 的 token 上限（中文 1 字 ≈ 1.5 token，≈ 1000 字）
KB_CACHE_TTL_SECONDS = 300        # 群历史缓存 TTL
KB_FETCH_TIMEOUT = 15             # wx history subprocess 超时


# 模块级缓存：{username: (messages_list, fetch_ts)}
_cache: dict = {}


def fetch_group_history(
    wx_cli_path: str,
    wx_cli_dir: Optional[str],
    username: str,
    identity_wxid: str,
) -> List[dict]:
    """拉取群最近 N 条文本消息（带 TTL 缓存）。

    Args:
        wx_cli_path: wx-cli 可执行文件路径（CLI 参数优先于 config，保证 bundled wx.exe）
        wx_cli_dir: wx-cli 工作目录（让 wx 能找到 config.json）
        username: 群 wxid（必须含 @chatroom 后缀）
        identity_wxid: 自己的 wxid，用于过滤自己发的消息

    Returns:
        按时间升序排列的 message 列表。任何异常返回空列表（降级）。
    """
    if not username or '@chatroom' not in username:
        return []

    # 缓存命中检查
    cached = _cache.get(username)
    if cached:
        messages, fetch_ts = cached
        if time.time() - fetch_ts < KB_CACHE_TTL_SECONDS:
            return messages

    # 拉取
    try:
        cmd = [
            wx_cli_path, 'history', username,
            '-n', str(KB_HISTORY_WINDOW),
            '--json',
            '--type', 'text',
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=KB_FETCH_TIMEOUT,
            cwd=wx_cli_dir,
        )

        if result.returncode != 0:
            print(f"[HistoryContext] wx history 失败 (code={result.returncode}): {result.stderr[:100]}", flush=True)
            return []

        data = json.loads(result.stdout)
        messages = data.get('messages', [])

        # 过滤自己发的消息（与 _process_new_message 保持一致）
        # sender 可能是 wxid 或 '192'（微信内部系统账号回显）
        filtered = [
            m for m in messages
            if m.get('sender', '') not in (identity_wxid, '192')
        ]

        # 写缓存
        _cache[username] = (filtered, time.time())

        return filtered

    except subprocess.TimeoutExpired:
        print(f"[HistoryContext] wx history 超时 ({KB_FETCH_TIMEOUT}s): {username}", flush=True)
        return []
    except json.JSONDecodeError as e:
        print(f"[HistoryContext] JSON 解析失败: {e}", flush=True)
        return []
    except Exception as e:
        print(f"[HistoryContext] 拉取群历史异常: {e}", flush=True)
        return []


def format_history_prefix(messages: List[dict]) -> str:
    """把消息列表格式化为"[最近对话]"前缀字符串。

    - 按时间升序逐条格式化为 "HH:MM 显示名: 内容"
    - 用 msg['time'] 的 HH:MM 部分（wx-cli 已格式化为 "%Y-%m-%d %H:%M"）
    - 显示名优先级：sender_contact_display > sender
    - 累加估算 token（len(line) * 0.67），超过 KB_CONTEXT_TOKEN_LIMIT 从头部丢弃
      （保留尾部最新消息）
    - 内容中的换行符替换为空格，避免破坏对话格式
    - 空列表返回空字符串
    """
    if not messages:
        return ''

    lines = []
    for m in messages:
        # time 字段格式 "%Y-%m-%d %H:%M"，取后 5 字符即 HH:MM
        time_str = m.get('time', '')
        if len(time_str) >= 16:
            hhmm = time_str[-5:]
        elif len(time_str) >= 5:
            hhmm = time_str[-5:]
        else:
            hhmm = '?:??'

        sender = (
            m.get('sender_contact_display')
            or m.get('sender')
            or '未知'
        )

        content = m.get('content', '')
        # 替换换行、压缩连续空白
        content = content.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
        if not content:
            continue

        lines.append(f"{hhmm} {sender}: {content}")

    if not lines:
        return ''

    # 从头部丢弃，保留尾部最新消息，直到估算 token ≤ 上限
    # 估算：中文 1 字 ≈ 1.5 token → 1 token ≈ 0.67 字
    kept = lines[:]
    while kept:
        est_tokens = sum(len(line) * 0.67 for line in kept)
        if est_tokens <= KB_CONTEXT_TOKEN_LIMIT:
            break
        kept.pop(0)

    if not kept:
        return ''

    return '[最近对话]\n' + '\n'.join(kept)


def get_history_context(
    wx_cli_path: str,
    wx_cli_dir: Optional[str],
    username: str,
    identity_wxid: str,
) -> str:
    """组合函数：拉群历史 → 格式化 → 返回 context 前缀。

    任何异常返回空字符串（降级），调用方无感知。
    """
    messages = fetch_group_history(wx_cli_path, wx_cli_dir, username, identity_wxid)
    if not messages:
        return ''
    return format_history_prefix(messages)


def invalidate_cache(username: Optional[str] = None) -> None:
    """手动清除缓存（测试用 / 配置热更新时使用）。

    Args:
        username: 指定群 wxid 清除。None 表示清空全部。
    """
    if username is None:
        _cache.clear()
    else:
        _cache.pop(username, None)
