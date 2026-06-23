# glue-layer/wxid_mapper.py
import subprocess
import json
import re
from typing import Dict


class WxidMapper:
    """wxid → 备注名/昵称 映射器"""

    def __init__(self, wx_cli_path: str = 'wx'):
        self.wx_cli_path = wx_cli_path
        self._name_map: Dict[str, str] = {}  # wxid → display_name
        self._group_map: Dict[str, str] = {}  # room_id → group_name

    def refresh(self) -> bool:
        """从 wx contacts 和 wx sessions 刷新映射表"""
        try:
            # 1. 加载联系人映射
            result = subprocess.run(
                [self.wx_cli_path, 'contacts', '--json'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=10
            )

            if result.returncode == 0:
                contacts = json.loads(result.stdout)
                for contact in contacts:
                    username = contact.get('username', '')
                    display = contact.get('display', '') or contact.get('remark', '') or username

                    # 过滤企业微信客服/公众号类型
                    if '@openim' in username or '@kefu' in username or '@weclaw' in username:
                        continue

                    if '@chatroom' in username:
                        # 只有当手动配置中没有这个群时才覆盖
                        if username not in self._group_map:
                            self._group_map[username] = display
                    else:
                        self._name_map[username] = display

            # 2. 加载群映射（从 sessions）
            result = subprocess.run(
                [self.wx_cli_path, 'sessions', '--json'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=10
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                sessions = data.get('sessions', [])
                for session in sessions:
                    username = session.get('username', '')
                    chat_name = session.get('chat', '')
                    chat_type = session.get('chat_type', '')

                    if chat_type == 'group' and '@chatroom' in username:
                        # 只有当手动配置中没有这个群时才尝试从其他来源获取群名
                        if username not in self._group_map:
                            # 如果 sessions 返回的 chat 不是 room_id，使用它作为群名
                            if chat_name and '@chatroom' not in chat_name:
                                self._group_map[username] = chat_name
                            else:
                                # 尝试从历史消息中提取群名
                                group_name = self._extract_group_name_from_history(username)
                                if group_name:
                                    self._group_map[username] = group_name
                                else:
                                    # 兜底：使用 room_id
                                    self._group_map[username] = username

            print(f"[WxidMapper] 已加载 {len(self._name_map)} 联系人, {len(self._group_map)} 群", flush=True)
            print(f"[WxidMapper] 群映射: {json.dumps(self._group_map, ensure_ascii=False)}", flush=True)
            return True

        except Exception as e:
            print(f"[WxidMapper] 刷新失败: {e}", flush=True)
            return False

    def _extract_group_name_from_history(self, room_id: str) -> str:
        """从群聊历史消息中提取群名（从系统消息中解析）"""
        try:
            result = subprocess.run(
                [self.wx_cli_path, 'history', room_id, '--json', '-n', '50'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=15
            )

            if result.returncode != 0:
                return None

            data = json.loads(result.stdout)
            messages = data.get('messages', [])

            # 从后往前查找系统消息（最新的群名）
            for msg in reversed(messages):
                if msg.get('type') == '系统':
                    content = msg.get('content', '')
                    # 匹配 "修改群名为"xxx""（支持中文引号 "" 和英文引号 ""）
                    match = re.search(r'修改群名为[""\"](.*?)[""\"]', content)
                    if match:
                        return match.group(1)
                    # 兜底：不带引号的格式
                    match = re.search(r'修改群名为(\S+)', content)
                    if match:
                        # 去除可能的引号
                        name = match.group(1)
                        # 去除首尾的中文/英文引号
                        name = name.strip('"“”')
                        return name

            return None

        except Exception as e:
            print(f"[WxidMapper] 提取群名失败: {e}", flush=True)
            return None

    def get_contact_name(self, wxid: str) -> str:
        """获取联系人备注名"""
        return self._name_map.get(wxid, wxid)

    def get_group_name(self, room_id: str) -> str:
        """获取群名（支持按需查找：缓存未命中时实时查询 wx history）"""
        if room_id in self._group_map:
            cached = self._group_map[room_id]
            # 如果缓存的是 room_id 本身（兜底值），尝试重新查找
            if cached != room_id:
                return cached

        # 缓存未命中 或 缓存为兜底值 → 按需查找
        group_name = self._extract_group_name_from_history(room_id)
        if group_name:
            self._group_map[room_id] = group_name
            print(f"[WxidMapper] 按需查到群名: {room_id} → {group_name}", flush=True)
            return group_name

        # 仍然找不到，兜底用 room_id
        self._group_map[room_id] = room_id
        return room_id

    def translate_message(self, msg: dict) -> tuple:
        """
        翻译消息中的 wxid

        Args:
            msg: wx new-messages 返回的消息对象

        Returns:
            (target, sender_name, context)
            - target: SightFlow 搜索目标（联系人名或群名）
            - sender_name: 发送者名称（用于上下文）
            - context: AI 回复的上下文描述
        """
        chat_type = msg.get('chat_type', 'private')
        sender_wxid = msg.get('sender_wxid', '') or msg.get('username', '')
        content = msg.get('content', '')

        if chat_type == 'private':
            # 私聊：直接翻译 sender_wxid
            target = self.get_contact_name(sender_wxid)
            sender_name = target
            context = f"来自 {sender_name} 的私聊消息：{content}"

        elif chat_type == 'group':
            # 群聊：目标是群，sender 是群成员
            room_id = msg.get('room_id', '') or msg.get('username', '')
            target = self.get_group_name(room_id)
            sender_name = self.get_contact_name(sender_wxid)
            context = f"群【{target}】中 {sender_name} 说：{content}"

        else:
            # 其他类型（公众号等）
            target = self.get_contact_name(sender_wxid)
            sender_name = target
            context = f"{sender_name} 的消息：{content}"

        return target, sender_name, context
