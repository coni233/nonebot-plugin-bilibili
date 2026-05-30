"""B站通知插件 — 异步HTTP客户端"""

import hashlib
import time
import warnings
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from nonebot.log import logger

# 关闭 SSL 证书验证警告
warnings.filterwarnings("ignore", category=Warning, module="urllib3")
warnings.filterwarnings("ignore", category=Warning, module="httpx")

from .api import USER_INFO
from .model import cookie_storage


class BiliClient:
    """Bilibili API HTTP 客户端"""

    def __init__(self):
        self._client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(15.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://t.bilibili.com",
                "Origin": "https://t.bilibili.com",
            },
        )
        self._wbi_img: Optional[Dict[str, str]] = None
        self._wbi_fetch_time: int = 0

    @property
    def _cookie_str(self) -> str:
        """获取当前cookie（确保无尾部分号，httpx 校验严格）"""
        c = cookie_storage.cookie.strip().rstrip(";") if cookie_storage.cookie else ""
        uid = cookie_storage.uid
        if uid and f"DedeUserID={uid}" not in c:
            c += f"; DedeUserID={uid}"
        return c

    async def _ensure_wbi(self):
        """获取WBI签名所需的 img_key 和 sub_key"""
        now = int(time.time())
        if self._wbi_img and now - self._wbi_fetch_time < 86400:
            return
        try:
            resp = await self._client.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={"Cookie": self._cookie_str},
            )
            data = resp.json()
            if data.get("code") == 0 and data.get("data", {}).get("wbi_img"):
                self._wbi_img = data["data"]["wbi_img"]
                self._wbi_fetch_time = now
        except Exception as e:
            logger.warning(f"获取WBI img失败: {e}")

    def _wbi_sign(self, params: Dict[str, str]) -> Dict[str, str]:
        """WBI 签名"""
        if not self._wbi_img:
            return params
        img_url = self._wbi_img.get("img_url", "")
        sub_url = self._wbi_img.get("sub_url", "")
        mixin_key = self._get_mixin_key(img_url, sub_key=self._get_sub_key(sub_url))
        params["wts"] = str(int(time.time()))
        # 排序参数
        sorted_params = sorted(params.items(), key=lambda x: x[0])
        query = urlencode(sorted_params)
        sign_str = query + mixin_key
        params["w_rid"] = hashlib.md5(sign_str.encode()).hexdigest()
        return params

    @staticmethod
    def _get_sub_key(url: str) -> str:
        """从图片URL中提取sub_key"""
        return url.rsplit("/", 1)[-1].split(".")[0]

    @staticmethod
    def _get_mixin_key(img_url: str, sub_key: str) -> str:
        """计算mixin key"""
        import re
        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        mixin = img_key + sub_key
        # 混淆表
        order = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
                 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
                 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
                 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
        result = "".join(mixin[i] for i in order if i < len(mixin))
        return result[:32]

    async def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        use_wbi: bool = False,
    ) -> Dict[str, Any]:
        """发起请求"""
        headers = {"Cookie": self._cookie_str}
        if params is None:
            params = {}
        if use_wbi:
            await self._ensure_wbi()
            params = self._wbi_sign({k: str(v) for k, v in params.items()})
        try:
            resp = await self._client.request(
                method, url, params=params, json=data, headers=headers
            )
            return resp.json()
        except httpx.TimeoutException:
            logger.warning(f"请求超时: {url}")
            return {"code": -1, "message": "timeout"}
        except Exception as e:
            logger.warning(f"请求失败 {url}: {e}")
            return {"code": -1, "message": str(e)}

    async def get(
        self, url: str, params: Optional[Dict[str, Any]] = None, use_wbi: bool = False
    ) -> Dict[str, Any]:
        return await self.request("GET", url, params=params, use_wbi=use_wbi)

    async def post(
        self, url: str, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return await self.request("POST", url, data=data)

    # ========== 便捷方法 ==========

    async def get_login_qrcode(self) -> Optional[Dict]:
        """获取登录二维码"""
        data = await self.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
        if data.get("code") == 0:
            return data.get("data")
        return None

    async def get_login_info(self, qrcode_key: str) -> Optional[Dict]:
        """轮询扫码状态"""
        data = await self.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": qrcode_key},
        )
        return data

    async def get_user_info(self, uid: int) -> Optional[Dict]:
        """获取用户信息（WBI签名）"""
        data = await self.get(
            "https://api.bilibili.com/x/space/wbi/acc/info",
            params={"mid": uid},
            use_wbi=True,
        )
        if data.get("code") == 0:
            return data.get("data")
        return None

    async def get_new_dynamic(self, page: int = 1) -> Optional[Dict]:
        """获取最新动态feed"""
        data = await self.get(
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
            params={
                "timezone_offset": "-480",
                "type": "all",
                "page": page,
                "features": "itemOpusStyle",
            },
        )
        if data.get("code") == 0:
            return data.get("data")
        return None

    async def get_live_status(self, uids: list) -> Optional[Dict]:
        """批量获取直播状态"""
        if not uids:
            return None
        # 构建 uids[] 参数（用列表传多个同key参数，避免dict覆盖）
        import urllib.parse
        params_str = "&".join([f"uids[]={urllib.parse.quote(str(uid))}" for uid in uids])
        data = await self.get(
            f"https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids?{params_str}",
        )
        if data.get("code") == 0:
            return data.get("data")
        return None

    async def is_follow(self, uid: int) -> Optional[Dict]:
        """检查是否已关注"""
        data = await self.get(
            "https://api.bilibili.com/x/relation",
            params={"fid": uid},
        )
        if data.get("code") == 0:
            return data.get("data")
        return None

    async def follow_user(self, uid: int) -> Dict:
        """关注用户"""
        return await self.post(
            "https://api.bilibili.com/x/relation/modify",
            data={
                "fid": uid,
                "act": 1,
                "re_src": 11,
                "csrf": self._extract_csrf(),
            },
        )

    def _extract_csrf(self) -> str:
        """从cookie中提取bili_jct (csrf)"""
        cookie = self._cookie_str
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("bili_jct="):
                return part[9:]
        return ""

    async def close(self):
        await self._client.aclose()


# 全局单例
bili_client = BiliClient()
