"""B站通知插件 — 插件入口

Bilibili 动态/直播 订阅通知插件 for NoneBot2
参考 bilibili-dynamic-mirai-plugin v3 设计
"""

import os

# 确保 htmlrender 后端配置（在驱动加载前设置环境变量）
# 兼容旧版 (render_backend) 和新版 (htmlrender_browser) 配置键
for env_key in ("HTMLRENDER_RENDER_BACKEND", "HTMLRENDER_BROWSER", "RENDER_BACKEND"):
    if env_key not in os.environ:
        os.environ[env_key] = "playwright"

from nonebot import get_driver, load_plugin, require
from nonebot.log import logger
from nonebot.plugin import PluginMetadata, get_plugin_config

from .config import BiliPluginConfig

# ===== 依赖声明（必须在任何 localstore 引用之前）=====
require("nonebot_plugin_apscheduler")
require("nonebot_plugin_htmlrender")
require("nonebot_plugin_localstore")
# require("nonebot_plugin_alconna")  # 改用 on_command，不依赖 Alconna

# ===== 插件配置（全局唯一实例，仅获取一次）=====
plugin_config: BiliPluginConfig = get_plugin_config(BiliPluginConfig)

# ===== 数据模型（localstore 已在上面 require，安全导入）=====
from .model import cookie_storage, sub_storage, user_storage

from . import command  # noqa: F401, 注册命令
from . import web  # noqa: F401, 注册后台路由

__plugin_meta__ = PluginMetadata(
    name="B站通知插件",
    description="Bilibili 动态/直播 订阅推送",
    usage=(
        "/bili help - 显示帮助\n"
        "/bili add <uid> - 订阅UP主\n"
        "/bili del <uid> - 取消订阅\n"
        "/bili list - 本群订阅列表\n"
        "/bili atall on/off <uid> - @全体管理\n"
        "/bili login - 扫码登录"
    ),
    type="application",
    homepage="https://github.com/mengbingnaixi/nonebot-plugin-bilibili",
    supported_adapters={"~onebot.v11"},
    config=BiliPluginConfig,
)

driver = get_driver()


@driver.on_startup
async def _():
    """插件启动时执行"""
    # 迁移: 为旧数据补充 sub_time
    import time
    now = int(time.time())
    migrated = 0
    for gid, data in sub_storage.data.items():
        st = data.setdefault("sub_time", {})
        for uid in data.get("uids", []):
            key = str(uid)
            if key not in st:
                st[key] = 0  # 0 = 不限，不阻止历史动态
                migrated += 1
    if migrated:
        sub_storage.save()
        logger.info(f"已迁移 {migrated} 条订阅时间戳")

    logger.info("B站通知插件启动中...")
    logger.info(f"Cookie状态: {'已配置' if cookie_storage.cookie else '未配置'}")
    logger.info(
        f"已加载订阅数据: {len(sub_storage.data)} 个群/好友, "
        f"{len(sub_storage.get_all_uids())} 个UP主"
    )

    # 启动定时检测
    try:
        from .dynamic import start_dynamic_checker
        from .live import start_live_checker

        start_dynamic_checker()
        start_live_checker()
    except Exception as e:
        logger.error(f"启动定时检测失败: {e}")


@driver.on_shutdown
async def _():
    """插件关闭时保存数据"""
    sub_storage.save()
    user_storage.save()
    cookie_storage.save()
    logger.info("B站通知插件已关闭，数据已保存")
