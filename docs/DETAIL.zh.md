# 虚拟主播与桌宠

这个包是一个 dsh 组合包。它向已经在本机跑着的舞台注册两个工具：`stage_say` 和 `stage_status`。

## 要先开着的服务

工具要打到本机舞台。先在这个仓库里启动：

```text
pip install -r requirements.txt
python server.py
```

打开 `http://127.0.0.1:8787/`。组合包不会替你启动这个进程，也不附带角色模型。直播身份码在页面的「连接」里按密钥填写，只留在本机，不进这个包。

## 工具

`stage_say` 念一句。下一句再调一次。前面那句的声音不会等后面的调用。

舞台自己的口播也是一句合成完就交给页面开播，不等整段都合成完。

`stage_status` 只报告当前模型和 dsh 是否已连接。不返回密钥。

## 安装

在要看到这些工具的 profile 里：

```sh
dsh plugin --profile headless add github:Zbcc0826/chacha-stage
```

入口是普通 JavaScript，从 git 安装不需要构建脚本，也不需要 `allowBuilds`。`package.json` 的 `dsh.bundle` 指向 `cordis.patch.yml`。这一层排在 `@deepseek-ai/dsh-base` 之后。profile 自己的 patch 仍然可以按 `id: chacha-stage` 覆盖这一行。
