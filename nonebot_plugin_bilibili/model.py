"""B站通知插件 — 数据模型 + JSON持久化"""

import json
from pathlib import Path
from enum import Enum
from typing import Dict, List, Optional, Set

from nonebot.log import logger


class AtAllType(str, Enum):
    """@全体类型，与 bilibili-dynamic-mirai-plugin 一致"""
    ALL = "all"           # 全部（动态+直播）
    DYNAMIC = "dynamic"   # 全部动态
    VIDEO = "video"       # 视频
    MUSIC = "music"       # 音乐
    ARTICLE = "article"   # 专栏
    LIVE = "live"         # 直播

    @staticmethod
    def from_str(s: str) -> Optional["AtAllType"]:
        mapping = {
            "全部": AtAllType.ALL, "all": AtAllType.ALL, "a": AtAllType.ALL,
            "全部动态": AtAllType.DYNAMIC, "dynamic": AtAllType.DYNAMIC, "d": AtAllType.DYNAMIC,
            "直播": AtAllType.LIVE, "live": AtAllType.LIVE, "l": AtAllType.LIVE,
            "视频": AtAllType.VIDEO, "video": AtAllType.VIDEO, "v": AtAllType.VIDEO,
            "音乐": AtAllType.MUSIC, "music": AtAllType.MUSIC, "m": AtAllType.MUSIC,
            "专栏": AtAllType.ARTICLE, "article": AtAllType.ARTICLE,
        }
        return mapping.get(s.lower())


def get_data_dir() -> Path:
    """获取插件数据目录（通过 nonebot_plugin_localstore）

    惰性导入：避免模块级别 import nonebot_plugin_localstore
    在 NoneBot require() 插件之前触发加载。
    """
    from nonebot_plugin_localstore import get_plugin_data_dir  # noqa: PLC0415

    return get_plugin_data_dir()


# ========== 文件持久化 ==========

class JsonStorage:
    """JSON文件持久化基类"""

    def __init__(self, filename: str):
        self.filepath = get_data_dir() / filename
        self._data: dict = {}
        self._load()

    def _load(self):
        if self.filepath.exists():
            try:
                self._data = json.loads(self.filepath.read_text("utf-8"))
            except Exception as e:
                logger.warning(f"读取 {self.filepath.name} 失败: {e}")
                self._data = {}
        else:
            self._data = {}

    def save(self):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.filepath.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    @property
    def data(self) -> dict:
        return self._data

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()


class CookieStorage(JsonStorage):
    """Cookie持久化"""

    def __init__(self):
        super().__init__("cookie.json")

    @property
    def cookie(self) -> str:
        return self._data.get("cookie", "")

    @cookie.setter
    def cookie(self, value: str):
        self._data["cookie"] = value
        self.save()

    @property
    def uid(self) -> int:
        return self._data.get("uid", 0)

    @uid.setter
    def uid(self, value: int):
        self._data["uid"] = value
        self.save()


