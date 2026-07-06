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
    from nonebot_plugin_bilibili import plugin_config
    return plugin_config.web_password or ""


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
        ups = [{"uid": uid, "name": user_storage.get_name(uid) or str(uid), "face": user_storage.get_face(uid) or ""} for uid in uids]
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
                # 跳过直播类型动态，走直播间检测
                if item.get("type", "") in ("DYNAMIC_TYPE_LIVE", "DYNAMIC_TYPE_LIVE_RCMD"):
                    dyn_info = None
                else:
                    msg = DynamicMessage(item)
                    uid = msg.mid
                    title = msg.name
                    # 根据动态子类型选择模板：视频/动态/其他
                    _tpl_map = {
                        "DYNAMIC_TYPE_AV": "video",
                        "DYNAMIC_TYPE_PGC": "video",
                        "DYNAMIC_TYPE_PGC_UNION": "video",
                        "DYNAMIC_TYPE_UGC_SEASON": "video",
                        "DYNAMIC_TYPE_MUSIC": "video",
                    }
                    push_type = _tpl_map.get(msg.type_str, "dynamic")
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
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>B站通知助手</title>
<style>
:root {
  --pink: #FB7299;
  --pink-light: #fde8ee;
  --blue: #00A1D6;
  --blue-light: #e6f7fc;
  --bg: #f5f6fa;
  --card: #fff;
  --text: #1a1a2e;
  --text2: #6b7280;
  --text3: #9ca3af;
  --border: #e5e7eb;
  --shadow: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
  --shadow-lg: 0 4px 16px rgba(0,0,0,.08);
  --radius: 12px;
  --radius-sm: 8px;
  --sidebar-w: 220px;
  --sidebar-collapsed: 64px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans SC",sans-serif;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;display:flex;min-height:100vh}
/* ===== 侧边栏 ===== */
.sidebar{width:var(--sidebar-w);background:linear-gradient(180deg,#1e1b4b 0%,#312e81 40%,#1e1b4b 100%);color:#fff;display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;z-index:50;transition:width .3s cubic-bezier(.4,0,.2,1);overflow:hidden}
.sidebar-header{padding:20px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid rgba(255,255,255,.1)}
.sidebar-logo{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--pink),var(--blue));display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.sidebar-title{font-size:15px;font-weight:700;letter-spacing:.5px;white-space:nowrap;overflow:hidden}
.sidebar-nav{flex:1;padding:12px 8px;overflow-y:auto}
.nav-item{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:10px;cursor:pointer;transition:all .2s;margin-bottom:4px;color:rgba(255,255,255,.65);font-size:13px;font-weight:500;white-space:nowrap;user-select:none}
.nav-item:hover{background:rgba(255,255,255,.1);color:#fff}
.nav-item.active{background:rgba(255,255,255,.15);color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.2)}
.nav-icon{font-size:18px;width:24px;text-align:center;flex-shrink:0}
.nav-badge{background:var(--pink);color:#fff;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:700;margin-left:auto}
.sidebar-footer{padding:12px 16px;border-top:1px solid rgba(255,255,255,.1);font-size:11px;color:rgba(255,255,255,.35)}
/* ===== 主内容 ===== */
.main{margin-left:var(--sidebar-w);flex:1;min-width:0;transition:margin-left .3s;padding:0}
.page-header{padding:20px 24px 0;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.page-header h2{font-size:22px;font-weight:700;color:var(--text)}
.page-subtitle{font-size:13px;color:var(--text2);margin-top:2px}
.page-content{padding:20px 24px 40px}
.page{display:none}
.page.active{display:block;animation:fadeIn .3s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
/* ===== 卡片 ===== */
.card{background:var(--card);border-radius:var(--radius);padding:20px;box-shadow:var(--shadow);border:1px solid var(--border);transition:box-shadow .2s}
.card:hover{box-shadow:var(--shadow-lg)}
.card-header{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.card-header h3{font-size:15px;font-weight:600}
/* ===== 统计卡片 ===== */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.stat-card{background:var(--card);border-radius:var(--radius);padding:16px 20px;box-shadow:var(--shadow);border:1px solid var(--border);display:flex;align-items:center;gap:14px;transition:transform .2s,box-shadow .2s;cursor:default}
.stat-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.stat-icon{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0}
.stat-icon.pink{background:var(--pink-light);color:var(--pink)}
.stat-icon.blue{background:var(--blue-light);color:var(--blue)}
.stat-icon.green{background:#e8f5e9;color:#2e7d32}
.stat-icon.orange{background:#fff3e0;color:#e65100}
.stat-info .stat-num{font-size:24px;font-weight:800;color:var(--text);line-height:1.2}
.stat-info .stat-label{font-size:12px;color:var(--text2);margin-top:2px}
/* ===== 按钮 ===== */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:9px 18px;border:none;border-radius:var(--radius-sm);cursor:pointer;font-size:13px;font-weight:600;transition:all .2s;white-space:nowrap;font-family:inherit}
.btn:active{transform:scale(.97)}
.btn-primary{background:linear-gradient(135deg,var(--pink),#e85d8a);color:#fff;box-shadow:0 2px 8px rgba(251,114,153,.35)}
.btn-primary:hover{box-shadow:0 4px 14px rgba(251,114,153,.45);transform:translateY(-1px)}
.btn-blue{background:linear-gradient(135deg,var(--blue),#0089b5);color:#fff;box-shadow:0 2px 8px rgba(0,161,214,.3)}
.btn-blue:hover{box-shadow:0 4px 14px rgba(0,161,214,.4);transform:translateY(-1px)}
.btn-green{background:linear-gradient(135deg,#4caf50,#43a047);color:#fff;box-shadow:0 2px 8px rgba(76,175,80,.3)}
.btn-green:hover{transform:translateY(-1px)}
.btn-red{background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff}
.btn-outline{background:transparent;color:var(--blue);border:1.5px solid var(--blue)}
.btn-outline:hover{background:var(--blue-light)}
.btn-sm{padding:5px 12px;font-size:11px;border-radius:6px}
.btn-xs{padding:3px 8px;font-size:10px;border-radius:5px}
/* ===== 输入框 ===== */
.in{padding:9px 14px;border:1.5px solid var(--border);border-radius:var(--radius-sm);font-size:13px;outline:none;transition:border-color .2s,box-shadow .2s;font-family:inherit;background:var(--card);color:var(--text)}
.in:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(0,161,214,.1)}
.in.w{width:100%}
textarea.in{resize:vertical;min-height:100px;font-family:ui-monospace,SFMono-Regular,monospace}
select.in{padding:9px 14px;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%236b7280' d='M6 8L1 3h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center;padding-right:32px}
/* ===== 标签 ===== */
.tag{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:16px;font-size:11px;font-weight:500}
.tag-blue{background:var(--blue-light);color:var(--blue)}
.tag-pink{background:var(--pink-light);color:var(--pink)}
.tag-green{background:#e8f5e9;color:#2e7d32}
.tag-gray{background:#f3f4f6;color:#6b7280}
/* ===== UP主卡片 ===== */
.up-card{background:var(--card);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow);border:1px solid var(--border);margin-bottom:12px;transition:box-shadow .2s}
.up-card:hover{box-shadow:var(--shadow-lg)}
.up-card-header{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.up-avatar{width:44px;height:44px;border-radius:50%;overflow:hidden;flex-shrink:0;border:2px solid var(--border)}
.up-avatar img{width:100%;height:100%;object-fit:cover}
.up-avatar-fallback{width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg,var(--pink),var(--blue));color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;flex-shrink:0}
.up-name{font-size:15px;font-weight:600;color:var(--text)}
.up-uid{font-size:11px;color:var(--text2);margin-top:1px}
/* ===== 群标签行 ===== */
.group-row{display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f9fafb;border-radius:var(--radius-sm);margin-bottom:6px;flex-wrap:wrap}
.group-row:hover{background:#f3f4f6}
.group-chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;color:var(--text);cursor:pointer}
.group-chip .arrow{font-size:10px;color:var(--text3);transition:transform .2s}
.group-chip.expanded .arrow{transform:rotate(90deg)}
.group-actions{display:flex;align-items:center;gap:6px;margin-left:auto}
/* ===== 折叠面板 ===== */
.toggle-panel{overflow:hidden;transition:max-height .3s ease,opacity .3s ease;max-height:0;opacity:0}
.toggle-panel.open{max-height:600px;opacity:1}
.toggle-inner{padding:12px;margin:4px 0 8px;background:#fafbfc;border-radius:var(--radius-sm);border:1px solid var(--border)}
/* ===== @标签开关 ===== */
.atag{display:inline-flex;align-items:center;padding:4px 10px;margin:2px;border-radius:6px;font-size:11px;cursor:pointer;border:1.5px solid #ddd;transition:all .2s;font-weight:500;user-select:none}
.atag.on{background:#e8f5e9;color:#2e7d32;border-color:#a5d6a7}
.atag.off{background:#f5f5f5;color:#999;border-color:#e0e0e0}
/* ===== Toast ===== */
.toast-container{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px}
.toast{background:#1f2937;color:#fff;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:500;box-shadow:0 8px 24px rgba(0,0,0,.25);animation:slideIn .3s ease;max-width:360px}
.toast.success{background:#059669}
.toast.error{background:#dc2626}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
/* ===== 预加载 ===== */
.preload{display:none}
/* ===== 空状态 ===== */
.empty-state{text-align:center;padding:60px 20px}
.empty-icon{font-size:56px;margin-bottom:16px}
.empty-text{font-size:15px;color:var(--text2);font-weight:500}
.empty-hint{font-size:12px;color:var(--text3);margin-top:6px}
/* ===== 工具条 ===== */
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.toolbar .in{flex:1;min-width:160px}
/* ===== 模板卡片 ===== */
.tpl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.tpl-card{background:var(--card);border-radius:var(--radius);padding:20px 16px;text-align:center;cursor:pointer;border:1px solid var(--border);transition:all .2s}
.tpl-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg);border-color:var(--blue)}
.tpl-card .tpl-icon{font-size:32px;margin-bottom:10px}
.tpl-card .tpl-name{font-size:13px;font-weight:600;color:var(--text)}
.tpl-card .tpl-hint{font-size:11px;color:var(--text3);margin-top:4px}
.tpl-card.upload{border-style:dashed;border-color:var(--blue);color:var(--blue)}
/* ===== 底部栏 (移动端) ===== */
.mobile-bar{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:8px 16px;z-index:40;gap:8px;flex-wrap:wrap;box-shadow:0 -2px 12px rgba(0,0,0,.06)}
/* ===== 响应式 ===== */
@media (max-width: 768px){
  .sidebar{width:var(--sidebar-collapsed)}
  .sidebar-title,.nav-item span:not(.nav-icon),.nav-badge,.sidebar-footer{display:none}
  .nav-item{justify-content:center;padding:12px}
  .main{margin-left:var(--sidebar-collapsed)}
  .page-header{padding:16px 16px 0}
  .page-content{padding:16px}
  .stats-grid{grid-template-columns:repeat(2,1fr)}
  .mobile-bar{display:flex}
}
@media (max-width: 480px){
  .stats-grid{grid-template-columns:1fr 1fr}
  .tpl-grid{grid-template-columns:repeat(2,1fr)}
}
/* ===== 滚动条 ===== */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#c1c1c1;border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:#a1a1a1}
/* ===== 搜索栏 ===== */
.search-box{position:relative;flex:1;min-width:180px}
.search-box .in{padding-left:36px;width:100%}
.search-icon{position:absolute;left:11px;top:50%;transform:translateY(-50%);font-size:14px;color:var(--text3);pointer-events:none}
.search-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);cursor:pointer;font-size:14px;color:var(--text3);display:none;width:22px;height:22px;align-items:center;justify-content:center;border-radius:50%;transition:all .2s}
.search-clear:hover{background:#e5e7eb;color:var(--text)}
.search-clear.visible{display:flex}
.search-count{font-size:12px;color:var(--text2);white-space:nowrap}
/* ===== 加载骨架屏 ===== */
.skeleton{background:linear-gradient(90deg,#f0f0f0 25%,#e8e8e8 50%,#f0f0f0 75%);background-size:200% 100%;animation:shimmer 1.5s infinite;border-radius:6px}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.skel-card{height:80px;margin-bottom:12px;border-radius:var(--radius)}
.skel-title{width:60%;height:18px;margin-bottom:8px}
.skel-sub{width:40%;height:14px}
/* ===== UP主头像 (带 fallback) ===== */
.up-avatar-img{width:100%;height:100%;object-fit:cover}
.up-avatar-wrapper{width:44px;height:44px;border-radius:50%;overflow:hidden;flex-shrink:0;border:2px solid var(--border);position:relative;background:#f3f4f6}
.up-avatar-wrapper .up-avatar-fallback{position:absolute;inset:0;border-radius:50%}
.up-avatar-wrapper img{width:100%;height:100%;object-fit:cover;position:relative;z-index:1}
.up-avatar-wrapper img[src=""],.up-avatar-wrapper img.error{display:none}
/* ===== UP主卡片增强 ===== */
.up-card{border-left:3px solid transparent;transition:border-color .2s,box-shadow .2s}
.up-card:hover{border-left-color:var(--blue)}
.up-card-header{cursor:default}
.up-stats{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--text3);margin-top:2px}
.up-stats span{display:inline-flex;align-items:center;gap:3px}
/* ===== 快速导航条 ===== */
.quick-nav{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:16px;padding:8px;background:var(--card);border-radius:var(--radius-sm);border:1px solid var(--border);align-items:center}
.quick-nav-label{font-size:11px;color:var(--text3);margin-right:4px;font-weight:600;white-space:nowrap}
.quick-nav-chip{padding:3px 10px;font-size:11px;border-radius:14px;cursor:pointer;border:1.5px solid var(--border);color:var(--text2);transition:all .2s;font-weight:500;white-space:nowrap;user-select:none}
.quick-nav-chip:hover{border-color:var(--blue);color:var(--blue);background:var(--blue-light)}
.quick-nav-chip.active{background:var(--blue);color:#fff;border-color:var(--blue)}
/* ===== 模板选择器精简 ===== */
.tpl-compact{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.tpl-compact select{padding:4px 24px 4px 8px;font-size:11px;border-radius:5px;border:1px solid var(--border);background:var(--card);color:var(--text);appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath fill='%236b7280' d='M5 7L1 3h8z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 6px center;cursor:pointer;transition:border-color .2s}
.tpl-compact select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 2px rgba(0,161,214,.15)}
.tpl-compact select:not([value=""]){border-color:var(--blue);color:var(--blue)}
/* ===== 操作按钮行 ===== */
.group-actions-row{display:flex;align-items:center;gap:6px;margin-top:4px}
/* ===== 响应式增强 ===== */
@media (max-width:768px){
  .quick-nav{overflow-x:auto;flex-wrap:nowrap;padding:6px 8px;-webkit-overflow-scrolling:touch}
  .quick-nav::-webkit-scrollbar{height:0}
  .up-card{border-left:none;border-top:3px solid transparent}
  .up-card:hover{border-top-color:var(--blue);border-left-color:transparent}
}
</style>
</head>
<body>

<!-- 侧边栏 -->
<aside class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <div class="sidebar-logo">📺</div>
    <div class="sidebar-title">B站通知助手</div>
  </div>
  <nav class="sidebar-nav" id="sidebar-nav">
    <div class="nav-item active" data-page="dashboard"><span class="nav-icon">📊</span><span>仪表盘</span></div>
    <div class="nav-item" data-page="sub"><span class="nav-icon">📌</span><span>订阅管理</span><span class="nav-badge" id="nav-sub-count">0</span></div>
    <div class="nav-item" data-page="push"><span class="nav-icon">📤</span><span>手动推送</span></div>
    <div class="nav-item" data-page="tpl"><span class="nav-icon">🎨</span><span>模板管理</span></div>
    <div class="nav-item" data-page="cookie"><span class="nav-icon">🍪</span><span>Cookie</span></div>
    <div class="nav-item" data-page="settings"><span class="nav-icon">⚙️</span><span>设置</span></div>
  </nav>
  <div class="sidebar-footer">v1.0 · NoneBot2</div>
</aside>

<!-- 主内容 -->
<main class="main" id="main">
  <!-- 仪表盘 -->
  <section class="page active" id="page-dashboard">
    <div class="page-header"><h2>📊 仪表盘</h2></div>
    <div class="page-content">
      <div class="stats-grid" id="dashboard-stats"></div>
      <div class="card" style="margin-bottom:12px">
        <div class="card-header"><h3>⚡ 快捷操作</h3></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-primary" onclick="sw('sub')">📌 管理订阅</button>
          <button class="btn btn-blue" onclick="sw('push')">📤 手动推送</button>
          <button class="btn btn-outline" onclick="sw('cookie')">🍪 Cookie管理</button>
        </div>
      </div>
      <div id="dashboard-recent" class="card">
        <div class="card-header"><h3>📋 最近订阅</h3></div>
        <div id="dashboard-recent-content"></div>
      </div>
    </div>
  </section>

  <!-- 订阅管理 -->
  <section class="page" id="page-sub">
    <div class="page-header">
      <div><h2>📌 订阅管理</h2><p class="page-subtitle">管理群聊中的 UP主 订阅</p></div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-outline btn-sm" id="btn-scan-groups">🔍 扫描群列表</button>
      </div>
    </div>
    <div class="page-content">
      <div class="toolbar">
        <div class="search-box">
          <span class="search-icon">🔍</span>
          <input class="in" id="sub-search" placeholder="搜索 UP主 名称或 UID..." oninput="filterSubs()">
          <span class="search-clear" id="search-clear" onclick="clSearch()">✕</span>
        </div>
        <span class="search-count" id="search-count"></span>
        <input class="in" id="inp-add-group" placeholder="输入群号添加管理" style="max-width:180px">
        <button class="btn btn-primary btn-sm" id="btn-add-group">➕ 添加群</button>
        <select class="in" id="quick-group" style="max-width:160px" onchange="quickAddSub()"><option value="">快速添加UP主</option></select>
        <input class="in" id="quick-uid" placeholder="UP主UID" style="max-width:100px">
        <button class="btn btn-blue btn-sm" onclick="quickAddSub()">添加</button>
      </div>
      <div id="sub-quick-nav"></div>
      <div id="sub-content"></div>
    </div>
  </section>

  <!-- 手动推送 -->
  <section class="page" id="page-push">
    <div class="page-header"><h2>📤 手动推送</h2></div>
    <div class="page-content" id="push-content"></div>
  </section>

  <!-- 模板管理 -->
  <section class="page" id="page-tpl">
    <div class="page-header"><h2>🎨 模板管理</h2></div>
    <div class="page-content" id="tpl-content"></div>
  </section>

  <!-- Cookie -->
  <section class="page" id="page-cookie">
    <div class="page-header"><h2>🍪 Cookie 管理</h2></div>
    <div class="page-content" id="cookie-content"></div>
  </section>

  <!-- 设置 -->
  <section class="page" id="page-settings">
    <div class="page-header"><h2>⚙️ 设置</h2></div>
    <div class="page-content" id="settings-content"></div>
  </section>
</main>

<!-- 底部栏(移动端) -->
<div class="mobile-bar" id="mobile-bar">
  <input class="in" id="m-add-group" placeholder="输入群号" style="flex:1">
  <button class="btn btn-primary btn-sm" id="m-btn-add-group">添加</button>
  <button class="btn btn-outline btn-sm" onclick="sw('dashboard')">返回</button>
</div>

<!-- Toast 容器 -->
<div class="toast-container" id="toast-container"></div>

<!-- 群扫描弹窗 -->
<div id="scan-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:100;align-items:center;justify-content:center" onclick="if(event.target===this)this.style.display='none'">
  <div class="card" style="max-width:500px;width:90%;max-height:70vh;overflow-y:auto" onclick="event.stopPropagation()">
    <div class="card-header"><h3>🔍 Bot 加入的群列表</h3><span style="cursor:pointer;margin-left:auto;font-size:18px" onclick="document.getElementById('scan-modal').style.display='none'">✕</span></div>
    <div id="scan-result" style="font-size:13px;color:var(--text2)">加载中...</div>
  </div>
</div>

<script>
var API="/bili/api";
var CUR="dashboard";
var PAGES=["dashboard","sub","push","tpl","cookie","settings"];

// ===== 导航 =====
function sw(k){
  CUR=k;
  document.querySelectorAll(".nav-item").forEach(function(el){el.classList.toggle("active",el.dataset.page===k)});
  document.querySelectorAll(".page").forEach(function(p){p.classList.toggle("active",p.id==="page-"+k)});
  if(k==="dashboard")loadDashboard();
  if(k==="sub"){loadSub();document.getElementById("mobile-bar").style.display="flex"}
  else{document.getElementById("mobile-bar").style.display="none"}
  if(k==="push")loadPush();
  if(k==="cookie")loadCookie();
  if(k==="tpl")loadTpl();
  if(k==="settings")renderSettings();
}
document.getElementById("sidebar-nav").addEventListener("click",function(e){
  var item=e.target.closest(".nav-item");
  if(!item)return;
  sw(item.dataset.page);
});

// ===== Toast =====
function toast(m,type){
  type=type||"";
  var c=document.getElementById("toast-container");
  var t=document.createElement("div");
  t.className="toast "+type;
  t.textContent=m;
  c.appendChild(t);
  setTimeout(function(){t.style.opacity="0";t.style.transform="translateX(100px)";t.style.transition="all .3s ease";setTimeout(function(){t.remove()},300)},2500);
}

// ===== 工具 =====
function escHtml(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
var _tplCache=null;
async function getTpls(){
  if(_tplCache)return _tplCache;
  try{var r=await(await fetch(API+"/template/list")).json();_tplCache=r.templates||["dynamic.html","live.html","help.html"]}catch(e){_tplCache=["dynamic.html","live.html","help.html"]}
  return _tplCache;
}
function tplOptions(cur,allTpls){
  var o='<option value="">默认</option>';
  if(!allTpls)return o;
  var curBase=cur?cur.replace(/\.html$/,""):"";
  allTpls.forEach(function(t){
    var val=t.replace(/\.html$/,"");
    o+='<option value="'+val+'" '+(curBase===val?"selected":"")+'>'+val.charAt(0).toUpperCase()+val.slice(1)+'</option>';
  });
  return o;
}

// ===== 仪表盘 =====
async function loadDashboard(){
  var s=await(await fetch(API+"/status")).json();
  if(s.error)return;
  var g=await(await fetch(API+"/groups")).json();
  var html='<div class="stat-card"><div class="stat-icon pink">📋</div><div class="stat-info"><div class="stat-num">'+s.group_count+'</div><div class="stat-label">已管理群</div></div></div>';
  html+='<div class="stat-card"><div class="stat-icon blue">👤</div><div class="stat-info"><div class="stat-num">'+s.up_count+'</div><div class="stat-label">已订阅UP主</div></div></div>';
  html+='<div class="stat-card"><div class="stat-icon '+(s.cookie_configured?'green':'orange')+'">🍪</div><div class="stat-info"><div class="stat-num" style="font-size:16px">'+(s.cookie_configured?'已配置':'未配置')+'</div><div class="stat-label">Cookie状态'+(s.cookie_uid?' · UID:'+s.cookie_uid:'')+'</div></div></div>';
  html+='<div class="stat-card"><div class="stat-icon blue">📡</div><div class="stat-info"><div class="stat-num">运行中</div><div class="stat-label">服务状态</div></div></div>';
  document.getElementById("dashboard-stats").innerHTML=html;
  // 最近订阅
  var upList=[];
  g.groups.forEach(function(gr){gr.ups.forEach(function(u){if(!upList.find(function(x){return x.uid===u.uid}))upList.push(u)})});
  var recentHtml='';
  if(upList.length===0){
    recentHtml='<div class="empty-state"><div class="empty-icon">📭</div><div class="empty-text">暂无订阅</div><div class="empty-hint">去「订阅管理」添加吧~</div></div>';
  }else{
    recentHtml='<div style="display:flex;flex-wrap:wrap;gap:10px">';
    upList.forEach(function(up){
      recentHtml+='<div style="display:flex;align-items:center;gap:8px;background:#f8fafc;border-radius:20px;padding:5px 14px 5px 5px;border:1px solid var(--border)">';
      if(up.face){
        var faceUrl=up.face+"@48w_48h";
        recentHtml+='<img src="'+escHtml(faceUrl)+'" style="width:30px;height:30px;border-radius:50%;object-fit:cover" loading="lazy" onerror="this.style.display=\'none\'">';
      }
      recentHtml+='<span style="font-size:13px;font-weight:500">'+escHtml(up.name)+'</span>';
      recentHtml+='<span style="font-size:10px;color:var(--text3)">UID:'+up.uid+'</span></div>';
    });
    recentHtml+='</div>';
  }
  document.getElementById("dashboard-recent-content").innerHTML=recentHtml;
  document.getElementById("nav-sub-count").textContent=s.up_count||"";
}

// ===== 订阅管理 (增强版) =====
var _subData=null;          // 缓存完整数据
var _openPanels=new Set();  // 保持展开状态
var _subFilter="";          // 当前搜索词
var _debounceTimer=null;    // 搜索防抖

async function loadSub(){
  // 加载骨架屏
  var c=document.getElementById("sub-content");
  c.innerHTML='<div class="skel-card skeleton" style="height:100px"></div><div class="skel-card skeleton" style="height:100px"></div><div class="skel-card skeleton" style="height:100px"></div>';
  document.getElementById("sub-quick-nav").innerHTML="";
  try{
    var s=await(await fetch(API+"/status")).json();
    if(s.error){c.innerHTML='<div class="empty-state"><div class="empty-icon">⚠️</div><div class="empty-text">加载失败</div></div>';return}
    var g=await(await fetch(API+"/groups")).json();
    var tpls=await getTpls();
    _tplCache=tpls;
    // 缓存数据
    _subData={status:s,groups:g.groups,tpls:tpls};
    document.getElementById("nav-sub-count").textContent=s.up_count||"";
    // 渲染
    renderSubs(_subFilter);
    // 更新快速添加下拉
    var qg=document.getElementById("quick-group");
    qg.innerHTML='<option value="">快速添加UP主</option>';
    g.groups.forEach(function(gr){qg.innerHTML+='<option value="'+gr.group_id+'">群 '+gr.group_id+' ('+gr.up_count+'个UP)</option>'});
  }catch(e){
    c.innerHTML='<div class="empty-state"><div class="empty-icon">⚠️</div><div class="empty-text">加载失败: '+escHtml(e.message)+'</div></div>';
  }
}

function renderSubs(filter){
  if(!_subData){loadSub();return}
  var d=_subData,g=d.groups,tpls=d.tpls;
  var q=(filter||"").toLowerCase().trim();
  _subFilter=q;
  // 更新搜索状态
  var sc=document.getElementById("search-clear");
  var sinp=document.getElementById("sub-search");
  if(q!==sinp.value.toLowerCase().trim()){sinp.value=q}
  sc.classList.toggle("visible",q.length>0);
  // 构建 UP主→群组映射
  var upMap={},emptyGroups=[];
  g.forEach(function(gr){
    if(gr.ups.length===0){emptyGroups.push(gr);return}
    gr.ups.forEach(function(u){
      if(!upMap[u.uid])upMap[u.uid]={name:u.name,uid:u.uid,face:u.face||"",groups:[]};
      upMap[u.uid].groups.push({gid:gr.group_id,atall:gr.atall[String(u.uid)]||[],grpData:gr});
    });
  });
  var uidsAll=Object.keys(upMap).sort(function(a,b){return upMap[a].name.localeCompare(upMap[b].name,"zh")});
  // 搜索过滤
  var uids=uidsAll;
  if(q){
    uids=uidsAll.filter(function(uid){
      var up=upMap[uid];
      return up.name.toLowerCase().includes(q)||String(uid).includes(q);
    });
  }
  var totalCount=uidsAll.length,filteredCount=uids.length;
  document.getElementById("search-count").textContent=q?(filteredCount+"/"+totalCount+" 个UP主"):(totalCount+" 个UP主");
  // 快速导航 (超过5个UP主时显示)
  var navHtml="";
  if(uids.length>5){
    navHtml='<div class="quick-nav"><span class="quick-nav-label">快速定位:</span>';
    uids.forEach(function(uid){navHtml+='<span class="quick-nav-chip" data-target="up-'+uid+'">'+escHtml(upMap[uid].name.charAt(0))+'</span>'});
    navHtml+='</div>';
  }
  document.getElementById("sub-quick-nav").innerHTML=navHtml;
  // 绑定快速导航点击
  document.querySelectorAll(".quick-nav-chip").forEach(function(el){
    el.onclick=function(){
      var target=document.getElementById(this.dataset.target);
      if(target){target.scrollIntoView({behavior:"smooth",block:"start"});this.classList.add("active");setTimeout(function(){el.classList.remove("active")},1000)}
    };
  });
  // 构建 HTML
  var html="";
  // 空状态
  if(uids.length===0 && emptyGroups.length===0){
    html='<div class="empty-state"><div class="empty-icon">📭</div><div class="empty-text">'+(q?"没有匹配的UP主":"暂无订阅")+'</div><div class="empty-hint">'+(q?"尝试其他关键词":"输入群号开始管理订阅吧~")+'</div></div>';
  }
  // UP主卡片
  uids.forEach(function(uid,idx){
    var up=upMap[uid];
    var hasTpl=up.groups.some(function(x){return x.grpData.template_dynamic||x.grpData.template_live});
    var filterCnt=up.groups.reduce(function(s,x){return s+(x.grpData.filters?x.grpData.filters.length:0)},0);
    html+='<div class="up-card" id="up-'+uid+'"><div class="up-card-header">';
    // 头像
    html+='<div class="up-avatar-wrapper"><div class="up-avatar-fallback" style="background:linear-gradient(135deg,'+uidColor(uid)+')">'+up.name.charAt(0)+'</div>';
    if(up.face)html+='<img src="'+escHtml(up.face)+'" alt="" loading="lazy" onload="this.style.display=\'\'" onerror="this.classList.add(\'error\')">';
    html+='</div>';
    html+='<div><div class="up-name" style="display:flex;align-items:center;gap:8px">'+escHtml(up.name);
    if(filterCnt>0)html+=' <span class="tag tag-gray" style="font-size:10px;padding:1px 6px">🔇 '+filterCnt+'个屏蔽词</span>';
    if(hasTpl)html+=' <span class="tag tag-blue" style="font-size:10px;padding:1px 6px">📝 自定义模板</span>';
    html+='</div>';
    html+='<div class="up-stats"><span>UID: '+uid+'</span><span>📌 '+up.groups.length+' 个群</span>';
    var tplCount=up.groups.filter(function(x){return x.grpData.template_dynamic||x.grpData.template_live}).length;
    if(tplCount>0)html+='<span>📝 '+tplCount+' 个群自定义模板</span>';
    html+='</div></div></div>';
    // 群组行
    up.groups.forEach(function(grp){
      var gid=grp.gid,atallOn=grp.atall.length>0,grpData=grp.grpData;
      var panelId="ex-"+uid+"-"+gid;
      var isOpen=_openPanels.has(panelId);
      html+='<div class="group-row"><div class="group-chip '+(isOpen?"expanded":"")+'" data-panel="'+panelId+'" onclick="_togglePanel(\''+panelId+'\')">';
      html+='📌 群 '+gid;
      // 状态徽章
      var badges=[];
      if(atallOn)badges.push('<span class="tag tag-pink" style="font-size:10px;padding:1px 6px">🔔</span>');
      if(grpData.filters&&grpData.filters.length)badges.push('<span class="tag tag-gray" style="font-size:10px;padding:1px 6px">🔇'+grpData.filters.length+'</span>');
      if(grpData.template_dynamic||grpData.template_live)badges.push('<span class="tag tag-blue" style="font-size:10px;padding:1px 6px">📝</span>');
      if(badges.length)html+=' '+badges.join("");
      html+=' <span class="arrow">▶</span></div>';
      html+='<div class="group-actions">';
      html+='<button class="btn btn-red btn-xs" onclick="_dSubSmart(\''+gid+'\','+uid+',\''+escHtml(up.name)+'\')">取消订阅</button>';
      html+='</div></div>';
      // 折叠面板
      html+='<div class="toggle-panel '+(isOpen?"open":"")+'" id="'+panelId+'"><div class="toggle-inner">';
      // @全体
      html+='<div style="font-size:11px;font-weight:600;color:var(--text2);margin-bottom:6px">🔔 @全体通知</div>';
      html+='<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px" class="atall-group" data-gid="'+gid+'" data-uid="'+uid+'">';
      ["all","dynamic","video","music","article","live"].forEach(function(t){
        var on=grp.atall.includes(t);
        var lb={all:"全部",dynamic:"动态",video:"视频",music:"音乐",article:"专栏",live:"直播"}[t];
        html+='<span class="atag '+(on?"on":"off")+'" data-type="'+t+'">'+lb+'</span>';
      });
      html+='</div>';
      // 屏蔽词
      html+='<div style="font-size:11px;font-weight:600;color:var(--text2);margin-bottom:4px;border-top:1px solid var(--border);padding-top:8px">🔇 屏蔽词</div>';
      html+='<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px" id="fl-'+gid+'">';
      if(grpData.filters&&grpData.filters.length){
        grpData.filters.forEach(function(f){
          html+='<span class="tag tag-gray">'+escHtml(f.keyword)+' <span style="cursor:pointer;color:var(--pink);font-weight:700" onclick="_dFlSmart(\''+gid+'\',\''+escHtml(f.keyword)+'\')">✕</span></span>';
        });
      }else{html+='<span style="font-size:11px;color:var(--text3)">暂无屏蔽词</span>'}
      html+='</div>';
      html+='<div style="display:flex;gap:6px;align-items:center;margin-bottom:6px">';
      html+='<input class="in" id="if-'+gid+'" placeholder="添加屏蔽词" style="width:100px;font-size:11px">';
      html+='<button class="btn btn-outline btn-xs" onclick="_aFlSmart(\''+gid+'\')">屏蔽</button>';
      html+='</div>';
      // 模板选择
      html+='<div style="border-top:1px solid var(--border);padding-top:8px">';
      html+='<span style="font-size:11px;font-weight:600;color:var(--text2);display:block;margin-bottom:4px">📝 模板覆盖</span>';
      html+='<div class="tpl-compact">';
      html+='<label style="font-size:11px;color:var(--text3)">动态 <select class="in tpl-sel" data-gid="'+gid+'" data-type="dynamic" style="padding:4px 24px 4px 8px;font-size:11px;width:auto">'+tplOptions(grpData.template_dynamic||"",tpls)+'</select></label>';
      html+='<label style="font-size:11px;color:var(--text3)">视频 <select class="in tpl-sel" data-gid="'+gid+'" data-type="video" style="padding:4px 24px 4px 8px;font-size:11px;width:auto">'+tplOptions(grpData.template_video||"",tpls)+'</select></label>';
      html+='<label style="font-size:11px;color:var(--text3)">直播 <select class="in tpl-sel" data-gid="'+gid+'" data-type="live" style="padding:4px 24px 4px 8px;font-size:11px;width:auto">'+tplOptions(grpData.template_live||"",tpls)+'</select></label>';
      html+='</div></div>';
      html+='</div></div>';
    });
    html+='</div>';
  });
  // 空群（待订阅）
  if(emptyGroups.length){
    html+='<div class="card" style="margin-top:12px"><div class="card-header"><h3>📋 待订阅的群 ('+emptyGroups.length+'个)</h3></div>';
    emptyGroups.forEach(function(gr){
      html+='<div class="group-row"><span style="font-weight:500;color:var(--text);min-width:80px">群 '+gr.group_id+'</span>';
      html+='<input class="in" id="iu-'+gr.group_id+'" placeholder="UP主UID" style="width:100px;font-size:11px">';
      html+='<button class="btn btn-blue btn-xs add-sub" data-gid="'+gr.group_id+'">添加订阅</button>';
      html+='<span style="cursor:pointer;color:var(--pink);margin-left:auto;font-weight:700;font-size:14px" onclick="dGroup(\''+gr.group_id+'\')">✕ 移除此群</span></div>';
    });
    html+='</div>';
  }
  document.getElementById("sub-content").innerHTML=html;
  // 绑定事件
  bindSubEvents();
}

// 事件绑定 (render后调用)
function bindSubEvents(){
  document.getElementById("btn-add-group").onclick=function(){
    var inp=document.getElementById("inp-add-group");
    var gid=inp.value.trim();
    if(!gid||!/^[0-9]+$/.test(gid)){toast("请输入正确的群号","error");return}
    inp.value="";
    aGroup(gid);
  };
  document.getElementById("btn-scan-groups").onclick=scanGroups;
  // @全体 toggle
  document.querySelectorAll(".atall-group").forEach(function(grp){
    var gid=grp.dataset.gid,uid=parseInt(grp.dataset.uid);
    grp.querySelectorAll(".atag").forEach(function(el){
      el.onclick=function(){
        var type=this.dataset.type,wasOn=this.classList.contains("on");
        this.classList.toggle("on");this.classList.toggle("off");
        fetch(API+"/group/"+gid+"/atall",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:uid,type:type,enable:!wasOn})})
          .then(function(r){return r.json()}).then(function(d){
            if(d.error){toast(d.error,"error");_smartReload()}else toast((wasOn?"关闭":"开启")+" @"+type,"success")
          });
      };
    });
  });
  // 模板切换
  document.querySelectorAll(".tpl-sel").forEach(function(el){
    el.onchange=function(){
      var gid=this.dataset.gid,ttype=this.dataset.type||"dynamic",tpl=this.value;
      fetch(API+"/group/"+gid+"/template",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({template:tpl,type:ttype})})
        .then(function(r){return r.json()}).then(function(d){toast(d.message||d.error||"已保存",d.error?"error":"success")});
    };
  });
  // 空群添加订阅
  document.querySelectorAll(".add-sub").forEach(function(el){
    el.onclick=function(){
      var gid=this.dataset.gid,inp=document.getElementById("iu-"+gid),u=inp.value.trim();
      if(!u||!/^[0-9]+$/.test(u)){toast("请输入UID","error");return}
      inp.value="";aSub(gid,parseInt(u));
    };
  });
  // 底部栏
  document.getElementById("m-btn-add-group").onclick=function(){
    var gid=document.getElementById("m-add-group").value.trim();
    if(!gid||!/^[0-9]+$/.test(gid)){toast("请输入群号","error");return}
    document.getElementById("m-add-group").value="";
    aGroup(gid);
  };
}

// ===== 搜索 =====
function filterSubs(){
  clearTimeout(_debounceTimer);
  _debounceTimer=setTimeout(function(){
    var q=document.getElementById("sub-search").value;
    _subFilter=q;renderSubs(q);
    if(!q)document.getElementById("sub-quick-nav").innerHTML="";
  },250);
}
function clSearch(){
  document.getElementById("sub-search").value="";
  _subFilter="";
  document.getElementById("search-clear").classList.remove("visible");
  document.getElementById("search-count").textContent="";
  document.getElementById("sub-quick-nav").innerHTML="";
  renderSubs("");
}

// ===== 面板切换 (不重载) =====
function _togglePanel(panelId){
  var panel=document.getElementById(panelId),chip=document.querySelector('.group-chip[data-panel="'+panelId+'"]');
  if(!panel)return;
  var isOpen=panel.classList.contains("open");
  if(isOpen){panel.classList.remove("open");if(chip)chip.classList.remove("expanded");_openPanels.delete(panelId)}
  else{panel.classList.add("open");if(chip)chip.classList.add("expanded");_openPanels.add(panelId)}
}

// ===== 智能重载 (保留状态) =====
function _smartReload(){
  loadSub().then(function(){
    // 清除旧数据重新拉
    _subData=null;loadSub();
  });
}

// ===== 快速添加 =====
function quickAddSub(){
  var gid=document.getElementById("quick-group").value;
  var uid=document.getElementById("quick-uid").value.trim();
  if(!gid||!uid){return}
  if(!/^[0-9]+$/.test(uid)){toast("请输入正确UID","error");return}
  document.getElementById("quick-uid").value="";
  aSub(gid,parseInt(uid));
}

// ===== API 操作 =====
async function aSub(g,u){
  var r=await(await fetch(API+"/group/"+g+"/add",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:u})})).json();
  toast(r.message||r.error||"已添加",r.error?"error":"success");
  _subData=null;loadSub();loadDashboard();
}
async function _dSubSmart(g,u,name){
  if(!confirm("确定在群 "+g+" 中取消订阅「"+name+"」(UID:"+u+") 吗？"))return;
  await dSub(g,u);
}
async function dSub(g,u){
  var r=await(await fetch(API+"/group/"+g+"/del",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:u})})).json();
  toast(r.message||r.error||"已取消",r.error?"error":"success");
  _subData=null;loadSub();loadDashboard();
}
async function aGroup(gid){
  var r=await(await fetch(API+"/group/"+gid+"/addall",{method:"POST"})).json();
  toast(r.message||r.error||"已添加",r.error?"error":"success");
  _subData=null;loadSub();loadDashboard();
}
async function dGroup(gid){
  if(!confirm("确认移除群 "+gid+" 的管理？所有数据将清空。"))return;
  var r=await(await fetch(API+"/group/"+gid+"/delall",{method:"POST"})).json();
  toast(r.message||r.error||"已移除",r.error?"error":"success");
  _subData=null;loadSub();loadDashboard();
}
async function _aFlSmart(gid){
  var inp=document.getElementById("if-"+gid),k=inp.value.trim();
  if(!k){toast("请输入屏蔽词","error");return}
  inp.value="";
  await aFl(gid,k);
}
async function aFl(g,k){
  var r=await(await fetch(API+"/group/"+g+"/filter",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keyword:k})})).json();
  toast(r.message||r.error||"已添加",r.error?"error":"success");
  _subData=null;loadSub();
}
async function _dFlSmart(g,k){
  await dFl(g,k);
}
async function dFl(g,k){
  var r=await(await fetch(API+"/group/"+g+"/filter/del",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keyword:k})})).json();
  toast(r.message||r.error||"已删除",r.error?"error":"success");
  _subData=null;loadSub();
}

// ===== 生成颜色 =====
function uidColor(uid){
  var colors=[["#FB7299","#e85d8a"],["#00A1D6","#0089b5"],["#4caf50","#43a047"],["#ff9800","#f57c00"],["#9c27b0","#7b1fa2"],["#607d8b","#455a64"],["#e91e63","#c2185b"],["#3f51b5","#303f9f"]];
  var i=(parseInt(uid)||0)%colors.length;
  return colors[i][0]+","+colors[i][1];
}

async function scanGroups(){
  var modal=document.getElementById("scan-modal");
  var box=document.getElementById("scan-result");
  modal.style.display="flex";
  box.innerHTML='<div style="text-align:center;padding:24px;font-size:13px">⏳ 扫描中...</div>';
  try{
    var r=await(await fetch(API+"/groups/all")).json();
    if(r.error){box.innerHTML='<div style="color:var(--pink);padding:12px">获取失败: '+r.error+'</div>';return}
    var html='<div style="display:flex;flex-direction:column;gap:6px">';
    r.groups.forEach(function(g){
      html+='<div class="group-row"><span style="font-weight:500">'+escHtml(g.group_name)+'</span> <span style="color:var(--text3);font-size:12px">('+g.group_id+' · '+g.member_count+'人)</span>';
      if(g.managed){html+='<span class="tag tag-green" style="margin-left:auto">✅ 已管理</span>'}
      else{html+='<button class="btn btn-blue btn-xs" style="margin-left:auto" onclick="aGroup(\''+g.group_id+'\');document.getElementById(\'scan-modal\').style.display=\'none\'">添加管理</button>'}
      html+='</div>';
    });
    if(r.groups.length===0)html+='<div style="text-align:center;padding:20px;color:var(--text3)">未找到任何群</div>';
    html+='</div>';
    box.innerHTML=html;
  }catch(e){box.innerHTML='<div style="color:var(--pink);padding:12px">请求失败: '+e.message+'</div>'}
}

// ===== 手动推送 =====
async function loadPush(){
  fetch(API+"/status").then(function(r){return r.json()}).then(function(s){
    fetch(API+"/groups").then(function(r2){return r2.json()}).then(function(g){
      var groupOpts='<option value="">自动定位（推送到所有订阅该UP主的群）</option>';
      g.groups.forEach(function(gr){
        var label=gr.ups.length>0?gr.ups.map(function(u){return u.name}).join(", "):"无订阅";
        groupOpts+='<option value="'+gr.group_id+'">群 '+gr.group_id+' ('+label+')</option>';
      });
      renderPush(groupOpts);
    });
  });
}
function renderPush(groupOpts){
  var html='<div class="card"><div class="card-header"><h3>📤 手动推送内容</h3></div>'+
    '<p style="font-size:13px;color:var(--text2);margin-bottom:14px">输入内容ID，选择目标群后推送。支持 BV号、av号、动态ID、直播间号。</p>'+
    '<div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">'+
    '<input class="in" id="push-id" placeholder="BV号 / av号 / 动态ID / 直播间号" style="flex:2;min-width:200px">'+
    '<select class="in" id="push-group" style="flex:1;min-width:180px">'+groupOpts+'</select>'+
    '<button class="btn btn-primary" id="btn-push" style="padding:9px 24px">🚀 推送</button></div>'+
    '<div id="push-result"></div>'+
    '<div class="card" style="background:#f8fafc;margin-top:12px"><div class="card-header"><h3>📖 支持格式</h3></div>'+
    '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;font-size:12px;color:var(--text2)">'+
    '<div><span class="tag tag-blue">BV号</span> <code style="background:#eef2f7;padding:2px 6px;border-radius:4px;font-size:11px">BV1GJ411x7t7</code></div>'+
    '<div><span class="tag tag-green">av号</span> <code style="background:#eef2f7;padding:2px 6px;border-radius:4px;font-size:11px">av123456</code></div>'+
    '<div><span class="tag tag-pink">动态ID</span> <code style="background:#eef2f7;padding:2px 6px;border-radius:4px;font-size:11px">123456789</code></div>'+
    '<div><span class="tag tag-blue">直播间</span> <code style="background:#eef2f7;padding:2px 6px;border-radius:4px;font-size:11px">12345</code></div></div></div></div>';
  document.getElementById("push-content").innerHTML=html;
  document.getElementById("btn-push").onclick=async function(){
    var id=document.getElementById("push-id").value.trim();
    if(!id){toast("请输入ID","error");return}
    var gid=document.getElementById("push-group").value;
    var result=document.getElementById("push-result");
    this.disabled=true;this.textContent="⏳ 推送中...";
    result.innerHTML='<div style="text-align:center;padding:16px;color:var(--text3)">⏳ 正在获取内容并推送...</div>';
    try{
      var body={id:id};if(gid)body.group_id=gid;
      var r=await(await fetch(API+"/push",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json();
      if(r.error){
        result.innerHTML='<div style="padding:14px;background:#fef2f2;border-radius:var(--radius-sm);color:#dc2626;font-size:13px">❌ '+r.error+'</div>';
      }else{
        var typeIcon={dynamic:"📰",video:"🎬",live:"🔴"}[r.push_type]||"📄";
        var typeName={dynamic:"动态",video:"视频",live:"直播"}[r.push_type]||r.push_type;
        result.innerHTML='<div style="padding:14px;background:#f0faf0;border-radius:var(--radius-sm);font-size:13px">'+
          '✅ 推送完成<br><div style="margin-top:8px;color:var(--text2);font-size:12px">'+
          typeIcon+' 类型: '+typeName+' | 👤 UID: '+r.uid+'<br>'+
          '📌 '+escHtml(r.title||"")+'<br>'+
          '📋 目标: '+r.total_groups+'群 | ✅ 成功: '+r.success+
          (r.failed&&r.failed.length?' | ❌ 失败: '+r.failed.join(", "):'')+
          '</div></div>';
      }
    }catch(e){result.innerHTML='<div style="padding:14px;background:#fef2f2;border-radius:var(--radius-sm);color:#dc2626;font-size:13px">❌ '+e.message+'</div>'}
    this.disabled=false;this.textContent="🚀 推送";
  };
}

// ===== Cookie =====
async function loadCookie(){
  var s=await(await fetch(API+"/status")).json();
  var html='<div class="card"><div class="card-header"><h3>🍪 Cookie 配置</h3><span class="tag '+(s.cookie_configured?"tag-green":"tag-pink")+'">'+(s.cookie_configured?"已配置":"未配置")+'</span></div>';
  if(s.cookie_uid)html+='<p style="font-size:13px;color:var(--text2);margin-bottom:12px">当前绑定 UID: <strong>'+s.cookie_uid+'</strong></p>';
  html+='<div style="margin-bottom:12px"><label style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:6px;display:block">Cookie 字符串</label><textarea class="in w" id="cv" style="min-height:60px;font-size:12px"></textarea></div>'+
    '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">'+
    '<button class="btn btn-primary btn-sm" id="btn-save-cookie">💾 保存Cookie</button>'+
    '<button class="btn btn-red btn-sm" id="btn-clear-cookie">🗑️ 清除Cookie</button>'+
    '<button class="btn btn-blue btn-sm" id="btn-qr-login">📱 扫码登录</button></div>'+
    '<div id="qrbox" style="display:none;text-align:center"><div id="qrmsg" style="font-size:13px;color:var(--text2);margin-bottom:10px"></div><div id="qrimg"></div></div></div>';
  document.getElementById("cookie-content").innerHTML=html;
  document.getElementById("btn-save-cookie").onclick=svCookie;
  document.getElementById("btn-clear-cookie").onclick=clCookie;
  document.getElementById("btn-qr-login").onclick=qrLogin;
}
async function svCookie(){var v=document.getElementById("cv").value.trim();if(!v){toast("请输入Cookie","error");return}var r=await(await fetch(API+"/cookie/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({cookie:v})})).json();toast(r.message||r.error||"已保存",r.error?"error":"success");loadCookie();loadDashboard()}
async function clCookie(){if(!confirm("确定清除Cookie？"))return;var r=await(await fetch(API+"/cookie/clear",{method:"POST"})).json();toast(r.message||r.error||"已清除",r.error?"error":"success");loadCookie();loadDashboard()}
async function qrLogin(){
  document.getElementById("qrbox").style.display="block";
  document.getElementById("qrmsg").textContent="正在生成二维码...";
  var r=await(await fetch(API+"/cookie/qrcode")).json();
  if(r.error){document.getElementById("qrmsg").textContent="获取失败: "+r.error;return}
  document.getElementById("qrimg").innerHTML='<img src="data:image/png;base64,'+r.qrcode_png+'" style="width:160px;height:160px;border-radius:8px;border:1px solid var(--border)">';
  document.getElementById("qrmsg").textContent="📱 请使用B站手机APP扫码登录";
  var poll=function(){
    fetch(API+"/cookie/poll?key="+r.qrcode_key).then(function(r2){return r2.json()}).then(function(d){
      if(d.success){document.getElementById("qrmsg").textContent="✅ 登录成功!";document.getElementById("qrimg").innerHTML="";loadCookie();loadDashboard();return}
      if(d.error&&d.error.includes("超时")){document.getElementById("qrmsg").textContent="⏰ 二维码已过期，请重新获取";return}
      setTimeout(poll,3000);
    });
  };
  setTimeout(poll,3000);
}

// ===== 模板管理 =====
var TPL_VARS_DYNAMIC={"name":"UP主名称","avatar":"头像URL","time":"相对时间","pub_time":"完整发布时间","type_text":"动态类型","content":"纯文本内容","content_html":"HTML内容","images":"图片URL数组","media_title":"视频/专栏标题","media_desc":"视频/专栏简介","media_cover":"视频/专栏封面","media_link":"跳转链接","media_badge":"角标文字","comment_count":"评论数","forward_count":"转发数","like_count":"点赞数","forward_name":"转发来源UP主","forward_content":"转发原文","dynamic_id":"动态ID"};
var TPL_VARS_LIVE={"cover":"封面URL","title":"直播标题","name":"主播名称","avatar":"主播头像","uid":"主播UID","area":"直播分区","start_time":"开播时间","online":"人气值(格式化)","online_raw":"人气值(数字)","live_link":"直播间链接","room_id":"房间号"};

async function loadTpl(){
  var r=await(await fetch(API+"/template/list")).json();
  var tpls=r.templates||["dynamic.html","live.html","help.html"];
  var html='<div class="tpl-grid">';
  tpls.forEach(function(t){
    var icon="📄";if(t.includes("dynamic"))icon="📰";else if(t.includes("live"))icon="🔴";else if(t.includes("video"))icon="🎬";else if(t.includes("help"))icon="❓";
    html+='<div class="tpl-card" onclick="loadTplEdit(\''+t+'\')"><div class="tpl-icon">'+icon+'</div><div class="tpl-name">'+t.replace(".html","")+'</div><div class="tpl-hint">点击编辑</div></div>';
  });
  html+='<div class="tpl-card upload" onclick="document.getElementById(\'tpl-upload-input\').click()"><div class="tpl-icon">📤</div><div class="tpl-name" style="color:var(--blue)">上传模板</div><div class="tpl-hint">选择文件</div></div>';
  html+='<input type="file" id="tpl-upload-input" accept=".html" style="display:none" onchange="uploadTplFile(this)"></div>';
  html+='<div id="tpl-editor"></div>';
  document.getElementById("tpl-content").innerHTML=html;
}
async function loadTplEdit(path){
  var r=await(await fetch(API+"/template?path="+path)).json();
  if(r.error){toast(r.error,"error");return}
  var guessType="dynamic";if(path.includes("live"))guessType="live";else if(path.includes("video"))guessType="video";
  var vars=guessType==="live"?TPL_VARS_LIVE:TPL_VARS_DYNAMIC;
  var varDoc='<div style="margin-top:8px"><div style="font-size:13px;font-weight:600;color:var(--text);cursor:pointer;margin-bottom:6px" onclick="var d=document.getElementById(\'tpl-vars-body\');d.style.display=d.style.display==\'none\'?\'block\':\'none\'">📖 模板变量 <span style="font-size:11px;color:var(--text3)">点击展开</span></div><div id="tpl-vars-body" style="display:none;font-size:12px">';
  for(var k in vars)varDoc+='<div style="display:flex;padding:3px 0;border-bottom:1px solid var(--border)"><span style="color:var(--blue);font-family:monospace;min-width:140px;font-size:11px">'+k+'</span><span style="color:var(--text2);font-size:11px">'+vars[k]+'</span></div>';
  varDoc+='</div></div>';
  var typeSel='<select class="in" id="preview-type" style="padding:4px 8px;font-size:12px;width:auto">'+
    '<option value="dynamic" '+(guessType==="dynamic"?"selected":"")+'>动态</option>'+
    '<option value="video" '+(guessType==="video"?"selected":"")+'>视频</option>'+
    '<option value="live" '+(guessType==="live"?"selected":"")+'>直播</option></select>';
  var html='<div class="card" style="margin-top:16px"><div class="card-header"><h3>✏️ 编辑: '+path+'</h3><button class="btn btn-outline btn-sm" onclick="loadTpl()">← 返回列表</button></div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">'+
    '<div><textarea class="in w" id="tplct" style="min-height:300px;font-size:12px">'+escHtml(r.content||"")+'</textarea>'+
    '<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-primary btn-sm" onclick="svTpl(\''+path+'\')">💾 保存</button>'+
    '<button class="btn btn-blue btn-sm" onclick="previewTpl(\''+path+'\')">👁️ 预览 '+typeSel+'</button></div></div>'+
    '<div><div id="tpl-preview" style="background:#f8fafc;border-radius:var(--radius-sm);padding:12px;text-align:center;font-size:12px;color:var(--text3);min-height:100px;display:flex;align-items:center;justify-content:center">点击「预览」查看效果</div>'+varDoc+'</div></div></div>';
  document.getElementById("tpl-editor").innerHTML=html;
}
async function previewTpl(path){
  var box=document.getElementById("tpl-preview"),typeEl=document.getElementById("preview-type"),ptype=typeEl?typeEl.value:"dynamic";
  box.innerHTML="<span style='color:var(--text3)'>⏳ 渲染中...</span>";
  var r=await(await fetch(API+"/template/preview?path="+path+"&type="+ptype)).json();
  if(r.error){box.innerHTML="<span style='color:var(--pink)'>渲染失败: "+r.error+"</span>";return}
  box.innerHTML='<img src="data:image/png;base64,'+r.image+'" style="max-width:100%;border-radius:6px">';
}
async function svTpl(path){var c=document.getElementById("tplct").value;var r=await(await fetch(API+"/template/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:path,content:c})})).json();toast(r.message||r.error||"已保存",r.error?"error":"success")}
async function uploadTplFile(input){
  var file=input.files[0];if(!file)return;
  if(!file.name.endsWith(".html")){toast("仅支持 .html 文件","error");return}
  var text=await file.text();
  var r=await(await fetch(API+"/template/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:file.name,content:text})})).json();
  toast(r.message||r.error||"已上传",r.error?"error":"success");
  input.value="";loadTpl();
}

// ===== 设置 =====
function renderSettings(){
  document.getElementById("settings-content").innerHTML='<div class="card"><div class="card-header"><h3>🔤 字体设置</h3></div>'+
    '<p style="font-size:13px;color:var(--text2);margin-bottom:14px">设置推送图片渲染时使用的字体名称</p>'+
    '<label style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:6px;display:block">字体名称</label>'+
    '<input class="in" id="fn" value="HarmonyOS Sans SC" style="max-width:300px">'+
    '<div style="margin-top:12px"><button class="btn btn-primary btn-sm" id="btn-save-font">💾 保存设置</button></div></div>'+
    '<div class="card" style="margin-top:12px"><div class="card-header"><h3>ℹ️ 关于</h3></div>'+
    '<div style="font-size:13px;color:var(--text2);line-height:1.8">'+
    '<div><strong>B站通知助手</strong> NoneBot2 插件</div>'+
    '<div>🤖 自动检测 B站 UP主 动态/直播并推送到 QQ 群</div>'+
    '<div>📋 Web 后台管理订阅、模板、Cookie</div>'+
    '<div style="margin-top:8px;font-size:12px;color:var(--text3)">Powered by NoneBot2 · Playwright · Jinja2</div></div></div>';
  document.getElementById("btn-save-font").onclick=async function(){var n=document.getElementById("fn").value.trim();var r=await(await fetch(API+"/font/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({font_name:n})})).json();toast(r.message||r.error||"已保存",r.error?"error":"success")};
}

// ===== 初始化 =====
loadDashboard();
// 移动端自动隐藏侧边栏信息
if(window.innerWidth<=768)document.querySelector(".sidebar").style.width="var(--sidebar-collapsed)";
</script>
</body>
</html>
"""


driver = get_driver()


@driver.on_startup
async def _():
    from nonebot_plugin_bilibili import plugin_config
    if not plugin_config.web_enable:
        logger.info("B站后台已关闭（bilibili_web_enable=false）")
        return
    try:
        app = get_app()
        app.include_router(router)
        port = getattr(driver.config, "port", 8090)
        host = getattr(driver.config, "host", "0.0.0.0")
        logger.success(f"B站后台: http://{host}:{port}/bili/")
    except Exception as e:
        logger.warning(f"B站后台启动失败: {e}")
