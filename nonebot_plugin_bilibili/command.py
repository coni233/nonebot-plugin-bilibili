"""B站通知插件 — 所有命令处理

使用 on_command 实现命令系统。
权限规则：
- 群聊中：管理员(群管理/群主)可用管理命令，普通成员仅可查看
- 私聊中：仅超级用户可用命令且必须回复；普通成员静默忽略
"""

import os
from typing import Optional

from nonebot import get_bot, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
# from nonebot.params import ArgPlainText, CommandArg  # 改用 event.get_plaintext
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me

try:
    from nonebot_plugin_htmlrender import html_to_pic
except ImportError:
    html_to_pic = None

from .client import bili_client
from .login import do_login, poll_login
from .model import sub_storage, user_storage
from .utils import dynamic_link, live_link, space_link

# ========== 权限辅助 ==========

def is_group_admin(event: MessageEvent) -> bool:
    """检查是否为群管理员或群主"""
    if isinstance(event, PrivateMessageEvent):
        return False
    if not isinstance(event, GroupMessageEvent):
        return False
    # 群主或管理员
    return event.sender.role in ("admin", "owner")

def can_manage(event: MessageEvent) -> bool:
    """是否可以执行管理操作：超级用户 + 群管理员(在群聊中)"""
    if isinstance(event, PrivateMessageEvent):
        return event.user_id in get_superusers()
    return is_group_admin(event) or event.user_id in get_superusers()

def get_superusers() -> set:
    """获取超级用户列表（返回 int 集合）"""
    from nonebot import get_driver
    raw = get_driver().config.superusers
    return {int(u) for u in raw}

def should_reply(event: MessageEvent) -> bool:
    """判断是否需要回复：
    - 群聊中总是回复
    - 私聊中仅回复超级用户
    """
    if isinstance(event, GroupMessageEvent):
        return True
    if isinstance(event, PrivateMessageEvent):
        return event.user_id in get_superusers()
    return False

def get_group_id(event: MessageEvent) -> str:
    """获取当前群ID"""
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return f"private_{event.user_id}"

def _parse_parts(event: MessageEvent, parts: list, min_count: int) -> tuple:
    """解析命令参数，超级用户私聊时末尾可加群号
    返回: (去掉群号的parts, 群号str或None)
    群聊中忽略末尾数字，始终用当前群号
    """
    if isinstance(event, GroupMessageEvent):
        return parts, None
    # 私聊仅超级用户可指定群
    if event.user_id not in get_superusers():
        return parts, None
    if len(parts) > min_count and parts[-1].lstrip("-").isdigit():
        return parts[:-1], parts[-1]
    return parts, None

async def reply(event: MessageEvent, message: str, matcher: Matcher):
    """安全回复消息"""
    if not should_reply(event):
        return
    if isinstance(event, GroupMessageEvent):
        await matcher.finish(message)
    else:
        await matcher.finish(message)

# ========== 命令定义 ==========

from nonebot import get_plugin_config
from .config import BiliPluginConfig

_plugin_config = get_plugin_config(BiliPluginConfig)

# 统一使用 on_command（简单可靠）
# 注意：不采用 Alconna 是因为 on_alconna + 无 handler 会导致命令无响应
bili_cmd = on_command("bili", priority=_plugin_config.command_priority, block=True)

