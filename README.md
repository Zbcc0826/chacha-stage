# chacha-stage

给 dsh 配套的虚拟主播 / 桌宠扩展包。工具层能力对接进 dsh 体系，舞台页面、口播合成、桌宠窗口这些展示层由本仓库的 Python 服务提供。

作者：[早坂茶茶Unofficial](https://space.bilibili.com/3382868)，致敬开源社区。

> 
> 碎碎念：内置测试用的大肥鱼模型，尾巴色块有点溢出，要修得写不少额外逻辑，先把资源放在基础功能上了。能work就不是bug，源码的注释应该是比较完善，有需求或者bug让dsh或者其他agent改就行。

## 包介绍

这是一个 dsh bundle 组合包，不是那种用 `dsh --profile <name>` 直接启动的独立配置文件。它的作用很简单：给你的 dsh 自动插入一层插件，不用手动改配置。

具体来说，它会在 `@deepseek-ai/dsh-base` 之后，自动追加一层插件配置：

```
- id: chacha-stage
  name: dsh-chacha-stage
```

插件本身是纯 JS 写的，没有构建步骤，从 git 直接安装就能用，也不用额外开 `allowBuilds`。

### 安装

直接用 dsh 插件命令安装：

```
dsh plugin --profile headless add github:Zbcc0826/chacha-stage
```

装完可以让dsh先看一眼配置层，确认装上了：

```
dsh --profile headless --dump-config
```

输出里能看到 `# == dsh-chacha-stage` 这一层就对了。

### 卸载

```
dsh plugin --profile headless remove dsh-chacha-stage
```

依赖和配置层会一起删掉。

如果想自己改配置，在你自己的 `cordis.patch.yml` 里，按 `id: chacha-stage` 就能覆盖这一层的全部配置，优先级比本包自带的高。

插件的显示名和多语言文案在 `locale/` 目录下，更详细的使用说明看 `docs/DETAIL.zh.md`。

## 模型准备

内置测试用的角色是「大肥鱼」。如果要使用自己的live2D模型，可能需要让你的agent调试一些兼容性内容。

- 原画：[神宫凉子](https://space.bilibili.com/13737731)
- 建模：[霓虹猫_NeonNeko](https://space.bilibili.com/3546725618813315)

⚠️ 出于尊重原作者的使用协议，仓库里没有打包模型文件。请自行去原作者的发布页下载压缩包，解压到 `models/deepseek-chan/` 目录下，**保留原文件名不要改**。

引擎会自动读取目录下的 `.motion3.json` 和 `.exp3.json`，把对应的动作、表情注册进口播的可选列表里。

项目里自带的 `vendor/live2dcubismcore.min.js` 是 Live2D 官方的可再分发核心库，使用协议见 [Live2D Proprietary Software License](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)。

## 密钥配置

所有密钥都只存在你本地，不会上传、不会写进仓库，请放心填写。

1. **DeepSeek API 密钥**
口播生成、血条余额显示都靠它。在直播间页面的「连接 dsh」里填上就行，引擎会写到 dsh 主目录的 `.env` 里；你也可以自己手动设置环境变量 `DEEPSEEK_API_KEY`。
血条功能需要单独填一次密钥，用来查询 API 余额；插件的 `stage_status` 只返回连接状态，不会返回密钥内容。
2. **B站直播身份码**
用来接弹幕。在页面「连接」里填写身份码，存在本地 `bili.local` 的 `key` 字段里，页面和日志都不会回显。连接弹幕时才会放进请求的 `IDCode` 参数里。
留空保存会清空本地保存的身份码。

## 启动运行

舞台服务是本地 Python 写的，先装依赖再启动：

```
pip install -r requirements.txt
python server.py
```

启动后访问：`http://127.0.0.1:8787/`
舞台样式、字段的配置说明在：`http://127.0.0.1:8787/schema/engine.md`

### 几个细节

- 引擎式封装l2D模型动作：长动作和短动作封装成单词，短参数形式和口播同步生成播放。
- 口播逐句流式处理：一句合成完就立刻推给页面播放，不用等所有句子都生成完，体感更流畅。
- 打开「自由操控」后，口播请求的 JSON 里可以额外带 `stage` 参数，用来调整角色位置、缩放、显隐、控件位置、遮挡层，甚至直接写一段自定义 CSS。关掉自由操控后这些指令会失效，默认样式不受影响。
- 大部分样式和展示效果都可以交给 dsh 或者其他助手来控制，不用手工硬改代码。

## 其他

项目做了dsh编辑友好方向的设计，想换模型、改样式、加功能跟你的大肥鱼说，让它直接翻源码就行，绝大部分内容都有自定义的空间。
