# PaperFlow

PaperFlow 是一个免费、私有优先、本地优先且无数据库的论文发现工作流：每天从 Hugging Face Daily/Trending 与 arXiv 获取论文，确定性排序后写入 Obsidian，并可由 GitHub Actions 通过 Gmail SMTP 发送日报。Zotero 负责收藏、PDF、批注和阅读，Codex Skill 负责把自然语言请求映射为安全的 CLI 命令。

## 用途与非目标

适合个人在 Windows 上完成“发现 → 筛选 → Obsidian 报告/笔记 → Zotero 阅读”的轻量流程。它不使用 SQLite 或其他数据库，不提供 Web UI、向量检索或云端持久化，也不会自动修改 Zotero 数据库。PaperFlow 的元数据和报告文件都保留在本地；核心流程不要求购买服务或配置模型。Zotero AI Sidebar 是独立的可选项目，不随 PaperFlow 分发。

## Windows 前置条件

- Windows PowerShell 5.1 或更新版本
- Git
- Python 3.11 或更新版本
- Codex CLI/桌面端（安装器只检查并给提示，不自动安装）
- Obsidian 与一个已存在的 Vault
- Zotero；AI Sidebar 可稍后单独安装并人工确认

## 克隆与安装

在 PowerShell 中克隆你自己的仓库地址并进入所选项目目录。以下以 `D:\PaperFlow` 为例：

```powershell
git clone https://github.com/YOUR_ACCOUNT/paperflow.git "D:\PaperFlow"
Set-Location "D:\PaperFlow"
```

先运行只读检查；它不会安装或写入任何内容。这里使用一个已经存在于 C 盘的 Obsidian Vault：

```powershell
.\scripts\install-windows.ps1 -CheckOnly -DataRoot "D:\PaperFlowData" -VaultPath "C:\Users\you\Documents\Obsidian\ResearchVault"
```

前置条件已满足时正式安装。`VaultPath` 必须是已存在目录；也可以提供另一个已存在的 Vault。这个 Vault 是用户内容，不是 PaperFlow 缓存，安装器不会作为缓存移动、复制或重新安置它。脚本会先展示检查表，修改用户 PATH 前还会再次询问：

```powershell
.\scripts\install-windows.ps1 -DataRoot "D:\PaperFlowData" -VaultPath "C:\Users\you\Documents\Obsidian\ResearchVault"
```

安装后的职责边界如下：

- 源码仍在所选项目目录 `D:\PaperFlow`，虚拟环境仍在 `D:\PaperFlow\.venv`；`-DataRoot` 不移动源码或虚拟环境。
- 命令 wrapper 位于 `D:\PaperFlowData\bin\paperflow.cmd`，配置位于 `D:\PaperFlowData\config\config.toml`，缓存与临时目录分别为 `D:\PaperFlowData\cache` 和 `D:\PaperFlowData\tmp`。
- 小型 Codex Skill 仍安装到 `%USERPROFILE%\.agents\skills\paperflow`，不放进数据根目录。
- wrapper 为每次 PaperFlow 命令设置 `PAPERFLOW_HOME`、缓存目录和临时目录。`PAPERFLOW_HOME` 是 PaperFlow 专用环境变量，不会全局迁移其他程序的数据。

安装器先按 `requirements.lock` 安装精确锁定的运行时与 setuptools 构建依赖，再以 `--no-deps --no-build-isolation` 安装 PaperFlow 本身，从而固定构建环境。使用 `-DataRoot` 时，这两个 pip 子进程临时使用 `PIP_NO_CACHE_DIR=1`，并把 `TEMP`、`TMP` 指向 `D:\PaperFlowData\tmp`；无论成功或失败，随后都会恢复安装进程原有的 TEMP、TMP 和 PIP_NO_CACHE_DIR。这里提供的是精确版本级可复现；为保持轻量，锁文件不包含制品哈希，不声称 hermetic 或 artifact-level 防篡改。安装末尾会直接运行只读 `paperflow --json doctor`；如果出现 warning，请按其 JSON 输出处理 required 检查。

从旧版默认位置迁移到 `-DataRoot` 时，安装器会逐字节复制旧 `config.toml`，创建新 wrapper，再运行 doctor；只有 doctor 成功、用户同意 PATH 迁移且 PATH 写入后读回核对完全一致，才会只清理旧位置中精确匹配的 wrapper 和 config.toml，即 `%LOCALAPPDATA%\PaperFlow\bin\paperflow.cmd` 与 `%APPDATA%\PaperFlow\config.toml`。拒绝 PATH 迁移、doctor 或 PATH 核对失败时，会保留旧位置的已知文件；未知相邻文件始终保留。安装器不会递归清空旧目录。

若希望安装器通过 winget 补齐 Git、Python、Zotero 或 Obsidian，显式添加 `-InstallMissing`。每项安装仍受 PowerShell `ShouldProcess` 控制；不加此参数就绝不安装软件。

