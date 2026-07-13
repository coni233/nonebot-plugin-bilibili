"""B站通知插件 — 配置模型

通过 get_plugin_config 从 .env 加载，env 键名使用 bilibili_ 前缀。
嵌套字段使用双下划线分隔，如: bilibili_check__interval=15

⚠ 技术说明：NoneBot 的自研 Settings 实现不支持 pydantic-settings 的 env_prefix，
因此通过 model_validator(mode="before") 手动剥离 bilibili_ 前缀并展开嵌套字段。
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator


class BiliPushConfig(BaseModel):
    """推送配置"""
    message_interval: int = 100  # 同群连续消息间隔(ms)
    push_interval: int = 500  # 跨群推送间隔(ms)


class BiliCheckConfig(BaseModel):
    """检测配置"""
    interval: int = 20  # 动态检测间隔(秒)
    live_interval: int = 15  # 直播检测间隔(秒)
    low_speed: str = "0-0x2"  # 低峰时段倍率
    timeout: int = 10  # HTTP超时(秒)


_PREFIX = "bilibili_"
_DELIMITER = "__"


class BiliPluginConfig(BaseModel):
    """B站通知插件配置 — 通过 get_plugin_config 统一加载

    .env 配置示例:
        bilibili_admin=0
        bilibili_cookie=SESSDATA=xxx
        bilibili_data_dir=./data/bilibili
        bilibili_command_priority=5
        bilibili_check__interval=20
        bilibili_check__live_interval=15
        bilibili_check__low_speed=0-0x2
        bilibili_check__timeout=10
        bilibili_web_enable=true
        bilibili_web_password=admin123
    """
    model_config = {"extra": "ignore"}

    admin: int = 0  # 超级管理员QQ (0=使用SUPERUSER)
    cookie: str = ""  # bilibili Cookie（备选，优先使用 data/cookie.json）
    data_dir: str = "./data/bilibili"  # 插件数据目录（cookie/subscribers/users/dynamic_history.json）
    command_priority: int = 5  # /bili 命令响应优先级（越小越高）
    check: BiliCheckConfig = Field(default_factory=BiliCheckConfig)
    push: BiliPushConfig = Field(default_factory=BiliPushConfig)
    web_enable: bool = True  # 网页后台开关
    web_password: str = ""  # 网页后台密码（空=免密）

    @model_validator(mode="before")
    @classmethod
    def _extract_bilibili_prefix(cls, data: Any) -> Any:
        """剥离 bilibili_ 前缀并展开嵌套字段。

        NoneBot 的自研 Settings 不支持 env_prefix，所有 .env 配置项
        会被原样传入（如 bilibili_check__interval=30）。
        此 validator 将前缀剥离后展开嵌套，供 pydantic 校验。

        转换示例：
            {"bilibili_check__interval": "30"} → {"check": {"interval": "30"}}
            {"bilibili_data_dir": "./data"}    → {"data_dir": "./data"}
        """
        if not isinstance(data, dict):
            return data
        result: dict[str, Any] = {}
        for key, val in data.items():
            if key.startswith(_PREFIX):
                stem = key[len(_PREFIX):]
                if _DELIMITER in stem:
                    parts = stem.split(_DELIMITER)
                    node: Any = result
                    for part in parts[:-1]:
                        node = node.setdefault(part, {})
                    node[parts[-1]] = val
                else:
                    result[stem] = val
            else:
                result[key] = val
        return result
