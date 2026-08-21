# PaperFlow

PaperFlow 是一个无数据库的论文发现工作流：每天从 Hugging Face Daily/Trending 与 arXiv 获取论文，确定性排序后写入 Obsidian，并可由 GitHub Actions 通过 Gmail SMTP 发送日报。Zotero 负责收藏、PDF、批注和阅读，Codex Skill 负责把自然语言请求映射为安全的 CLI 命令。

## 用途与非目标

适合个人在 Windows 上完成“发现 → 筛选 → Obsidian 报告/笔记 → Zotero 阅读”的轻量流程。它不提供数据库、Web UI、向量检索、云端持久化或模型 API，也不会自动修改 Zotero 数据库。Zotero AI Sidebar 是独立的可选项目，不随 PaperFlow 分发。

## Windows 前置条件

- Windows PowerShell 5.1 或更新版本
- Git
- Python 3.11 或更新版本
- Codex CLI/桌面端（安装器只检查并给提示，不自动安装）
- Obsidian 与一个已存在的 Vault
- Zotero；AI Sidebar 可稍后单独安装并人工确认

## 克隆与安装

在 PowerShell 中克隆你自己的仓库地址并进入目录：

```powershell
git clone https://github.com/YOUR_ACCOUNT/paperflow.git
Set-Location .\paperflow
```

先运行只读检查；它不会安装或写入任何内容：

```powershell
.\scripts\install-windows.ps1 -CheckOnly -VaultPath "D:\ObsidianVault"
```

前置条件已满足时正式安装。`VaultPath` 必须是已存在目录；脚本会先展示检查表，修改用户 PATH 前还会再次询问：

```powershell
.\scripts\install-windows.ps1 -VaultPath "D:\ObsidianVault"
```

若希望安装器通过 winget 补齐 Git、Python、Zotero 或 Obsidian，显式添加 `-InstallMissing`。每项安装仍受 PowerShell `ShouldProcess` 控制；不加此参数就绝不安装软件。

```powershell
.\scripts\install-windows.ps1 -InstallMissing -VaultPath "D:\ObsidianVault"
```

若当前策略禁止脚本，可仅对本次进程放宽：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1 -CheckOnly
```

## 本地配置

正式安装且 `VaultPath` 有效时会写入 `%APPDATA%\PaperFlow\config.toml`。也可参考以下内容手动编辑：

```toml
vault_path = "D:\\ObsidianVault"
top_n = 10
timezone = "Asia/Hong_Kong"
history_reports = 30
arxiv_categories = ["cs.RO", "cs.CV", "cs.AI", "cs.LG"]

