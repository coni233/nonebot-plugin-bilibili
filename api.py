"""B站通知插件 — Bilibili API 端点常量"""

# ========== 登录 ==========
LOGIN_QRCODE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
LOGIN_INFO = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# ========== 动态 ==========
NEW_DYNAMIC = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all"
SPACE_DYNAMIC = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
DYNAMIC_DETAIL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"

# ========== 视频 ==========
VIDEO_DETAIL = "https://api.bilibili.com/x/web-interface/view"

# ========== 专栏 ==========
ARTICLE_DETAIL = "https://api.bilibili.com/x/article/viewinfo"
ARTICLE_LIST = "https://api.bilibili.com/x/article/cards"

# ========== 直播 ==========
LIVE_LIST = "https://api.live.bilibili.com/xlive/web-ucenter/v1/xfetter/GetWebList"
LIVE_STATUS_BATCH = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"
LIVE_DETAIL = "https://api.live.bilibili.com/room/v1/Room/get_info"

# ========== 搜索 ==========
SEARCH = "https://api.bilibili.com/x/web-interface/search/type"

# ========== 用户空间 ==========
USER_INFO = "https://api.bilibili.com/x/space/acc/info"
USER_INFO_WBI = "https://api.bilibili.com/x/space/wbi/acc/info"
USER_ID = "https://api.bilibili.com/x/web-interface/nav"
SPACE_SEARCH = "https://api.bilibili.com/x/space/wbi/arc/search"

# ========== 关注 ==========
IS_FOLLOW = "https://api.bilibili.com/x/relation"
FOLLOW = "https://api.bilibili.com/x/relation/modify"

# ========== 分组 ==========
GROUP_LIST = "https://api.bilibili.com/x/relation/tags"
CREATE_GROUP = "https://api.bilibili.com/x/relation/tag/create"
ADD_USER_TO_GROUP = "https://api.bilibili.com/x/relation/tags/addUsers"
DEL_GROUP = "https://api.bilibili.com/x/relation/tag/del"

# ========== PGC(番剧) ==========
PGC_MEDIA_INFO = "https://api.bilibili.com/pgc/review/user"
PGC_INFO = "https://api.bilibili.com/pgc/view/web/season"

# ========== 短链 ==========
SHORT_LINK = "https://api.bilibili.com/x/share/click"
