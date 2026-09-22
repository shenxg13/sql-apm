# 质量工具安装与运行

## 工具合同

唯一机器可读版本来源是 `scripts/quality/tool-versions.env`。

| 工具 | 版本 | 用途 |
| --- | --- | --- |
| Bash | 最低 5.1.0 | Shell 入口 |
| Git | 最低 2.39.0 | 枚举文件与版本控制 |
| jq | 最低 1.6.0 | JSON 与流程校验 |
| GNU coreutils | 最低 8.32.0 | base64 和基础文件工具 |
| ShellCheck | 固定 0.10.0 | Shell 静态检查 |
| shfmt | 固定 3.13.1 | Shell 格式检查 |
| Node.js | 固定 24.19.0 | Markdown 与模板校验 |
| npm | 固定 11.17.0 | 安装 Markdown 检查器 |
| markdownlint-cli2 | 固定 0.23.2 | Markdown 校验 |
| GitHub CLI | 最低 2.45.0，仅真实远程操作需要 | Issue／PR 与 GitHub |

首版支持 Linux／WSL 与 GNU 工具链。本地离线入口无需真实 `gh`；模拟客户端由
测试提供。现有合规工具可以继续使用；升级版本应同步合同、说明及回归结果。

## 本地安装

Bash、Git、jq 和 coreutils 从所用 Linux 发行版的受信任软件源安装。
版本须满足上述下限，使用 `command -v` 和工具的版本命令核对。

ShellCheck 从 [官方发行](https://github.com/koalaman/shellcheck/releases/tag/v0.10.0)
获取，shfmt 从 [官方发行](https://github.com/mvdan/sh/releases/tag/v3.13.1)
获取。工具合同记录 Linux x86_64 资产摘要；其他架构应选官方对应资产，
核对来源并记录摘要，不能复用 x86_64 摘要。

Node.js 从 [官方目录](https://nodejs.org/dist/v24.19.0/) 安装，核对该目录的
SHASUMS256.txt，或使用能提供精确版本的已有版本管理器。使用用户拥有的安装
目录，不对系统 npm 执行 `sudo npm install`。

下面假设 Node/npm 安装前缀可由当前用户写入；安装前应取得相应授权：

```bash
source scripts/quality/tool-versions.env
npm install --global "npm@$NPM_VERSION" "markdownlint-cli2@$MARKDOWNLINT_CLI2_VERSION"
scripts/quality/check.sh --check-tools-only
```

运行真实 GitHub 操作时，另从 [官方 GitHub CLI](https://cli.github.com/)
安装 `gh`，按运行时指南完成网络与身份验证。

## CI 安装

`.github/workflows/quality.yml` 固定 Actions 提交，读取同一版本合同，由
`scripts/quality/install_ci_tools.sh` 下载并核对 ShellCheck、shfmt 摘要，安装
固定 npm 与 Markdown 工具，随后调用统一检查入口。
CI 辅助工具为 curl、tar、xz、sha256sum 和 install，工作目录位于 runner 临时目录。
安装脚本不修改业务依赖或凭据；本地入口不会自动调用它。

## 运行与排障

```bash
scripts/quality/check.sh
```

缺少工具或版本不符会明确失败。先核对来源、版本与 PATH；不得降低门禁或把
不可运行检查描述为成功。缺失 GitHub 登录只影响真实远程操作。
