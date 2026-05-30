"""B站通知插件 — 配置模型"""

from typing import Optional
from pydantic import BaseModel, Field


class BiliPushConfig(BaseModel):
    """推送配置"""
    message_interval: int = 100  # 同群连续消息间隔(ms)
    push_interval: int = 500  # 跨群推送间隔(ms)


class BiliCheckConfig(BaseModel):
    """检测配置"""
    interval: int = 15  # 动态检测间隔(秒)
    live_interval: int = 15  # 直播检测间隔(秒)
    low_speed: str = "0-0x2"  # 低峰时段倍率
    timeout: int = 10  # HTTP超时(秒)


class BiliPluginConfig(BaseModel):
    """插件配置类"""
    admin: int = 0  # 超级管理员QQ (0=使用SUPERUSER)

    cookie: str = ""  # bilibili cookie

    check: BiliCheckConfig = Field(default_factory=BiliCheckConfig)
    push: BiliPushConfig = Field(default_factory=BiliPushConfig)
