# PaperFlow 可配置 D 盘数据根目录设计

## 目标

PaperFlow 在 Windows 上支持显式的数据根目录，使配置、命令入口、临时文件和未来缓存可以统一放在 D 盘。当前电脑采用 `D:\PaperFlowData`。源码与虚拟环境继续位于 `D:\PaperFlow`，Codex Skill 继续保留在用户目录中，现有 Obsidian Vault 不迁移。

成功标准：

- 安装命令接受 `-DataRoot "D:\PaperFlowData"`。
- `paperflow` 从 `D:\PaperFlowData\config\config.toml` 读取本地配置。
- 用户 PATH 指向 `D:\PaperFlowData\bin`，不再指向 `%LOCALAPPDATA%\PaperFlow\bin`。
- pip 安装不保留缓存，安装期和运行期临时目录位于 `D:\PaperFlowData\tmp`。
- 未来 PaperFlow 缓存统一位于 `D:\PaperFlowData\cache`。
- Codex Skill 仍复制到 `%USERPROFILE%\.agents\skills\paperflow`；它是小型功能文件，不视为缓存。
- Obsidian 报告仍写入用户已选择的 Vault；Vault 及其文档不视为缓存。
- 迁移成功后删除本次安装产生的旧 C 盘 PaperFlow wrapper 与配置，不触碰其他用户文件。

## 非目标

- 不迁移整个 Obsidian Vault。
- 不迁移 Codex、Python、Git 或浏览器自身的缓存。
- 不改变 GitHub Actions 的无状态云端运行方式。
- 不引入数据库、目录联接、符号链接或全局 `APPDATA` / `LOCALAPPDATA` 重定向。
- 不自动安装 Zotero、Obsidian 或 AI Sidebar。

## 方案选择

采用应用专用 `PAPERFLOW_HOME`。与修改进程级 `APPDATA` / `LOCALAPPDATA` 相比，它不会让其他库或子进程误用 PaperFlow 的目录；与目录联接相比，它的配置来源清晰，可在其他电脑上通过同一安装参数复现。

## 路径布局

```text
D:\PaperFlow\
├─ src\
├─ scripts\
└─ .venv\

D:\PaperFlowData\
├─ bin\paperflow.cmd
├─ config\config.toml
├─ cache\
└─ tmp\

C:\Users\<user>\.agents\skills\paperflow\
└─ SKILL.md
```

`D:\PaperFlowData` 是用户可替换的示例；实现不得硬编码盘符。

## 配置解析

`paperflow.config.default_config_path()` 使用以下优先级：

1. `PAPERFLOW_HOME` 已设置且是绝对路径时，返回 `<PAPERFLOW_HOME>\config\config.toml`。
2. 未设置时保持兼容，返回 `%APPDATA%\PaperFlow\config.toml`。

空字符串、相对路径、文件路径或包含非法换行的 `PAPERFLOW_HOME` 必须作为配置错误处理，不得静默回退到 C 盘。

## 安装器接口

`scripts/install-windows.ps1` 增加可选参数：

```powershell
.\scripts\install-windows.ps1 `
  -VaultPath "C:\Users\admin\Documents\Obsidian Vault" `
  -DataRoot "D:\PaperFlowData"
```

未提供 `-DataRoot` 时维持现有安装路径，保证已有用户升级不被强制迁移。提供时必须满足：

- 是绝对本地文件系统路径。
- 目标本身不存在或是普通目录。
- `bin`、`config`、`cache`、`tmp` 的解析后路径都位于 DataRoot 内。
- `-CheckOnly` 只显示计划路径，不创建目录或改 PATH。

安装器在 DataRoot 下创建目录，并生成 wrapper。wrapper 在启动 PaperFlow 前设置：

```text
PAPERFLOW_HOME=<DataRoot>
PAPERFLOW_CACHE_DIR=<DataRoot>\cache
TMP=<DataRoot>\tmp
TEMP=<DataRoot>\tmp
```

