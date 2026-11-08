# 配置、数据源注册与日志

## 三层配置

所有可调参数集中在 `paper_author_searcher.settings.Settings`（各分区即 `config/` 中的 dataclass：`matching`、`anchors`、`http`、`sources`、`collection`、`ownership`、`deduplication`、`pdf`、`download`）。`config/` 按关注点分为 `identity`（匹配、锚点、同名裁决、发现）、`sources`（HTTP 与各来源）、`collection`（收集、归属、精度检查、去重）、`fulltext`（原文链接、下载、存储）和 `service`。优先级从低到高：

1. 代码默认值；
2. JSON 配置文件：`--settings path.json` 或 `$PAPER_SEARCH_SETTINGS`，例如 `{"http": {"timeout": 30}, "collection": {"maximum_contenders": 5}}`；
3. 环境变量覆盖：`PAPER_SEARCH__<分区>__<字段>`，例如 `PAPER_SEARCH__HTTP__TIMEOUT=30`、`PAPER_SEARCH__HTTP__RETRY_STATUSES=429,503`。

值会按字段类型转换，并重新执行各分区自身的校验；未知分区或字段直接报错，不会被静默忽略。命令行参数（如 `--timeout`）仍优先于以上三层。

配置均为不可变 dataclass，可使用 `dataclasses.replace` 修改。

## 凭据

凭据只从环境变量读取（`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`、`UNPAYWALL_EMAIL`、`CROSSREF_MAILTO`），配置文件中出现 `credentials` 会被拒绝；`repr` 与 `Settings.to_dict()` 只显示 set/unset，不输出值。只有 `settings.py`（和日志引导）读取环境，有架构测试保证。

## 依赖注入

服务之间的依赖全部由 `composition.ServiceContainer`（组合根）注入：库中的类不自行构造 HTTP 客户端、数据源或子服务，缺失某阶段所需服务时会明确报错（`MissingStageService`），不会悄悄回退到默认实现。测试中可直接向构造函数传入替身。

```python
from paper_author_searcher import ProfessorQuery
from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.settings import Settings

container = ServiceContainer(Settings.load(), RunOptions(mode='metadata'))
result = container.resolver.resolve(ProfessorQuery(
    name='Andrew Ng', university='Stanford University',
    research_interests=['machine learning']))
print(result.to_dict())
```

`ProfessorResolver(config=MatchingConfig(...))` 注入策略，默认 OpenAlex adapter 同步使用该姓名兼容阈值；自定义 provider 可传入 `source_config=SourceConfig(...)` 与配置后的 `HttpClient(config=HttpConfig(...))`。原有构造参数、统一输出和 `providers=[]` 的用法保留。

## 数据源注册表

数据源按名字注册在 `source_catalog.py`（`IDENTITY_SOURCES`、`PAPER_SOURCES`、`PDF_SOURCES`），由配置决定启用哪些、按什么顺序：

```json
{"sources": {"identity_sources": ["openalex", "semantic_scholar"]},
 "pdf": {"sources": ["existing_metadata", "openalex", "arxiv", "public_pages"]}}
```

新增数据源：写好适配器，在 `source_catalog.py` 用 `@PDF_SOURCES.register('name')` 注册工厂函数，再把名字加入配置即可，无需改动 Resolver / Collector / Workflow。配置中出现未注册的名字会在构建时直接报错。身份来源只需实现 `AuthorProvider` 协议的 `name` 和 `search(query)`，返回 `AuthorCandidate`。

生产逻辑没有针对具体教授或 author ID 的特殊分支；测试数据使用独立的合成记录；联网检查由用户输入驱动，不内置真实教授或预期 Author ID。

## 日志

命令结果写到 stdout，诊断信息（进度、重试、限流、预算拒绝、被隔离的来源失败）通过 `logging` 写到 stderr，管道输出不会被污染。所有命令都支持：

- `--log-level {DEBUG,INFO,WARNING,ERROR}`，默认读 `$PAPER_SEARCH_LOG_LEVEL`，否则为 `INFO`；`DEBUG` 时被隔离的失败会附带 traceback。
- `--log-format {text,json}`，默认读 `$PAPER_SEARCH_LOG_FORMAT`；`json` 每行一个对象，便于日志采集。

作为库嵌入时，包只挂 `NullHandler`，由宿主应用决定日志配置。
