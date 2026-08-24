# PaperFlow

PaperFlow 是一个免费、私有优先、本地优先且无数据库的研究工作流。公开来源适配器负责确定性采集，Codex 负责检索策略、深读、比较、证据判断和报告分析，PaperFlow 再校验引用并生成化工能源与机器人两份独立日报。Zotero 仍负责收藏、PDF、批注和阅读。

## 用途与非目标

适合个人在 Windows 上完成“多源发现 → Codex 智能筛选/深读 → 本地双日报 → Zotero 阅读”的轻量流程。它不使用 SQLite 或其他数据库，不提供 Web UI 或向量检索，也不会自动修改 Zotero 数据库。定时执行由 Codex 本地任务负责；电脑关机或休眠时该次任务直接错过，不做补跑。PaperFlow 本身不调用付费模型 API；Codex 的可用性取决于你的 Codex 账户方案。

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

长期关注主题、arXiv 分类、时区、每日数量和历史保留数量统一放在版本化的 `config/topics.toml`。两类智能日报的公开检索策略分别位于 `config/domains/chemical-energy.toml` 和 `config/domains/robotics.toml`；私人关键词覆盖可放在数据根的 `config/domains/<domain>.local.toml`。这些文件可能暴露研究兴趣，公开仓库中不要写秘密。Crossref、OpenAlex 等 provider 地址由程序内部维护，不是用户配置项。

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
paperflow --json research prepare --domain chemical-energy
paperflow --json research prepare --domain robotics
paperflow --json research inspect --domain chemical-energy --context <context.json>
paperflow --json research finalize --context <context.json> --analysis <analysis.json>
paperflow --json research finalize --context <context.json> --analysis <analysis.json> --pdf
```

也可直接使用默认的人类可读输出：

```powershell
paperflow doctor
paperflow search "3d reconstruction"
paperflow daily
```

智能日报流程是：`research prepare` 采集当前时间窗口并写入不可变 context → Codex 阅读上下文、按需做有界补充搜索并生成结构化 analysis → `research finalize` 校验来源并生成 Markdown/JSON/HTML，显式增加 `--pdf` 时再调用本机 Chrome 或 Edge 输出 PDF。报告显示带时区的明确检索起止时间。报告位于 `%PAPERFLOW_HOME%\reports\<domain>\YYYY-MM-DD.*`，运行证据位于 `%PAPERFLOW_HOME%\runs\...`。该流程没有 `--date`、补跑或回填参数。

原有 one-off search、watch、Obsidian daily/note 命令继续兼容。`search` 在线结果不会自动保存；`watch add/remove` 与 `note` 仍需要用户确认。

`daily --date YYYY-MM-DD` 对 Hugging Face Daily、Hugging Face Trending 和 arXiv 三个来源都按论文发布日期筛选。旧日期可能为空，也可能受上游源的保留范围限制；命令不会为每篇论文额外发起请求。

退出码 2 表示配置或输入错误，3 表示来源或文件读写失败。`partial=true` 表示部分来源不可用但上下文仍可分析。

## Codex Skill

仓库内 Skill 位于 `.agents\skills\paperflow`，安装器复制到当前用户的 `%USERPROFILE%\.agents\skills\paperflow`。Codex 遇到“今日论文、主动搜索、长期关注主题、保存论文笔记、PaperFlow 诊断”等请求时，会优先使用结构化 JSON 命令。复杂问题可拆成一到三次有界搜索并按 arXiv ID 合并；临时搜索不会自动加入关注。修改关注主题、保存或覆盖笔记、commit 或 push 前都必须先获得明确确认。重新启动 Codex 后可让它执行“运行 PaperFlow doctor”。

## Zotero 协作流程

1. 在日报或搜索结果中打开论文链接。
2. 用 Zotero Connector 手工保存论文、元数据与 PDF 到目标 collection。
3. 在 Zotero 中阅读、标注；PaperFlow 永不写 `zotero.sqlite`。
4. 如需问答或摘要，单独安装 huangkiki/zotero-ai-sidebar（AI Sidebar），并在该扩展中自行配置服务。PaperFlow 不读取 Sidebar 密钥，也不自动配置 WebDAV。

## 隐私边界

- 使用 `-DataRoot` 时，配置、cache、tmp、runs、reports 与 wrapper 均位于所选数据根目录；默认建议 `D:\PaperFlowData`。
- Codex 本地任务会读取 context 和公开论文/PDF，并在本机写 analysis 与报告。仓库不再包含 GitHub 每日邮件 workflow；CI 只运行测试。
- PaperFlow 的网络访问限于代码固定的公开论文 API 和配置的 HTTPS 订阅源。Scopus、Web of Science 和 SciFinder 只允许在受控浏览器中由用户完成 CARSI/CAS 登录后进行有界检索；PaperFlow 不自动处理或保存登录、CAPTCHA、密码、Cookie，也不自动化下载学校数据库资源。
- 公开的领域配置会暴露研究兴趣；私人覆盖文件不要提交 Git。
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
