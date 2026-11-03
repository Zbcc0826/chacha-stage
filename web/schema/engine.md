# 大肥鱼引擎：给 dsh 改代码用的格式

本机页面是 `http://127.0.0.1:8787/`。口播由用户自己的 DeepSeek Harness 生成。用户在「连接 dsh」里填写自己的密钥，引擎把它写进 dsh 主目录的 `.env`，接口不把密钥返回来。也可以用环境变量 `DEEPSEEK_API_KEY`。不要把密钥写进 `dsh.local`、口播 JSON 或这份文档。

模型是大肥鱼。原画神宫凉子，建模霓虹猫_NeonNeko。模型文件在本地 `models/deepseek-chan/`，不随引擎再分发。往 `Animation` 放新的 `.motion3.json`，或往 `Exp3` 放新的 `.exp3.json`，重启页面后就会出现在按钮和口播名单里。待机文件要能被认成 `Idle`。

## 样式文件

| 文件 | 管哪一块 |
| --- | --- |
| `web/css/base.css` | 页面骨架、顶栏、舞台和字幕 |
| `web/css/dock.css` | 右侧控件、参数表 |
| `web/css/hud.css` | 血条和蓝条 |
| `web/css/danmaku.css` | 弹幕气泡 |
| `web/css/engine.css` | 直播背景、桌宠、自由操控、署名 |
| `web/style.css` | 按上面的顺序引入 |

运行中的自定义样式不要改这些文件，写进自由操控的 CSS 框，或口播 JSON 的 `stage.css`。它会进页面上的 `#engine-user-css`。不要写 `</style>`、`@import` 或脚本。

## 口播 JSON

机器可读的约束在 [`stage.schema.json`](stage.schema.json)。每一句可以带：

- `mood`：引擎参数表情，`neutral`、`happy`、`shy`、`angry`、`sad`、`surprise`、`star`、`think`
- `motion`：模型自带动作。当前包括 `Idle`、`举手切默认手`、`默认手切举手`、`狗尾草切换出来`、`狗尾草切回默认手`、`竖手指切出来动画`、`竖手指切回默认`、`脱捕`
- `expression`：模型自带表情，名字就是 exp3 文件名，例如 `脸红`、`爱心眼`
- `cues[].act`：引擎短动作，`nod`、`shake`、`tilt`、`look_left`、`look_right`、`look_up`、`look_down`、`wink`、`bounce`、`lean`
- `vibe`：跨句身体动态，`still`、`relax`、`lively`、`shy`、`fidget`、`sleepy`、`listen`

实时名单以 `GET /api/stage` 为准，不要把这份说明当成永远完整的列表。

## 自由操控

直播间和桌宠里是同一个开关。关掉时，`stage` 会被丢掉。打开时可以改：

- `mode`：`live` 直播间，`pet` 桌宠
- `modelId`：只能填模型库里勾了「允许高自由度使用」的 id。没勾的模型不会被换上。人在页面上仍然可以自己切换任何已保存的模型。
- `model.x` / `model.y` / `model.scale`：角色位置和缩放
- `background`：底色、背景图透明度和尺寸。图片本身只能由人在页面导入
- `widgets.hud`、`widgets.danmaku`：显示、位置、缩放、遮挡层 `z`。血条样式还有 `style`，弹幕还有 `count`
- `toggles`：`hud`、`danmaku`、`physics`、`breath`、`sway`、`blink`、`mouse`、`live`、`captions`
- `css`：本页样式

页面上的开关表在 `web/engine.js` 的 `FREEDOM_CONTROLS`。服务端同一份名字在 `engine_model.py` 的 `TOGGLE_IDS`。要加一个新开关，两处一起加，并在 `stage.schema.json` 的 `toggles` 里声明。

血条数值来自已连接 dsh 的 DeepSeek 余额，100 元为满血。蓝条是这条会话的上下文余量。接口只告诉页面比例，不回密钥。

## 页面会保存的本地键

- `engine-ui`：模式、背景、遮挡层、自由操控、CSS
- `chacha-hud`：血条位置和样式
- `chacha-danmaku`：弹幕位置、条数和缩放
- `chacha-voice`：音色

软件作者：哔哩哔哩 [早坂茶茶Unofficial](https://space.bilibili.com/3382868)。模型作者：[神宫凉子](https://space.bilibili.com/13737731)。
