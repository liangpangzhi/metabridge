# 元影桥（metabridge）

把**飞牛影视（trim.media）已刮削完成**的元数据，导出成 Emby / Jellyfin / Kodi 可直接读取的 NFO，并**写回媒体文件所在的原文件夹**；海报、背景图、剧集剧照一并复制到同目录。

> 本应用只读打开飞牛影视数据库，不修改、不移动、不删除任何视频文件，也不联网抓取数据。

## 仓库与下载

- 源码仓库：https://github.com/liangpangzhi/metabridge
- 安装包下载（Release v1.0.3）：https://github.com/liangpangzhi/metabridge/releases/tag/v1.0.3

## 交付物

- `dist/metabridge-1.0.3-x86.fpk` — Intel / AMD 飞牛设备
- `dist/metabridge-1.0.3-arm.fpk` — ARM 飞牛设备
- `src/metabridge/` — fpk 源码工程（可直接用 `fnpack build` 打包）
- `tools/` — 自检、图标生成、自定义打包、数据库结构诊断脚本

## 安装

飞牛桌面 → 应用中心 → 右上角「手动安装」→ 选择对应架构的 `.fpk`。

也可 SSH 执行：

```bash
appcenter-cli install-fpk /path/to/metabridge-1.0.3-x86.fpk
```

安装后桌面出现「元影桥」图标，默认端口 **22690**。

> 应用以 root 权限运行：飞牛影视数据库 `/usr/local/apps/@appdata/trim.media/database/trimmedia.db` 属主为 root，普通应用用户无法读取。

## 使用

1. 打开「元影桥」→「剧集总概述」，确认数据库状态为“已连接”，并看到电影 / 剧集条目数量。
2. 进入「设置」核对：
   - **数据库路径**：默认自动探测；如自动探测失败，填实际路径。
   - **图片根目录**：默认自动探测 `/vol*/@appmeta/trim.media/img`；多个目录用英文冒号分隔。
   - **媒体目录过滤**：留空表示处理数据库里所有媒体路径；可填一个或多个绝对路径缩小范围。
3. 进入「预览与确认」→ 点「生成预览」，逐部核对元数据与封面。
4. 每张卡片对应一部电影或一部剧集，显示**封面、年份、评分、类型、简介、演员、TMDB/IMDb ID**，并可展开查看即将写入的每个文件与目标目录。
5. 勾选本次要导出的作品（支持全选 / 全不选 / 反选 / 搜索过滤），点「确认导出所选」，在弹窗中再次核对作品数、文件数和目标目录后确认。未确认前不会写入任何文件。
6. 若直接在「导出结果」页点「开始导出」，也会先弹出范围确认；当预览中已有勾选时，只导出勾选的作品。
7. 导出完成后在「导出结果」页查看进度、统计与实时日志；完整历史日志在「日志」页。

### 写出文件规则

| 类型 | 写入位置 | 文件 |
|---|---|---|
| 电影 | 视频同目录 | `<视频名>.nfo`（可选 `movie.nfo`）、`poster.*`、`fanart.*` |
| 剧集 | 剧集根目录 | `tvshow.nfo`、`poster.*`、`fanart.*` |
| 季 | 季目录 | `season.nfo`、`season01.*` … |
| 分集 | 视频同目录 | `<视频名>.nfo`、`<视频名>-thumb.*` |

图片保留飞牛缓存中的原始扩展名（常见 `.webp`，也可能是 `.jpg` / `.png`），`*` 表示实际扩展名。

季目录识别支持 `Season 01` / `S01` / `第1季` 等常见命名；平铺目录也能处理（剧集根目录即视频所在目录）。

NFO 字段包含：`title / originaltitle / year / premiered / plot / runtime / rating / votes / genre / tag / country / studio / mpaa / actor / uniqueid(tmdb,imdb,tvdb) / art`。飞牛数据库中已有的 TMDB / IMDb ID 会原样保留，Emby 可据此精准匹配。

## 真实环境验证（飞牛 fnOS）

