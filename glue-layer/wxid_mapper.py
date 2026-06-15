# glue-layer/wxid_mapper.py
import subprocess
import json
from typing import Dict

class WxidMapper:
    """wxid → 备注名/昵称 映射器"""

    def __init__(self, wx_cli_path: str = 'wx'):
        self.wx_cli_path = wx_cli_path
        self._name_map: Dict[str, str] = {}  # wxid → display_name
        self._group_map: Dict[str, str] = {}  # room_id → group_name

    def refresh(self) -> bool:
        """从 wx contacts 刷新映射表"""
        try:
            result = subprocess.run(
                [self.wx_cli_path, 'contacts', '--json'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                print(f"[WxidMapper] wx contacts 失败: {result.stderr}")
                return False

            contacts = json.loads(result.stdout)

            # 构建映射表
            for contact in contacts.get('contacts', []):
                username = contact.get('username', '')
                display = contact.get('display', '') or contact.get('remark', '') or username

                if '@chatroom' in username:
                    # 群聊
                    self._group_map[username] = display
                else:
                    # 私聊联系人
                    self._name_map[username] = display

            print(f"[WxidMapper] 已加载 {len(self._name_map)} 联系人, {len(self._group_map)} 群")
            return True

        except Exception as e:
            print(f"[WxidMapper] 刷新失败: {e}")
            return False

    def get_contact_name(self, wxid: str) -> str:
        """获取联系人备注名"""
        return self._name_map.get(wxid, wxid)

    def get_group_name(self, room_id: str) -> str:
        """获取群名"""
        return self._group_map.get(room_id, room_id)

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