@bili_cmd.handle()
async def bili_handle(
    matcher: Matcher,
    event: MessageEvent,
):
    # 直接从事件提取纯文本，避免 CommandArg 依赖注入问题
    text = event.get_plaintext().strip()
    # 去掉命令前缀 /bili
    for prefix in ["/bili ", "/bili"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    parts = text.split()
    if not parts:
        await help_command(event, matcher)
        return

    cmd = parts[0]

    if cmd == "login":
        await login_command(event, matcher)
    elif cmd == "add":
        if len(parts) < 2:
            await reply(event, "用法: /bili add <uid> [目标]", matcher)
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await reply(event, "UID 必须是数字", matcher)
            return
        target = parts[2] if len(parts) > 2 else ""
        await add_command(event, matcher, uid, target)
    elif cmd == "del":
        if len(parts) < 2:
            await reply(event, "用法: /bili del <uid> [目标]", matcher)
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await reply(event, "UID 必须是数字", matcher)
            return
        target = parts[2] if len(parts) > 2 else ""
        await del_command(event, matcher, uid, target)
    elif cmd == "list":
        _, gid = _parse_parts(event, parts, 1)
        if gid is None:
            gid = get_group_id(event)
        else:
            gid = gid.lstrip("-")
        await list_command(event, matcher, gid)
    elif cmd == "listall":
        await listall_command(event, matcher)
    elif cmd == "delall":
        _, gid = _parse_parts(event, parts, 1)
        if gid is None:
            gid = get_group_id(event)
        else:
            gid = gid.lstrip("-")
        await delall_command(event, matcher, gid)
    elif cmd == "delallall":
        await delallall_command(event, matcher)
    elif cmd == "atall":
        p, gid = _parse_parts(event, parts, 4)
        if gid is None:
            gid = get_group_id(event)
        else:
            gid = gid.lstrip("-")
        # list 子命令特殊处理：/bili atall list 只有两个参数
        if len(p) > 1 and p[1] == "list":
            await atall_command(event, matcher, "", "list", 0, gid)
            return
        atype = p[1] if len(p) > 1 else ""
        action = p[2] if len(p) > 2 else ""
        uid = int(p[3]) if len(p) > 3 and p[3].isdigit() else 0
        await atall_command(event, matcher, atype, action, uid, gid)
    elif cmd == "filter":
        p, gid = _parse_parts(event, parts, 2)
        if gid is None:
            gid = get_group_id(event)
        else:
            gid = gid.lstrip("-")
        action = p[1] if len(p) > 1 else ""
        keyword = p[2] if len(p) > 2 else ""
        await filter_command(event, matcher, action, keyword, gid)

    elif cmd in ("help", "h", "?"):
        await help_command(event, matcher)
    else:
        await reply(event, f"未知命令: {cmd}\n使用 /bili help 查看帮助", matcher)

# ========== 命令处理函数 ==========

async def login_command(event: MessageEvent, matcher: Matcher):
    """扫码登录"""
    if event.user_id not in get_superusers():
        await reply(event, "仅超级用户可以执行登录", matcher)
        return
    if not should_reply(event):
        return

    # 获取二维码
    result = await do_login()
    if result is None:
        await reply(event, "❌ 获取二维码失败，请检查网络", matcher)
        return

    qrcode_key, qrcode_url, png_data = result

    # 发送二维码图片
    try:
        await matcher.send(MessageSegment.image(png_data))
        await matcher.send("请使用 Bilibili 手机APP 扫描二维码登录 (3分钟有效)")
    except Exception as e:
        # 图片发送失败，发送链接
        await matcher.send(f"请扫码登录 (二维码生成失败: {e})\n{qrcode_url}")

    # 轮询登录结果
    cookie = await poll_login(qrcode_key)
    if cookie:
        await reply(event, "✅ 登录成功！Cookie已保存", matcher)
        logger.success(f"B站登录成功: UID={cookie_storage.uid}")
    else:
        await reply(event, "❌ 登录超时或失败，请重试", matcher)

async def add_command(event: MessageEvent, matcher: Matcher, uid: int, target: str = ""):
    """订阅UP主 — target 可以是群号或分组名"""
    if not can_manage(event):
        await reply(event, "❌ 权限不足，仅管理员/超级用户可订阅", matcher)
        return
    if not should_reply(event):
        return

    # 解析目标
    if not target:
        group_ids = [get_group_id(event)]
    elif target.lstrip("-").isdigit():
        group_ids = [target.lstrip("-")]
    else:
        await reply(event, "⚠️ 不支持分组订阅", matcher)
        return

    # 获取UP主名称
    try:
        user_info = await bili_client.get_user_info(uid)
        name = user_info.get("name", str(uid)) if user_info else str(uid)
        user_storage.set_name(uid, name)
        if user_info:
            face = user_info.get("face", "")
            if face:
                user_storage.set_face(uid, face)
    except Exception as e:
        logger.warning(f"获取用户信息失败: {e}")
        name = str(uid)

    # 逐个添加
    added = 0
    for gid in group_ids:
        current_uids = sub_storage.get_group_uids(gid)
        if uid not in current_uids:
            sub_storage.add_group_uid(gid, uid)
            added += 1

    if added > 0:
        await reply(event, f"✅ 订阅成功!\n📺 {name}\n🔗 UID: {uid}", matcher)
    else:
        await reply(event, f"⚠️ {name}({uid}) 已在所有目标中", matcher)

async def del_command(event: MessageEvent, matcher: Matcher, uid: int, target: str = ""):
    """取消订阅 — target 可以是群号或分组名"""
    if not can_manage(event):
        await reply(event, "❌ 权限不足", matcher)
        return
    if not should_reply(event):
        return

    # 解析目标
    if not target:
        group_ids = [get_group_id(event)]
    elif target.lstrip("-").isdigit():
        group_ids = [target.lstrip("-")]
    else:
        await reply(event, "⚠️ 不支持分组订阅", matcher)
        return

    removed = 0
    for gid in group_ids:
        if sub_storage.remove_group_uid(gid, uid):
            removed += 1

    name = user_storage.get_name(uid) or str(uid)
    if removed > 0:
        await reply(event, f"✅ 已取消订阅 {name}({uid})，共 {removed} 个目标", matcher)
    else:
        await reply(event, f"⚠️ 未订阅该UP主(UID: {uid})", matcher)

async def list_command(event: MessageEvent, matcher: Matcher, target_group: str = None):
    """查看本群订阅"""
    if not should_reply(event):
        return

    if target_group:
        group_id = target_group.lstrip("-")
    else:
        group_id = get_group_id(event)
    uids = sub_storage.get_group_uids(group_id)

    if not uids:
        await reply(event, "📭 本群暂无订阅", matcher)
        return

    lines = ["📋 本群订阅列表:"]
    for uid in uids:
        name = user_storage.get_name(uid)
        if name:
            lines.append(f"• {name} (UID: {uid})")
        else:
            lines.append(f"• UID: {uid}")
    lines.append(f"\n共 {len(uids)} 个订阅")

    await reply(event, "\n".join(lines), matcher)

async def listall_command(event: MessageEvent, matcher: Matcher):
    """查看全部订阅(超级用户)"""
    if event.user_id not in get_superusers():
        await reply(event, "❌ 仅超级用户可查看全部订阅", matcher)
        return
    if not should_reply(event):
        return

    groups_info = []
    total = 0
    for gid, data in sub_storage.items():
        uids = data.get("uids", [])
        if not uids:
            continue
        total += len(uids)
        names = []
        for uid in uids:
            name = user_storage.get_name(uid) or str(uid)
            names.append(name)
        groups_info.append(f"📌 {gid}: {', '.join(names)}")

    if not groups_info:
        await reply(event, "📭 暂无任何订阅", matcher)
        return

    text = f"📋 全部订阅 (共 {total} 个):\n\n" + "\n".join(groups_info)
    await reply(event, text, matcher)

async def delall_command(event: MessageEvent, matcher: Matcher, target_group: str = None):
    """清除本群所有订阅"""
    if not can_manage(event):
        await reply(event, "❌ 权限不足", matcher)
        return
    if not should_reply(event):
        return

    if target_group:
        group_id = target_group.lstrip("-")
    else:
        group_id = get_group_id(event)
    count = sub_storage.clear_group(group_id)
    await reply(event, f"✅ 已清除本群所有订阅 (共 {count} 个)", matcher)

async def delallall_command(event: MessageEvent, matcher: Matcher):
    """删除所有订阅(超级用户)"""
    if event.user_id not in get_superusers():
        await reply(event, "❌ 仅超级用户可删除全部订阅", matcher)
        return
    if not should_reply(event):
        return

    count = sub_storage.clear_all()
    await reply(event, f"✅ 已删除所有订阅 (共 {count} 个)", matcher)

async def atall_command(event: MessageEvent, matcher: Matcher, atype: str, action: str, uid: int, target_group: str = None):
    """@全体管理"""
    if not can_manage(event):
        await reply(event, "❌ 权限不足", matcher)
        return
    if not should_reply(event):
        return

    if target_group:
        group_id = target_group.lstrip("-")
    else:
        group_id = get_group_id(event)

    # 列表
    if action == "list":
        lines = ["🔔 本群@全体:"]
        for uid_str, types in sub_storage.get(group_id, {}).get("atall", {}).items():
            name = user_storage.get_name(int(uid_str)) or uid_str
            types_str = ", ".join(types)
            lines.append(f"  {name}({uid_str}): {types_str}")
        if len(lines) == 1:
            lines.append("  无")
        await reply(event, "\n".join(lines), matcher)
        return

    # 校验类型
    valid_types = {"all", "dynamic", "video", "music", "article", "live"}
    if atype not in valid_types:
        await reply(event, "用法: /bili atall all|dynamic|video|music|article|live on|off <uid>\n类型: all(全部) dynamic(全部动态) video(视频) music(音乐) article(专栏) live(直播)", matcher)
        return

    if action in ("on", "1", "true", "yes"):
        if not uid:
            await reply(event, f"用法: /bili atall {atype} on <uid>", matcher)
            return
        current_uids = sub_storage.get_group_uids(group_id)
        if uid not in current_uids:
            await reply(event, f"⚠️ 尚未订阅该UP主(UID: {uid})，请先订阅", matcher)
            return
        sub_storage.set_atall(group_id, uid, atype, True)
        name = user_storage.get_name(uid) or str(uid)
        await reply(event, f"✅ 已开启 {name} 的{atype}@全体", matcher)

    elif action in ("off", "0", "false", "no"):
        if not uid:
            await reply(event, f"用法: /bili atall {atype} off <uid>", matcher)
            return
        sub_storage.set_atall(group_id, uid, atype, False)
        name = user_storage.get_name(uid) or str(uid)
        await reply(event, f"✅ 已关闭 {name} 的{atype}@全体", matcher)

    else:
        await reply(event, f"用法: /bili atall {atype} on|off <uid>", matcher)

async def filter_command(event: MessageEvent, matcher: Matcher, action: str, keyword: str, target_group: str = None):
    """推送过滤管理"""
    if not can_manage(event):
        await reply(event, "❌ 权限不足", matcher)
        return
    if not should_reply(event):
        return

    if target_group:
        group_id = target_group.lstrip("-")
    else:
        group_id = get_group_id(event)

    if action == "list":
        filters = sub_storage.get(group_id, {}).get("filters", [])
        if not filters:
            await reply(event, "📭 本群暂无过滤规则", matcher)
            return
        lines = ["🔍 本群过滤规则:"]
        for i, f in enumerate(filters):
            lines.append(f"  {i+1}. [{f.get('type','regex')}] {f.get('keyword','')}")
        await reply(event, "\n".join(lines), matcher)

    elif action == "add":
        if not keyword:
            await reply(event, "用法: /bili filter add <keyword>", matcher)
            return
        data = sub_storage.get(group_id, {"uids": [], "atall": {}, "filters": []})
        filters = data.get("filters", [])
        # 检查是否已存在
        if any(f.get("keyword") == keyword for f in filters):
            await reply(event, f"⚠️ 过滤词 '{keyword}' 已存在", matcher)
            return
        filters.append({"type": "regex", "keyword": keyword})
        sub_storage[group_id] = {**data, "filters": filters}
        sub_storage.save()
        await reply(event, f"✅ 已添加过滤词: {keyword}", matcher)

    elif action == "del":
        if not keyword:
            await reply(event, "用法: /bili filter del <id或关键词>", matcher)
            return
        data = sub_storage.get(group_id, {"uids": [], "atall": {}, "filters": []})
        filters = data.get("filters", [])
        # 支持按序号或关键词删除
        if keyword.isdigit():
            idx = int(keyword) - 1
            if 0 <= idx < len(filters):
                removed = filters.pop(idx)
                sub_storage[group_id] = {**data, "filters": filters}
                sub_storage.save()
                await reply(event, f"✅ 已删除过滤规则: {removed.get('keyword')}", matcher)
            else:
                await reply(event, f"❌ 序号无效", matcher)
        else:
            new_filters = [f for f in filters if f.get("keyword") != keyword]
            if len(new_filters) < len(filters):
                sub_storage[group_id] = {**data, "filters": new_filters}
                sub_storage.save()
                await reply(event, f"✅ 已删除过滤词: {keyword}", matcher)
            else:
                await reply(event, f"⚠️ 未找到过滤词: {keyword}", matcher)
    else:
        await reply(event, "用法: /bili filter add/del/list [keyword]", matcher)


async def help_command(event: MessageEvent, matcher: Matcher):
    """显示帮助"""
    if not should_reply(event):
        return

    try:
        if html_to_pic is not None:
            import jinja2

            template_dir = os.path.join(os.path.dirname(__file__), "templates")
            env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(template_dir),
                autoescape=True,
            )
            template = env.get_template("help.html")
            html = template.render()
            import base64
            pic_bytes = await html_to_pic(html, viewport={"width": 540, "height": 10})
            pic_b64 = "base64://" + base64.b64encode(pic_bytes).decode()
            await matcher.finish(MessageSegment.image(pic_b64))
            return
        else:
            await text_help(event, matcher)
            return
    except Exception as e:
        from nonebot.exception import FinishedException
        if isinstance(e, FinishedException):
            raise
        logger.warning(f"HTML帮助渲染失败: {e}")
        await text_help(event, matcher)

async def text_help(event: MessageEvent, matcher: Matcher):
    """文本版帮助"""
    help_text = (
        "🅱️ B站通知助手\n"
        "━━━━━━━━━━━━\n\n"
        "📋 订阅管理:\n"
        "  /bili add <uid> - 订阅UP主\n"
        "  /bili del <uid> - 取消订阅\n"
        "  /bili list - 本群订阅列表\n"
        "  /bili listall - 全部订阅(超管)\n"
        "  /bili delall - 清空本群订阅\n"
        "  /bili delallall - 删除全部(超管)\n\n"
        "🔔 @全体:\n"
        "  /bili atall all/dynamic/video/music/article/live on/off <uid>\n"
        "  /bili atall list\n\n"
        "🔍 过滤:\n"
        "  /bili filter add/del/list\n\n"
        "⚙️ 系统:\n"
        "  /bili login - 扫码登录(超管)\n"
        "  /bili help - 显示帮助\n\n"
        "💡 私聊仅超级用户可用"
    )
    await matcher.finish(help_text)
