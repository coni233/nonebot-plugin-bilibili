# nonebot-plugin-bilibili

基于 NoneBot2 + OneBot V11 的 Bilibili 动态/直播订阅推送插件，参考 [bilibili-dynamic-mirai-plugin](https://github.com/Colter23/bilibili-dynamic-mirai-plugin) v3 设计。

---

## 特性

- **动态推送** — 定时轮询订阅 UP 主的新动态，通过 Playwright 渲染 HTML 为图片推送至群
- **直播推送** — 实时检测开播/下播状态，推送直播卡片通知
- **Web 管理后台** — 在线管理订阅群、UP 主、Cookie、模板、过滤词
- **扫码登录** — 命令行或 Web 后台均可手机扫码获取 B 站 Cookie
- **@全体 管理** — 六种类型独立开关：全部 / 动态 / 视频 / 音乐 / 专栏 / 直播
- **关键词过滤** — 支持正则过滤，按关键词屏蔽不想接收的动态
- **自定义模板** — 每群/每位 UP 主可独立设置动态、视频、直播推送模板
- **订阅时间戳** — 记录订阅时间，避免推送历史旧动态
- **UP 主信息缓存** — 自动缓存 UP 名称与头像，减少 API 调用

---

## 安装

### 前置要求

- Python >= 3.10
- NoneBot2 >= 2.5.0
- [Playwright](https://playwright.dev) 浏览器（用于 HTML 渲染图片）

```bash
playwright install chromium
```

### 方式一：nb-cli（推荐）

```bash
nb install nonebot-plugin-bilibili
```

### 方式二：pip

```bash
pip install nonebot-plugin-bilibili
```

然后在 `pyproject.toml` 中启用：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_bilibili"]
```

### 方式三：本地开发

```bash
git clone https://github.com/mengbingnaixi/nonebot-plugin-bilibili.git
pip install -e nonebot-plugin-bilibili
```

---

## 配置

所有配置通过 `.env` 或 `.env.prod` 设置，使用 `bilibili_` 前缀。嵌套字段以双下划线分隔。

```dotenv
# ========== 必填 ==========
SUPERUSERS=["你的QQ号"]

# ========== B站 Cookie ==========
# 优先从 Web 后台扫码获取，也可手动填入
bilibili_cookie=SESSDATA=xxx;bili_jct=xxx

# ========== 检测设置 ==========
bilibili_check__interval=20          # 动态检测间隔（秒），默认 20
bilibili_check__live_interval=15     # 直播检测间隔（秒），默认 15
bilibili_check__low_speed=0-0x2      # 低峰时段倍率，格式 "开始小时-结束小时x倍率"
bilibili_check__timeout=10           # HTTP 请求超时（秒），默认 10

# ========== 推送设置 ==========
bilibili_push__message_interval=100  # 同群连续消息间隔（毫秒），默认 100
bilibili_push__push_interval=500     # 跨群推送间隔（毫秒），默认 500

# ========== Web 后台 ==========
bilibili_web_enable=true             # 启用 Web 管理后台，默认 true
bilibili_web_password=你的密码        # 后台密码（空则免密访问）

# ========== 超级管理员 ==========
bilibili_admin=0                     # 管理员 QQ 号，0 表示使用 SUPERUSERS
```

### 完整配置对照表

| 环境变量 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| `bilibili_admin` | int | `0` | 管理员 QQ 号（`0` 则使用 `SUPERUSERS`） |
| `bilibili_cookie` | str | `""` | B 站 Cookie（备选，优先使用 Web 扫码） |
| `bilibili_check__interval` | int | `20` | 动态轮询间隔（秒） |
| `bilibili_check__live_interval` | int | `15` | 直播轮询间隔（秒） |
| `bilibili_check__low_speed` | str | `0-0x2` | 低峰倍率 `时-时x倍` |
| `bilibili_check__timeout` | int | `10` | API 请求超时（秒） |
| `bilibili_push__message_interval` | int | `100` | 同群消息间隔（毫秒） |
| `bilibili_push__push_interval` | int | `500` | 跨群推送间隔（毫秒） |
| `bilibili_web_enable` | bool | `true` | Web 后台开关 |
| `bilibili_web_password` | str | `""` | Web 后台密码（空=免密） |

---

## 指令

所有命令以 `/bili` 开头，群聊中管理员与超级用户均可用管理命令，普通成员仅可查看。

### 订阅管理

| 命令 | 说明 | 权限 |
|------|------|------|
| `/bili add <uid>` | 订阅 UP 主（本群） | 管理员 / 超管 |
| `/bili del <uid>` | 取消订阅（本群） | 管理员 / 超管 |
| `/bili list` | 查看本群订阅列表 | 所有人 |
| `/bili listall` | 查看全部群订阅 | 超级用户 |
| `/bili delall` | 清除本群所有订阅 | 管理员 / 超管 |
| `/bili delallall` | 删除所有群的全部订阅 | 超级用户 |

### @全体 管理

| 命令 | 说明 |
|------|------|
| `/bili atall <类型> on <uid>` | 开启指定类型的 @全体 |
| `/bili atall <类型> off <uid>` | 关闭指定类型的 @全体 |
| `/bili atall list` | 查看本群 @全体 配置 |

**类型说明：**

| 值 | 触发范围 |
|------|---------|
| `all` | 全部动态 + 直播 |
| `dynamic` | 所有动态类型（不含直播） |
| `video` | 仅视频投稿 |
| `music` | 仅音乐 |
| `article` | 仅专栏 |
| `live` | 仅开播通知 |

> **互斥规则**：开启 `all` 会覆盖其他类型；开启 `dynamic` 会覆盖 `video`/`music`/`article`。与参考插件逻辑完全一致。

### 过滤管理

| 命令 | 说明 |
|------|------|
| `/bili filter add <关键词>` | 添加过滤词（正则匹配） |
| `/bili filter del <关键词或序号>` | 删除过滤词 |
| `/bili filter list` | 查看本群过滤规则 |

### 系统

| 命令 | 说明 | 权限 |
|------|------|------|
| `/bili login` | 手机扫码登录 B 站 | 超级用户 |
| `/bili help` | 显示帮助（图形化/文字版） | 所有人 |

### 私聊管理

超级用户可在私聊中使用命令，末尾添加 `-群号` 管理指定群：

```
/bili add 12345 -123456789
/bili del 12345 -123456789
/bili list -123456789
```

---

## Web 管理后台

启动 NoneBot 后浏览器访问 `http://<host>:<port>/bili/`。

> 若设置了 `bilibili_web_password`，需要先登录后使用。

### 仪表盘

顶部状态栏展示：Cookie 状态、已绑定 UID、订阅群数、UP 主总数。

### 订阅管理

- **群搜索** — 支持按群号/群名搜索，250ms 防抖
- **群列表面板** — 展开查看该群订阅的所有 UP 主
- **UP 主卡片** — 显示头像（fallback 为名字首字母）、名称、UID、订阅时间
- **快速索引导航** — 订阅超过 5 个 UP 主时显示字母索引
- **@全体 开关** — 每个 UP 下方六种类型独立开关按钮
- **过滤词管理** — 每群独立添加/删除正则过滤关键词
- **模板选择器** — 每群可独立选择动态、视频、直播模板（高亮标记非默认）
- **添加/移除 UP** — 面板内直接操作
- **清空** — 一键移除群所有数据

### Cookie 管理

- 实时显示 Cookie 状态和绑定的 B 站 UID
- **扫码登录** — 点击生成二维码，使用 Bilibili App 扫码
- **手动填入** — 粘贴完整 Cookie 字符串
- **清除 Cookie** — 一键清除

### 模板管理

- 卡片式列表展示所有可用模板（`dynamic.html`、`video.html`、`live.html`）
- **在线编辑** — 代码编辑器实时修改 HTML 模板
- **预览** — 点击预览渲染效果图（模拟动态/视频数据）
- **上传** — 上传自定义 `.html` 模板文件
- **保存** — 修改后立即生效

### 字体设置

- 设置推送图片渲染使用的字体，影响中文显示效果

### 调试推送

- 提供推送测试工具，模拟推送消息用于调试模板渲染效果

---

## 自定义模板

模板使用 Jinja2 + HTML/CSS，位于插件 `templates/` 目录。可在 Web 后台在线编辑和预览。

### 模板文件

| 文件 | 用途 |
|------|------|
| `dynamic.html` | 动态推送消息 |
| `video.html` | 视频投稿推送 |
| `live.html` | 开播/下播通知 |

### 动态模板变量 (dynamic.html)

| 变量 | 类型 | 说明 |
|------|------|------|
| `name` | str | UP 主名称 |
| `avatar` | str | UP 主头像 URL |
| `pub_time` | str | 发布时间（相对时间，如"3分钟前"） |
| `type_text` | str | 动态类型文字（"投稿视频"/"文字动态"/"转发动态"等） |
| `content` | str | 动态纯文本内容 |
| `content_html` | str | 动态 HTML 内容（含 emoji/话题/@提及等富文本） |
| `images` | list[str] | 动态图片 URL 列表 |
| `media_title` | str | 关联媒体标题（视频名/专栏名） |
| `media_cover` | str | 关联媒体封面图 URL |
| `media_link` | str | 关联媒体跳转链接 |
| `media_badge` | str | 关联媒体标签（如 "bilibili"） |
| `media_desc` | str | 关联媒体简介 |
| `media_type` | str | 关联媒体类型 |
| `comment_count` | int | 评论数 |
| `forward_count` | int | 转发数 |
| `like_count` | int | 点赞数 |
| `dynamic_id` | str | 动态 ID |

**转发动态额外变量：**

| 变量 | 说明 |
|------|------|
| `forward_name` | 转发源 UP 名称 |
| `forward_avatar` | 转发源 UP 头像 |
| `forward_content` | 转发源动态内容 |
| `forward_content_html` | 转发源动态 HTML |
| `forward_images` | 转发源图片列表 |
| `forward_media_title` | 转发源媒体标题 |

### 视频模板变量 (video.html)

专为视频投稿设计，封面大图 + 标题覆盖。

| 变量 | 说明 |
|------|------|
| `name` / `avatar` | UP 主信息 |
| `title` / `desc` | 视频标题和简介 |
| `cover` | 视频封面 URL |
| `link` | 视频链接 |
| `duration` | 视频时长（秒） |
| `play_count` / `like_count` / `coin_count` / `favorite_count` / `comment_count` / `share_count` / `danmaku_count` | 视频统计数据 |
| `pub_time` | 发布时间（相对） |
| `bvid` / `aid` / `cid` | 视频标识 |

### 直播模板变量 (live.html)

| 变量 | 说明 |
|------|------|
| `cover` | 直播间封面 URL |
| `title` | 直播标题 |
| `name` / `avatar` / `uid` | 主播信息 |
| `area` | 直播分区 |
| `start_time` | 开播时间 |
| `live_link` | 直播间链接 |
| `status` | 直播状态 (`LIVE` / `PREPARING` / `ROUND` / `END`) |

---

## 数据存储

插件通过 `nonebot_plugin_localstore` 管理数据目录，默认路径由 LocalStore 决定。数据结构：

```
<localstore_data_dir>/
├── cookie.json        # B 站 Cookie + UID
├── subscribers.json   # 订阅数据（群 → UP 主列表、@全体、过滤词、模板）
└── users.json         # UP 主名称和头像缓存
```

### subscribers.json 结构

```json
{
  "123456789": {
    "uids": [12345, 67890],
    "sub_time": {
      "12345": 1710000000,
      "67890": 1710000100
    },
    "atall": {
      "12345": ["live"],
      "67890": ["all"]
    },
    "filters": [
      {"type": "regex", "keyword": "抽奖"}
    ],
    "template": {
      "dynamic": "custom_dynamic.html",
      "live": "live.html"
    }
  }
}
```

需自定义路径时，可设置 `localstore` 的全局配置，或修改 `.env` 中的 `LOCALSTORE_DATA_DIR`。

---

## 权限模型

| 角色 | 群聊 | 私聊 |
|------|------|------|
| 普通成员 | 仅可执行 `/bili list`、`/bili help` | 不回复 |
| 群管理员 / 群主 | 全部管理命令 | 不回复 |
| 超级用户 | 全部命令 | 全部命令 + `-群号` 指定目标群 |

---

## 技术栈

| 模块 | 技术选型 |
|------|----------|
| 框架 | NoneBot2 (>= 2.5.0) |
| 协议 | OneBot V11 (nonebot-adapter-onebot >= 2.4.6) |
| 命令系统 | `on_command`（标准 NoneBot 命令） |
| 定时任务 | APScheduler（通过 nonebot-plugin-apscheduler） |
| HTML 渲染 | Playwright Chromium（通过 nonebot-plugin-htmlrender） |
| Web 后台 | FastAPI（NoneBot 内置） |
| 页面模板 | Jinja2（推送渲染）+ 内联 HTML（管理后台） |
| 二维码 | qrcode（扫码登录） |
| HTTP 客户端 | httpx（异步请求 B 站 API） |
| 数据模型 | Pydantic >= 2.0（配置验证） |
| 数据存储 | JSON 文件（通过 nonebot-plugin-localstore） |
| B 站 API | WBI 签名 + Cookie 认证 |

---

## 常见问题

### Q: 启动时报 `Module nonebot_plugin_localstore is not loaded as a plugin!`

A: 确认 `pyproject.toml` 中已安装 `nonebot-plugin-localstore`，并在 `__init__.py` 中先 `require("nonebot_plugin_localstore")` 再导入其模块。

### Q: 动态/直播推送重复发送

A: 检查是否同时运行了多个 Bot 实例。使用 `tasklist | findstr python` 确认只有 1 个进程。插件已设置 `replace_existing=True` 防止重复注册定时任务。

### Q: 推送图片中文显示口口口

A: 需要安装中文字体到系统中，并在 Web 后台「字体设置」中指定字体文件路径。Linux 可安装 `fonts-wqy-microhei`。

### Q: Cookie 多久过期？需要频繁续吗？

A: B 站 Cookie 有效期通常约 30 天。过期后 Web 后台会提示，重新扫码即可。插件不会自动续期。

### Q: 如何更新到最新版本？

```bash
# nb-cli 方式
nb plugin update nonebot-plugin-bilibili

# pip 方式
pip install -U nonebot-plugin-bilibili
```

---

## 鸣谢

- [bilibili-dynamic-mirai-plugin](https://github.com/Colter23/bilibili-dynamic-mirai-plugin) — 参考 v3 架构设计
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) — B 站 API 文档
- [NoneBot2](https://nonebot.dev/) — Python 异步聊天机器人框架

---

## 许可证

MIT License

Copyright (c) 2024 mengbingnaixi
