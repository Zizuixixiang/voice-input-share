# 语音输入小工具（Voice Input）

按住快捷键说话，松开自动识别成文字并粘贴到当前光标处。Windows 桌面工具，带一个可拖动的悬浮按钮和托盘图标。

- **按住** `Ctrl+Alt+V` 录音，松开即识别粘贴
- **按一下** `Ctrl+Shift+R` 开始录音，再按一下停止（适合长句）
- 也可以**点屏幕上的悬浮圆按钮**开始/停止

支持两种语音识别服务，在 `config.json` 里切换：

| provider | 服务 | 费用 | 说明 |
|----------|------|------|------|
| `siliconflow`（默认） | 硅基流动 SenseVoice | **免费** | 推荐，注册送额度，日常够用 |
| `volcengine` | 火山引擎 / 豆包 Seed-ASR | 按量付费 | 识别质量高，需要开通 |

---

## 一、安装

需要先装好 **Python 3.9 或更高版本**（安装时记得勾选 "Add Python to PATH"）。

```bat
:: 下载/解压本项目后，在项目文件夹里打开 PowerShell 或 CMD，执行：
pip install -r requirements.txt
```

## 二、申请 Key 并填写配置

1. 把 `config.example.json` **复制一份**，改名为 `config.json`。
2. 按下面方法拿到 Key，填进 `config.json` 对应的 `api_key` 里。

### 方案 A：硅基流动（免费，默认推荐）

1. 打开 https://cloud.siliconflow.cn/ ，注册账号（新用户有免费额度）。
2. 登录后，左侧菜单进入 **「API 密钥」**（账户设置里），点击 **新建 API 密钥**。
3. 复制以 `sk-` 开头的字符串。
4. 填到 `config.json` 里：

```json
{
    "provider": "siliconflow",
    "siliconflow": {
        "api_key": "sk-你复制的密钥",
        ...
    }
}
```

> 模型默认用免费的 `FunAudioLLM/SenseVoiceSmall`，不用改。

### 方案 B：火山引擎 / 豆包（按量付费，可选）

1. 打开 https://console.volcengine.com/ ，注册并完成实名认证。
2. 搜索/进入 **「语音技术」**，开通 **「录音文件识别（大模型）/ Seed-ASR」** 服务。
3. 在 **API Key 管理** 里创建一个 API Key（一串 UUID 格式）。
4. 填到 `config.json`，并把 `provider` 改成 `volcengine`：

```json
{
    "provider": "volcengine",
    "volcengine": {
        "api_key": "你的火山 API Key",
        ...
    }
}
```

## 三、运行

双击 **`start.bat`** 即可（推荐，后台静默运行）。

或者在命令行里：

```bat
python voice_input.py
```

启动后屏幕下方会出现一个红色话筒悬浮按钮，托盘也有图标。退出：右键托盘图标 → Quit。

---

## 常见问题

- **没反应 / 识别为空**：检查 `config.json` 里的 `api_key` 是否填对、网络是否能访问对应服务。
- **快捷键冲突**：改 `config.json` 里的 `hotkey` / `toggle_hotkey`，例如 `"ctrl+alt+z"`。
- **粘贴后不想自动粘贴**：把 `paste_after` 改成 `false`，识别结果只会复制到剪贴板。
- **公司网络要走代理**：把 `use_system_proxy` 改成 `true`。
- **杀毒/系统拦截快捷键监听**：`keyboard` 库需要管理员权限才能全局监听，必要时用管理员身份运行 `start.bat`。

## 自己打包成 exe（可选，发给没装 Python 的人）

```bat
pip install pyinstaller
pyinstaller --noconsole --onefile --add-data "config.json;." voice_input.py
```

生成的 exe 在 `dist\` 目录下。注意：exe 体积较大（几百 MB），且部分杀毒软件可能误报。

---

## 安全提示

`config.json` 里有你的 API Key，**不要发给别人、不要传到 GitHub**（本项目已用 `.gitignore` 自动忽略它）。每个人用自己的 Key。
