# Python 3.9.5 开发环境验证

2026-09-25，用户明确暂停 Issue #1 业务实施，授权先完成 Python 环境准备。
本次仅验证 AlmaLinux 9.8、x86_64、WSL2 开发环境，不代表 Kylin 离线兼容性。

## 已准备的环境

| 项目 | 实测结果 |
| --- | --- |
| 独立解释器 | `var/python-3.9.5/bin/python3.9`，精确版本 3.9.5 |
| 虚拟环境 | `.venv/`，不包含系统 site-packages |
| 系统解释器 | `/usr/bin/python3` 保持 3.9.25 |
| 包工具 | pip 26.0.1、setuptools 82.0.1、wheel 0.48.0、packaging 26.0 |
| SSL | OpenSSL 3.5.5，复用本机动态库 |
| SQLite | 3.34.1，复用本机动态库 |

仅安装包工具及其依赖，未安装 SQL APM 业务依赖。解释器、虚拟环境、构建源码、
开发包、下载 wheel、验证脚本及原始构建日志均在 Git 忽略目录内。

## 来源与构建

Python 源码来自[官方 3.9.5 发布页](https://www.python.org/downloads/release/python-395/)
中的 `Python-3.9.5.tgz`。MD5 与该发布页的
`364158b3113cf8ac8db7868ce40ebc7b` 一致；本地计算的 SHA-256 为
`e0fbd5b6e1ee242524430dee3c91baf4cbbaba4a72dd1674b90fda87b713c7ab`。
SHA-256 在此固定输入，不宣称已经独立核验发行者签名。

首次编译缺少 bz2、lzma、sqlite3。检查确认系统缺少对应开发包；
`sudo -n dnf install` 返回需要密码，没有安装任何系统软件。
随后仅使用 AlmaLinux BaseOS／AppStream 下载与已安装运行库匹配的开发包：

- bzip2-devel 1.0.8-11.el9.x86_64。
- xz-devel 5.2.5-8.el9_0.x86_64。
- sqlite-devel 3.34.1-10.el9_8.x86_64。

三个 RPM 的 `rpm -K` 检查均为 `digests signatures OK`。
仅将头文件提取至 `var/python-build/deps/include/`，在其 `lib/` 下建立指向
现有系统运行库的链接。本地提取脚本为 `var/python-build/extract_headers.py`。
没有将 RPM 安装到系统，也没有修改系统解释器或 shell 启动配置。

最初下载访问了无关的 `pgdg-common` 软件源并遇到元数据签名错误；
限定本次命令只使用 BaseOS／AppStream 后成功，未关闭签名验证或修改软件源配置。

构建使用 GCC 11.5.0，配置如下，其中 `REPO` 为当前仓库的绝对路径：

```bash
CPPFLAGS="-I${REPO}/var/python-build/deps/include" \
LDFLAGS="-L${REPO}/var/python-build/deps/lib" \
./configure --prefix="${REPO}/var/python-3.9.5" --without-ensurepip
make -j4
make install
```

源码及构建日志位于 `var/python-build/`。旧的空虚拟环境移至该目录内备份，
然后以独立解释器重新创建 `.venv/`，通过 `ensurepip` 引导安装 pip。
从官方 PyPI 元数据读取各 wheel 的 Python 版本要求与 SHA-256，下载并验证后，
使用 `--no-index --require-hashes` 安装上述固定版本工具。
下载来源及摘要保存在该目录的 `bootstrap-downloads.json` 和
`bootstrap-requirements.txt`；这份清单只覆盖环境引导工具。

需要在本机重建空虚拟环境时，先确认既有环境中的依赖已经记录并备份，随后使用：

```bash
var/python-3.9.5/bin/python3.9 -m venv --without-pip .venv
.venv/bin/python -m ensurepip --upgrade
PIP_CONFIG_FILE=/dev/null .venv/bin/python -m pip --isolated \
  --disable-pip-version-check install --no-index \
  --find-links=var/python-build/wheels --require-hashes \
  -r var/python-build/bootstrap-requirements.txt
```

上述忽略目录是本机材料，不会随 Git 克隆。其他机器需先取得官方源码和匹配的
开发库，再按其系统环境构建；本次配置不是跨机器可搬运的 Python 发行包。

## 验证结果

以下实际检查均通过，原始结果保存在 `var/python-build/verification.json`：

- 精确 Python 版本、解释器路径、虚拟环境与基础解释器隔离。
- bz2、gzip、lzma、zlib 的压缩解压往返。
- SHA-256 已知向量、Decimal 运算和中文 JSON 往返。
- SQLite 内存数据库建表、参数化插入及查询。
- Asia/Shanghai 时区、UUID4、ctypes 调用。
- multiprocessing 的 spawn 子进程启动、队列通信及正常退出。
- 开启主机名和证书验证访问 python.org 与 PyPI，均返回 HTTP 200。

另行完成的包工具检查：

- pip 从官方 PyPI 经 HTTPS 下载固定版本 wheel，未关闭证书验证。
- 无依赖人工测试包通过本地 wheel 构建、安装到独立测试目录及导入验证。
- `python -m pip check` 返回 `No broken requirements found`。

本地功能检查可用 `.venv/bin/python var/python-build/verify_environment.py` 重跑；
其中两项 HTTPS 检查需要网络。测试包及构建安装日志保存在该目录内，
测试包没有安装到项目虚拟环境的 site-packages。

## 验证边界

未构建 dbm.gnu、dbm.ndbm、tkinter、可选 C 扩展 `_uuid` 和 nis。
Python 层 `uuid.uuid4()` 已验证可用。本次不将这些未构建模块表述为已具备，
后续若有依赖使用它们，需先补齐相应开发库并重建解释器。

本次是开发环境的有界功能验证，未运行完整 CPython 回归套件、业务验收或
Kylin 部署测试。标准库使用本机动态库，操作系统更新或目录迁移后需重新验证。
日常使用命令见[本地开发说明](../runbooks/local-development.md)。
