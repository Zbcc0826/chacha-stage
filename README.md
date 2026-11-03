# chacha-stage

可由 DeepSeek Harness 调用的虚拟主播和桌宠引擎。包含一个简易的直播间模式和桌宠模式。

软件作者：[早坂茶茶Unofficial](https://space.bilibili.com/3382868)。向开源社区致敬。

另外，服务示例用的大肥鱼模型尾巴这个色块溢出可能需要更复杂的代码来处理，先把资源投到更重要的基础功能上了。

## 模型

内置角色是大肥鱼。原画 [神宫凉子](https://space.bilibili.com/13737731)，建模霓虹猫_NeonNeko。

大肥鱼的模型文件按作者的使用须知，不放进这个仓库。请从作者的原始发布页取得压缩包，解压到 `models/deepseek-chan/`，并保留原来的文件名。引擎会读取目录里的 `.motion3.json` 和 `.exp3.json`，把它们登记成口播可以选的动作和表情。

Live2D Cubism Core 随运行时放在 `vendor/live2dcubismcore.min.js`，属于 Live2D 的可再分发代码，协议见 [Live2D Proprietary Software License](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)。

## 密钥

口播和血条使用你自己的DeepSeek密钥。在直播间「连接 dsh」里填写，引擎会把它写进 dsh 主目录的 `.env`。也可以自己设置环境变量 `DEEPSEEK_API_KEY`。跟你的dsh说需求，它会做好的。血条可能需要在服务内填一次apikey以跟踪api余额。

## 运行

```text
pip install -r requirements.txt
python server.py
```

打开 `http://127.0.0.1:8787/`。给 dsh 改舞台和样式的格式在 `http://127.0.0.1:8787/schema/engine.md`。

自由操控打开后，口播 JSON 可以带 `stage`，用来改角色位置、缩放、开关、控件位置、遮挡层和一段本页 CSS。关掉后这些指令会被丢掉。大部分样式和效果都可以让dsh或其他ai助手进行管理和自定义。
