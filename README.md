# G9TV

运行在 ComfyUI 里的本地创作工作台，包含素材画布、模型生成入口、声音工作台、视频音轨提取和独立多轨剪辑窗口。

## 安装

1. 安装可运行的 ComfyUI。将本仓库下载到 `ComfyUI/custom_nodes/comfy-simple-studio`，目录内应直接包含 `__init__.py`。
2. 用 ComfyUI 自己的 Python 安装 `requirements.txt`。示例：`python -m pip install -r requirements.txt`。不要使用另一个不属于 ComfyUI 的 Python 环境。
3. 重启 ComfyUI，打开侧栏 G9 TV。独立剪辑窗口需要 Windows 和 Microsoft Edge。

也可以下载仓库 `dist/G9TV-plugin.zip`，解压到上述目录。这是程序包，**不包含 ComfyUI、模型权重、CUDA、第三方节点或用户项目**。

## 模型与功能

- 生图、H3 视频、SeedVR2、Breeze TTS 和 Stable Audio 使用现有模型及对应的第三方 ComfyUI 节点；需要单独安装。界面存在不代表模型已经安装或显卡一定能运行。
- 声音、视频和图片可进入独立多轨剪辑台；支持裁剪、分割、混音、字幕、基础画面调整和 MP4/WAV 导出。
- 提取音频保留原视频中的混合音轨，不会自动分离人声、音乐和音效。
- MP4 导出采用实时预览录制再编码，导出时保持窗口可见；复杂多轨、大视频和 4K 会占用较多资源。
- Stable Audio 的音效质量仍需逐条试听。本版本不保证所有提示词都得到合适的声音。
- 本地 H3 提示词优化依赖 Ollama 与 Qwen 模型，是可选功能。

## 联网更新

G9TV 顶部“检查更新”按需访问本仓库的版本清单；发现新版本后，点击“下载并安装”。生成队列有任务时拒绝安装。安装时验证 SHA-256，先备份程序文件，失败时恢复；成功后需重启 ComfyUI。

也可以双击插件目录的 `Update-G9TV.cmd`。请先保存项目并停止生成。命令行回退：使用 ComfyUI 的 Python 执行 `g9_update.py --rollback`，然后重启。

更新不会上传个人素材，不会更新模型权重、第三方节点、ComfyUI 或显卡驱动。网络不通时仍可使用已安装功能。没有定时后台下载或强制自动安装。

## 数据目录

已有 `D:/ComfyUI/StudioProjects` 时继续使用它；否则保存在 ComfyUI 用户目录的 `G9TV/projects`。可通过 `G9TV_PROJECTS` 指定目录。更新前请自行保留重要项目备份。

可选路径：`G9TV_MOSS_PATH` 指定 MOSS 模型目录；`G9TV_OLLAMA_EXE` 指定 Ollama 程序；`G9TV_OLLAMA_MODELS` 指定其模型目录。

## 发布新版本

修改代码和 `version.json` 的版本号，运行 `python tools/build_release.py`，将生成的 `dist/G9TV-plugin.zip` 和 `update-manifest.json` 与源码一起提交到 main。其他设备随后点击检查更新即可获得这一版本。

只发布程序文件，不要提交 `.env`、密钥、模型、input/output、录音、角色库或项目。仓库公开不改变第三方模型与依赖各自的许可证。
