"""B站通知插件 — 工具函数"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional


# ========== 链接生成 ==========

def dynamic_link(did: str) -> str:
    """生成动态链接"""
    return f"https://t.bilibili.com/{did}"


def video_link(aid: str) -> str:
    """生成视频链接"""
    return f"https://www.bilibili.com/video/av{aid}"


def video_link_bv(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}"


def article_link(cvid: str) -> str:
    """生成专栏链接"""
    return f"https://www.bilibili.com/read/cv{cvid}"


def live_link(room_id: int) -> str:
    """生成直播链接"""
    return f"https://live.bilibili.com/{room_id}"


def space_link(uid: int) -> str:
    """生成空间链接"""
    return f"https://space.bilibili.com/{uid}"


def episode_link(eid: str) -> str:
    """生成番剧链接"""
    return f"https://www.bilibili.com/bangumi/play/ep{eid}"


# ========== 时间格式化 ==========

def format_timestamp(ts: int) -> str:
    """将时间戳格式化为可读时间"""
    dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
    now = datetime.now(tz=timezone(timedelta(hours=8)))
    diff = now - dt

    if diff.days == 0:
        if diff.seconds < 60:
            return "刚刚"
        elif diff.seconds < 3600:
            return f"{diff.seconds // 60}分钟前"
        else:
            return f"{diff.seconds // 3600}小时前"
    elif diff.days == 1:
        return "昨天"
    elif diff.days < 7:
        return f"{diff.days}天前"
    else:
        return dt.strftime("%m-%d %H:%M")


def format_time_full(ts: int) -> str:
    """完整时间格式"""
    dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ========== Emoji 正则 ==========

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # 表情符号
    "\U0001F300-\U0001F5FF"  # 符号和杂项
    "\U0001F680-\U0001F6FF"  # 交通和地图
    "\U0001F1E0-\U0001F1FF"  # 国旗
    "\U00002702-\U000027B0"  # 其他符号
    "\U000024C2-\U0001F251"
    "\u200d"  # 零宽连接符
    "\u2600-\u27BF"
    "\u2B55"
    "\u3030"
    "\u303D"
    "\u3297"
    "\u3299"
    "\ufe0f"  # 变体选择符
    "]+",
    re.UNICODE,
)


def extract_text_plain(rich_text_nodes: list) -> str:
    """从动态富文本节点提取纯文本"""
    parts = []
    for node in rich_text_nodes:
        parts.append(node.get("text", ""))
    return "".join(parts)


def rich_text_to_html(nodes: list) -> str:
    """将动态富文本节点列表转换为 HTML（处理 emoji/at/链接等）"""
    if not nodes:
        return ""
    import html as _html

    parts = []
    for node in nodes:
        t = node.get("type", "")
        text = node.get("text", "")
        if t == "RICH_TEXT_NODE_TYPE_TEXT":
            parts.append(_html.escape(text))
        elif t == "RICH_TEXT_NODE_TYPE_EMOJI":
            emoji = node.get("emoji") or {}
            icon_url = emoji.get("icon_url", "")
            if icon_url:
                alt = _html.escape(text)
                parts.append(f'<img class="bili-emoji" src="{icon_url}" alt="{alt}">')
            else:
                parts.append(_html.escape(text))
        elif t == "RICH_TEXT_NODE_TYPE_WEB":
            url = node.get("jump_url", "")
            if url:
                parts.append(f'<a href="{url}">{_html.escape(text)}</a>')
            else:
                parts.append(_html.escape(text))
        elif t == "RICH_TEXT_NODE_TYPE_AT":
            parts.append(f'<span class="at">@{_html.escape(text)}</span>')
        elif t == "RICH_TEXT_NODE_TYPE_TOPIC":
            url = node.get("jump_url", "")
            if url:
                parts.append(f'<a href="{url}" class="topic">{_html.escape(text)}</a>')
            else:
                parts.append(f'<span class="topic">{_html.escape(text)}</span>')
        elif t == "RICH_TEXT_NODE_TYPE_BV":
            url = node.get("jump_url", "")
            if url:
                parts.append(f'<a href="{url}">{_html.escape(text)}</a>')
            else:
                parts.append(_html.escape(text))
        elif t == "RICH_TEXT_NODE_TYPE_VOTE":
            parts.append(f'<span class="vote">{_html.escape(text)}</span>')
        elif t == "RICH_TEXT_NODE_TYPE_LOTTERY":
            parts.append(f'<span class="lottery">{_html.escape(text)}</span>')
        elif t == "RICH_TEXT_NODE_TYPE_GOODS":
            url = node.get("jump_url", "")
            if url:
                parts.append(f'<a href="{url}">{_html.escape(text)}</a>')
            else:
                parts.append(_html.escape(text))
        else:
            # 未知类型，取文本
            parts.append(_html.escape(text))
    return "".join(parts)





# ========== 动态类型映射 ==========

DYNAMIC_TYPE_MAP = {
    "DYNAMIC_TYPE_WORD": "文字动态",
    "DYNAMIC_TYPE_DRAW": "图片动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
    "DYNAMIC_TYPE_AV": "投稿视频",
    "DYNAMIC_TYPE_MUSIC": "音乐",
    "DYNAMIC_TYPE_LIVE": "直播",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播",
    "DYNAMIC_TYPE_PGC": "番剧",
    "DYNAMIC_TYPE_PGC_UNION": "番剧",
    "DYNAMIC_TYPE_COMMON_SQUARE": "动态",
    "DYNAMIC_TYPE_COMMON_VERTICAL": "动态",
    "DYNAMIC_TYPE_UGC_SEASON": "合集",
    "DYNAMIC_TYPE_NONE": "动态已删除",
    "MAJOR_TYPE_ARCHIVE": "视频",
    "MAJOR_TYPE_DRAW": "图片",
    "MAJOR_TYPE_ARTICLE": "专栏",
    "MAJOR_TYPE_MUSIC": "音乐",
    "MAJOR_TYPE_LIVE": "直播",
    "MAJOR_TYPE_LIVE_RCMD": "直播",
    "MAJOR_TYPE_PGC": "番剧",
    "MAJOR_TYPE_COMMON": "活动",
    "MAJOR_TYPE_OPUS": "新动态",
}


def get_dynamic_type_text(type_str: str) -> str:
    return DYNAMIC_TYPE_MAP.get(type_str, type_str)


# ========== 数字格式化 ==========

def format_number(num: int) -> str:
    """格式化数字 (万)"""
    if num >= 10000:
        return f"{num / 10000:.1f}万"
    return str(num)
