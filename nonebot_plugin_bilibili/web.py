"""B站通知插件 — 网页后台

.env 配置:
  bili_web_enable=true    (默认 true，设为 false 关闭后台)
  bili_web_password=密码  (设置后访问需登录)
"""

import hashlib
import os
import re
import time

from nonebot import get_driver, get_app
from nonebot.log import logger
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .model import sub_storage, cookie_storage, user_storage

router = APIRouter(prefix="/bili")


def _get_password() -> str:
    return str(getattr(get_driver().config, "bili_web_password", "") or "")


def _make_token(password: str) -> str:
    """生成 SHA256 鉴权令牌"""
    return hashlib.sha256(f"bili_web::{password}::salt_v2".encode()).hexdigest()


def _check_auth(request: Request) -> bool:
    pwd = _get_password()
    if not pwd:
        return True
    token = request.cookies.get("bili_token", "")
    # 兼容旧版 MD5 令牌
    if token == hashlib.md5(f"bili_web_{pwd}_salt".encode()).hexdigest():
        return True
    return token == _make_token(pwd)


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    path = os.path.join(os.path.dirname(__file__), "static", "login.html")
    if os.path.exists(path):
        return HTMLResponse(open(path, encoding="utf-8").read())
    return HTMLResponse("<h2>B站通知助手</h2><form action='/bili/api/login' method='post'><input name='password' type='password'><button>登录</button></form>")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not _check_auth(request):
        return RedirectResponse(url="/bili/login")
    return HTMLResponse(HTML_PAGE)


@router.post("/api/login")
async def api_login(request: Request):
    data = await request.json()
    password = data.get("password", "")
    pwd = _get_password()
    if pwd and password != pwd:
        return JSONResponse({"success": False, "error": "密码错误"}, status_code=401)
    token = _make_token(password)
    resp = JSONResponse({"success": True, "token": token})
    if pwd:
        resp.set_cookie(key="bili_token", value=token, httponly=True, max_age=86400 * 7)
    return resp


