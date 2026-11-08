# 身份验证规则与诊断

本文是身份解析（`resolution/`）的打分、合并和状态规则。检索策略、锚点与同名裁决见 [身份解析：诊断、合并、锚点与校准](identity_resolution.md)，输入格式与身份发现见 [锁定身份与多源检索](identity_and_sources.md)。

## 命令行输出

```bash
PYTHONPATH=src python3 -m paper_author_searcher.cli.resolve \
  --name 'Geoffrey Hinton' --university 'University of Toronto'
```

默认输出候选教授的姓名、匹配学校、各来源 author ID、confidence、status、简短证据及来源错误。没有验证学校时学校字段为 null，不把输入学校写成已验证信息。使用 `--verbose` 输出完整 profile、全部 candidates 和 source_errors。

可选参数：`--department`、可重复的 `--research` 和 `--university-alias`。也可 `python3 -m pip install -e .` 后使用 `professor-resolve`。CLI 仅 resolved 返回 0，其余状态返回 2。

## 学校与跨来源合并

学校证据分为 `matched`、`unknown`、`mismatch`。空 affiliations 是信息缺失，不按学校冲突扣分；有机构但未匹配输入学校时降低评分。这只表示已观察信息不相符，并不能证明教授从未在该校任职。OpenAlex 同时使用历史机构、最近机构及论文中属于该候选本人的 authorship 机构，避免把共同作者的学校算给候选人。

同名同校不再足以跨来源合并。兼容姓名之外，需要一致 ORCID、至少两个共同 DOI、一个共同 DOI 加至少两个共同作者，或共同学校加至少三个共同作者和至少两个一致研究主题。冲突 ORCID 或明确冲突的中间名阻止合并。同一来源的不同 ID 仅在兼容姓名且 ORCID 一致时合并，其余保持独立；合并后的 `author_id_aliases` 保留全部 ID，兼容字段 `author_ids` 保留每来源一个代表 ID。同一来源重复记录不增加跨来源验证分。完整链接分组避免通过中间候选传递合并。较丰富的共同 DOI 证据优先于单篇片段，但这些规则仍是启发式，不能保证消除数据源自身的混合身份。

## Confidence 与状态

`identity_confidence` 是启发式证据评分，不是校准后的正确率。姓名贡献至多 0.55，学校匹配至多 0.30（观察到不匹配扣 0.20，缺失不扣分），最近学校 0.02，跨来源验证 0–0.08（单篇共同论文不增加排序分），院系和研究方向各 0.025。学校未知/未匹配或者只有姓名缩写时评分至多 0.69，保持 `needs_review`。

- `resolved`：评分至少 0.8，有学校与兼容姓名证据，没有接近的独立候选。
- `ambiguous`：有学校匹配，但前两名评分差小于 0.10；首选置信度降至至多 0.69。
- `needs_review`：学校或姓名证据不足。即使 ORCID/DOI 验证了两条记录属于同一作者，也不能在缺少目标学校证据时自动确认目标教授。

学校简称、译名通过 `university_aliases` 指定；研究方向使用词形相似度，不进行跨语言语义推断。历史机构不代表现任学校。

## 歧义诊断

`match_score` 保留评分规则计算的原始分数，`identity_confidence` 则应用证据不足和歧义限制。排序及前两名差距使用原始分数，不使用被压到 0.69 的最终 confidence。`ambiguity` 在默认 CLI 中也显示前两名的全部 Author ID、原始分数、学校证据、分数差距及未能合并的原因。它表示尚未确认是同一人的竞争记录，并不证明两者一定是不同教授。

`requested_university` 是用户输入；`university` 和 `matched_affiliations` 使用来源中实际观察到、匹配输入的机构名称。历史机构证据仍不等于现任任职验证。

## 网络与来源诊断

密钥环境变量：`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`。客户端保留 SSL 验证，设置超时、响应大小上限及 429/5xx 有限重试。

DBLP 使用 `/search/author/api?q=...&format=json` 和 `/pid/<id>.xml`。检查 HTTP 状态码、Content-Type、空响应及格式错误；HTTP 200 的 HTML 防爬页面明确报错，不作为 JSON 解析，也不绕过防爬。XML 获取失败保留搜索候选和 metadata_errors；某一来源失败不丢弃其他来源结果。DBLP 官方作者搜索格式仍是 `/search/author/api?q=...&format=json&h=...`，见 [官方 API FAQ](https://dblp.org/faq/13501473.html)。HTML 防爬响应无法当作 JSON 使用；当前实现将来源标记为出错并继续其他 Provider，不绕过验证。可通过显式注入 OpenAlex 与 Semantic Scholar 的 provider 列表暂时不启用 DBLP。

OpenAlex 默认搜索 25 个候选，并为最多 8 个姓名兼容候选查询每人最多 50 篇高引用论文的 DOI、authorships、topics；Semantic Scholar 使用返回论文的 DOI、共同作者和标题；DBLP 使用作者 XML。只请求身份所需元数据，不请求全文。候选数及 enrichment 上限可通过 provider 构造参数调整。采样与搜索截断可能遗漏验证证据，重复记录仍可能导致 ambiguous。

若 Python 缺少系统 CA 配置，且 `/etc/ssl/cert.pem` 包含可信 CA，可设置 `SSL_CERT_FILE=/etc/ssl/cert.pem`。

## 测试覆盖

Mock 测试覆盖多语言和不同研究领域的合成作者同名隔离、缺失学校与不匹配学校、历史机构、ORCID 冲突、DOI 规范化、共同作者、OpenAlex 本人机构提取、CLI 输出以及 DBLP HTML/JSON/HTTP 错误。联网检查（`scripts/ops/live_check.py`）使用传入的姓名和学校，验证输出结构、置信度范围以及学校未知时不能 resolved 等规则，不断言固定 Author ID。它是接口集成检查，不能证明真实身份匹配正确；独立的真实身份评估需要人工核验的数据集。数据源重复身份仍可能导致 ambiguous，来源异常会明确显示。

接口参考：[OpenAlex Authors](https://help.openalex.org/data/authors/)、[OpenAlex Authorships](https://help.openalex.org/data/authorships/)、[Semantic Scholar API](https://api.semanticscholar.org/api-docs/)、[DBLP Search API](https://dblp.org/faq/How+to+use+the+dblp+search+API.html)。
