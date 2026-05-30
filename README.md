# B站通知助手 — NoneBot2 插件

基于 NoneBot2 + OneBot V11 的 Bilibili 动态/直播订阅推送插件。

参考 [bilibili-dynamic-mirai-plugin](https://github.com/Colter23/bilibili-dynamic-mirai-plugin) v3 设计。

## 特性

- **动态推送** — 15 秒轮询检测订阅 UP 主的新动态，HTML 渲染为图片推送至群
- **直播推送** — 实时检测开播/下播状态，推送卡片通知
- **Web 管理后台** — 浏览器管理订阅、Cookie、模板、字体，无需操作指令
- **扫码登录** — 支持手机扫码或手动填入 Cookie
- **@全体 管理** — 六种独立类型（全部/动态/视频/音乐/专栏/直播）
- **关键词过滤** — 按关键词屏蔽不想接收的动态
- **自定义模板** — 每群可独立设置动态/视频/直播模板，支持在线编辑上传
- **分组管理** — 按 UP 主维度展示订阅，支持多群订阅同一 UP 主
- **私聊管理** — 超级用户私聊可管理任意群

## 安装

### 前置依赖

```
nonebot2[fastapi,httpx,websockets]>=2.5.0
nonebot-adapter-onebot>=2.4.6
nonebot-plugin-htmlrender>=0.6.7
nonebot-plugin-localstore>=0.7.4
nonebot-plugin-apscheduler>=0.5.0
```

### 安装插件

将 `nonebot_plugin_bilibili` 文件夹复制到项目的插件目录：
# 方式一（nb-cli）
nb install nonebot-plugin-bilibili

# 方式二（pip）
pip install nonebot-plugin-bilibili

```toml
[tool.nonebot.plugins]
"nonebot-plugin-bilibili" = ["nonebot_plugin_bilibili"]
```

### 安装依赖

```bash
pip install qrcode
playwright install chromium
```

## 配置

在 `.env` 或 `.env.prod` 中配置：

```dotenv
SUPERUSERS=["你的QQ号"]
HOST=0.0.0.0

# Web 后台（可选）
bili_web_enable=true
BILI_WEB_PASSWORD=你的管理密码
```

## 指令

### 订阅管理

| 命令 | 说明 | 权限 |
|------|------|------|
| `/bili add <uid> [群号]` | 订阅 UP 主 | 管理员 / 超级用户 |
| `/bili del <uid> [群号]` | 取消订阅 | 管理员 / 超级用户 |
| `/bili list [群号]` | 查看本群订阅列表 | 所有人 |
| `/bili listall` | 查看全部订阅 | 超级用户 |
| `/bili delall [群号]` | 清除本群所有订阅 | 管理员 |
| `/bili delallall` | 删除所有订阅 | 超级用户 |

### @全体 管理

| 命令 | 说明 |
|------|------|
| `/bili atall <类型> on <uid> [群号]` | 开启 @全体 |
| `/bili atall <类型> off <uid> [群号]` | 关闭 @全体 |
| `/bili atall list [群号]` | 查看本群配置 |

类型：`all`（全部）/ `dynamic`（动态）/ `video`（视频）/ `music`（音乐）/ `article`（专栏）/ `live`（直播）

### 过滤管理

| 命令 | 说明 |
|------|------|
| `/bili filter add <keyword>` | 添加过滤词 |
| `/bili filter del <id/keyword>` | 删除过滤规则 |
| `/bili filter list` | 查看本群过滤规则 |

### 系统

| 命令 | 说明 | 权限 |
|------|------|------|
| `/bili login` | 扫码登录 B 站 | 超级用户 |
| `/bili help` | 显示帮助图片 | 所有人 |

## Web 管理后台

启动后访问 `http://IP:你配置的端口/bili/` 即可进入管理后台。

### 订阅标签页

- **添加群** — 输入群号或点击「检测群列表」自动扫描 Bot 加入的所有群
- **UP 主卡片** — 按 UP 主分组显示，每个 UP 主列出所有订阅该 UP 主的群
- **@全体 控制** — 每个群的 UP 主条目下方有六种 @全体 开关按钮
- **屏蔽词** — 每个群可独立设置过滤关键词
- **模板设置** — 每个群可独立选择动态/视频/直播推送模板
- **删除群** — 移除群的所有管理数据

### Cookie 标签页

- 显示 Cookie 配置状态和绑定 UID
- 手动填入 Cookie 或扫码登录

### 模板标签页

- 以卡片形式显示所有可用模板
- 点击进入编辑器，修改后保存
- 上传新的 `.html` 模板文件
- 点击「预览」渲染模板效果图

### 字体标签页

- 设置推送图片使用的字体名称

## 自定义模板

模板使用 Jinja2 + HTML/CSS 渲染，位于 `templates/` 目录。

### 动态模板（dynamic.html）

支持变量：

| 变量 | 说明 |
|------|------|
| `name` | UP 主名称 |
| `avatar` | UP 主头像 URL |
| `pub_time` | 发布时间 (YYYY-MM-DD HH:mm:ss) |
| `type_text` | 动态类型文字 |
| `content` | 动态纯文本内容 |
| `content_html` | 带表情/链接的 HTML 内容 |
| `images` | 图片 URL 列表 |
| `media_title` | 视频/专栏标题 |
| `media_cover` | 视频/专栏封面 URL |
| `media_link` | 跳转链接 |
| `media_badge` | 媒体标签（时长等） |
| `comment_count` | 评论数 |
| `forward_count` | 转发数 |
| `like_count` | 点赞数 |
| `forward_name` | 转发来源 UP 主 |
| `forward_content` | 转发原内容 |
| `dynamic_id` | 动态 ID |

### 视频模板（video.html）

专为视频投稿设计，支持封面大图+播放按钮+标题覆盖。

### 直播模板（live.html）

| 变量 | 说明 |
|------|------|
| `cover` | 直播间封面 URL |
| `title` | 直播标题 |
| `name` | 主播名称 |
| `avatar` | 主播头像 URL |
| `uid` | 主播 UID |
| `area` | 直播分区 |
| `start_time` | 开播时间 |
| `live_link` | 直播间链接 |

## 数据存储

项目本地目录 `data/bilibili/`：

```
项目根/data/bilibili/
├── cookie.json        # B 站 Cookie + UID
├── subscribers.json   # 订阅数据（群、UP主、@全体、过滤、模板）
└── users.json         # UP 主名称缓存
```

可通过 `.env` 中 `bilibili_data_dir` 自定义路径：

```dotenv
bilibili_data_dir=/自定义/路径/data/bilibili
```

## 权限说明

| 角色 | 群聊 | 私聊 |
|------|------|------|
| 普通成员 | 仅 `/bili list` | 不回复 |
| 群管理员/群主 | 全部管理命令 | 不回复 |
| 超级用户 | 全部命令 | 全部命令（加 `-群号` 管理） |

## 技术栈

- **框架**: NoneBot2 + OneBot V11
- **命令**: `on_command`
- **定时任务**: APScheduler（15 秒间隔）
- **图片渲染**: Playwright（htmlrender）
- **数据存储**: JSON 文件
- **Web 后台**: FastAPI
- **B 站 API**: WBI 签名 + Cookie 认证

## 参考

- [bilibili-dynamic-mirai-plugin](https://github.com/Colter23/bilibili-dynamic-mirai-plugin)
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)
- [NoneBot2](https://nonebot.dev/)
