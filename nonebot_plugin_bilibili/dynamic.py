"""B站通知插件 — 动态定时检测与推送"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Set

from nonebot import get_bot, require
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_htmlrender")

from nonebot_plugin_apscheduler import scheduler

from .client import bili_client
from .config import BiliCheckConfig
from .model import get_data_dir, sub_storage, user_storage
from .utils import (
    format_timestamp,
    format_time_full,
    get_dynamic_type_text,
    dynamic_link,
    video_link,
    article_link,
    extract_text_plain,
    rich_text_to_html,
)


# ========== 动态消息构建 ==========

class DynamicMessage:
    """一条待推送的动态消息"""

    def __init__(self, data: dict):
        self._data = data
        self.mid: int = self._get_mid()
        self.did: str = self._data.get("id_str", "0")
        self.type_str: str = self._data.get("type", "")
        self.type_text: str = get_dynamic_type_text(self.type_str)
        self.pub_ts: int = self._get_pub_ts()
        self.time: str = format_timestamp(self.pub_ts)
        self.full_time: str = format_time_full(self.pub_ts)

        modules = self._data.get("modules", {})
        author = modules.get("module_author", {})
        self.name: str = author.get("name", "未知")
        self.avatar: str = author.get("face", "")
        self.vip_type: int = author.get("vip", {}).get("type", 0)

        dynamic = modules.get("module_dynamic", {})
        self.content: str = self._extract_content(dynamic)
        self.content_html: str = self._extract_content_html(dynamic)
        self.images: List[str] = self._extract_images(dynamic)
        self.media_type: str = ""
        self.media_title: str = ""
        self.media_desc: str = ""
        self.media_cover: str = ""
        self.media_link: str = ""
        self.media_badge: str = ""
        self._extract_media(dynamic)

        # 转发内容
        self.forward_name: str = ""
        self.forward_content: str = ""
        self.forward_content_html: str = ""
        self.forward_images: List[str] = []
        orig = self._data.get("orig")
        if orig:
            orig_author = orig.get("modules", {}).get("module_author", {})
            self.forward_name = orig_author.get("name", "")
            orig_dynamic = orig.get("modules", {}).get("module_dynamic", {})
            self.forward_content = self._extract_content(orig_dynamic)
            self.forward_content_html = self._extract_content_html(orig_dynamic)
            self.forward_images = self._extract_images(orig_dynamic)

        stat = modules.get("module_stat", {})
        self.comment_count: int = stat.get("comment", {}).get("count", 0)
        self.forward_count: int = stat.get("forward", {}).get("count", 0)
        self.like_count: int = stat.get("like", {}).get("count", 0)

    def _get_mid(self) -> int:
        modules = self._data.get("modules", {})
        author = modules.get("module_author", {})
        mid = author.get("mid", 0)
        return int(mid) if mid else 0

    def _get_pub_ts(self) -> int:
        modules = self._data.get("modules", {})
        author = modules.get("module_author", {})
        ts = author.get("pub_ts", 0)
        return int(ts) if ts else 0

    def _extract_content(self, dynamic: dict) -> str:
        desc = dynamic.get("desc")
        if desc:
            return extract_text_plain(desc.get("rich_text_nodes", []))

    def _extract_content_html(self, dynamic: dict) -> str:
        """从模块动态中提取 HTML 格式内容（处理 emoji 图片/链接等）"""
        desc = dynamic.get("desc")
        if desc:
            nodes = desc.get("rich_text_nodes", [])
            if nodes:
                return rich_text_to_html(nodes)
        # 无 rich_text_nodes 时 fallback 到 major 标题
        major = dynamic.get("major", {})
        if major:
            mt = major.get("type", "")
            if mt == "MAJOR_TYPE_OPUS":
                opus = major.get("opus", {})
                summary = opus.get("summary", {})
                if isinstance(summary, dict):
                    return rich_text_to_html(summary.get("rich_text_nodes", []))
                return summary if summary else ""
            archive = major.get("archive")
            if archive:
                return archive.get("title", "")
            article = major.get("article")
            if article:
                return article.get("title", "")
        return ""

    def _extract_images(self, dynamic: dict) -> List[str]:
        major = dynamic.get("major", {})
        if not major:
            return []
        mt = major.get("type", "")
        if mt == "MAJOR_TYPE_DRAW":
            draw = major.get("draw", {})
            return [item.get("src", "") for item in draw.get("items", [])]
        if mt == "MAJOR_TYPE_OPUS":
            opus = major.get("opus", {})
            return [pic.get("url", "") for pic in opus.get("pics", [])]
        archive = major.get("archive")
        if archive and archive.get("cover"):
            return [archive["cover"]]
        article = major.get("article")
        if article and article.get("covers"):
            return article["covers"]
        if mt in ("MAJOR_TYPE_PGC", "MAJOR_TYPE_LIVE", "MAJOR_TYPE_MUSIC"):
            for key in ("pgc", "live", "music"):
                obj = major.get(key)
                if obj and obj.get("cover"):
                    return [obj["cover"]]
        return []

    def _extract_media(self, dynamic: dict):
        major = dynamic.get("major", {})
        if not major:
            return
        mt = major.get("type", "")

        if mt == "MAJOR_TYPE_ARCHIVE":
            archive = major.get("archive", {})
            self.media_type = "视频"
            self.media_title = archive.get("title", "")
            self.media_desc = archive.get("desc", "")
            self.media_cover = archive.get("cover", "")
            aid = archive.get("aid", "")
            self.media_link = video_link(str(aid))
            self.media_badge = archive.get("badge", {}).get("text", "视频")

        elif mt == "MAJOR_TYPE_ARTICLE":
            article = major.get("article", {})
            self.media_type = "专栏"
            self.media_title = article.get("title", "")
            self.media_desc = article.get("desc", "")
            covers = article.get("covers", [])
            self.media_cover = covers[0] if covers else ""
            cvid = article.get("id", "")
            self.media_link = article_link(cvid)
            self.media_badge = "专栏"

        elif mt == "MAJOR_TYPE_PGC":
            pgc = major.get("pgc", {})
            self.media_type = "番剧"
            self.media_title = pgc.get("title", "")
            self.media_cover = pgc.get("cover", "")
            self.media_badge = pgc.get("badge", {}).get("text", "番剧")

        elif mt == "MAJOR_TYPE_LIVE":
            live = major.get("live", {})
            self.media_type = "直播回放"
            self.media_title = live.get("title", "")
            self.media_cover = live.get("cover", "")

    @property
    def template_data(self) -> dict:
        return {
            "name": self.name,
            "avatar": self.avatar,
            "vip_type": self.vip_type,
            "time": self.time,
            "pub_time": self.full_time,
            "type_text": self.type_text,
            "content": self.content,
            "content_html": self.content_html or None,
            "forward_name": self.forward_name or None,
            "forward_content": self.forward_content or None,
            "forward_content_html": self.forward_content_html or None,
            "forward_images": self.forward_images[:9] if self.forward_images else None,
            "images": self.images[:9] if self.images else None,
            "media_title": self.media_title or None,
            "media_desc": self.media_desc or None,
            "media_cover": self.media_cover or None,
            "media_link": self.media_link or None,
            "media_badge": self.media_badge or None,
            "show_stats": True,
            "comment_count": self.comment_count,
            "forward_count": self.forward_count,
            "like_count": self.like_count,
            "dynamic_id": self.did,
        }

    @property
    def text_message(self) -> str:
        """纯文本推送"""
        lines = [
            f"📢 {self.name} @{self.type_text}",
            f"⏰ {self.time}",
            "",
        ]
        if self.content:
            # 截断过长内容
            text = self.content[:200]
            if len(self.content) > 200:
                text += "..."
            lines.append(text)
            lines.append("")
        if self.media_link:
            lines.append(f"🔗 {self.media_link}")
        elif self.did != "0":
            lines.append(f"🔗 {dynamic_link(self.did)}")
        return "\n".join(lines)


# ========== 动态检测 ==========

class DynamicChecker:
    """动态检测器"""

    def __init__(self):
        self._history: Set[str] = set()
        self._history_max = 500
        self._running = False
        self._history_file = get_data_dir() / "dynamic_history.json"
        self._load_history()

    def _load_history(self):
        """从磁盘加载已推送的动态 ID 历史（防止重启后重复推送）"""
        try:
            if self._history_file.exists():
                data = json.loads(self._history_file.read_text("utf-8"))
                if isinstance(data, list):
                    self._history = set(data)
                    logger.info(f"已加载 {len(self._history)} 条动态推送历史")
        except Exception as e:
            logger.warning(f"加载动态推送历史失败: {e}")

    def _save_history(self):
        """保存已推送的动态 ID 历史到磁盘"""
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text(
                json.dumps(list(self._history), ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存动态推送历史失败: {e}")

    async def check(self):
        """执行一次动态检测 — 按 UID 逐个查询空间动态（不依赖关注关系）"""
        if self._running:
            return
        self._running = True
        try:
            # 1. 获取所有已订阅 UID 及其群组订阅时间
            subscribed_uids = sub_storage.get_all_uids()
            if not subscribed_uids:
                return


            # 2. 按 UID 逐个拉取空间动态（避免关注列表限制）
            all_new_items: List[dict] = []
            uid_results: dict = {}  # 记录每个 UID 的拉取结果
            for uid in subscribed_uids:
                try:
                    data = await bili_client.get_user_dynamics(uid)
                    if not data:
                        uid_results[int(uid)] = {"fetched": 0, "new": 0, "error": "API返回空"}
                        continue

                    items = data.get("items", [])
                    new_count = 0

                    # 获取该 UID 在各群中最早的订阅时间（用于过滤旧动态）
                    groups = sub_storage.get_groups_for_uid(uid)
                    sub_times = [sub_storage.get_sub_time(g, uid) for g in groups]
                    min_sub_time = min(sub_times) if sub_times else 0

                    # 3. 过滤：去重 + 去旧（pub_ts 必须 >= 订阅时间）
                    for item in items:
                        did = item.get("id_str", "")
                        if not did or did in self._history:
                            continue
                        pub_ts = self._get_pub_ts(item)
                        if min_sub_time > 0 and pub_ts < min_sub_time:
                            continue
                        all_new_items.append(item)
                        self._history.add(did)
                        new_count += 1

                    uid_results[int(uid)] = {"fetched": len(items), "new": new_count}
                    await asyncio.sleep(0.8)  # 请求间隔，避免风控

                except Exception as e:
                    logger.error(f"动态检测: uid={uid} 查询失败: {e}")

            # 汇总本次检测结果
            if uid_results:
                parts = []
                for u, info in uid_results.items():
                    err = info.get("error", "")
                    if err:
                        parts.append(f"uid={u}({err})")
                    else:
                        parts.append(f"uid={u}(拉取{info['fetched']}条/新{info['new']}条)")


            # 限制历史大小
            if len(self._history) > self._history_max:
                self._history = set(list(self._history)[-self._history_max:])

            # 持久化推送历史到磁盘（防止重启后重复推送）
            self._save_history()

            if not all_new_items:
                return

            # 4. 按时间排序
            all_new_items.sort(key=lambda x: self._get_pub_ts(x))

            logger.info(f"动态检测: 发现 {len(all_new_items)} 条新动态待推送")

            # 5. 推送
            for item in all_new_items:
                await self._push(item)
                await asyncio.sleep(0.5)  # 间隔，避免风控

        except Exception as e:
            logger.error(f"动态检测异常: {e}")
        finally:
            self._running = False

    def _get_mid(self, item: dict) -> int:
        modules = item.get("modules", {})
        author = modules.get("module_author", {})
        mid = author.get("mid", 0)
        return int(mid) if mid else 0

    def _get_pub_ts(self, item: dict) -> int:
        modules = item.get("modules", {})
        author = modules.get("module_author", {})
        ts = author.get("pub_ts", 0)
        return int(ts) if ts else 0

    async def _push(self, item: dict):
        """推送一条动态到订阅群"""
        # 跳过直播类型动态（由 LiveChecker 处理）
        type_str = item.get("type", "")
        if type_str in ("DYNAMIC_TYPE_LIVE", "DYNAMIC_TYPE_LIVE_RCMD"):
            return

        msg = DynamicMessage(item)

        # 获取订阅了该UP主的所有群
        mid = self._get_mid(item)
        groups = sub_storage.get_groups_for_uid(mid)
        if not groups:
            return

        # 缓存UP主名称和头像
        user_storage.set_name(mid, msg.name)
        if msg.avatar:
            user_storage.set_face(mid, msg.avatar)

        # 获取bot
        try:
            bot = get_bot()
        except Exception:
            logger.warning("获取Bot失败")
            return

        for group_id in groups:
            try:
                # 检查过滤
                filters = sub_storage.get(group_id, {}).get("filters", [])
                if filters and any(f.get("keyword", "") in msg.content for f in filters if f.get("keyword")):
                    continue

                # 检查@全体（动态类型 → 映射具体子类型）—— 提前到外层，确保 fallback 也能用
                atall_type_map = {
                    "DYNAMIC_TYPE_AV": "video",
                    "DYNAMIC_TYPE_MUSIC": "music",
                    "DYNAMIC_TYPE_ARTICLE": "article",
                }
                msg_subtype = atall_type_map.get(msg.type_str, "dynamic")
                need_atall = sub_storage.check_atall(group_id, mid, msg_subtype)

                # 验证 @全体 权限 (NapCatQQ 会静默删除无权限时 at 标记)
                if need_atall:
                    try:
                        remain = await bot.call_api("get_group_at_all_remain", group_id=int(group_id))
                        can_at_all = remain.get("can_at_all", False)
                        remain_count = remain.get("remain_at_all_count_for_group", 0)
                        if not can_at_all:
                            logger.warning(f"[AtAll] 群 {group_id} 无 @全体 权限（bot 需为群主/管理员），已跳过")
                            need_atall = False
                        elif remain_count <= 0:
                            logger.warning(f"[AtAll] 群 {group_id} @全体 次数已用完，已跳过")
                            need_atall = False
                        else:
                            pass  # 有权限，允许发送
                    except Exception as e:
                        logger.warning(f"[AtAll] 查询群 {group_id} @全体 权限失败: {e}，跳过 @全体")
                        need_atall = False

                # 渲染HTML推送
                try:
                    import base64
                    from nonebot_plugin_htmlrender import html_to_pic
                    import jinja2
                    import os

                    # 使用群自定义模板：按动态子类型选择模板
                    _tpl_type_map = {
                        "DYNAMIC_TYPE_AV": "video",
                        "DYNAMIC_TYPE_PGC": "video",
                        "DYNAMIC_TYPE_PGC_UNION": "video",
                        "DYNAMIC_TYPE_UGC_SEASON": "video",
                        "DYNAMIC_TYPE_MUSIC": "video",
                    }
                    tpl_key = _tpl_type_map.get(msg.type_str, "dynamic")
                    tpl_key = f"template_{tpl_key}"
                    tpl_name = sub_storage.get(group_id, {}).get(tpl_key, "") or \
                               sub_storage.get(group_id, {}).get("template_dynamic", "") or \
                               "dynamic.html"
                    if not tpl_name.endswith(".html"):
                        tpl_name += ".html"
                    template_dir = os.path.join(os.path.dirname(__file__), "templates")
                    env = jinja2.Environment(
                        loader=jinja2.FileSystemLoader(template_dir),
                        autoescape=True,
                    )
                    template = env.get_template(tpl_name)
                    html = template.render(**msg.template_data)
                    pic_bytes = await html_to_pic(html, viewport={"width": 580, "height": 10})
                    pic_b64 = "base64://" + base64.b64encode(pic_bytes).decode()
                    message = Message(MessageSegment.image(pic_b64))
                except Exception as e:
                    logger.warning(f"HTML渲染推送失败，使用纯文本: {e}")
                    # 纯文本备用
                    text = f"📢 {msg.name} {msg.type_text}\n{msg.time}\n\n"
                    if msg.content:
                        text += f"{msg.content[:150]}\n\n"
                    text += f"🔗 {dynamic_link(msg.did)}"
                    message = Message(MessageSegment.text(text))

                # @全体 拼入主消息一起发送 (NapCatQQ 拒绝只有 at 的空消息体 retcode=1200)
                if need_atall:
                    message = MessageSegment.at("all") + message

                await bot.send_group_msg(
                    group_id=int(group_id),
                    message=message,
                )

                logger.info(f"推送动态 {msg.did} 到群 {group_id}")

            except Exception as e:
                logger.error(f"推送动态到群 {group_id} 失败: {e}")


# ========== 启动定时任务 ==========

dynamic_checker = DynamicChecker()


def start_dynamic_checker():
    """启动动态定时检测"""
    from nonebot_plugin_bilibili import plugin_config
    interval = plugin_config.check.interval
    scheduler.add_job(
        dynamic_checker.check,
        "interval",
        seconds=interval,
        id="bilibili_dynamic_check",
        replace_existing=True,
        misfire_grace_time=30,
    )
    logger.info(f"B站动态检测已启动({interval}秒间隔)")