```powershell
.\scripts\install-windows.ps1 -InstallMissing -DataRoot "D:\PaperFlowData" -VaultPath "C:\Users\you\Documents\Obsidian\ResearchVault"
```

若当前策略禁止脚本，可仅对本次进程放宽：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1 -CheckOnly -DataRoot "D:\PaperFlowData"
```

## 本地配置

使用上述 `-DataRoot` 正式安装且 `VaultPath` 有效时，会写入 `D:\PaperFlowData\config\config.toml`。也可参考以下内容手动编辑：

已有 config.toml 会逐字节保留，重新安装不会覆盖 keywords、timezone 或其他用户修改。

```toml
vault_path = "C:\\Users\\you\\Documents\\Obsidian\\ResearchVault"
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
paperflow --json daily --date 2026-08-20
paperflow --json search "robotics"
paperflow --json note 2401.01234
paperflow --json doctor
```

也可直接使用默认的人类可读输出：

```powershell
paperflow doctor
paperflow search "3d reconstruction"
paperflow daily
```

`daily` 默认原子写入或更新 `<Vault>\PaperFlow\Reports\YYYY-MM-DD.md`；单篇笔记出现在 `<Vault>\PaperFlow\Papers\<arxiv_id>.md`。`search` 同时查本地历史和 arXiv；`note` 写入单篇笔记，已有文件时不会覆盖；`doctor` 只读检查环境。云端邮件使用：

`daily --date YYYY-MM-DD` 对 Hugging Face Daily、Hugging Face Trending 和 arXiv 三个来源都按论文发布日期筛选。旧日期可能为空，也可能受上游源的保留范围限制；命令不会为每篇论文额外发起请求。

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

- 使用 `-DataRoot` 时，本地配置、cache、tmp 与 wrapper 只保存在所选数据根目录；Obsidian 内容只保存在你指定的现有 Vault。
- 云端任务只读取 GitHub Secrets，不上传报告 Artifact，也不使用数据库或 SQLite。
- 安装器不读取或采集 Gmail Secrets，不接触 `zotero.sqlite`，不读取 AI Sidebar 密钥。
- 本地发现流程的网络访问限于 Hugging Face、arXiv 等论文提供方；只有用户另行配置相关功能时才会访问模型端点。邮件模式会额外连接 Gmail SMTP。PaperFlow 本身不要求付费模型或付费数据库。

## 升级与卸载

升级或重新安装：在 `D:\PaperFlow` 内执行 `git pull --ff-only`，然后复用同一数据根目录和 Vault 运行以下命令；它会按 `requirements.lock` 更新环境、保留现有配置并幂等更新 wrapper 与 Skill。开发与测试环境使用包含运行时锁的 `requirements-dev.lock`。

```powershell
.\scripts\install-windows.ps1 -DataRoot "D:\PaperFlowData" -VaultPath "C:\Users\you\Documents\Obsidian\ResearchVault"
```

卸载时先关闭相关终端，从“用户环境变量 Path”中精确删除 `D:\PaperFlowData\bin`，再逐项删除 `D:\PaperFlow\.venv`、`D:\PaperFlowData\bin\paperflow.cmd`、`D:\PaperFlowData\config\config.toml`、`D:\PaperFlowData\cache`、`D:\PaperFlowData\tmp` 和 `%USERPROFILE%\.agents\skills\paperflow`。确认 `D:\PaperFlowData\bin`、`config` 及数据根目录为空后，才可删除这些空目录。不要递归删除 Vault，也不要递归删除未知的旧版内容；Obsidian 报告、Zotero 条目、未知相邻文件和其他用户数据不属于卸载目标。

## 故障排查

- **arXiv 429**：表示请求过多；稍后重试，避免短时间重复运行。单一来源失败时日报可能以 `partial=true` 成功。
- **Gmail App Password**：需先启用两步验证并按 Google 当前账户政策创建 App Password；普通 Gmail 密码不能替代。退出码 5 通常表示 SMTP 凭据、网络或账户策略问题。
- **PowerShell policy**：遇到“running scripts is disabled”时，用上文的 `powershell -NoProfile -ExecutionPolicy Bypass -File ...` 仅对该次命令放宽，不必永久降低系统策略。
- **命令找不到**：安装后打开新终端，或直接运行 `D:\PaperFlowData\bin\paperflow.cmd --json doctor`。

## 费用说明

PaperFlow 软件本身免费开源，采用 Apache License 2.0。Zotero、Obsidian 的基础软件可免费使用，AI Sidebar 是另行安装的开源项目。GitHub Actions 的可用免费额度、Gmail/邮件服务限制及第三方服务政策取决于账户与平台当前规则，因此这里不作“绝对永久免费”承诺。