[keywords]
robotics = 5
"3d reconstruction" = 8
```

## 命令

所有自动化和 Codex 调用都推荐使用 JSON 输出：

```powershell
paperflow --json daily
paperflow --json search "robotics"
paperflow --json note 2401.01234
paperflow --json doctor
```

`daily` 默认原子写入或更新 `PaperFlow\Reports\YYYY-MM-DD.md`；`search` 同时查本地历史和 arXiv；`note` 写入单篇笔记，已有文件时不会覆盖；`doctor` 只读检查环境。云端邮件使用：

```text
paperflow --json daily --email --no-write
```

它不写本地 Vault。退出码为 2 表示配置问题，3 表示全部来源失败，5 表示邮件发送失败。

## Codex Skill

仓库内 Skill 位于 `.agents\skills\paperflow`，安装器复制到当前用户的 `%USERPROFILE%\.agents\skills\paperflow`。Codex 遇到“今日论文、论文搜索、保存论文笔记、PaperFlow 诊断”等请求时，会优先使用结构化 JSON 命令；保存笔记或覆盖已有笔记前会遵守 Skill 中的确认边界。重新启动 Codex 后可让它执行“运行 PaperFlow doctor”。

## Zotero 协作流程

1. 在日报或搜索结果中打开论文链接。
2. 用 Zotero Connector 手工保存论文、元数据与 PDF 到目标 collection。
3. 在 Zotero 中阅读、标注；PaperFlow 永不写 `zotero.sqlite`。
4. 如需问答或摘要，单独安装 huangkiki/zotero-ai-sidebar（AI Sidebar），并在该扩展中自行配置服务。PaperFlow 不读取 Sidebar 密钥，也不自动配置 WebDAV。

## GitHub Actions 云端邮件

仓库自带 `.github/workflows/daily.yml`，定时任务无状态运行。到 GitHub 仓库的 Settings → Secrets and variables → Actions 新建三个 Secrets：

- `PAPERFLOW_GMAIL_ADDRESS`：发件 Gmail 地址。
- `PAPERFLOW_GMAIL_APP_PASSWORD`：Google 账户生成的 App Password，不是登录密码。
- `PAPERFLOW_PRIVATE_CONFIG_JSON`：紧凑 JSON，包含关键词、收件人和公开偏好，不含本地 Vault 路径。

`PAPERFLOW_PRIVATE_CONFIG_JSON` 的有效紧凑示例（请替换收件地址）：

<!-- cloud-config-example -->
```json
{"keywords":{"robotics":5,"3d reconstruction":8},"arxiv_categories":["cs.RO","cs.CV","cs.AI","cs.LG"],"timezone":"Asia/Hong_Kong","top_n":10,"history_reports":30,"mail_to":"you@example.com"}
```

首次配置后，在 Actions → Daily PaperFlow email → Run workflow 触发 `workflow_dispatch`，检查运行日志和收件箱。不要把真实 Secret 写入仓库、Issue 或日志。

## 隐私边界

- 本地配置只保存在 `%APPDATA%\PaperFlow\config.toml`，Obsidian 内容只保存在你的 Vault。
- 云端任务只读取 GitHub Secrets，不上传报告 Artifact，也不使用数据库或 SQLite。
- 安装器不读取或采集 Gmail Secrets，不接触 `zotero.sqlite`，不读取 AI Sidebar 密钥。
- 论文查询会访问 Hugging Face 与 arXiv；邮件模式会连接 Gmail SMTP。除此之外没有模型 API。

## 升级与卸载

升级：在仓库内执行 `git pull --ff-only`，再运行 `& .\.venv\Scripts\python.exe -m pip install .`；如 Skill 有更新，重新运行安装脚本即可幂等复制。

卸载时关闭相关终端，然后删除以下项目：仓库内 `.venv`、`%LOCALAPPDATA%\PaperFlow`、`%APPDATA%\PaperFlow`、`%USERPROFILE%\.agents\skills\paperflow`。若安装时加入了 PATH，再从“用户环境变量 Path”中删除 `%LOCALAPPDATA%\PaperFlow\bin`。脚本不会自动删除 Obsidian 报告、Zotero 条目或用户数据。

## 故障排查

- **arXiv 429**：表示请求过多；稍后重试，避免短时间重复运行。单一来源失败时日报可能以 `partial=true` 成功。
- **Gmail App Password**：需先启用两步验证并按 Google 当前账户政策创建 App Password；普通 Gmail 密码不能替代。退出码 5 通常表示 SMTP 凭据、网络或账户策略问题。
- **PowerShell policy**：遇到“running scripts is disabled”时，用上文的 `powershell -NoProfile -ExecutionPolicy Bypass -File ...` 仅对该次命令放宽，不必永久降低系统策略。
- **命令找不到**：安装后打开新终端，或直接运行 `%LOCALAPPDATA%\PaperFlow\bin\paperflow.cmd --json doctor`。

## 费用说明

PaperFlow 软件本身免费开源，采用 Apache License 2.0。Zotero、Obsidian 的基础软件可免费使用，AI Sidebar 是另行安装的开源项目。GitHub Actions 的可用免费额度、Gmail/邮件服务限制及第三方服务政策取决于账户与平台当前规则，因此这里不作“绝对永久免费”承诺。
