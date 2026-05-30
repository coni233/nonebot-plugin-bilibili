"""B站通知插件 — 直播状态定时检测与推送"""

import asyncio
from datetime import datetime
from typing import Dict, Set

from nonebot import get_bot, require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_htmlrender")

from nonebot_plugin_apscheduler import scheduler

from .client import bili_client
from .model import sub_storage, user_storage
from .utils import live_link, format_timestamp, format_time_full, format_number


class LiveChecker:
    """直播状态检测器"""

    def __init__(self):
        self._live_status: Dict[int, bool] = {}  # uid -> is_living
        self._running = False

    async def check(self):
        """执行一次直播状态检测"""
        if self._running:
            return
        self._running = True
        try:
            subscribed_uids = sub_storage.get_all_uids()
            if not subscribed_uids:
                return

            uid_list = list(subscribed_uids)
            # 分批查询，每批最多50个
            for i in range(0, len(uid_list), 50):
                batch = uid_list[i : i + 50]
                await self._check_batch(batch)
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"直播检测异常: {e}")
        finally:
            self._running = False

    async def _check_batch(self, uids: list):
        """检查一批用户的直播状态"""
        data = await bili_client.get_live_status(uids)
        if not data:
            return

        try:
            bot = get_bot()
        except Exception:
            return

        for uid_str, info in data.items():
            try:
                uid = int(uid_str)
            except (ValueError, TypeError):
                continue

            if not info:
                continue

            # 调试: 打印直播数据字段，确认 cover 字段名
            logger.debug(f"直播数据 uid={uid}: title={info.get('title','')} cover_from_user= {'有' if info.get('cover_from_user') else '空'} cover= {'有' if info.get('cover') else '空'} user_cover= {'有' if info.get('user_cover') else '空'} face= {'有' if info.get('face') else '空'} live_status={info.get('live_status')}")

            live_status = info.get("live_status", 0)
            is_living = live_status == 1
            was_living = self._live_status.get(uid, False)

            if is_living and not was_living:
                # 开播通知
                self._live_status[uid] = True
                await self._push_live_start(bot, uid, info)
            elif not is_living and was_living:
                # 下播通知
                self._live_status[uid] = False
                await self._push_live_end(bot, uid, info)

            # 初始化状态
            if uid not in self._live_status:
                self._live_status[uid] = is_living

    async def _push_live_start(self, bot, uid: int, info: dict):
        """推送开播通知"""
        title = info.get("title", "未命名直播")
        # cover_from_user 是 get_status_info_by_uids 的封面字段
        cover = info.get("cover_from_user", "") or info.get("cover", "") or info.get("user_cover", "")
        # B站 API 返回 http 链接，Playwright 截图需要 https
        if cover.startswith("http://"):
            cover = "https://" + cover[7:]

        room_id = info.get("room_id", 0)
        area = info.get("area_name", info.get("parent_area_name", ""))
        online = info.get("online", 0)
        name = info.get("uname", user_storage.get_name(uid))
        face = info.get("face", "")

        groups = sub_storage.get_groups_for_uid(uid)
        if not groups:
            return

        # 更新缓存
        if name:
            user_storage.set_name(uid, name)

        # 模板数据
        template_data = {
            "cover": cover,
            "title": title,
            "name": name,
            "avatar": face,
            "uid": uid,
            "area": area,
            "start_time": format_time_full(int(info.get("live_start_time", info.get("live_time", 0)) or 0)),
            "online": format_number(int(online)) if online else "0",
            "online_raw": int(online or 0),
            "live_link": live_link(room_id),
            "room_id": room_id,
        }

        for group_id in groups:
            try:
                # HTML渲染推送
                try:
                    import base64
                    from nonebot_plugin_htmlrender import html_to_pic
                    import jinja2
                    import os

                    # 使用群自定义模板
                    tpl_name = sub_storage.get(group_id, {}).get("template_live", "") or "live.html"
                    if not tpl_name.endswith(".html"):
                        tpl_name += ".html"
                    template_dir = os.path.join(os.path.dirname(__file__), "templates")
                    env = jinja2.Environment(
                        loader=jinja2.FileSystemLoader(template_dir),
                        autoescape=True,
                    )
                    template = env.get_template(tpl_name)
                    html = template.render(**template_data)
                    pic_bytes = await html_to_pic(html, viewport={"width": 580, "height": 10})
                    pic_b64 = "base64://" + base64.b64encode(pic_bytes).decode()

                    # 检查@全体(直播)
                    need_atall = sub_storage.check_atall(group_id, uid, "live")
                    msg_list = [{"type": "image", "data": {"file": pic_b64}}]
                    if need_atall:
                        msg_list.append({"type": "at", "data": {"qq": "all"}})
                    await bot.call_api(
                        "send_group_msg",
                        group_id=int(group_id),
                        message=msg_list,
                    )
                except Exception as e:
                    logger.warning(f"HTML渲染开播推送失败: {e}")
                    text = (
                        f"🔴 {name} 开播啦！\n"
                        f"━━━━━━━━━━━━\n"
                        f"📺 {title}\n"
                        f"🏷️ {area}\n"
                        f"👤 {online} 人气\n"
                        f"🔗 {live_link(room_id)}"
                    )
                    await bot.call_api(
                        "send_group_msg",
                        group_id=int(group_id),
                        message=text,
                    )

                logger.info(f"推送开播通知 {name}({uid}) 到群 {group_id}")

            except Exception as e:
                logger.error(f"推送开播到群 {group_id} 失败: {e}")

    async def _push_live_end(self, bot, uid: int, info: dict):
        """推送下播通知"""
        name = info.get("uname", user_storage.get_name(uid))
        groups = sub_storage.get_groups_for_uid(uid)
        if not groups:
            return

        text = f"🔴 {name} 直播结束啦，下次见~"

        for group_id in groups:
            try:
                await bot.call_api(
                    "send_group_msg",
                    group_id=int(group_id),
                    message=text,
                )
            except Exception as e:
                logger.error(f"推送下播到群 {group_id} 失败: {e}")


# ========== 启动 ==========

live_checker = LiveChecker()


def start_live_checker():
    """启动直播定时检测"""
    scheduler.add_job(
        live_checker.check,
        "interval",
        seconds=15,
        id="bilibili_live_check",
        misfire_grace_time=30,
        max_instances=2,
    )
    logger.info("B站直播检测已启动(15秒间隔)")
