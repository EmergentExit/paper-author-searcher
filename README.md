# Paper Author Searcher

输入一位研究者的姓名和学校，先确认是哪一个人，再找出这个人的论文，最后下载能合法公开获取的 PDF。这个项目是 cold-email agent 的上游：agent 用这里输出的论文来写邮件。

线上服务：<https://paper-searcher.fyi>（REST、gRPC、MCP 都需要 API key）。

## 效果

在 32 人的分层评测集上（4 种资历 × 4 个领域 × 常见中文名 / 其他名字）：

| 指标 | 结果 |
| --- | --- |
| 身份精度（找到的人确实是要找的人） | 100% |
| 论文召回（开发集，按人平均） | 约 92% |
| 论文召回（14 位没参与调参的研究者，严格评分） | 约 80% |
| 作者归属精度 | 约 99.6% |

上线门槛是每一层召回 ≥ 80%、精度 ≥ 95%、身份精度 100%。真值由 AI 根据研究者本人或实验室的书目整理，还没有经过人工认证，所以这些数字只是参考。评分报告见 [reports/benchmark/career-chg.md](reports/benchmark/career-chg.md)，指标口径见 [分层评测集与上线门槛](docs/benchmark.md)。

## 快速开始

需要 Python ≥ 3.10。核心功能不依赖第三方包；下载 PDF 需要安装 `download` extra。

```bash
python3 -m pip install -e '.[download]'
export OPENALEX_API_KEY=...            # 推荐
export SEMANTIC_SCHOLAR_API_KEY=...    # 推荐
export UNPAYWALL_EMAIL=you@example.com # 用于查找开放获取 PDF
```

所有 key 都可以不设，但相应来源会被跳过或更容易被限流。key 只从环境变量读取，不会写进输出。

**网页**（最简单）：浏览器会自动打开，填姓名和学校后点"搜索"，结束后可以单篇或打包下载 PDF。

```bash
paper-search-api serve --open
```

**命令行**：一次跑完整流程，结果保存在 `output/` 下单独的目录里。

```bash
paper-search --name 'Geoffrey Hinton' --university 'University of Toronto' \
  --output-dir output --max-downloads 3
```

**Python**：

```python
from paper_author_searcher import ProfessorQuery
from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.settings import Settings

container = ServiceContainer(Settings.load(), RunOptions(mode='metadata'))
result = container.resolver.resolve(ProfessorQuery(name='Andrew Ng', university='Stanford University'))
print(result.to_dict())
```

## 工作流程

1. **确认身份**：在 OpenAlex、Semantic Scholar、DBLP、ORCID 等来源中找候选人，用学校、ORCID、共同论文和共同作者判断是不是同一个人。遇到分不清的同名者时停下来，让用户选择。见 [身份验证规则](docs/identity_verification.md)、[身份解析](docs/identity_resolution.md)、[锁定身份与多源检索](docs/identity_and_sources.md)。
2. **收集论文**：按已经确认的作者 ID 从各来源完整翻页，再用本人主页或 CV 上的发表列表逐篇补查。见 [论文收集与归属验证](docs/paper_collection.md)。
3. **验证归属**：逐篇检查署名、ORCID 和发表时的机构，结果分成 `verified`、`needs_review`、`rejected`。默认只有 `verified` 进入后续步骤。同上。
4. **查找 PDF**：在开放获取来源和公开页面中找 PDF 链接，并核对论文身份。见 [PDF Resolver](docs/pdf_resolver.md)、[PDF 来源发现](docs/pdf_discovery.md)。
5. **下载并检查内容**：下载后解析 PDF，确认文件内容确实是这篇论文。见 [PDF Downloader](docs/download.md)、[PDF 内容验证](docs/pdf_validation.md)。

完整流程的命令、续跑和输出结构见 [docs/workflow.md](docs/workflow.md)。

## 对外接口

- [REST API](docs/api.md)：异步任务、API key 鉴权、幂等提交、同名者确认。
- [gRPC](docs/grpc.md)：与 REST 功能相同，地址是 `grpc.paper-searcher.fyi`。
- [MCP 服务](docs/mcp.md)：给 AI 助手使用，地址是 `https://paper-searcher.fyi/mcp`。
- [输出契约](docs/agent-contract.md)：cold-email agent 应该读取哪些字段。
- [部署与运维](docs/deployment.md)：Docker、worker、对象存储、健康检查和告警。

## 配置

所有参数集中在 `Settings` 中。优先级从低到高：代码默认值 → JSON 文件（`--settings` 或 `$PAPER_SEARCH_SETTINGS`）→ 环境变量 → 命令行参数。

```bash
PAPER_SEARCH__HTTP__TIMEOUT=30 paper-search --name '...' --university '...'
```

未知的分区或字段会直接报错。数据源的启用和顺序、依赖注入以及日志选项见 [配置、数据源注册与日志](docs/configuration.md)。

## 项目结构

```text
src/paper_author_searcher/
├── resolution/   确认身份：候选检索、打分、锚点、同名裁决
├── collection/   收集论文、去重、逐篇归属验证（ownership/）
├── fulltext/     查找 PDF 链接、下载、内容验证
├── workflow/     把各阶段串起来，支持断点续跑
├── transport/    HTTP、缓存、限流、预算、网络安全
├── service/      搜索服务：任务队列、存储、agent 输出
├── api/  rpc/  mcp_api/   REST、gRPC、MCP 前端
├── cli/          命令行入口
├── benchmark/    分层评测、评分和回放门禁
├── quality/      离线 Evaluation 与诊断
├── config/       各模块的默认参数（不可变 dataclass）
└── composition.py  组合根：所有依赖在这里组装
```

## 开发与测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

- **单元测试**全部使用合成数据，不联网。联网冒烟检查是 `scripts/ops/live_check.py`。
- **回放门禁**：对已录制的请求离线重放，逐人比较身份和每篇论文的判定，任何变化都会失败。重构和提速的改动必须通过它。见 [判定一致性门禁](docs/benchmark.md)。
- **CI**：每个 PR 跑 lint、类型检查和 Python 3.10 / 3.12 的测试；回放门禁在自托管 runner 上运行。
- 每项性能改动的测量和取舍记录在 [优化记录](docs/optimization.md)。

## 局限

- 不保证找全。召回依赖各数据库的收录情况，冷门研究者和常见中文名更难。
- PDF 只能覆盖一部分论文：付费墙、需要登录或有反爬的页面不会绕过，开发集上大约一半到六成的论文能拿到经过验证的全文。
- `verified` 是保守的规则判定，不是人工认证；拿不准的论文会放在 `needs_review`，不会被丢弃。
- 评测真值还没有人工认证，结论见 [真实场景评估](docs/performance.md) 和 [Ground Truth 审核](docs/ground_truth_review.md)。