已在目标飞牛设备上以只读方式核验：

- 数据库 `/usr/local/apps/@appdata/trim.media/database/trimmedia.db` 权限为 `root:root 600`，因此应用以 root 运行是必需的。
- 图片缓存 `/vol1/@appmeta/trim.media/img`，实际文件为 `.webp` 且仅 root 可读；导出时保留原始扩展名（生成 `poster.webp` / `fanart.webp` / `season01.webp`），并把文件权限设为 644，方便 Emby 读取。
- 当前库规模：电影 835、剧集 224、分集 10123；预览路径正确落到 `/vol7/.../电影/片名 (年份)/` 与 `/vol2/.../电视剧/剧名 (年份)/Season 01/`。
- 应用图标在真机上曾显示为空白，原因是 fnOS 桌面按 `icon_{16,24,32,48,64,72,96,128,256}.png`（下划线）取图，而包内是 `icon-64.png`（连字符）。已补齐全部尺寸并改为 DVD 光驱样式。
- 类型标签来自 `item_tag`（飞牛自有数字 ID），已映射为动作 / 冒险 / 科幻 / 喜剧 / 犯罪 / 纪录片 / 剧情 / 家庭 / 奇幻 / 战争 / 恐怖 / 音乐 / 悬疑 / 爱情 / 动画 / 惊悚 / 历史 / 西部；未知 ID 不写入，避免 NFO 出现无意义数字。
- 演员来自 `item_person` + `person`，角色（role）已写入 `<actor><role>`；飞牛内部演员头像路径不会被写进 NFO（Emby 无法直接使用）。

## 安全设计

- 数据库：`sqlite3.connect("file:...?mode=ro", uri=True)`，全程只读。
- 视频文件：从不改名、不移动、不删除。
- NFO：默认**不覆盖**已存在文件；勾选“覆盖已存在 NFO”后，还可用“仅覆盖带本工具标记的 NFO”进一步限制范围。
- 图片：默认只补缺失，不覆盖已有海报。
- 写入采用临时文件 + 原子替换，避免半截文件。
- 可在「设置」中设置访问令牌，启用后请求需带 `X-Token`。

## 数据库结构适配

核心读取飞牛影视的表：

- `item`：`type`（`Movie` / `TV` / `Season` / `Episode`）、`guid`、`parent_guid`、`season_number`、`episode_number`，以及标题、简介、评分、TMDB/IMDb ID、海报字段等。
- `item_media`：`item_guid` → `path`（视频绝对路径）。
- `person` / `item_person`：演员（若版本中存在）。

不同飞牛影视版本的列名可能有差异，代码通过列名候选与 `PRAGMA table_info` 做兼容；若某版本字段变化导致元数据缺失，可在 NAS 上执行只读诊断并把输出反馈：

```bash
python3 tools/inspect_db.py
```

## 从源码构建

需要 Python 3 + Pillow（仅生成图标时需要）：

```bash
python3 tools/selftest.py                 # 离线自检：造一个假库验证 NFO 输出
python3 tools/make_icons.py               # 由 assets/app_icon_source.png 生成全部尺寸图标
python3 tools/build_fpk.py 1.0.3 --platform x86,arm
```

产物在 `dist/`，每个包附带 `.sha256`。

也可使用官方工具：

```bash
cd src/metabridge && fnpack build
```

## 已知限制

- 仅导出**飞牛影视数据库里已经刮削好的信息**；未刮削或未匹配的条目没有元数据可写。
- 演员信息依赖数据库中 `person` / `item_person` 表；部分版本或内容类型可能没有演员数据。
- 图片来自 `@appmeta/trim.media/img` 的本地缓存；若飞牛影视未下载海报，则只写 NFO 中的远程图片地址（需在设置里开启远程 art）。
- 访问令牌是轻量级 LAN 防护，请勿把端口直接暴露到公网。

---

说明：本工具与飞牛 fnOS、Emby、Jellyfin、Kodi 官方均无隶属关系。
