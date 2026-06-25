#!/usr/bin/env python3
"""
fake_wx.py — 模拟 wx-cli 的 JSON 输出，用于集成测试。

用法: python fake_wx.py <command> [args...]

支持的命令:
  contacts --json           → 返回联系人列表
  sessions --json           → 返回会话列表
  new-messages --json       → 返回新消息（受状态文件控制）
  whoami --json             → 返回当前用户 wxid
  ping --json               → 返回 pong
"""
import json
import sys
import os


def main():
    args = sys.argv[1:]

    if not args:
        print(json.dumps({"error": "no command"}), file=sys.stderr)
        sys.exit(1)

    command = args[0]

    # 状态文件路径（通过环境变量传入，控制 fake 行为）
    state_file = os.environ.get('FAKE_WX_STATE', '')
    state = {}
    if state_file and os.path.exists(state_file):
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)

    if command == 'contacts':
        contacts = state.get('contacts', [
            {"username": "wxid_alice", "display": "Alice", "remark": "爱丽丝"},
            {"username": "wxid_bob", "display": "Bob", "remark": ""},
            {"username": "wxid_self", "display": "我自己", "remark": ""},
        ])
        print(json.dumps(contacts, ensure_ascii=False))

    elif command == 'sessions':
        sessions = state.get('sessions', {
            "sessions": [
                {"username": "wxid_alice", "chat": "Alice", "chat_type": "private"},
                {"username": "12345@chatroom", "chat": "测试群", "chat_type": "group"},
            ]
        })
        print(json.dumps(sessions, ensure_ascii=False))

    elif command == 'new-messages':
        # 返回新消息列表（从状态文件读取，支持多次调用不同结果）
        messages = state.get('messages', [])
        new_state = state.get('new_state', {"cursor": "abc123"})
        result = {
            "messages": messages,
            "new_state": new_state,
            "meta": {"status": "ok", "unknown_shards": 0}
        }
        print(json.dumps(result, ensure_ascii=False))

    elif command == 'whoami':
        result = state.get('whoami', {"wxid": "wxid_self", "nick_name": "测试用户"})
        print(json.dumps(result, ensure_ascii=False))

    elif command == 'ping':
        print(json.dumps({"status": "ok"}))

    elif command == 'history':
        # history <room_id> --json -n <count>
        history = state.get('history', {"messages": []})
        print(json.dumps(history, ensure_ascii=False))

    else:
        print(json.dumps({"error": f"unknown command: {command}"}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