@router.get("/api/status")
async def api_status(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {
        "cookie_configured": bool(cookie_storage.cookie),
        "cookie_uid": cookie_storage.uid,
        "group_count": len(sub_storage.data),
        "up_count": len(sub_storage.get_all_uids()),
    }


@router.get("/api/groups")
async def api_groups(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    groups = []
    for gid, data in sub_storage.items():
        uids = data.get("uids", [])
        atall = data.get("atall", {})
        filters = data.get("filters", [])
        ups = [{"uid": uid, "name": user_storage.get_name(uid) or str(uid)} for uid in uids]
        tpl_dyn = data.get("template_dynamic", "")
        tpl_live = data.get("template_live", "")
        groups.append({"group_id": gid, "up_count": len(uids), "ups": ups, "atall": atall, "filters": filters, "filter_count": len(filters), "template_dynamic": tpl_dyn, "template_live": tpl_live})
    return {"groups": groups}


@router.get("/api/group/{group_id}")
async def api_group_detail(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    data = sub_storage.get(group_id)
    if data is None:
        return JSONResponse({"error": "未找到该群数据"}, status_code=404)
    uids = data.get("uids", [])
    ups = [{"uid": uid, "name": user_storage.get_name(uid) or str(uid)} for uid in uids]
    return {"group_id": group_id, "ups": ups, "atall": data.get("atall", {}), "filters": data.get("filters", [])}


@router.post("/api/group/{group_id}/add")
async def api_add_sub(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    uid = body.get("uid")
    if not uid:
        return JSONResponse({"error": "缺少 uid"}, status_code=400)
    try:
        uid = int(uid)
    except (ValueError, TypeError):
        return JSONResponse({"error": "uid 必须是数字"}, status_code=400)
    if uid in sub_storage.get_group_uids(group_id):
        return {"message": f"UID {uid} 已订阅"}
    from .client import bili_client
    try:
        info = await bili_client.get_user_info(uid)
        name = info.get("name", str(uid)) if info else str(uid)
        user_storage.set_name(uid, name)
        if info:
            face = info.get("face", "")
            if face:
                user_storage.set_face(uid, face)
    except Exception as e:
        logger.warning(f"获取用户信息失败: {e}")
        name = str(uid)
    sub_storage.add_group_uid(group_id, uid)
    return {"message": "ok"}


@router.post("/api/group/{group_id}/del")
async def api_del_sub(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    uid = body.get("uid")
    if not uid:
        return JSONResponse({"error": "缺少 uid"}, status_code=400)
    try:
        uid = int(uid)
    except (ValueError, TypeError):
        return JSONResponse({"error": "uid 必须是数字"}, status_code=400)
    if sub_storage.remove_group_uid(group_id, uid):
        return {"message": "ok"}
    return JSONResponse({"error": "未订阅该UP主"}, status_code=404)


@router.post("/api/group/{group_id}/delall")
async def api_group_delall(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    sub_storage.clear_group(group_id)
    return {"message": "ok"}


@router.post("/api/group/{group_id}/addall")
async def api_group_addall(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if group_id not in sub_storage:
        sub_storage[group_id] = {"uids": [], "atall": {}, "sub_time": {}}
        sub_storage.save()
    return {"message": "ok"}


@router.get("/api/groups/all")
async def api_groups_all(request: Request):
    """获取Bot加入的所有群"""
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        bot = None
        from nonebot import get_bot
        bot = get_bot()
        if bot:
            group_list = await bot.call_api("get_group_list")
            managed = set(sub_storage.data.keys())
            result = []
            for g in group_list:
                gid = str(g.get("group_id", ""))
                result.append({
                    "group_id": gid,
                    "group_name": g.get("group_name", ""),
                    "member_count": g.get("member_count", 0),
                    "managed": gid in managed,
                })
            return {"groups": result}
    except Exception as e:
        logger.warning(f"获取群列表失败: {e}")
    return JSONResponse({"error": "获取失败"}, status_code=500)


@router.post("/api/group/{group_id}/atall")
async def api_set_atall(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    uid = body.get("uid")
    atype = body.get("type", "all")
    enable = body.get("enable", True)
    if not uid:
        return JSONResponse({"error": "缺少 uid"}, status_code=400)
    try:
        uid = int(uid)
    except (ValueError, TypeError):
        return JSONResponse({"error": "uid 必须是数字"}, status_code=400)
    sub_storage.set_atall(group_id, uid, atype, enable)
    return {"message": "ok"}


@router.post("/api/group/{group_id}/filter")
async def api_add_filter(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    keyword = body.get("keyword", "").strip()
    if not keyword:
        return JSONResponse({"error": "缺少 keyword"}, status_code=400)
    gdata = sub_storage.get(group_id, {"filters": []})
    filters = gdata.get("filters", [])
    if any(f.get("keyword") == keyword for f in filters):
        return {"message": "已存在"}
    filters.append({"type": "regex", "keyword": keyword})
    sub_storage[group_id] = gdata
    sub_storage.save()
    return {"message": "ok"}


@router.post("/api/group/{group_id}/template")
async def api_group_template(request: Request, group_id: str):
    """设置群的推送模板"""
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    tpl = body.get("template", "")
    ttype = body.get("type", "dynamic")  # dynamic | live | video
    if ttype not in ("dynamic", "live", "video"):
        return JSONResponse({"error": "无效类型"}, status_code=400)
    # 验证模板文件存在
    if tpl:
        tdir = os.path.join(os.path.dirname(__file__), "templates")
        safe = os.path.normpath(os.path.join(tdir, os.path.basename(tpl if tpl.endswith(".html") else tpl + ".html")))
        if not safe.startswith(os.path.normpath(tdir)) or not os.path.exists(safe):
            return JSONResponse({"error": f"模板文件 '{tpl}' 不存在"}, status_code=400)
    data = sub_storage.get(group_id, {"uids": [], "atall": {}, "filters": []})
    key = f"template_{ttype}"
    if tpl:
        data[key] = tpl if tpl.endswith(".html") else tpl + ".html"
    else:
        data.pop(key, None)
    sub_storage[group_id] = data
    sub_storage.save()
    return {"message": "ok"}


@router.post("/api/group/{group_id}/filter/del")
async def api_del_filter(request: Request, group_id: str):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    keyword = body.get("keyword", "").strip()
    if not keyword:
        return JSONResponse({"error": "缺少 keyword"}, status_code=400)
    gdata = sub_storage.get(group_id, {"filters": []})
    filters = gdata.get("filters", [])
    new_filters = [f for f in filters if f.get("keyword") != keyword]
    if len(new_filters) == len(filters):
        return JSONResponse({"error": "未找到"}, status_code=404)
    sub_storage[group_id] = {**gdata, "filters": new_filters}
    sub_storage.save()
    return {"message": "ok"}


@router.get("/api/cookie/qrcode")
async def api_cookie_qrcode(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from .login import do_login
    result = await do_login()
    if result is None:
        return JSONResponse({"error": "获取二维码失败"}, status_code=500)
    import base64
    qrcode_key, qrcode_url, png_data = result
    png_b64 = base64.b64encode(png_data).decode()
    return {"qrcode_key": qrcode_key, "qrcode_url": qrcode_url, "qrcode_png": png_b64}


@router.get("/api/cookie/poll")
async def api_cookie_poll(request: Request, key: str = ""):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not key:
        return JSONResponse({"error": "缺少 key"}, status_code=400)
    from .login import poll_login
    cookie = await poll_login(key, timeout=180)
    if cookie:
        return {"success": True, "cookie": cookie, "uid": cookie_storage.uid}
    return {"success": False, "error": "登录超时或失败"}


@router.post("/api/cookie/save")
async def api_cookie_save(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    cookie = body.get("cookie", "").strip()
    if not cookie:
        return JSONResponse({"error": "Cookie 不能为空"}, status_code=400)
    cookie_storage.cookie = cookie
    import re
    m = re.search(r"DedeUserID=(\d+)", cookie)
    if m:
        cookie_storage.uid = int(m.group(1))
    cookie_storage.save()
    return {"message": "ok"}


@router.post("/api/cookie/clear")
async def api_cookie_clear(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cookie_storage.cookie = ""
    cookie_storage.uid = 0
    cookie_storage.save()
    return {"message": "ok"}


@router.post("/api/font/save")
async def api_font_save(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    font_name = body.get("font_name", "")
    logger.info(f"字体设置: {font_name}")
    return {"message": "ok"}


@router.get("/api/template/list")
async def api_template_list(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    tdir = os.path.join(os.path.dirname(__file__), "templates")
    files = []
    if os.path.exists(tdir):
        for f in sorted(os.listdir(tdir)):
            if f.endswith(".html"):
                files.append(f)
    if not files:
        files = ["dynamic.html", "live.html", "help.html"]
    return {"templates": files}


@router.get("/api/template/preview")
async def api_template_preview(request: Request, path: str = "dynamic.html", type: str = "dynamic"):
    """渲染模板为预览图。type=dynamic|live|video 决定填充的样本数据类型"""
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import base64
    tdir = os.path.join(os.path.dirname(__file__), "templates")
    safe = os.path.normpath(os.path.join(tdir, os.path.basename(path)))
    if not safe.startswith(os.path.normpath(tdir)) or not os.path.exists(safe):
        return JSONResponse({"error": "模板不存在"}, status_code=404)
    try:
        from nonebot_plugin_htmlrender import html_to_pic
        import jinja2
        # 读取模板
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(tdir),
            autoescape=True,
        )
        template = env.get_template(os.path.basename(path))
        # 根据类型填充样本数据
        if type == "live":
            sample_data = {
                "cover": "",
                "title": "【示例】今晚八点直播打游戏",
                "name": "示例主播",
                "avatar": "",
                "uid": 123456,
                "area": "虚拟主播",
                "start_time": "2025-01-15 20:00:00",
                "online": "1.2万",
                "online_raw": 12345,
                "live_link": "https://live.bilibili.com/12345",
                "room_id": 12345,
            }
        elif type == "video":
            sample_data = {
                "name": "示例UP主",
                "avatar": "",
                "time": "3小时前",
                "pub_time": "2025-01-15 17:00:00",
                "type_text": "投稿视频",
                "content": "今天发布了新视频，大家来看看吧~",
                "images": [],
                "media_type": "视频",
                "media_title": "【示例】这是一个视频标题",
                "media_desc": "视频简介内容",
                "media_cover": "",
                "media_link": "https://www.bilibili.com/video/av123456",
                "media_badge": "视频",
                "comment_count": 1234,
                "forward_count": 567,
                "like_count": 9999,
                "dynamic_id": "123456789",
            }
        else:  # dynamic
            sample_data = {
                "name": "示例UP主",
                "avatar": "",
                "time": "30分钟前",
                "pub_time": "2025-01-15 19:30:00",
                "type_text": "文字动态",
                "content": "今天天气真好~ 分享一张照片给大家",
                "content_html": "今天天气真好~ 分享一张照片给大家",
                "forward_name": None,
                "forward_content": None,
                "forward_content_html": None,
                "forward_images": None,
                "images": [],
                "media_title": None,
                "media_desc": None,
                "media_cover": None,
                "media_link": None,
                "media_badge": None,
                "comment_count": 233,
                "forward_count": 42,
                "like_count": 5678,
                "dynamic_id": "123456789",
            }
        html = template.render(**sample_data)
        pic_bytes = await html_to_pic(html, viewport={"width": 580, "height": 10})
        pic_b64 = base64.b64encode(pic_bytes).decode()
        return {"image": pic_b64}
    except Exception as e:
        logger.warning(f"模板预览失败: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/template")
async def api_template_get(request: Request, path: str = "dynamic.html"):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    tdir = os.path.join(os.path.dirname(__file__), "templates")
    safe = os.path.normpath(os.path.join(tdir, os.path.basename(path)))
    if not safe.startswith(os.path.normpath(tdir)):
        return JSONResponse({"error": "路径不合法"}, status_code=400)
    if not os.path.exists(safe):
        return JSONResponse({"error": "模板文件不存在"}, status_code=404)
    content = open(safe, encoding="utf-8").read()
    return {"path": path, "content": content}


@router.post("/api/template/save")
async def api_template_save(request: Request):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    path = body.get("path", "")
    content = body.get("content", "")
    if not path:
        return JSONResponse({"error": "路径不能为空"}, status_code=400)
    tdir = os.path.join(os.path.dirname(__file__), "templates")
    safe = os.path.normpath(os.path.join(tdir, os.path.basename(path)))
    if not safe.startswith(os.path.normpath(tdir)):
        return JSONResponse({"error": "路径不合法"}, status_code=400)
    with open(safe, "w", encoding="utf-8") as f:
        f.write(content)
    return {"message": "ok"}


@router.post("/api/push")
async def api_push(request: Request):
    """手动推送 — 自动识别类型并推送到订阅群"""
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    raw_id = body.get("id", "").strip()
    target_group = body.get("group_id", "").strip()
    if not raw_id:
        return JSONResponse({"error": "缺少 id"}, status_code=400)

    from .client import bili_client
    from .model import sub_storage, user_storage
    from .dynamic import DynamicMessage

    id_lower = raw_id.lower()
    uid = 0
    push_type = ""
    title = ""

    try:
        # === 1. 自动识别类型并获取数据 ===
        if id_lower.startswith("bv"):
            push_type = "video"
            info = await bili_client.get_video_detail(bvid=raw_id)
            if not info:
                return JSONResponse({"error": "视频不存在"}, status_code=404)
            uid = info.get("owner", {}).get("mid", 0)
            title = info.get("title", "")
            # 构建类动态数据用于模板渲染
            template_data = {
                "name": info.get("owner", {}).get("name", ""),
                "avatar": info.get("owner", {}).get("face", ""),
                "time": "手动推送",
                "pub_time": info.get("pubdate", ""),
                "type_text": "投稿视频",
                "content": info.get("desc", ""),
                "images": [info.get("pic", "")] if info.get("pic") else [],
                "media_type": "视频",
                "media_title": title,
                "media_desc": info.get("desc", ""),
                "media_cover": info.get("pic", ""),
                "media_link": f"https://www.bilibili.com/video/{raw_id}",
                "media_badge": "视频",
                "comment_count": info.get("stat", {}).get("reply", 0),
                "forward_count": info.get("stat", {}).get("share", 0),
                "like_count": info.get("stat", {}).get("like", 0),
                "dynamic_id": "",
            }

        elif id_lower.startswith("av"):
            aid = int(raw_id[2:])
            push_type = "video"
            info = await bili_client.get_video_detail(aid=aid)
            if not info:
                return JSONResponse({"error": "视频不存在"}, status_code=404)
            uid = info.get("owner", {}).get("mid", 0)
            bvid = info.get("bvid", raw_id)
            title = info.get("title", "")
            template_data = {
                "name": info.get("owner", {}).get("name", ""),
                "avatar": info.get("owner", {}).get("face", ""),
                "time": "手动推送",
                "pub_time": info.get("pubdate", ""),
                "type_text": "投稿视频",
                "content": info.get("desc", ""),
                "images": [info.get("pic", "")] if info.get("pic") else [],
                "media_type": "视频",
                "media_title": title,
                "media_desc": info.get("desc", ""),
                "media_cover": info.get("pic", ""),
                "media_link": f"https://www.bilibili.com/video/{bvid}",
                "media_badge": "视频",
                "comment_count": info.get("stat", {}).get("reply", 0),
                "forward_count": info.get("stat", {}).get("share", 0),
                "like_count": info.get("stat", {}).get("like", 0),
                "dynamic_id": "",
            }

        elif raw_id.isdigit():
            # 先尝试动态详情
            dyn_info = await bili_client.get_dynamic_detail(raw_id)
            if dyn_info and dyn_info.get("item"):
                item = dyn_info["item"]
                msg = DynamicMessage(item)
                uid = msg.mid
                title = msg.name
                # 根据动态子类型选择模板：视频/动态/其他
                if msg.type_str == "DYNAMIC_TYPE_AV":
                    push_type = "video"
                else:
                    push_type = "dynamic"
                template_data = msg.template_data
            else:
                # 再尝试直播间
                room_id = int(raw_id)
                live_info = await bili_client.get_live_room_info(room_id)
                if live_info and live_info.get("room_id"):
                    push_type = "live"
                    uid = live_info.get("uid", 0)
                    title = live_info.get("title", "未命名直播")
                    uname = live_info.get("uname", "")
                    cover = live_info.get("user_cover", "") or live_info.get("cover", "")
                    if cover.startswith("http://"):
                        cover = "https://" + cover[7:]
                    template_data = {
                        "cover": cover,
                        "title": title,
                        "name": uname,
                        "avatar": live_info.get("face", ""),
                        "uid": uid,
                        "area": live_info.get("area_name", ""),
                        "start_time": "手动推送",
                        "online": "0",
                        "online_raw": 0,
                        "live_link": f"https://live.bilibili.com/{room_id}",
                        "room_id": room_id,
                    }
                else:
                    return JSONResponse({"error": "未找到该动态或直播间"}, status_code=404)
        else:
            return JSONResponse({"error": "无法识别类型，支持: BV号、av号、动态ID、直播间号"}, status_code=400)

        if not uid:
            return JSONResponse({"error": "无法获取UP主信息"}, status_code=404)

        # 缓存UP主名称和头像
        if title:
            user_storage.set_name(uid, title.split("的")[0] if "的" in title else template_data.get("name", str(uid)))
        if template_data.get("avatar"):
            user_storage.set_face(uid, template_data["avatar"])

        # === 2. 确定目标群 ===
        if target_group:
            # 推送到指定群
            if target_group not in sub_storage:
                return JSONResponse({"error": f"群 {target_group} 未在管理中"}, status_code=404)
            groups = [target_group]
        else:
            # 推送到订阅了该UP主的所有群
            groups = sub_storage.get_groups_for_uid(uid)
            if not groups:
                return JSONResponse({"error": "该UP主未被任何群订阅，请指定群号"}, status_code=404)

        # === 3. 推送 ===
        import base64, os, jinja2
        from nonebot_plugin_htmlrender import html_to_pic
        from nonebot import get_bot

        try:
            bot = get_bot()
        except Exception:
            return JSONResponse({"error": "获取Bot失败"}, status_code=500)

        success_count = 0
        failed_groups = []
        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_dir),
            autoescape=True,
        )

        for group_id in groups:
            try:
                if push_type == "live":
                    tpl_name = sub_storage.get(group_id, {}).get("template_live", "") or "live.html"
                elif push_type == "video":
                    tpl_name = sub_storage.get(group_id, {}).get("template_video", "") or \
                               sub_storage.get(group_id, {}).get("template_dynamic", "") or "video.html"
                else:
                    tpl_name = sub_storage.get(group_id, {}).get("template_dynamic", "") or "dynamic.html"
                if not tpl_name.endswith(".html"):
                    tpl_name += ".html"

                template = env.get_template(tpl_name)
                html = template.render(**template_data)
                pic_bytes = await html_to_pic(html, viewport={"width": 580, "height": 10})
                pic_b64 = "base64://" + base64.b64encode(pic_bytes).decode()

                await bot.call_api(
                    "send_group_msg",
                    group_id=int(group_id),
                    message=[{"type": "image", "data": {"file": pic_b64}}],
                )
                success_count += 1
                logger.info(f"手动推送 {push_type} ({raw_id}) 到群 {group_id} 成功")
            except Exception as e:
                logger.error(f"手动推送到群 {group_id} 失败: {e}")
                failed_groups.append(group_id)

        return {
            "message": "ok",
            "push_type": push_type,
            "uid": uid,
            "title": title,
            "total_groups": len(groups),
            "success": success_count,
            "failed": failed_groups,
        }

    except Exception as e:
        logger.error(f"手动推送异常: {e}")
        return JSONResponse({"error": f"推送失败: {e}"}, status_code=500)


HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>B站通知助手</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:sans-serif;background:#f0f2f5;color:#333}
.header{background:linear-gradient(135deg,#fb7299,#178bcf);color:#fff;padding:16px 20px}
.header h1{font-size:18px}
.header p{font-size:12px;opacity:.8;margin-top:2px}
.tabs{display:flex;background:#fff;border-bottom:2px solid #e8e8e8}
.tab{padding:10px 16px;cursor:pointer;font-size:13px;border-bottom:2px solid transparent;margin-bottom:-2px;color:#9499a0}
.tab:hover{color:#178bcf}
.tab.cur{color:#178bcf;border-bottom-color:#178bcf}
.page{padding:12px 16px;display:none;padding-bottom:80px}
.page.cur{display:block}
/* APP 风格卡片 */
.card{background:#fff;border-radius:12px;padding:14px 16px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,.08);transition:box-shadow .2s}
.card:active{box-shadow:0 1px 3px rgba(0,0,0,.05)}
.card h3{font-size:15px;margin-bottom:8px}
.stat{display:inline-flex;flex-direction:column;align-items:center;padding:12px 20px;margin-right:8px;margin-bottom:8px;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.06);min-width:80px}
.stat .n{font-size:26px;font-weight:700;color:#178bcf;line-height:1.2}
.stat .l{font-size:11px;color:#9499a0;margin-top:2px}
.tag{display:inline-flex;align-items:center;padding:4px 12px;margin:3px;border-radius:16px;font-size:12px;background:#f0f4ff;color:#178bcf}
.tag .del{cursor:pointer;margin-left:6px;color:#999;font-weight:700}
.tag .del:hover{color:#fb7299}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:8px 18px;border:none;border-radius:10px;cursor:pointer;font-size:13px;font-weight:600;color:#fff;background:#178bcf;transition:opacity .2s;min-height:36px}
.btn:hover{opacity:.85}
.btn:active{opacity:.7}
.btn.green{background:#4caf50}
.btn.red{background:#fb7299}
.btn.sm{padding:5px 12px;font-size:11px;min-height:28px;border-radius:8px}
.btn.outline{background:transparent;color:#178bcf;border:1.5px solid #178bcf}
.in{padding:8px 12px;border:1.5px solid #e8e8e8;border-radius:10px;font-size:13px;outline:none;width:140px;transition:border-color .2s}
.in:focus{border-color:#178bcf}
.in.w{width:100%}
.mt{margin-top:10px}
.mb{margin-bottom:10px}
.w{width:100%}
label{font-size:12px;color:#666;display:block;margin-bottom:4px;font-weight:500}
textarea{width:100%;min-height:120px;padding:10px 12px;border:1.5px solid #e8e8e8;border-radius:10px;font-size:13px;font-family:monospace;resize:vertical}
textarea:focus{border-color:#178bcf;outline:none}
select{padding:6px 12px;border:1.5px solid #e8e8e8;border-radius:10px;font-size:12px;outline:none;background:#fff}
/* APP 风格开关按钮 */
.atag{display:inline-flex;align-items:center;justify-content:center;padding:4px 10px;margin:2px;border-radius:8px;font-size:11px;cursor:pointer;border:1.5px solid #ddd;transition:all .2s;font-weight:500}
.atag.on{background:#e8f5e9;color:#2e7d32;border-color:#a5d6a7}
.atag.off{background:#f5f5f5;color:#999;border-color:#e0e0e0}
.hide{display:none}
/* APP 底部操作栏 */
.app-bar{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #f0f0f0;display:flex;padding:8px 16px;gap:8px;z-index:100;box-shadow:0 -2px 8px rgba(0,0,0,.05)}
.app-bar .in{flex:1;width:auto}
/* 空状态 */
.empty-state{text-align:center;padding:60px 20px;color:#9499a0}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.empty-state p{font-size:14px}
/* 群标签 */
.group-chip{display:inline-flex;align-items:center;background:#f6f8fa;border-radius:8px;padding:6px 10px;margin:3px 4px;font-size:12px;color:#444;border:1px solid #f0f0f0}
.group-chip .del{cursor:pointer;margin-left:6px;color:#fb7299;font-weight:700}
/* UP主头像占位 */
.up-avatar{display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,#fb7299,#178bcf);color:#fff;font-size:16px;font-weight:700;flex-shrink:0}
/* 统计条 */
.stats-row{display:flex;gap:12px;margin-bottom:16px;overflow-x:auto;padding:2px}
.stats-row .stat{flex:1;min-width:70px}
/* 展开折叠 */
.toggle-area{overflow:hidden;transition:max-height .3s ease}
.toggle-area.collapsed{max-height:0!important}
.group-chip{cursor:pointer;user-select:none}
.group-chip:hover{background:#eef2f7!important}
/* ===== PC 端自适应 ===== */
@media (min-width: 768px){
  body{background:#eef1f5}
  .page{padding:16px 24px 100px}
  .card{padding:12px 16px;margin-bottom:10px}
  .stat{padding:10px 16px;min-width:60px}
  .stat .n{font-size:20px}
  /* 多列网格自适应 */
  .cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:12px}
  .app-bar{padding:10px 24px}
}
</style>
</head>
<body>

<div class="header">
  <h1>B站通知助手</h1>
  <p>Bilibili 动态/直播 订阅管理</p>
</div>

<div class="tabs" id="tabs"></div>

<div id="page-sub" class="page cur"><div id="sub-content"></div><div class="app-bar" id="sub-app-bar"></div></div>
<div id="page-push" class="page"></div>
<div id="page-font" class="page"></div>
<div id="page-tpl" class="page"></div>
<div id="page-cookie" class="page"></div>

<script>
var API="/bili/api";
var CUR="sub";
var TABS={sub:"订阅",push:"推送",font:"字体",tpl:"模板",cookie:"Cookie"};

function renderTabs(){
  var h="";
  for(var k in TABS){
    h+="<div class='tab"+(k==CUR?" cur":"")+"' data-tab='"+k+"'>"+TABS[k]+"</div>";
  }
  document.getElementById("tabs").innerHTML=h;
  document.querySelectorAll(".tab").forEach(function(t){
    t.onclick=function(){sw(this.dataset.tab)};
  });
}

function sw(k){
  CUR=k;
  renderTabs();
  document.querySelectorAll(".page").forEach(function(p){p.classList.remove("cur")});
  document.getElementById("page-"+k).classList.add("cur");
  if(k=="sub")loadSub();
  if(k=="push")loadPush();
  if(k=="cookie")loadCookie();
  if(k=="tpl")loadTpl();
  if(k=="font")renderFont();
}

function toast(m){
  var t=document.createElement("div");
  t.style.cssText="position:fixed;top:16px;right:16px;background:#323232;color:#fff;padding:10px 18px;border-radius:8px;font-size:13px;z-index:999";
  t.textContent=m;
  document.body.appendChild(t);
  setTimeout(function(){t.remove()},2500);
}

renderTabs();
loadSub();

// ==== 模板缓存与选项生成 ====
var _tplCache=null;
async function getTpls(){
  if(_tplCache)return _tplCache;
  var r=await(await fetch(API+"/template/list")).json();
  _tplCache=r.templates||["dynamic.html","live.html","help.html"];
  return _tplCache;
}
function tplOptions(cur,allTpls){
  var o='<option value="">默认</option>';
  if(!allTpls)return o;
  var curBase=cur?cur.replace(/\.html$/,""):"";
  allTpls.forEach(function(t){
    var val=t.replace(/\.html$/,"");
    var lb=val.charAt(0).toUpperCase()+val.slice(1);
    o+='<option value="'+val+'" '+(curBase==val?'selected':'')+'>'+lb+'</option>';
  });
  return o;
}

// ==== 订阅 ====
async function loadSub(){
  var s=await(await fetch(API+"/status")).json();
  if(s.error)return;
  var g=await(await fetch(API+"/groups")).json();
  var tpls=await getTpls();
  var upMap={}, emptyGroups=[];
  g.groups.forEach(function(gr){
    if(gr.ups.length==0){emptyGroups.push(gr);return}
    gr.ups.forEach(function(u){
      if(!upMap[u.uid])upMap[u.uid]={name:u.name,face:u.face||'',groups:[]};
      upMap[u.uid].groups.push({gid:gr.group_id,atall:gr.atall[String(u.uid)]||[],grpData:gr});
    });
  });
  // 统计行
  var html='<div class="stats-row"><div class="stat"><div class="n">'+s.group_count+'</div><div class="l">已管理</div></div><div class="stat"><div class="n">'+s.up_count+'</div><div class="l">UP主</div></div></div>';
  var uids=Object.keys(upMap).sort();
  html+='<div class="cards-grid" id="cards-grid">';
  // 空状态
  if(uids.length===0 && emptyGroups.length===0){
    html+='<div class="empty-state"><div class="icon">📭</div><p>暂无订阅</p><p style="font-size:12px;margin-top:8px">在下方输入群号开始管理</p></div>';
  }
  // UP主卡片
  uids.forEach(function(uid){
    var up=upMap[uid];
    var initial=(up.name||"U").charAt(0).toUpperCase();
    html+='<div class="card"><div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">';
    if(up.face){
      html+='<div style="width:40px;height:40px;border-radius:50%;overflow:hidden;flex-shrink:0;background:#f0f2f5"><img src="'+up.face+'" style="width:100%;height:100%;object-fit:cover" onerror="this.style.display=\'none\';this.parentNode.innerHTML=\'<div class=\\"up-avatar\\">'+initial+'</div>\'"></div>';
    }else{
      html+='<div class="up-avatar">'+initial+'</div>';
    }
    html+='<div><div style="font-size:15px;font-weight:600;color:#18191c">'+escHtml(up.name)+'</div><div style="font-size:11px;color:#9499a0;margin-top:2px">UID '+uid+'</div></div>';
    html+='</div><div style="display:flex;flex-wrap:wrap;gap:6px">';
    // 每个群显示为一个带展开功能的标签
    up.groups.forEach(function(grp){
      var gid=grp.gid;
      var atallOn=grp.atall.length>0;
      var grpData=grp.grpData;
      html+='<div style="display:flex;flex-wrap:wrap;width:100%;margin:2px 0">';
      // 群标签行
      html+='<div class="group-chip" style="display:inline-flex;align-items:center;background:#f6f8fa;border-radius:8px;padding:6px 10px;font-size:12px;color:#444;border:1px solid #f0f0f0;cursor:pointer;flex:1" data-target="ex-'+uid+'-'+gid+'">';
      html+='<span>📌 '+gid+'</span>';
      if(atallOn) html+=' <span style="font-size:10px;background:#fb7299;color:#fff;border-radius:10px;padding:1px 6px;margin-left:4px">@</span>';
      html+='<span style="margin-left:auto;font-size:10px;color:#999" class="exp-icon">▶</span>';
      html+='</div>';
      html+='<span class="del" data-gid="'+gid+'" data-uid="'+uid+'" style="display:inline-flex;align-items:center;justify-content:center;width:32px;cursor:pointer;color:#fb7299;font-weight:700;font-size:14px">✕</span>';
      html+='</div>';
      // 每个群独立的折叠面板
      html+='<div id="ex-'+uid+'-'+gid+'" class="toggle-area collapsed" style="width:100%;overflow:hidden;transition:max-height .3s ease">';
      html+='<div style="padding:10px 12px;background:#fafbfc;border-radius:8px;margin:4px 0">';
      // @全体 开关按钮（默认可见，局部更新）
      html+='<div style="font-size:11px;color:#666;margin-bottom:6px;font-weight:500">🔔 @全体</div>';
      html+='<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px" class="atall-group" data-gid="'+gid+'" data-uid="'+uid+'">';
      html+=["all","dynamic","video","music","article","live"].map(function(t){
        var on=grp.atall.includes(t);
        var lb={all:"全部",dynamic:"动态",video:"视频",music:"音乐",article:"专栏",live:"直播"}[t];
        return '<span class="atag '+(on?"on":"off")+'" data-type="'+t+'">'+lb+'</span>';
      }).join("");
      html+='</div>';
      // 屏蔽词
      if(grpData&&grpData.filters&&grpData.filters.length){
        html+='<div style="font-size:11px;color:#999;margin-bottom:8px;border-top:1px solid #eee;padding-top:8px">🔇 '+grpData.filters.map(function(f){return '<span class="tag" style="font-size:11px">'+escHtml(f.keyword)+'<span class="del" data-gid="'+gid+'" data-kw="'+escHtml(f.keyword)+'" style="cursor:pointer;margin-left:4px;color:#fb7299">✕</span></span>'}).join(" ")+'</div>';
      }
      // 模板选择
      var tplDyn=grpData?grpData.template_dynamic||"":"";
      var tplLiv=grpData?grpData.template_live||"":"";
      var tplVid=grpData?grpData.template_video||"":"";
      html+='<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-top:1px solid #eee;padding-top:8px">';
      html+='<span style="font-size:11px;color:#666;font-weight:500">模板</span>';
      html+='<label style="font-size:10px;color:#9499a0">动态<select class="tpl-sel" data-gid="'+gid+'" data-type="dynamic" style="font-size:11px;padding:3px 6px;margin-left:3px">'+tplOptions(tplDyn,tpls)+'</select></label>';
      html+='<label style="font-size:10px;color:#9499a0">视频<select class="tpl-sel" data-gid="'+gid+'" data-type="video" style="font-size:11px;padding:3px 6px;margin-left:3px">'+tplOptions(tplVid,tpls)+'</select></label>';
      html+='<label style="font-size:10px;color:#9499a0">直播<select class="tpl-sel" data-gid="'+gid+'" data-type="live" style="font-size:11px;padding:3px 6px;margin-left:3px">'+tplOptions(tplLiv,tpls)+'</select></label></div>';
      html+='</div></div>';
    });
    html+='</div></div>';
  });
  html+='</div>'; // end cards-grid
  // 空群管理
  if(emptyGroups.length){
    html+='<div class="card"><div style="font-size:14px;font-weight:600;color:#444;margin-bottom:10px">📋 待订阅的群</div>';
    emptyGroups.forEach(function(gr){
      html+='<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:8px 0;border-bottom:1px solid #f5f5f5">';
      html+='<span style="font-size:13px;color:#61666d;min-width:80px">群 '+gr.group_id+'</span>';
      html+='<input class="in" id="iu-'+gr.group_id+'" placeholder="UID" style="width:90px">';
      html+='<button class="btn sm add-sub" data-gid="'+gr.group_id+'">添加</button>';
      html+='<input class="in" id="if-'+gr.group_id+'" placeholder="屏蔽词" style="width:80px">';
      html+='<button class="btn sm red add-filter" data-gid="'+gr.group_id+'">屏蔽</button>';
      html+='<span class="del-group" data-gid="'+gr.group_id+'" style="cursor:pointer;font-size:14px;color:#fb7299;margin-left:auto">✕</span>';
      html+='</div>';
    });
    html+='</div>';
  }
  // 底部操作栏
  var barHtml='<input class="in" id="inp-add-group" placeholder="输入群号添加管理"><button class="btn green sm" id="btn-add-group">添加</button><button class="btn outline sm" id="btn-scan-groups">扫描</button>';
  document.getElementById("sub-content").innerHTML=html;
  document.getElementById("sub-app-bar").innerHTML=barHtml+'<div id="group-scan-result" style="display:none"></div>';
  document.getElementById("group-scan-result").style.display="none";
  
  // 事件绑定
  document.querySelectorAll(".del").forEach(function(el){
    el.onclick=function(e){e.stopPropagation();
      var gid=this.dataset.gid;
      var uid=this.dataset.uid;
      var kw=this.dataset.kw;
      if(uid){dSub(gid,parseInt(uid));}
      if(kw){dFl(gid,kw);}
    };
  });
  document.querySelectorAll(".atall-group").forEach(function(grp){
    var gid=grp.dataset.gid;
    var uid=parseInt(grp.dataset.uid);
    grp.querySelectorAll(".atag").forEach(function(el){
      el.onclick=function(){
        var type=this.dataset.type;
        var wasOn=this.classList.contains("on");
        // 局部切换样式
        this.classList.toggle("on");
        this.classList.toggle("off");
        // 发送请求
        fetch(API+"/group/"+gid+"/atall",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:uid,type:type,enable:!wasOn})})
          .then(function(r){return r.json()})
          .then(function(d){if(d.error){toast(d.error);loadSub()}else{toast((wasOn?"关闭":"开启")+"@"+type)}});
      };
    });
  });
  document.querySelectorAll(".add-sub").forEach(function(el){
    el.onclick=function(){
      var gid=this.dataset.gid;
      var inp=document.getElementById("iu-"+gid);
      var u=inp.value.trim();
      if(!u||!/^[0-9]+$/.test(u)){toast("输入UID");return}
      inp.value="";
      aSub(gid,parseInt(u));
    };
  });
  document.querySelectorAll(".add-filter").forEach(function(el){
    el.onclick=function(){
      var gid=this.dataset.gid;
      var inp=document.getElementById("if-"+gid);
      var k=inp.value.trim();
      if(!k){toast("输入过滤词");return}
      inp.value="";
      aFl(gid,k);
    };
  });
  document.querySelectorAll(".tpl-sel").forEach(function(el){
    el.onchange=function(){
      var gid=this.dataset.gid;
      var ttype=this.dataset.type||"dynamic";
      var tpl=this.value;
      fetch(API+"/group/"+gid+"/template",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({template:tpl,type:ttype})}).then(function(r){return r.json()}).then(function(d){toast(d.message||d.error||"已保存")});
    };
  });
  document.querySelectorAll(".del-group").forEach(function(el){
    el.onclick=function(){
      var gid=this.dataset.gid;
      if(!confirm("删除群 "+gid+" 的所有数据？"))return;
      fetch(API+"/group/"+gid+"/delall",{method:"POST"}).then(function(r){return r.json()}).then(function(d){toast(d.message||d.error||"ok");loadSub()});
    };
  });
  document.querySelectorAll("[data-target]").forEach(function(el){
    el.onclick=function(){
      var target=document.getElementById(this.dataset.target);
      if(!target)return;
      var isCollapsed=target.classList.contains("collapsed");
      var icon=this.querySelector(".exp-icon");
      if(isCollapsed){
        // 展开
        target.classList.remove("collapsed");
        target.style.maxHeight="0";
        requestAnimationFrame(function(){
          target.style.maxHeight=target.scrollHeight+"px";
          // 过渡完成后清除内联 max-height
          var done=function(){target.style.maxHeight="";target.removeEventListener("transitionend",done)};
          target.addEventListener("transitionend",done);
        });
        if(icon)icon.textContent="▼";
      }else{
        // 折叠
        target.style.maxHeight=target.scrollHeight+"px";
        requestAnimationFrame(function(){
          target.classList.add("collapsed");
          target.style.maxHeight="0";
        });
        if(icon)icon.textContent="▶";
      }
    };
  });
  document.getElementById("btn-add-group").onclick=function(){
    var inp=document.getElementById("inp-add-group");
    var gid=inp.value.trim();
    if(!gid||!/^[0-9]+$/.test(gid)){toast("输入群号");return}
    inp.value="";
    fetch(API+"/group/"+gid+"/addall",{method:"POST"}).then(function(r){return r.json()}).then(function(d){toast(d.message||d.error||"ok");loadSub()});
  };
  document.getElementById("btn-scan-groups").onclick=function(){
    var box=document.getElementById("group-scan-result");
    box.style.display="block";
    box.innerHTML="<span style='font-size:12px;color:#999'>扫描中...</span>";
    fetch(API+"/groups/all").then(function(r){return r.json()}).then(function(d){
      if(d.error){box.innerHTML="<span style='font-size:12px;color:#fb7299'>获取失败</span>";return}
      var html="<div style='display:flex;flex-wrap:wrap;gap:6px;margin-top:6px'>";
      d.groups.forEach(function(g){
        html+="<span style='display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:6px;font-size:12px;"+(g.managed?"background:#e8f5e9;color:#2e7d32;border:1px solid #a5d6a7":"background:#f5f5f5;color:#666;border:1px solid #ddd;cursor:pointer")+"' "+(g.managed?"":("onclick=\"addGroupScan('"+g.group_id+"')\""))+">"+
          escHtml(g.group_name)+" ("+g.group_id+")"+(g.managed?" ✅":"")+
        "</span>";
      });
      html+="</div>";
      box.innerHTML=html;
    });
  };
}

// ==== 手动推送 ====
function loadPush(){
  // 获取已管理的群列表
  fetch(API+"/status").then(function(r){return r.json()}).then(function(s){
    fetch(API+"/groups").then(function(r2){return r2.json()}).then(function(g){
      var groupOpts='<option value="">自动定位（所有订阅的群）</option>';
      g.groups.forEach(function(gr){
        var gid=gr.group_id;
        var label=gr.ups.length>0?gr.ups.map(function(u){return u.name}).join(", "):"无订阅";
        groupOpts+='<option value="'+gid+'">群 '+gid+' ('+label+')</option>';
      });
      renderPush(groupOpts);
    });
  });
}
function renderPush(groupOpts){
  var html='<div class="card"><h3 style="margin-bottom:12px">📤 手动推送</h3>'+
    '<div style="font-size:12px;color:#666;margin-bottom:10px">输入动态ID/BV号/av号/直播间号，选择目标群后推送</div>'+
    '<div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap">'+
    '<input class="in" id="push-id" placeholder="BV号 / av号 / 动态ID / 直播间号" style="flex:2;min-width:200px">'+
    '<select id="push-group" style="flex:1;min-width:140px;padding:8px 12px;border:1.5px solid #e8e8e8;border-radius:10px;font-size:12px">'+groupOpts+'</select>'+
    '<button class="btn green" id="btn-push" style="white-space:nowrap;padding:8px 20px">🚀 推送</button></div>'+
    '<div id="push-result" style="display:none"></div>'+
    '<div style="margin-top:12px;padding:10px 14px;background:#f6f8fa;border-radius:8px;font-size:11px;color:#666;line-height:1.6">'+
    '<div style="font-weight:500;margin-bottom:4px">📖 支持格式</div>'+
    '• BV号: <code style="background:#eef2f7;padding:1px 6px;border-radius:4px">BV1GJ411x7t7</code> → 视频推送<br>'+
    '• av号: <code style="background:#eef2f7;padding:1px 6px;border-radius:4px">av123456</code> → 视频推送<br>'+
    '• 动态ID: <code style="background:#eef2f7;padding:1px 6px;border-radius:4px">123456789</code> → 动态推送<br>'+
    '• 直播间号: <code style="background:#eef2f7;padding:1px 6px;border-radius:4px">12345</code> → 直播推送'+
    '</div></div>';
  document.getElementById("page-push").innerHTML=html;
  
  document.getElementById("btn-push").onclick=async function(){
    var id=document.getElementById("push-id").value.trim();
    if(!id){toast("请输入ID");return}
    var gid=document.getElementById("push-group").value;
    var btn=this;
    var result=document.getElementById("push-result");
    btn.disabled=true;
    btn.textContent="⏳ 推送中...";
    result.style.display="block";
    result.innerHTML='<div style="padding:12px;text-align:center;color:#999;font-size:13px">⏳ 正在获取内容并推送...</div>';
    try{
      var body={id:id};
      if(gid)body.group_id=gid;
      var r=await(await fetch(API+"/push",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json();
      if(r.error){
        result.innerHTML='<div style="padding:12px;background:#fff5f5;border-radius:8px;color:#fb7299;font-size:13px">❌ '+r.error+'</div>';
      }else{
        var typeIcon={dynamic:"📰",video:"🎬",live:"🔴"}[r.push_type]||"📄";
        var typeName={dynamic:"动态",video:"视频",live:"直播"}[r.push_type]||r.push_type;
        result.innerHTML='<div style="padding:12px;background:#f0faf0;border-radius:8px;font-size:13px">'+
          '✅ 推送完成<br>'+
          '<div style="margin-top:6px;font-size:12px;color:#666">'+
          typeIcon+' 类型: '+typeName+'<br>'+
          '👤 UP主UID: '+r.uid+'<br>'+
          '📌 标题: '+escHtml(r.title||"")+'<br>'+
          '📋 目标群: '+r.total_groups+' 个<br>'+
          '✅ 成功: '+r.success+' 个'+
          (r.failed&&r.failed.length?'<br>❌ 失败: '+r.failed.join(", ")+' 个':'')+
          '</div></div>';
      }
    }catch(e){
      result.innerHTML='<div style="padding:12px;background:#fff5f5;border-radius:8px;color:#fb7299;font-size:13px">❌ 请求失败: '+e.message+'</div>';
    }
    btn.disabled=false;
    btn.textContent="🚀 推送";
  };
}

async function aSub(g,u){var r=await(await fetch(API+"/group/"+g+"/add",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:u})})).json();toast(r.message||r.error||"ok");loadSub()}
async function dSub(g,u){if(!confirm("取消订阅?"))return;var r=await(await fetch(API+"/group/"+g+"/del",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:u})})).json();toast(r.message||r.error||"ok");loadSub()}
async function tglAt(g,u,t){var d=await(await fetch(API+"/group/"+g)).json();if(d.error)return;var cur=d.atall[String(u)]||[];var e=!cur.includes(t);var r=await(await fetch(API+"/group/"+g+"/atall",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:u,type:t,enable:e})})).json();toast(r.message||r.error||"ok");loadSub()}
async function aFl(g,k){var r=await(await fetch(API+"/group/"+g+"/filter",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keyword:k})})).json();toast(r.message||r.error||"ok");loadSub()}
async function dFl(g,k){var r=await(await fetch(API+"/group/"+g+"/filter/del",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keyword:k})})).json();toast(r.message||r.error||"ok");loadSub()}
function addGroupScan(gid){
  fetch(API+"/group/"+gid+"/addall",{method:"POST"}).then(function(r){return r.json()}).then(function(d){toast("已添加群 "+gid);loadSub()});
}

// ==== Cookie ====
async function loadCookie(){
  var s=await(await fetch(API+"/status")).json();
  document.getElementById("page-cookie").innerHTML='<div class="card"><h3>Cookie</h3>'+
    '<div class="mb"><span style="font-size:13px;font-weight:600;color:'+(s.cookie_configured?"green":"red")+'">'+(s.cookie_configured?"已配置":"未配置")+'</span>'+(s.cookie_uid?" (UID:"+s.cookie_uid+")":"")+'</div>'+
    '<div class="mb"><label>Cookie 值</label><textarea id="cv" style="min-height:60px"></textarea></div>'+
    '<div><button class="btn" id="btn-save-cookie">保存</button> '+
    '<button class="btn red" id="btn-clear-cookie">清除</button> '+
    '<button class="btn green" id="btn-qr-login">扫码登录</button></div>'+
    '<div id="qrbox" class="hide mt"><div id="qrmsg" style="font-size:13px;color:#666;margin-bottom:6px"></div><div id="qrimg"></div></div>'+
    '</div>';
  document.getElementById("btn-save-cookie").onclick=svCookie;
  document.getElementById("btn-clear-cookie").onclick=clCookie;
  document.getElementById("btn-qr-login").onclick=qrLogin;
}

async function svCookie(){var v=document.getElementById("cv").value.trim();if(!v){toast("输入Cookie");return}var r=await(await fetch(API+"/cookie/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({cookie:v})})).json();toast(r.message||r.error||"ok");loadCookie()}
async function clCookie(){if(!confirm("清除Cookie?"))return;var r=await(await fetch(API+"/cookie/clear",{method:"POST"})).json();toast(r.message||r.error||"ok");loadCookie()}
async function qrLogin(){
  document.getElementById("qrbox").classList.remove("hide");
  document.getElementById("qrmsg").textContent="生成二维码...";
  var r=await(await fetch(API+"/cookie/qrcode")).json();
  if(r.error){document.getElementById("qrmsg").textContent="失败";return}
  document.getElementById("qrimg").innerHTML='<img src="data:image/png;base64,'+r.qrcode_png+'" style="width:180px;height:180px">';
  document.getElementById("qrmsg").textContent="请使用B站手机APP扫码";
  var poll=function(){
    fetch(API+"/cookie/poll?key="+r.qrcode_key).then(function(r2){return r2.json()}).then(function(d){
      if(d.success){document.getElementById("qrmsg").textContent="登录成功!";document.getElementById("qrimg").innerHTML="";loadCookie();return}
      if(d.error&&d.error.includes("超时")){document.getElementById("qrmsg").textContent="超时";return}
      setTimeout(poll,3000);
    });
  };
  setTimeout(poll,3000);
}

// ==== 模板 ====
var TPL_VARS_DYNAMIC = {
  "name": "UP主名称",
  "avatar": "头像URL",
  "time": "相对时间（3小时前）",
  "pub_time": "完整发布时间",
  "type_text": "动态类型（文字/视频/图片等）",
  "content": "纯文本内容",
  "content_html": "HTML格式内容（含emoji/链接）",
  "images": "图片URL数组",
  "media_title": "视频/专栏标题",
  "media_desc": "视频/专栏简介",
  "media_cover": "视频/专栏封面URL",
  "media_link": "跳转链接",
  "media_badge": "角标文字",
  "comment_count": "评论数",
  "forward_count": "转发数",
  "like_count": "点赞数",
  "forward_name": "转发来源UP主",
  "forward_content": "转发原文",
  "dynamic_id": "动态ID",
};
var TPL_VARS_LIVE = {
  "cover": "直播间封面URL",
  "title": "直播标题",
  "name": "主播名称",
  "avatar": "主播头像URL",
  "uid": "主播UID",
  "area": "直播分区",
  "start_time": "开播时间",
  "online": "人气值（格式化）",
  "online_raw": "人气值（数字）",
  "live_link": "直播间链接",
  "room_id": "房间号",
};

async function loadTpl(){
  var r=await(await fetch(API+"/template/list")).json();
  var tpls=r.templates||["dynamic.html","live.html","help.html"];
  var html='<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px">';
  tpls.forEach(function(t){
    var icon="📄";
    if(t.includes("dynamic")) icon="📰";
    else if(t.includes("live")) icon="🔴";
    else if(t.includes("video")) icon="🎬";
    else if(t.includes("help")) icon="❓";
    html+='<div class="card" style="cursor:pointer;flex:1;min-width:140px;text-align:center;padding:14px 12px" onclick="loadTplEdit(\''+t+'\')">';
    html+='<div style="font-size:24px;margin-bottom:6px">'+icon+'</div>';
    html+='<div style="font-size:13px;font-weight:600;color:#18191c">'+t.replace(".html","")+'</div>';
    html+='<div style="font-size:11px;color:#999;margin-top:4px">点击编辑</div>';
    html+='</div>';
  });
  html+='<div class="card" style="cursor:pointer;flex:1;min-width:140px;text-align:center;padding:14px 12px;border:2px dashed #ddd" onclick="uploadTpl()">';
  html+='<div style="font-size:24px;margin-bottom:6px">📤</div>';
  html+='<div style="font-size:13px;font-weight:600;color:#178bcf">上传模板</div>';
  html+='<div style="font-size:11px;color:#999;margin-top:4px">点击选择文件</div>';
  html+='<input type="file" id="tpl-upload-input" accept=".html" style="display:none" onchange="uploadTplFile(this)">';
  html+='</div></div>';
  // 变量文档折叠面板
  html+='<div class="card" id="tpl-vars-doc" style="display:none"></div>';
  document.getElementById("page-tpl").innerHTML=html+'<div id="tpl-editor"></div>';
}

async function loadTplEdit(path){
  var r=await(await fetch(API+"/template?path="+path)).json();
  if(r.error){toast(r.error);return}
  // 猜测类型
  var guessType="dynamic";
  if(path.includes("live")) guessType="live";
  else if(path.includes("video")) guessType="video";
  // 变量文档
  var vars=guessType=="live"?TPL_VARS_LIVE:TPL_VARS_DYNAMIC;
  var varDoc='<div style="margin-top:8px"><div style="font-size:13px;font-weight:600;color:#18191c;cursor:pointer" onclick="var d=document.getElementById(\'tpl-vars-body\');d.style.display=d.style.display==\'none\'?\'block\':\'none\'">📖 '+guessType+'模板变量 <span style="font-size:11px;color:#999;font-weight:400">点击展开</span></div>'+
    '<div id="tpl-vars-body" style="display:none;margin-top:6px;font-size:12px">';
  for(var k in vars){
    varDoc+='<div style="display:flex;padding:3px 0;border-bottom:1px solid #f0f0f0"><span style="color:#178bcf;font-family:monospace;min-width:140px">'+k+'</span><span style="color:#666">'+vars[k]+'</span></div>';
  }
  varDoc+='</div></div>';
  // 预览类型选择
  var typeSel='<select id="preview-type" style="padding:4px 8px;border:1px solid #ddd;border-radius:4px;font-size:12px;margin-left:4px">'+
    '<option value="dynamic" '+(guessType=="dynamic"?"selected":"")+'>动态</option>'+
    '<option value="video" '+(guessType=="video"?"selected":"")+'>视频</option>'+
    '<option value="live" '+(guessType=="live"?"selected":"")+'>直播</option></select>';
  document.getElementById("tpl-editor").innerHTML='<div class="card"><h3 style="margin-bottom:8px">编辑: '+path+'</h3>'+
    '<div style="display:flex;gap:12px;flex-wrap:wrap"><div style="flex:1;min-width:300px"><div class="mb"><textarea id="tplct" style="min-height:200px">'+escHtml(r.content||"")+'</textarea></div>'+
    '<button class="btn" onclick="svTpl(\''+path+'\')">💾 保存</button> '+
    '<button class="btn" onclick="previewTpl(\''+path+'\')" style="background:#4caf50">👁️ 预览'+typeSel+'</button> '+
    '<button class="btn red" onclick="loadTpl()">← 返回</button></div>'+
    '<div style="flex:1;min-width:300px"><div id="tpl-preview" style="background:#f6f8fa;border-radius:8px;padding:8px;text-align:center;font-size:12px;color:#999">点击「预览」查看效果</div>'+
    varDoc+'</div></div></div>';
}

async function previewTpl(path){
  var box=document.getElementById("tpl-preview");
  var typeEl=document.getElementById("preview-type");
  var ptype=typeEl?typeEl.value:"dynamic";
  box.innerHTML="<span style='font-size:12px;color:#999'>渲染中...</span>";
  var r=await(await fetch(API+"/template/preview?path="+path+"&type="+ptype)).json();
  if(r.error){box.innerHTML="<span style='color:#fb7299'>渲染失败: "+r.error+"</span>";return}
  box.innerHTML='<img src="data:image/png;base64,'+r.image+'" style="max-width:100%;border-radius:4px">';
}
function escHtml(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
async function svTpl(path){
  var c=document.getElementById("tplct").value;
  var r=await(await fetch(API+"/template/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:path,content:c})})).json();
  toast(r.message||r.error||"ok");
}
function uploadTpl(){document.getElementById("tpl-upload-input").click()}
async function uploadTplFile(input){
  var file=input.files[0];if(!file)return;
  var name=file.name;
  if(!name.endsWith(".html")){toast("仅支持 .html 文件");return}
  var text=await file.text();
  var r=await(await fetch(API+"/template/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:name,content:text})})).json();
  toast(r.message||r.error||"已上传");
  input.value="";
  loadTpl();
}



// ==== 字体 ====
function renderFont(){
  document.getElementById("page-font").innerHTML='<div class="card"><h3>字体设置</h3>'+
    '<div class="mb"><label>字体名称</label><input class="in w" id="fn" value="HarmonyOS Sans SC"></div>'+
    '<button class="btn" id="btn-save-font">保存</button></div>';
  document.getElementById("btn-save-font").onclick=svFont;
}
async function svFont(){
  var n=document.getElementById("fn").value.trim();
  var r=await(await fetch(API+"/font/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({font_name:n})})).json();
  toast(r.message||r.error||"ok");
}
</script>
</body>
</html>"""


driver = get_driver()


@driver.on_startup
async def _():
    enable = getattr(driver.config, "bili_web_enable", True)
    if not str(enable).lower() in ("true", "1", "yes"):
        logger.info("B站后台已关闭（bili_web_enable=false）")
        return
    try:
        app = get_app()
        app.include_router(router)
        port = getattr(driver.config, "port", 8090)
        host = getattr(driver.config, "host", "0.0.0.0")
        logger.success(f"B站后台: http://{host}:{port}/bili/")
    except Exception as e:
        logger.warning(f"B站后台启动失败: {e}")
