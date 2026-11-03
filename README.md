# chacha-stage

dsh 组合包，外加一台本机虚拟主播 / 桌宠。组合包负责把工具交给 dsh；舞台页面、口播合成和桌宠窗口由本仓库的 Python 服务提供。

软件作者：[早坂茶茶Unofficial](https://space.bilibili.com/3382868)。向开源社区致敬。

另外，服务示例用的大肥鱼模型尾巴这个色块溢出可能需要更复杂的代码来处理，先把资源投到更重要的基础功能上了。

## 这是什么包

`package.json` 声明 `dsh.bundle`，指向 `cordis.patch.yml`。这是组合包，不是 profile。profile 是用户用 `dsh --profile <name>` 启动的那一套；本包只贡献一层插件。

这一层在 `@deepseek-ai/dsh-base` 之后插入一行：

```yaml
- id: chacha-stage
  name: dsh-chacha-stage
```

`name` 是包名，不是本机路径。安装之后由 Node 解析到 `plugin/index.js`。插件导出 `apply`，并 `inject` 工具注册表，再登记 `stage_say` 和 `stage_status`。支持的 dsh 版本写在 `engines.dsh`。

入口是普通 JavaScript，没有构建步骤。从 git 安装不会跑 `prepare`，因此不需要在 profile 里给这个包开 `allowBuilds`。

```sh
dsh plugin --profile headless add github:Zbcc0826/chacha-stage
```

要先看层、先不启动：

```sh
dsh --profile headless --dump-config
```

输出里应有一层 `# == dsh-chacha-stage`。`dsh plugin --profile headless remove dsh-chacha-stage` 会同时去掉依赖和这一层。用户自己的 `cordis.patch.yml` 排在组合包之后，可以按 `id: chacha-stage` 覆盖整行配置。

插件展示名和说明在 `locale/zh.json`、`locale/en.json`。详细介绍是 `docs/DETAIL.zh.md`。

## 模型

内置角色是大肥鱼。原画 [神宫凉子](https://space.bilibili.com/13737731)，建模霓虹猫_NeonNeko。

大肥鱼的模型文件按作者的使用须知，不放进这个仓库。请从作者的原始发布页取得压缩包，解压到 `models/deepseek-chan/`，并保留原来的文件名。引擎会读取目录里的 `.motion3.json` 和 `.exp3.json`，把它们登记成口播可以选的动作和表情。

Live2D Cubism Core 随运行时放在 `vendor/live2dcubismcore.min.js`，属于 Live2D 的可再分发代码，协议见 [Live2D Proprietary Software License](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)。

## 密钥

口播和血条使用你自己的 DeepSeek 密钥。在直播间「连接 dsh」里填写，引擎会把它写进 dsh 主目录的 `.env`。也可以自己设置环境变量 `DEEPSEEK_API_KEY`。不要把密钥写进这个仓库。血条可能需要在服务内填一次密钥，用来跟踪 API 余额。插件的 `stage_status` 只报告有没有连上，不返回密钥。

接弹幕同样按密钥来。在「连接」里填写你自己的直播身份码，引擎把它写进本机 `bili.local` 的 `key`，页面和日志都不回显。连接弹幕时才放进事件流要求的 `IDCode` 参数。留空再保存会清掉本机这份码。不要把身份码写进这个仓库。

## 运行

工具要打到已经开着的舞台。先启动服务：

```text
pip install -r requirements.txt
python server.py
```

打开 `http://127.0.0.1:8787/`。给 dsh 改舞台和样式的格式在 `http://127.0.0.1:8787/schema/engine.md`。

多句口播按 `lines` 的顺序来。一句合成完就交给页面开播，不等后面的句子都合成完。

自由操控打开后，口播 JSON 可以带 `stage`，用来改角色位置、缩放、开关、控件位置、遮挡层和一段本页 CSS。关掉后这些指令会被丢掉。大部分样式和效果都可以让 dsh 或其他助手管理。
