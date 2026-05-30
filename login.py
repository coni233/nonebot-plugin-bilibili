"""B站通知插件 — QR扫码登录

直接参照 bilibili-dynamic-mirai-plugin 的简易登录流程:
1. 调用 API 生成二维码
2. 发送二维码图片给用户
3. 轮询扫码结果(每3秒)
4. 扫码成功后解析Cookie并保存
"""

import asyncio
import io
import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs

import httpx
from nonebot.log import logger

from .client import bili_client
from .model import cookie_storage


async def generate_qrcode_png(url: str) -> Optional[bytes]:
    """生成二维码图片PNG字节"""
    try:
        import qrcode

        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        logger.warning("qrcode 库未安装，尝试在线生成二维码")
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(
                    f"https://api.qrserver.com/v1/create-qr-code/",
                    params={"size": "300x300", "data": url},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return resp.content
        except Exception as e:
            logger.warning(f"在线二维码生成失败: {e}")
        return None


async def do_login() -> Optional[Tuple[str, str, bytes]]:
    """执行扫码登录，返回 (qrcode_key, url, png_bytes)，失败返回None"""
    qrcode_data = await bili_client.get_login_qrcode()
    if not qrcode_data:
        logger.error("获取登录二维码失败")
        return None

    qrcode_url = qrcode_data.get("url")
    qrcode_key = qrcode_data.get("qrcode_key")
    if not qrcode_url or not qrcode_key:
        logger.error("二维码数据不完整")
        return None

    png_data = await generate_qrcode_png(qrcode_url)
    if not png_data:
        logger.error("生成二维码图片失败")
        return None

    return qrcode_key, qrcode_url, png_data


async def poll_login(qrcode_key: str, timeout: int = 180) -> Optional[str]:
    """轮询扫码结果，返回Cookie字符串"""
    import asyncio

    start = asyncio.get_event_loop().time()
    while True:
        elapsed = asyncio.get_event_loop().time() - start
        if elapsed > timeout:
            logger.warning("登录超时")
            return None

        await asyncio.sleep(3)

        try:
            result = await bili_client.get_login_info(qrcode_key)
        except Exception as e:
            logger.warning(f"轮询登录状态失败: {e}")
            continue

        if not result:
            continue

        code = result.get("code")
        if code == 0:
            # 扫码成功，解析Cookie
            data = result.get("data", {})
            redirect_url = data.get("url", "")
            if not redirect_url:
                logger.error("登录成功但未获取到重定向URL")
                continue

            # 从URL参数中提取Cookie
            cookie = _parse_cookie_from_url(redirect_url)
            if cookie:
                # 同时提取UID
                uid = _extract_uid(cookie)
                cookie_storage.cookie = cookie
                if uid:
                    cookie_storage.uid = uid
                logger.success(f"B站登录成功! UID: {uid}")
                return cookie
        elif code == 86038:
            # 二维码已失效
            logger.warning("二维码已失效")
            return None
        elif code == -412:
            logger.warning("请求太频繁，稍后再试")
            await asyncio.sleep(5)

        # code=-1: 未扫码, code=-2: 未确认
        # 继续轮询


def _parse_cookie_from_url(url: str) -> str:
    """从登录回调URL中提取Cookie"""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    cookies = []
    for key in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
        values = params.get(key)
        if values:
            val = values[0]
            # 对特殊字符编码
            val = val.replace(",", "%2C").replace("*", "%2A")
            cookies.append(f"{key}={val}")

    return "; ".join(cookies) if cookies else ""


def _extract_uid(cookie: str) -> Optional[int]:
    """从Cookie中提取UID"""
    match = re.search(r"DedeUserID=(\d+)", cookie)
    if match:
        return int(match.group(1))
    return None
