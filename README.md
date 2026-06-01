# nonebot-plugin-bilibili

基于 NoneBot2 + OneBot V11 的 Bilibili 动态/直播订阅推送插件。

参考 [bilibili-dynamic-mirai-plugin](https://github.com/Colter23/bilibili-dynamic-mirai-plugin) v3 设计。

---

## 特性

- **动态推送** — 15 秒轮询检测订阅 UP 主的新动态，HTML 渲染为图片推送至群
- **直播推送** — 实时检测开播/下播状态，推送卡片通知
- **Web 管理后台** — 浏览器在线管理订阅、Cookie、模板
- **扫码登录** — 支持手机扫码获取 B 站 Cookie
- **@全体 管理** — 六种类型独立开关：全部/动态/视频/音乐/专栏/直播
- **关键词过滤** — 按关键词屏蔽不想接收的动态内容
- **自定义模板** — 每群可独立设置动态/视频/直播模板，支持在线编辑上传预览

---

## 安装

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
[tool.nonebot.plugins]
"nonebot-plugin-bilibili" = ["nonebot_plugin_bilibili"]
```

### 安装 Playwright

```bash
playwright install chromium
```

---

## 配置

在 `.env` 或 `.env.prod` 中配置：

```dotenv
SUPERUSERS=["你的QQ号"]
HOST=0.0.0.0

# Web 后台密码（可选，不设置则无需登录）
bili_web_enable=true
BILI_WEB_PASSWORD=你的管理密码
```

---

## 指令

### 订阅管理

| 命令 | 说明 | 权限 |
|------|------|------|
| `/bili add <uid> [群号]` | 订阅 UP 主 | 管理员 |
| `/bili del <uid> [群号]` | 取消订阅 | 管理员 |
| `/bili list [群号]` | 查看本群订阅列表 | 所有人 |
| `/bili delall [群号]` | 清除本群所有订阅 | 管理员 |

### @全体 管理

| 命令 | 说明 |
|------|------|
| `/bili atall <类型> on <uid> [群号]` | 开启 @全体 |
| `/bili atall <类型> off <uid> [群号]` | 关闭 @全体 |
| `/bili atall list [群号]` | 查看本群 @全体 配置 |

**类型说明：**

| 值 | 触发范围 |
|------|---------|
| `all` | 全部动态 + 直播 |
| `dynamic` | 所有动态类型（不含直播） |
| `video` | 仅视频投稿 |
| `music` | 仅音乐 |
| `article` | 仅专栏 |
| `live` | 仅开播通知 |

### 过滤管理

| 命令 | 说明 |
|------|------|
| `/bili filter add <keyword>` | 添加过滤词 |
| `/bili filter del <keyword>` | 删除过滤词 |
| `/bili filter list` | 查看本群过滤规则 |

### 系统

| 命令 | 说明 | 权限 |
|------|------|------|
| `/bili login` | 扫码登录 B 站 | 超级用户 |
| `/bili help` | 显示帮助图片 | 所有人 |

### 私聊管理

超级用户可在私聊中使用命令加 `-群号` 管理指定群：

```
/bili add 12345 -123456789
/bili del 12345 -123456789
/bili list -123456789
```

---

## Web 管理后台

启动后浏览器访问 `http://IP:你设置的端口号/bili/`。

### 订阅标签页

- **添加群** — 输入群号或点击「检测群列表」自动扫描 Bot 加入的所有群
- **UP 主卡片** — 按 UP 主分组显示，每个 UP 主列出所有订阅的群
- **@全体 控制** — 每个群条目下方有六种 @全体 开关按钮
- **屏蔽词** — 每群独立设置过滤关键词
- **模板设置** — 每群独立选择动态/视频/直播模板
- **删除群** — 移除群的所有管理数据

### Cookie 标签页

- 显示 Cookie 状态和绑定 UID
- 手动填入 Cookie 或扫码登录

### 模板标签页

- 卡片式显示所有可用模板
- 点击进入编辑器，修改后保存
- 上传新的 `.html` 模板文件
- 点击「预览」渲染模板效果图

### 字体标签页

- 设置推送图片使用的字体

---

## 自定义模板

模板使用 Jinja2 + HTML/CSS 渲染，位于插件 `templates/` 目录。

### 动态模板（dynamic.html）

| 变量 | 说明 |
|------|------|
| `name` / `avatar` | UP 主名称和头像 |
| `pub_time` | 发布时间 |
| `type_text` | 动态类型文字 |
| `content` / `content_html` | 动态内容（纯文本/HTML） |
| `images` | 图片 URL 列表 |
| `media_title` / `media_cover` | 视频/专栏标题和封面 |
| `media_link` / `media_badge` | 跳转链接和标签 |
| `comment_count` / `forward_count` / `like_count` | 统计 |
| `forward_name` / `forward_content` | 转发内容 |
| `dynamic_id` | 动态 ID |

### 视频模板（video.html）

专为视频投稿设计，封面大图 + 标题覆盖 + 作者信息 + 统计栏。

### 直播模板（live.html）

| 变量 | 说明 |
|------|------|
| `cover` / `title` | 直播间封面和标题 |
| `name` / `avatar` / `uid` | 主播信息 |
| `area` / `start_time` | 分区和开播时间 |
| `live_link` | 直播间链接 |

---

## 数据存储

```
项目根/data/bilibili/
├── cookie.json        # B 站 Cookie + UID
├── subscribers.json   # 订阅数据
└── users.json         # UP 主名称缓存
```

自定义路径：

```dotenv
bilibili_data_dir=/自定义/路径/data/bilibili
```

---

## 权限说明

| 角色 | 群聊 | 私聊 |
|------|------|------|
| 普通成员 | 仅 `/bili list` | 不回复 |
| 群管理员 | 全部管理命令 | 不回复 |
| 超级用户 | 全部命令 | 全部命令加 `-群号` |

---

## 技术栈

- **框架**: NoneBot2 + OneBot V11
- **命令**: `on_command`
- **定时任务**: APScheduler（15 秒间隔）
- **图片渲染**: Playwright（通过 nonebot-plugin-htmlrender）
- **数据存储**: JSON 文件
- **Web 后台**: FastAPI
- **B 站 API**: WBI 签名 + Cookie 认证

---

## 致谢

- [bilibili-dynamic-mirai-plugin](https://github.com/Colter23/bilibili-dynamic-mirai-plugin)
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)
- [NoneBot2](https://nonebot.dev/)

## 许可证

MIT
