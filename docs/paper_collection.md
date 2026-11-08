# 论文收集、去重与归属验证

本文对应 `collection/` 子包：`paper_collector.py` 负责身份门控、收集调度和保守去重，`paper_providers.py` 负责论文来源适配（复用身份阶段的 HTTP 客户端、API 地址和密钥），`paper_deduplication.py` 去重，`paper_ownership.py` 与 `ownership/` 验证归属，`paper_types.py` 解释论文类型。这一阶段只获取元数据，不下载 PDF。

## 收集

```bash
# 自动解析；resolved 后按已验证 ID 获取论文并保存结果
PYTHONPATH=src python3 -m paper_author_searcher.cli.resolve \
  --name "<professor name>" --university "<university>" \
  --collect-papers --output papers.json

# 查看候选与其 ID
PYTHONPATH=src python3 -m paper_author_searcher.cli.resolve \
  --name "<professor name>" --university "<university>" --list-candidates

# 人工确认某个返回的 ID；只获取此 ID，不扩展到其他候选
PYTHONPATH=src python3 -m paper_author_searcher.cli.resolve \
  --name "<professor name>" --university "<university>" \
  --collect-papers --select-author "openalex:<author ID>" --output papers.json
```

`--select-author` 必须出现在本次 Resolver 候选中，且要求 `--collect-papers`。`ambiguous` 和 `needs_review` 默认不会发起论文请求，会显示待选择候选。人工确认仅作用于所选 ID；不会把 profile 的其他 ID 或其他候选视为已人工确认。自动收集只接受 `resolved`，使用 Resolver 已验证的 `author_id_aliases`；不进行作者姓名论文搜索。直接使用库 API 时应传入真实 Resolver 返回的 profile，不要手工伪造 resolved 状态或未验证 aliases。

`collection.paper_count` 是全部去重条目数量，包含待审核和拒绝条目；不能当作已验证发表数量。`verified_paper_count` 单独报告归属 verified 的条目，`papers` 是可过滤的视图列表。每篇论文的 `records` 保留合并前元数据，包括来源、论文 ID、原始 Author ID、作者、DOI 和 URL。顶层 `source/source_paper_id/source_author_id` 指向代表记录，所有来源见 `records`。`collection_identity` 说明实际收集的身份与确认方式，避免人工选择后误读原本首选 profile。

OpenAlex 使用 cursor 遍历所有页面，Semantic Scholar 使用返回的 next offset，DBLP 使用官方 PID XML 完整导出（不使用按姓名的论文搜索）。所有可解析原始记录均保留。OpenAlex/Semantic Scholar 的准确目标 Author ID 只构成数据库归属声明，不能单独验证论文；DBLP 要求根 PID 相符，仅 editor 或缺失目标 PID 的记录由归属验证器单独判定。

`CollectionConfig` 管理分页大小和可选 `maximum_pages`，默认不设置页数/论文数限制。达到手动页数限制、分页 token 重复、响应格式错误、超时/限流或无法解析元数据会使来源 `complete=false`。论文归属待审核不改变 API 分页完成状态。已成功取得的前页保留。每个来源状态包含 `pages_fetched`、`records_received`、`rejected_records` 和 `error`。

全局 `complete=true` 仅表示所有配置来源的已确认 ID 都完成了本轮分页检索且没有解析失败；它不能证明数据库收录了教授全部论文。缺少某来源已确认 ID 或来源失败也标记不完整，不能把未查询的来源算作零篇。部分结果仍会输出和保存，CLI 返回 2；完整本轮收集返回 0。默认配置三个来源，DBLP 不可用时会保留 OpenAlex/Semantic Scholar 结果并标记不完整。库 API 可显式配置来源范围。

## 去重

