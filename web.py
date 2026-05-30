"""B站通知插件 — 网页后台

.env 配置:
  bili_web_enable=true    (默认 true，设为 false 关闭后台)
  bili_web_password=密码  (设置后访问需登录)
"""

import hashlib
import os

from nonebot import get_driver, get_app
from nonebot.log import logger
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .model import sub_storage, cookie_storage, user_storage

router = APIRouter(prefix="/bili")


def _get_password() -> str:
    return str(getattr(get_driver().config, "bili_web_password", "") or "")


def _make_token(password: str) -> str:
    return hashlib.md5(f"bili_web_{password}_salt".encode()).hexdigest()


def _check_auth(request: Request) -> bool:
    pwd = _get_password()
    if not pwd:
        return True
    return request.cookies.get("bili_token", "") == _make_token(pwd)


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
    if tpl not in ("dynamic", "live", "help", ""):
        return JSONResponse({"error": "无效模板"}, status_code=400)
    if ttype not in ("dynamic", "live", "video"):
        return JSONResponse({"error": "无效类型"}, status_code=400)
    data = sub_storage.get(group_id, {"uids": [], "atall": {}, "filters": []})
    key = f"template_{ttype}"
    if tpl:
        data[key] = tpl
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
async def api_template_preview(request: Request, path: str = "dynamic.html"):
    """渲染模板为预览图"""
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import base64
    tdir = os.path.join(os.path.dirname(__file__), "templates")
    safe = os.path.normpath(os.path.join(tdir, os.path.basename(path)))
    if not safe.startswith(os.path.normpath(tdir)) or not os.path.exists(safe):
        return JSONResponse({"error": "模板不存在"}, status_code=404)
    try:
        from nonebot_plugin_htmlrender import html_to_pic
        html = open(safe, encoding="utf-8").read()
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
.page{padding:12px 16px;display:none}
.page.cur{display:block}
.card{background:#fff;border-radius:8px;padding:12px 16px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.card h3{font-size:14px;margin-bottom:6px}
.stat{display:inline-block;padding:10px 16px;margin-right:8px;margin-bottom:8px;background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.stat .n{font-size:24px;font-weight:700;color:#178bcf}
.stat .l{font-size:11px;color:#9499a0}
.tag{display:inline-block;padding:3px 10px;margin:2px;border-radius:12px;font-size:12px;background:#f0f2f5}
.tag .del{cursor:pointer;margin-left:4px;color:#999}
.tag .del:hover{color:#fb7299}
.btn{padding:6px 14px;border:none;border-radius:6px;cursor:pointer;font-size:12px;color:#fff;background:#178bcf}
.btn:hover{opacity:.85}
.btn.green{background:#4caf50}
.btn.red{background:#fb7299}
.in{padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:12px;outline:none;width:140px}
.in:focus{border-color:#178bcf}
.mt{margin-top:8px}
.mb{margin-bottom:8px}
.w{width:100%}
label{font-size:12px;color:#666;display:block;margin-bottom:3px}
textarea{width:100%;min-height:120px;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:12px;font-family:monospace;resize:vertical}
textarea:focus{border-color:#178bcf;outline:none}
select{padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:12px;outline:none}
.atag{display:inline-block;padding:2px 8px;margin:2px;border-radius:4px;font-size:11px;cursor:pointer;border:1px solid #ddd}
.atag.on{background:#e8f5e9;color:#2e7d32;border-color:#a5d6a7}
.atag.off{background:#f5f5f5;color:#999}
.hide{display:none}
</style>
</head>
<body>

<div class="header">
  <h1>B站通知助手</h1>
  <p>Bilibili 动态/直播 订阅管理</p>
</div>

<div class="tabs" id="tabs"></div>

<div id="page-sub" class="page cur"></div>
<div id="page-font" class="page"></div>
<div id="page-tpl" class="page"></div>
<div id="page-cookie" class="page"></div>

<script>
var API="/bili/api";
var CUR="sub";
var TABS={sub:"订阅",font:"字体",tpl:"模板",cookie:"Cookie"};

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

// ==== 订阅 ====
async function loadSub(){
  var s=await(await fetch(API+"/status")).json();
  if(s.error)return;
  var g=await(await fetch(API+"/groups")).json();
  var html='<div style="margin-bottom:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><input class="in" id="inp-add-group" placeholder="输入群号添加管理" style="flex:1;min-width:150px"><button class="btn green" id="btn-add-group">添加</button><button class="btn" id="btn-scan-groups">📡 检测群列表</button></div><div class="mb" id="group-scan-result"></div><div><div class="stat"><div class="n">'+s.group_count+'</div><div class="l">已管理</div></div><div class="stat"><div class="n">'+s.up_count+'</div><div class="l">UP主</div></div></div>';
  // 按UP主重组数据
  var upMap={}, emptyGroups=[];
  g.groups.forEach(function(gr){
    if(gr.ups.length==0){emptyGroups.push(gr);return}
    gr.ups.forEach(function(u){
      if(!upMap[u.uid])upMap[u.uid]={name:u.name,groups:[]};
      upMap[u.uid].groups.push({gid:gr.group_id,atall:gr.atall[String(u.uid)]||[]});
    });
  });
  // 按UP主生成卡片
  Object.keys(upMap).sort().forEach(function(uid){
    var up=upMap[uid];
    html+='<div class="card"><h3 style="font-size:15px;color:#fb7299;font-weight:700;margin-bottom:8px">🎤 '+up.name+' <span style="font-size:11px;color:#999;font-weight:400">UID:'+uid+'</span></h3>';
    up.groups.forEach(function(grp){
      var gid=grp.gid;
      html+='<div style="border:1px solid #e8e8e8;border-radius:8px;padding:8px 10px;margin:6px 0">';
      html+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-size:13px;font-weight:600;color:#18191c">📌 群 '+gid+'</span><span class="del" data-gid="'+gid+'" data-uid="'+uid+'" style="cursor:pointer;font-size:12px;color:#fb7299">✕ 取消订阅</span></div>';
      // 显示该群所有过滤词
      var grpData=g.groups.find(function(x){return x.group_id==gid});
      if(grpData&&grpData.filters&&grpData.filters.length){
        html+='<div style="font-size:11px;color:#999;margin-bottom:4px">🔍 屏蔽词: '+grpData.filters.map(function(f){return f.keyword}).join(", ")+'</div>';
      }
      html+='<div style="font-size:12px;color:#666">@全体: ';
      html+=["all","dynamic","video","music","article","live"].map(function(t){
        var on=grp.atall.includes(t);
        var lb={all:"全部",dynamic:"全部动态",video:"视频",music:"音乐",article:"专栏",live:"直播"}[t];
        return '<span class="atag '+(on?"on":"off")+'" data-gid="'+gid+'" data-uid="'+uid+'" data-type="'+t+'" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:4px;font-size:11px;cursor:pointer;'+(on?"background:#e8f5e9;color:#2e7d32;border:1px solid #a5d6a7":"background:#f5f5f5;color:#999;border:1px solid #ddd")+'">'+lb+'</span>';
      }).join("");
      html+='</div>';
      // 模板选择
      var tplDyn=grpData?grpData.template_dynamic||"":"";
      var tplLiv=grpData?grpData.template_live||"":"";
      var tplVid=grpData?grpData.template_video||"":"";
      html+='<div style="margin-top:4px;font-size:11px;display:flex;align-items:center;gap:4px;flex-wrap:wrap"><span style="color:#999">动态:</span>';
      html+='<select class="tpl-sel" data-gid="'+gid+'" data-type="dynamic" style="padding:2px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px"><option value="">默认</option><option value="dynamic" '+(tplDyn=="dynamic"?"selected":"")+'>动态</option><option value="video">视频</option><option value="live" '+(tplDyn=="live"?"selected":"")+'>直播</option></select>';
      html+='<span style="color:#999;margin-left:6px">视频:</span>';
      html+='<select class="tpl-sel" data-gid="'+gid+'" data-type="video" style="padding:2px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px"><option value="">默认</option><option value="dynamic" '+(tplVid=="dynamic"?"selected":"")+'>动态</option><option value="video">视频</option><option value="live" '+(tplVid=="live"?"selected":"")+'>直播</option></select>';
      html+='<span style="color:#999;margin-left:6px">直播:</span>';
      html+='<select class="tpl-sel" data-gid="'+gid+'" data-type="live" style="padding:2px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px"><option value="">默认</option><option value="dynamic" '+(tplLiv=="dynamic"?"selected":"")+'>动态</option><option value="video">视频</option><option value="live" '+(tplLiv=="live"?"selected":"")+'>直播</option></select></div>';
      html+='</div>';
    });
    html+='</div>';
  });
  // 显示空群（有管理但无订阅）
  if(emptyGroups.length){
    html+='<div class="card"><h3 style="font-size:14px;color:#18191c;margin-bottom:8px">📋 已管理的群（无订阅）</h3>';
    emptyGroups.forEach(function(gr){
      html+='<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 8px;margin:4px 0;background:#f6f8fa;border-radius:8px">';
      html+='<span style="font-size:13px;color:#61666d">📌 群 '+gr.group_id+'</span>';
      html+='<div><input class="in" id="iu-'+gr.group_id+'" placeholder="输入UID" style="width:100px"><button class="btn add-sub" data-gid="'+gr.group_id+'" style="margin-left:4px">添加UP主</button>';
      html+=' <input class="in" id="if-'+gr.group_id+'" placeholder="屏蔽词" style="width:90px"><button class="btn add-filter" data-gid="'+gr.group_id+'" style="margin-left:4px;background:#fb7299">屏蔽</button>';
      html+=' <span style="font-size:11px;color:#999">动态:</span><select class="tpl-sel" data-gid="'+gr.group_id+'" data-type="dynamic" style="padding:2px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px"><option value="">默认</option><option value="dynamic" '+(gr.template_dynamic=="dynamic"?"selected":"")+'>动态</option><option value="video">视频</option><option value="live" '+(gr.template_dynamic=="live"?"selected":"")+'>直播</option></select>';
      html+=' <span style="font-size:11px;color:#999">视频:</span><select class="tpl-sel" data-gid="'+gr.group_id+'" data-type="video" style="padding:2px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px"><option value="">默认</option><option value="dynamic" '+(gr.template_video=="dynamic"?"selected":"")+'>动态</option><option value="video">视频</option><option value="live" '+(gr.template_video=="live"?"selected":"")+'>直播</option></select>';
      html+=' <span style="font-size:11px;color:#999">直播:</span><select class="tpl-sel" data-gid="'+gr.group_id+'" data-type="live" style="padding:2px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px"><option value="">默认</option><option value="dynamic" '+(gr.template_live=="dynamic"?"selected":"")+'>动态</option><option value="video">视频</option><option value="live" '+(gr.template_live=="live"?"selected":"")+'>直播</option></select>';
      html+=' <span class="del-group" data-gid="'+gr.group_id+'" style="cursor:pointer;font-size:12px;color:#fb7299;margin-left:6px">✕</span></div></div>';
    });
    html+='</div>';
  }
  document.getElementById("page-sub").innerHTML=html;
  
  // 事件绑定
  document.querySelectorAll(".del").forEach(function(el){
    el.onclick=function(){
      var gid=this.dataset.gid;
      var uid=this.dataset.uid;
      var kw=this.dataset.kw;
      if(uid){dSub(gid,parseInt(uid));}
      if(kw){dFl(gid,kw);}
    };
  });
  document.querySelectorAll(".atag").forEach(function(el){
    el.onclick=function(){
      var gid=this.dataset.gid;
      var uid=parseInt(this.dataset.uid);
      var type=this.dataset.type;
      tglAt(gid,uid,type);
    };
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
  document.getElementById("btn-add-group").onclick=function(){
    var inp=document.getElementById("inp-add-group");
    var gid=inp.value.trim();
    if(!gid||!/^[0-9]+$/.test(gid)){toast("输入群号");return}
    inp.value="";
    fetch(API+"/group/"+gid+"/addall",{method:"POST"}).then(function(r){return r.json()}).then(function(d){toast(d.message||d.error||"ok");loadSub()});
  };
  document.getElementById("btn-scan-groups").onclick=function(){
    var box=document.getElementById("group-scan-result");
    box.innerHTML="<span style='font-size:12px;color:#999'>扫描中...</span>";
    fetch(API+"/groups/all").then(function(r){return r.json()}).then(function(d){
      if(d.error){box.innerHTML="<span style='font-size:12px;color:#fb7299'>获取失败</span>";return}
      var html="<div style='display:flex;flex-wrap:wrap;gap:6px'>";
      d.groups.forEach(function(g){
        html+="<span style='display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:6px;font-size:12px;"+(g.managed?"background:#e8f5e9;color:#2e7d32;border:1px solid #a5d6a7":"background:#f5f5f5;color:#666;border:1px solid #ddd;cursor:pointer")+"' "+(g.managed?"":("onclick=\"addGroupScan('"+g.group_id+"')\""))+">"+
          g.group_name+" ("+g.group_id+")"+(g.managed?" ✅":"")+
        "</span>";
      });
      html+="</div>";
      box.innerHTML=html;
    });
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
async function loadTpl(){
  var r=await(await fetch(API+"/template/list")).json();
  var html='<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px">';
  (r.templates||["dynamic.html","live.html","help.html"]).forEach(function(t){
    html+='<div class="card" style="cursor:pointer;flex:1;min-width:140px;text-align:center;padding:14px 12px" onclick="loadTplEdit(\''+t+'\')">';
    html+='<div style="font-size:24px;margin-bottom:6px">'+(t.includes("dynamic")?"📰":t.includes("live")?"🔴":"❓")+'</div>';
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
  document.getElementById("page-tpl").innerHTML=html+'<div id="tpl-editor"></div>';
}

async function loadTplEdit(path){
  var r=await(await fetch(API+"/template?path="+path)).json();
  if(r.error){toast(r.error);return}
  document.getElementById("tpl-editor").innerHTML='<div class="card"><h3 style="margin-bottom:8px">编辑: '+path+'</h3>'+
    '<div style="display:flex;gap:12px;flex-wrap:wrap"><div style="flex:1;min-width:300px"><div class="mb"><textarea id="tplct" style="min-height:200px">'+escHtml(r.content||"")+'</textarea></div>'+
    '<button class="btn" onclick="svTpl(\''+path+'\')">保存</button> '+
    '<button class="btn" onclick="previewTpl(\''+path+'\')" style="background:#4caf50">预览</button> '+
    '<button class="btn red" onclick="loadTpl()">返回</button></div>'+
    '<div style="flex:1;min-width:300px"><div id="tpl-preview" style="background:#f6f8fa;border-radius:8px;padding:8px;text-align:center;font-size:12px;color:#999">点击「预览」查看效果</div></div></div></div>';
}

async function previewTpl(path){
  var box=document.getElementById("tpl-preview");
  box.innerHTML="<span style='font-size:12px;color:#999'>渲染中...</span>";
  var r=await(await fetch(API+"/template/preview?path="+path)).json();
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

async function svTpl(){
  var p=document.getElementById("tplsel").value;
  var c=document.getElementById("tplct").value;
  var r=await(await fetch(API+"/template/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p,content:c})})).json();
  toast(r.message||r.error||"ok");
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
