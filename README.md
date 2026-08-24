# PaperFlow

PaperFlow 是一个免费、私有优先、本地优先且无数据库的论文发现工作流：每天从 Hugging Face Daily/Trending 与 arXiv 获取论文，确定性排序后写入 Obsidian，并可由 GitHub Actions 通过 Gmail SMTP 发送日报。Zotero 负责收藏、PDF、批注和阅读，Codex Skill 负责把自然语言请求映射为安全的 CLI 命令。

## 用途与非目标

适合个人在 Windows 上完成“发现 → 筛选 → Obsidian 报告/笔记 → Zotero 阅读”的轻量流程。它不使用 SQLite 或其他数据库，不提供 Web UI 或向量检索，也不会自动修改 Zotero 数据库。本地运行与 GitHub Actions 云端运行具有不同的数据边界，详见“隐私边界”。核心流程不要求购买服务或配置模型。Zotero AI Sidebar 是独立的可选项目，不随 PaperFlow 分发。

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

先运行只读检查；它不会安装或写入任何内容。这里使用当前用户目录中已经存在的 Obsidian Vault：

```powershell
.\scripts\install-windows.ps1 -CheckOnly -DataRoot "D:\PaperFlowData" -VaultPath "$env:USERPROFILE\Documents\Obsidian Vault"
```

PowerShell 会把 `$env:USERPROFILE\Documents\Obsidian Vault` 按当前用户分别展开；Vault 位于其他位置的电脑请修改 `-VaultPath`。`VaultPath` 必须是已存在目录，也可以提供另一个已存在的 Vault。这个 Vault 是用户内容，不是 PaperFlow 缓存，安装器不会作为缓存移动、复制或重新安置它。

前置条件已满足时正式安装。脚本会先展示检查表，修改用户 PATH 前还会再次询问：

```powershell
.\scripts\install-windows.ps1 -DataRoot "D:\PaperFlowData" -VaultPath "$env:USERPROFILE\Documents\Obsidian Vault"
```

`-DataRoot` 必须是带盘符和根分隔符的本地绝对路径（drive-absolute local path，例如 `D:\PaperFlowData`），不支持 UNC、盘符相对路径或根相对路径，也不能包含分号。数据根目录及其现有祖先不能是重解析点；它不得与项目目录（包括 `.venv` 和 Skill 源）、Skill 安装目标或 Vault 构成相同、父级或子级重叠关系。Vault 校验采用安装器实际会保留或生成的配置优先级，因此现有或待迁移 config.toml 中的 vault_path 也会在任何写入前解析并检查；只有没有这两类配置时才使用命令行 `-VaultPath`。`D:\PaperFlowData` 与独立的 `D:\PaperFlow` 属于边界清晰的相邻目录，可以使用。

安装后的职责边界如下：

- 源码仍在所选项目目录 `D:\PaperFlow`，虚拟环境仍在 `D:\PaperFlow\.venv`；`-DataRoot` 不移动源码或虚拟环境。
- 命令 wrapper 位于 `D:\PaperFlowData\bin\paperflow.cmd`，配置位于 `D:\PaperFlowData\config\config.toml`，缓存与临时目录分别为 `D:\PaperFlowData\cache` 和 `D:\PaperFlowData\tmp`。
- 小型 Codex Skill 仍安装到 `%USERPROFILE%\.agents\skills\paperflow`，不放进数据根目录。
- wrapper 为每次 PaperFlow 命令设置 `PAPERFLOW_HOME`、绝对的 `PAPERFLOW_TOPICS_PATH`、缓存目录和临时目录。共享主题文件位于源码仓库的 `D:\PaperFlow\config\topics.toml`；`PAPERFLOW_HOME` 是 PaperFlow 专用环境变量，不会全局迁移其他程序的数据。

安装器先按 `requirements.lock` 安装精确锁定的运行时与 setuptools 构建依赖，再以 `--no-deps --no-build-isolation` 安装 PaperFlow 本身，从而固定构建环境。使用 `-DataRoot` 时，这两个 pip 子进程临时使用 `PIP_NO_CACHE_DIR=1`，并把 `TEMP`、`TMP` 指向 `D:\PaperFlowData\tmp`；无论成功或失败，随后都会恢复安装进程原有的 TEMP、TMP 和 PIP_NO_CACHE_DIR。这里提供的是精确版本级可复现；为保持轻量，锁文件不包含制品哈希，不声称 hermetic 或 artifact-level 防篡改。安装末尾会通过新生成的 `paperflow.cmd --json doctor` 运行只读诊断；如果出现 warning，请按其 JSON 输出处理 required 检查。

从旧版默认位置迁移到 `-DataRoot` 时，仅在新的 DataRoot config 不存在时，安装器才会把旧 `config.toml` 逐字节复制过去；已存在的目标 config 会原样保留。如果新旧 config 同时存在，安装器会同时保留新旧 config.toml，并警告需要 manual reconciliation。安装器随后创建新 wrapper 并运行 doctor。doctor 成功后，只有满足以下任一分支才会提交迁移并清理旧文件：PATH 替换成功并经写入后读回验证，或 PATH 已经精确指向新的 bin 且经读回验证。提交后只清理旧位置中精确匹配的 wrapper，即 `%LOCALAPPDATA%\PaperFlow\bin\paperflow.cmd`；旧 `%APPDATA%\PaperFlow\config.toml` 仅在本次安装已将其逐字节迁移到新位置时清理。