去重优先规范化 DOI 和稳定数据库/外部论文 ID。缺少任一 DOI 时，结合标题、年份、可兼容作者署名与来源元数据；仅在全名或有依据的首字母署名兼容时匹配作者。跨数据库类型代码先标准化，避免 article 与 JournalArticle 阻止真正重复。不同 DOI 或明确不同出版形式保留独立记录，并在同标题/兼容作者时附上可能的 `related_versions` 链接，不宣称已证实同一研究成果。完整链接比较防止无 DOI 中间记录合并冲突 DOI。作者/venue 缺失、题名变化与不同版本仍可能留有重复项；所有合并记录、规则证据及版本标识符保留。

测试包括 mock 分页、末页失败保留前页、错误作者 ID、DBLP PID/editor 校验、DOI 与无 DOI 去重、版本冲突、aliases、歧义门控、人工选择、JSON 保存以及不完整退出码。真实 API 仅做了小页 smoke check，不代表完整论文覆盖率评估。

接口参考：[Semantic Scholar 作者论文分页](https://api.semanticscholar.org/api-docs/snippets)、[DBLP 官方作者完整导出](https://dblp.org/faq/17334565.html)。

## 归属验证与审核输出

归属验证器独立验证每条来源记录和合并条目的归属。`verified` 是满足当前保守证据规则，非人工认证或统计正确率；`needs_review` 隔离证据不足或相互冲突的条目；`rejected` 表示明确的目标作者 ORCID/署名/作者关系冲突，仍保留原始数据。

Author ID 加同名不足以 verified。优先检查逐论文目标 Author ID、原始署名、ORCID 与实际论文机构；来源 ID 按自己的 namespace 验证，不比较不同数据库的 ID 字符串是否相同。正面证据包括：兼容目标署名及独立逐论文 ORCID 与教授身份一致；明确目标 ID、兼容署名及论文机构与已验证机构一致；或稳定论文标识符与两个已确认来源的逐论文目标 ID、兼容署名同时一致。数据库作者档案 ORCID（`resolved_author`、`author_profile`）与未知 scope 的 ORCID 不单独作为正面证据，来源档案中未确认的历史机构也不替代目标学校。标准条目的有限共同作者证据保留为辅助路径；种子集合固定，不从待审核论文循环自我增强。仅靠相似标题去重不会提升归属状态。混合 verified/rejected 来源决策、冲突 ORCID 或非标准条目需要审核。姓名缩写、缺失作者 ID 或机构不一致并不自动证明错误归属。人工选择 Author ID 不替代逐篇归属验证。

研究方向差异不构成拒绝依据，避免排除跨学科成果。来源关联声明和教授 profile 仍可能有错误；多个来源也可能复制相同错误。本规则提高可审查性，不能保证自动判定全部正确，仍需独立标注集评估 precision/recall。

新请求保存 `author_details`（可获取的姓名、Author ID、ORCID 和机构）、`external_ids`、`raw_metadata` 及作者关系观察结果。每条来源记录保留 `ownership_status` / `ownership_evidence`，合并条目也保留整体归属决策和去重证据。不可解析数据保存在来源 `rejected_records.raw_metadata`，与归属 rejected 的论文分开统计。

`paper_types.py` 只解释明确来源类型，不从标题或研究方向猜测类型。支持 research_paper、review、preprint、book_chapter、dataset、editorial、book 等；缺失、不支持或冲突的分类为 unknown，并记录原始标签与分类证据。

`statistics` 分开报告收到的原始记录数、保留的来源记录数、去重条目数、条目/来源记录的 verified / needs_review / rejected 数量以及不可解析记录数。`sources` 分别报告 `fetch_status`、`pagination_complete`、`complete`、页数、原始数量、归属统计和错误。`professor_identity_status` 与逐篇归属状态分开。数据库完整返回不代表教授全部发表的论文已取得。

```bash
# 正常收集；仅在 papers 视图中显示已验证学术论文
PYTHONPATH=src python3 -m paper_author_searcher.cli.resolve \
  --name "<professor>" --university "<university>" --collect-papers \
  --academic-only --ownership-status verified --output papers.json

# 离线重算既有 JSON，不调用 API、不补造旧记录缺失的 ORCID 等证据
PYTHONPATH=src python3 -m paper_author_searcher.cli.resolve \
  --reanalyze-json papers.json --output papers.reviewed.json
```

过滤时 `papers` 是视图，`all_papers` 保留完整归档（包含待审核/拒绝/非学术条目）；统计始终针对完整集合。`--ownership-status` 可重复使用。离线处理旧结果时证据可用性较低，因此常有较多 needs_review；需要重新获取 richer authorship 元数据或人工核验，不通过调整阈值强行确认。

## 逐论文作者身份证据

OpenAlex 选择 `authorships`、`ids` 和 `is_authors_truncated`，保存每位作者的来源 ID、ORCID、`raw_author_name`、署名顺序、通讯作者标记、机构、原始机构字符串及其映射。作者列表截断时使用同一 work ID 查询单篇详情；失败后保留原始论文、截断状态与错误，不将目标不在截断片段中解释为明确不属于该教授。ORCID 来自解析后的作者实体，原始署名来自论文记录，两者独立保留。通用逗号逆序署名格式会用于比较，原始值保持不变。

Semantic Scholar 作者论文列表请求 `authors` 获取实际返回的 Author ID 和姓名，以及论文 `externalIds`。该端点不支持 `authors.externalIds` / `authors.affiliations`，因此每个被检索的目标 ID 单独请求一次作者档案；只有逐论文列表明确包含此 ID 的作者才附上档案 ORCID 和机构。档案以 `author_profile` 保存，ORCID 标明 `orcid_scope=author_profile`，机构放在 `profile_affiliations`，不会当作论文发表时的机构。未提供的数据继续为空，不请求引用/参考文献作者作为论文作者，也不为每篇重复请求同一作者档案。

合并后的 `author_details` 带来源 namespace 和论文 ID；`records` 保留每份原始详情；`source_identifiers` 保留各来源论文/作者 ID、外部 ID、目标出现观察和作者列表完整性。冲突外部 ID 不任意覆盖，在各来源记录中保留。`target_author_present=true` 表示数据库作者列表中的 ID 观察，不是独立人工归属确认；缺失信息为 null，明确完整列表中没有目标 ID 才为 false。`statistics.source_record_authorship_counts` 报告证据可用情况。

`OwnershipConfig.maximum_coauthor_authors` 默认 50，控制共同作者辅助路径，未改变原有姓名匹配阈值。超过上限的记录不能靠共同作者重合或缺少独立身份依据的跨来源 ID 列表自动 verified，也不能成为共同作者种子；真实大规模合作论文仍可凭更强的身份依据确认。论文集、Peer Review、decision letter 等显式类型或通用标题结构进入角色审核，不自动删除，也不据此推断 unknown 的论文类型。来源类型在同一合并条目中出现非标准分类时整体需要审核；明确的 editor/reviewer/contributor 角色不会被当作 author。

```bash
# 正常 API 重新获取旧归档中已确认身份的论文元数据；建议使用新输出文件
SSL_CERT_FILE=/etc/ssl/cert.pem PYTHONPATH=src python3 -m paper_author_searcher.cli.resolve \
  --refresh-json papers.reviewed.json --output papers.authorship.json
```

`SSL_CERT_FILE` 只在该可信 CA 文件存在、当前 Python 需要配置 CA 时使用，不关闭 TLS 验证。自动刷新仍要求保存的身份 resolved；已保存的人工确认仅对原来选择的单一 ID 生效。`--reanalyze-json` 保持离线，不补造旧 JSON 缺少的作者 ID、ORCID 或机构。

来源 `pagination_complete` 与作者详情获取错误分开记录；详情扩展失败会留下 `metadata_errors` 并标记结果不完整，已取得记录仍保留。即使所有查询的 Author ID 都完成分页，也不证明教授论文全集或逐篇真实归属。真实归属准确率仍须使用人工 Ground Truth 和 [离线 Evaluation](evaluation.md) 验证。

字段与访问限制参考：[OpenAlex Authorships](https://help.openalex.org/data/authorships/)、[OpenAlex 作者列表截断说明](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/authors/limitations.md)、[Semantic Scholar 官方 API](https://api.semanticscholar.org/api-docs/graph)。