安装依赖时临时设置 `PIP_NO_CACHE_DIR=1`，并将该安装进程的 `TMP` / `TEMP` 指向 DataRoot 的 `tmp`；安装结束后恢复安装器原有环境，避免污染调用者。

## 迁移与提交边界

迁移采用“先建新路径、验证、再清理旧路径”的顺序：

1. 如果新配置不存在而旧 `%APPDATA%\PaperFlow\config.toml` 存在，逐字节复制到新位置。
2. 安装或更新 D 盘 wrapper。
3. 运行 D 盘 wrapper 的 `--json doctor`。
4. 只有当新配置可读、wrapper 可执行且 doctor 的 required 检查全部通过时，才更新用户 PATH。
5. PATH 更新成功后，删除精确匹配的旧 `%LOCALAPPDATA%\PaperFlow\bin\paperflow.cmd` 和旧 `%APPDATA%\PaperFlow\config.toml`。
6. 仅在旧父目录为空时删除空目录；绝不递归删除未知内容。

任何步骤失败都保留旧入口和旧配置，避免半迁移状态。重新运行安装器必须幂等，不重复 PATH 项，也不覆盖用户已修改的新配置。

## 运行期行为

PaperFlow 当前不维护数据库或持久缓存。`cache` 是为后续兼容预留的明确位置；搜索仍只访问 arXiv，日报仍访问 Hugging Face 与 arXiv。搜索结果不落盘，日报和论文笔记仍写入配置指定的 Vault。

`doctor` 增加 DataRoot 检查，报告：

- DataRoot 来源与解析路径。
- 配置是否位于 DataRoot 内。
- wrapper、cache、tmp 是否位于 DataRoot 内且可访问。

JSON 输出不得暴露密码、Token 或邮件 Secret。

## 错误处理

- DataRoot 非绝对路径：安装器在任何写入前失败。
- DataRoot 指向文件或重解析点：拒绝安装，避免路径逃逸。
- 新旧配置同时存在：保留新配置并提示旧配置尚未删除；不自动覆盖。
- PATH 写入失败：保留旧 PATH 和旧入口。
- doctor 失败：不清理 C 盘旧文件。
- C 盘旧目录包含未知文件：只删除已验证的 PaperFlow 文件，保留目录和未知内容并告警。

## 测试与验证

新增或扩展测试覆盖：

- `PAPERFLOW_HOME` 的路径优先级、绝对路径校验与旧路径兼容。
- 安装器存在 `-DataRoot`，并在 `-CheckOnly` 下零写入。
- wrapper 设置四个应用专用/临时环境变量并正确引用带空格路径。
- pip 安装使用 `PIP_NO_CACHE_DIR=1` 和 D 盘临时目录。
- PATH 从旧 bin 精确迁移到新 bin，无重复项。
- 新旧配置冲突、doctor 失败和未知旧文件下不执行破坏性清理。
- 现有完整测试套件继续通过。

本机验收：

1. 使用 `-DataRoot "D:\PaperFlowData"` 重新安装。
2. 验证 D 盘 wrapper、配置、cache、tmp 存在。
3. 验证用户 PATH 仅包含 D 盘 PaperFlow bin。
4. 运行 `paperflow --version`、`paperflow --json doctor` 和一次真实 arXiv 搜索。
5. 验证旧 C 盘 wrapper/config 已清理，Codex Skill 与 Obsidian Vault 未移动。

## 文档与升级

README 的安装、升级、卸载和隐私边界增加 `-DataRoot` 示例，并说明：

- DataRoot 控制 PaperFlow 自身可变数据，不控制第三方应用缓存。
- 不提供参数时仍使用旧的 `%APPDATA%` / `%LOCALAPPDATA%` 布局。
- Codex Skill 与 Vault 是功能文件和用户文档，不属于 PaperFlow 缓存。
