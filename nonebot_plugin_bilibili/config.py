"""B站通知插件 — 配置模型

通过 get_plugin_config 从 .env 加载，env 键名使用 bilibili_ 前缀。
嵌套字段使用双下划线分隔，如: bilibili_check__interval=15
"""

from pydantic import BaseModel, Field


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


class BiliPluginConfig(BaseModel):
    """B站通知插件配置 — 通过 get_plugin_config 统一加载

    .env 配置示例:
        bilibili_admin=0
        bilibili_cookie=SESSDATA=xxx
        bilibili_command_priority=5
        bilibili_check__interval=20
        bilibili_check__live_interval=15
        bilibili_check__low_speed=0-0x2
        bilibili_check__timeout=10
        bilibili_web_enable=true
        bilibili_web_password=admin123
    """
    model_config = {"extra": "ignore", "env_prefix": "bilibili_"}

    admin: int = 0  # 超级管理员QQ (0=使用SUPERUSER)
    cookie: str = ""  # bilibili Cookie（备选，优先使用 data/cookie.json）
    command_priority: int = 5  # /bili 命令响应优先级（越小越高）
    check: BiliCheckConfig = Field(default_factory=BiliCheckConfig)
    push: BiliPushConfig = Field(default_factory=BiliPushConfig)
    web_enable: bool = True  # 网页后台开关
    web_password: str = ""  # 网页后台密码（空=免密）