在迁移提交之前发生的任何失败，包括配置复制、wrapper 创建、doctor、PATH 持久化或写入后读回核对，都不会删除精确的旧版 wrapper/config；拒绝 PATH 迁移也会保留它们。PATH 写入或核对失败时，安装器会尝试恢复原始 PATH，并回滚并读回验证；若回滚也无法核对，会明确要求手动修复 PATH。未知相邻文件始终保留，安装器不会递归清空旧目录。

若希望安装器通过 winget 补齐 Git、Python、Zotero 或 Obsidian，显式添加 `-InstallMissing`。每项安装仍受 PowerShell `ShouldProcess` 控制；不加此参数就绝不安装软件。

```powershell
.\scripts\install-windows.ps1 -InstallMissing -DataRoot "D:\PaperFlowData" -VaultPath "$env:USERPROFILE\Documents\Obsidian Vault"
```

若当前策略禁止脚本，可仅对本次进程放宽：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1 -CheckOnly -DataRoot "D:\PaperFlowData"
```

## 本地配置

使用上述 `-DataRoot` 正式安装且 `VaultPath` 有效时，会写入 `D:\PaperFlowData\config\config.toml`。这个本地文件只需要保存 Vault 路径：

已有 config.toml 会逐字节保留。TOML 不会展开环境变量，因此手动配置时必须把下面的中性示例替换为你的实际绝对 Vault 路径。

```toml
vault_path = "D:\\ObsidianVault"
```

长期关注主题、arXiv 分类、时区、每日数量和历史保留数量统一放在版本化的 `config/topics.toml`。修改它即可同时影响本地日报和 GitHub Actions 邮件。该文件会公开你的研究兴趣；公开仓库中不要写入不希望公开的主题。论文 provider 地址由程序内部维护，不是用户配置项。旧版把 `keywords`、`timezone` 等字段内联在本地 config.toml 的格式仍兼容，但不再推荐。

## 命令

所有自动化和 Codex 调用都推荐使用 JSON 输出：

```powershell
paperflow --json daily
paperflow --json daily --date 2026-08-20
paperflow --json search "vision language action" --category cs.RO --since 30d --limit 20 --sort newest
paperflow --json watch list
paperflow --json watch add "vision language action" --weight 8
paperflow --json watch remove "robotics"
paperflow --json note 2401.01234
paperflow --json doctor
```

也可直接使用默认的人类可读输出：

```powershell
paperflow doctor
paperflow search "3d reconstruction"
paperflow daily
```

典型流程是：one-off search → shortlist → optional note；也可以在确认后 watch add，让它进入未来的本地或云端 daily。`daily` 默认原子写入或更新 `<Vault>\PaperFlow\Reports\YYYY-MM-DD.md`；单篇笔记出现在 `<Vault>\PaperFlow\Papers\<arxiv_id>.md`。`search` 同时查本地历史和 arXiv，在线结果不会自动保存；`watch list` 只读，`watch add/remove` 修改共享主题文件；`note` 写入单篇笔记，已有文件时不会覆盖；`doctor` 只读检查环境。云端邮件使用：

`daily --date YYYY-MM-DD` 对 Hugging Face Daily、Hugging Face Trending 和 arXiv 三个来源都按论文发布日期筛选。旧日期可能为空，也可能受上游源的保留范围限制；命令不会为每篇论文额外发起请求。

```text
paperflow --json daily --email --no-write
```

它不写本地 Vault。退出码为 2 表示配置问题，3 表示全部来源失败，5 表示邮件发送失败。

## Codex Skill

仓库内 Skill 位于 `.agents\skills\paperflow`，安装器复制到当前用户的 `%USERPROFILE%\.agents\skills\paperflow`。Codex 遇到“今日论文、主动搜索、长期关注主题、保存论文笔记、PaperFlow 诊断”等请求时，会优先使用结构化 JSON 命令。复杂问题可拆成一到三次有界搜索并按 arXiv ID 合并；临时搜索不会自动加入关注。修改关注主题、保存或覆盖笔记、commit 或 push 前都必须先获得明确确认。重新启动 Codex 后可让它执行“运行 PaperFlow doctor”。

## Zotero 协作流程

1. 在日报或搜索结果中打开论文链接。
2. 用 Zotero Connector 手工保存论文、元数据与 PDF 到目标 collection。
3. 在 Zotero 中阅读、标注；PaperFlow 永不写 `zotero.sqlite`。
4. 如需问答或摘要，单独安装 huangkiki/zotero-ai-sidebar（AI Sidebar），并在该扩展中自行配置服务。PaperFlow 不读取 Sidebar 密钥，也不自动配置 WebDAV。

## GitHub Actions 云端邮件

仓库自带 `.github/workflows/daily.yml`。任务本身不创建数据库或上传报告 Artifact，但会在 GitHub 托管 runner 上处理论文元数据，并可能通过日志和邮件留下副本；具体边界见下一节。到 GitHub 仓库的 Settings → Secrets and variables → Actions 新建三个 Secrets：

- `PAPERFLOW_GMAIL_ADDRESS`：发件 Gmail 地址。
- `PAPERFLOW_GMAIL_APP_PASSWORD`：Google 账户生成的 App Password，不是登录密码。
- `PAPERFLOW_MAIL_TO`：日报收件邮箱。

主题和公开偏好直接读取仓库的 `config/topics.toml`。旧的 `PAPERFLOW_PRIVATE_CONFIG_JSON` 仍作为兼容入口接受，但不再由随附 workflow 使用；升级后应改用上述三个邮件 Secrets。首次部署时 workflow 不会自行创建 Secrets。

首次配置后，在 Actions → Daily PaperFlow email → Run workflow 触发 `workflow_dispatch`，检查运行日志和收件箱。不要把真实 Secret 写入仓库、Issue 或日志。

## 隐私边界

- **本地模式**：使用 `-DataRoot` 时，配置、cache、tmp 与 wrapper 位于所选数据根目录；本地元数据和报告写入你指定的 Vault 并保留在本机。PaperFlow 的网络访问限于 Hugging Face 和 arXiv 等论文提供方，并且仅在启用邮件时连接 Gmail SMTP。PaperFlow 本身没有模型客户端；独立可选的 AI Sidebar 可使用它自己的配置访问模型端点，该访问不属于 PaperFlow 自身的进程或配置。
- **GitHub Actions 云端模式**：计划任务在 GitHub 托管 runner 上处理论文元数据。即使 workflow 不上传报告 Artifact、也不使用 SQLite 或其他数据库，JSON/stdout 中的论文详情仍可能进入 Actions 日志，并按 GitHub 的日志保留策略留存；发送的邮件内容会持续存在于发件人和收件人邮箱。
- GitHub Secrets 会向 workflow 注入 SMTP 凭据和收件地址；PaperFlow 和随附 workflow 不会有意输出这些值，Secrets 不会被有意打印。`PAPERFLOW_PRIVATE_CONFIG_JSON` 仅是旧版私有运行时配置兼容入口，不再由随附 workflow 使用。仍应避免把真实 Secret 写入仓库、Issue、配置示例或调试输出。公开的 `config/topics.toml` 会暴露研究兴趣。
- 如需更强隐私，优先使用本地调度并关闭邮件；若仍使用云端任务，可减少 workflow 输出，以降低 Actions 日志中的论文详情。
- 安装器不读取或采集 Gmail Secrets，不接触 `zotero.sqlite`，不读取 AI Sidebar 密钥。PaperFlow 本身不要求付费模型或付费数据库。

## 升级与卸载

升级或重新安装：在 `D:\PaperFlow` 内执行 `git pull --ff-only`，然后复用同一数据根目录和 Vault 运行以下命令；它会按 `requirements.lock` 更新环境、保留现有配置并幂等更新 wrapper 与 Skill。开发与测试环境使用包含运行时锁的 `requirements-dev.lock`。

```powershell
.\scripts\install-windows.ps1 -DataRoot "D:\PaperFlowData" -VaultPath "$env:USERPROFILE\Documents\Obsidian Vault"
```

卸载时先关闭相关终端，从“用户环境变量 Path”中精确删除 `D:\PaperFlowData\bin`，再逐项删除 `D:\PaperFlow\.venv`、`D:\PaperFlowData\bin\paperflow.cmd`、`D:\PaperFlowData\config\config.toml`、`D:\PaperFlowData\cache`、`D:\PaperFlowData\tmp` 和 `%USERPROFILE%\.agents\skills\paperflow`。确认 `D:\PaperFlowData\bin`、`config` 及数据根目录为空后，才可删除这些空目录。不要递归删除 Vault，也不要递归删除未知的旧版内容；Obsidian 报告、Zotero 条目、未知相邻文件和其他用户数据不属于卸载目标。

## 故障排查

- **arXiv 429**：表示请求过多；稍后重试，避免短时间重复运行。单一来源失败时日报可能以 `partial=true` 成功。
- **Gmail App Password**：需先启用两步验证并按 Google 当前账户政策创建 App Password；普通 Gmail 密码不能替代。退出码 5 通常表示 SMTP 凭据、网络或账户策略问题。
- **PowerShell policy**：遇到“running scripts is disabled”时，用上文的 `powershell -NoProfile -ExecutionPolicy Bypass -File ...` 仅对该次命令放宽，不必永久降低系统策略。
- **命令找不到**：安装后打开新终端，或直接运行 `D:\PaperFlowData\bin\paperflow.cmd --json doctor`。

## 费用说明

PaperFlow 软件本身免费开源，采用 Apache License 2.0。Zotero、Obsidian 的基础软件可免费使用，AI Sidebar 是另行安装的开源项目。GitHub Actions 的可用免费额度、Gmail/邮件服务限制及第三方服务政策取决于账户与平台当前规则，因此这里不作“绝对永久免费”承诺。