class SubStorage(JsonStorage):
    """订阅数据持久化"""

    def __init__(self):
        super().__init__("subscribers.json")

    def get_group_uids(self, group_id: str) -> List[int]:
        """获取某群订阅的UID列表"""
        return self._data.get(group_id, {}).get("uids", [])

    def add_group_uid(self, group_id: str, uid: int):
        if group_id not in self._data:
            self._data[group_id] = {"uids": [], "atall": {}, "sub_time": {}}
        if uid not in self._data[group_id]["uids"]:
            self._data[group_id]["uids"].append(uid)
            # 记录订阅时间戳，用于避免推送旧动态
            st = self._data[group_id].setdefault("sub_time", {})
            st[str(uid)] = int(__import__("time").time())
            self.save()

    def get_sub_time(self, group_id: str, uid: int) -> int:
        """获取某群某UID的订阅时间戳"""
        st = self._data.get(group_id, {}).get("sub_time", {})
        return st.get(str(uid), 0)

    def remove_group_uid(self, group_id: str, uid: int) -> bool:
        if group_id in self._data and uid in self._data[group_id].get("uids", []):
            self._data[group_id]["uids"].remove(uid)
            # 同时清理 atall
            self._data[group_id].get("atall", {}).pop(str(uid), None)
            self.save()
            return True
        return False

    def clear_group(self, group_id: str) -> int:
        count = len(self._data.get(group_id, {}).get("uids", []))
        if group_id in self._data:
            del self._data[group_id]
            self.save()
        return count

    def clear_all(self) -> int:
        count = sum(len(v.get("uids", [])) for v in self._data.values())
        self._data.clear()
        self.save()
        return count

    def get_atall_types(self, group_id: str, uid: int) -> List[str]:
        """获取某群某UID配置的@全体类型列表
        返回值示例: ['all'] | ['dynamic', 'live'] | []
        逻辑与 bilibili-dynamic-mirai-plugin 一致
        """
        atall = self._data.get(group_id, {}).get("atall", {})
        return atall.get(str(uid), [])

    def set_atall(self, group_id: str, uid: int, atall_type: str, enable: bool):
        """设置@全体类型
        与 bilibili-dynamic-mirai-plugin 的 AtAllService 逻辑一致
        """

        if group_id not in self._data:
            self._data[group_id] = {"uids": [], "atall": {}}
        atall = self._data[group_id].setdefault("atall", {})
        key = str(uid)
        types = atall.get(key, [])

        if not enable:
            # 删除指定类型
            if atall_type in types:
                types.remove(atall_type)
            if types:
                atall[key] = types
            else:
                atall.pop(key, None)
            self.save()
            return

        # 添加逻辑（与参考插件一致）
        parsed = AtAllType.from_str(atall_type)
        if not parsed:
            return

        if not types:
            types = [parsed.value]
        elif parsed == AtAllType.ALL:
            types = [AtAllType.ALL.value]
        elif parsed == AtAllType.DYNAMIC:
            types = [t for t in types if t not in ("all", "video", "music", "article")]
            if AtAllType.DYNAMIC.value not in types:
                types.append(AtAllType.DYNAMIC.value)
        elif parsed == AtAllType.LIVE:
            types = [t for t in types if t != "all"]
            if AtAllType.LIVE.value not in types:
                types.append(AtAllType.LIVE.value)
        else:  # video / music / article
            types = [t for t in types if t not in ("all", "dynamic")]
            if parsed.value not in types:
                types.append(parsed.value)

        atall[key] = types
        self.save()

    def check_atall(self, group_id: str, uid: int, msg_type: str) -> bool:
        """检查指定UID的某类型消息是否需要@全体
        msg_type: 'video' | 'music' | 'article' | 'dynamic' | 'live'
        逻辑与 SendTasker.checkAtAll 一致
        """
        types = self.get_atall_types(group_id, uid)
        if not types:
            return False
        if "all" in types:
            return True
        if msg_type == "live":
            return "live" in types
        # 动态类型
        if "dynamic" in types:
            return True
        # 特定动态子类型
        return msg_type in types

    def get_all_uids(self) -> Set[int]:
        """获取所有被订阅的UID"""
        uids = set()
        for v in self._data.values():
            uids.update(v.get("uids", []))
        return uids

    def get_groups_for_uid(self, uid: int) -> List[str]:
        """获取订阅了指定UID的所有群"""
        groups = []
        for gid, v in self._data.items():
            if uid in v.get("uids", []):
                groups.append(gid)
        return groups


class UserInfoStorage(JsonStorage):
    """UP主信息缓存"""

    def __init__(self):
        super().__init__("users.json")

    def get_name(self, uid: int) -> str:
        return self._data.get(str(uid), {}).get("name", "")

    def get_face(self, uid: int) -> str:
        return self._data.get(str(uid), {}).get("face", "")

    def set_face(self, uid: int, face: str):
        if face:
            key = str(uid)
            if key not in self._data:
                self._data[key] = {}
            self._data[key]["face"] = face
            self.save()

    def set_name(self, uid: int, name: str):
        key = str(uid)
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["name"] = name
        # 限制缓存大小
        if len(self._data) > 500:
            # 删除最早的条目
            for k in list(self._data.keys())[:100]:
                del self._data[k]
        self.save()

    def set_group(self, uid: int, group: str):
        key = str(uid)
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["group"] = group
        self.save()

    def get_group(self, uid: int) -> str:
        return self._data.get(str(uid), {}).get("group", "")


# ========== 全局单例 ==========

cookie_storage = CookieStorage()
sub_storage = SubStorage()
user_storage = UserInfoStorage()
