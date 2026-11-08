# PDF Resolver：只检索 URL，不下载文件

`fulltext/pdf_resolver.py` 中的 `PDFResolver` 读取 Paper Collector 的保存结果，不重新运行教授解析或论文收集。默认只处理 `ownership_status=verified`，显式 `--include-needs-review` 才处理待审核条目；被拒绝条目始终跳过。原有归属状态与证据保留，PDF 验证成功也不会将论文归属从 needs_review 改为 verified。各来源的请求与错误处理、复测方法见 [PDF 来源发现与复测](pdf_discovery.md)；下载阶段见 [PDF Downloader](download.md)。

```bash
# 已安装项目时也可使用 pdf-resolve 命令
PYTHONPATH=src python3 -m paper_author_searcher.cli.pdf \
  --input papers.authorship.json --output papers.pdf_urls.json

# 小规模验证：按保存顺序处理前 5 篇符合归属条件的论文
PYTHONPATH=src python3 -m paper_author_searcher.cli.pdf \
  --input ../paper-test-must/samples/hinton/hinton_papers.authorship.json --output ../paper-test-must/samples/hinton/hinton_papers.pdf_urls.sample.json \
  --max-papers 5 --timeout 8 --retries 1

# 明确包含待审核论文；Unpaywall 需要真实联系邮箱
PYTHONPATH=src python3 -m paper_author_searcher.cli.pdf \
  --input papers.authorship.json --output papers.pdf_urls.review.json \
  --include-needs-review --require-unpaywall
```

若当前 Python 需要指定可信 CA，可使用现有的 `SSL_CERT_FILE=/etc/ssl/cert.pem` 配置（该文件必须存在）。TLS 验证保持开启。未提供真实邮箱时 Unpaywall 单独标记 disabled，其他来源继续执行；支持现有的 `OPENALEX_API_KEY` 和 `SEMANTIC_SCHOLAR_API_KEY`。

## 检索来源

检索依次使用保存的 OpenAlex/Semantic Scholar PDF 元数据、OpenAlex locations、Semantic Scholar openAccessPdf、Crossref 已登记全文链接、Unpaywall 全部 OA locations、arXiv 官方 API、配置的期刊公开目录，以及公开学术页面。Semantic Scholar 优先批量查询已知论文标识符；缺 DOI 时可查询标题，但标题相同不足以验证身份。期刊目录只用于发现精确题名的引用页面，PDF 地址来自页面实际 metadata。Unpaywall、Crossref 和 OpenAlex 返回的落地页继续交给公开页面 Provider。

公开页面支持 `citation_pdf_url`、Dublin Core/EPrints 引用元数据、明确的 alternate PDF 和 ScholarlyArticle JSON-LD，也可通过重复的 `--bibliography-url` 指定作者、学校或会议公开目录。目录标题链接只提供发现线索，单凭标题不会成为已验证 PDF。原始元数据提供的旧 HTTP 路径优先尝试相同路径的 HTTPS；公开机构/学术来源中 OA 标记缺失的直接链接，必须有论文身份绑定且匿名响应确实为 PDF。原始 OA 标记及未知许可仍保留。不会通过 DOI 猜测 PDF 路径；网页、登录页和 HTML 中间页不算 PDF，不绕过访问限制。

邮箱通过 `UNPAYWALL_EMAIL` 环境变量读取，不写入结果 JSON。使用 `--require-unpaywall` 可在未配置或格式不合法时提前停止，避免复测时意外禁用该来源；代码不能验证邮箱归属，需要用户提供自己的真实联系邮箱。安全设置方式、相同论文范围的复测与离线对比命令见 [PDF 来源发现](pdf_discovery.md)。

## 探测上限与验证

`PDFConfig` 集中管理探测字节数、HTML 元数据读取上限、重定向上限、arXiv 标题查询和请求间隔、期刊目录及版本偏好。`HttpConfig` 继续管理超时、限流重试和最大退避。每次 PDF 探测只读取最多 1024 字节并关闭响应，即使服务器忽略 Range 也不会读取完整正文或写入 PDF 文件；引用页面先读取 65536 字节，确有截断时最多扩展一次到 1 MiB，期刊目录预览上限默认 2 MiB。共享页面缓存同时受 TTL、条目数及总字节数限制。

候选 `validation_status=verified` 同时要求公开来源依据、论文元数据一致性和实际 `%PDF-` 文件头。Content-Type 为 PDF 或 URL 以 `.pdf` 结尾均不能单独确认。DOI 冲突、已知 arXiv 标识符冲突或来源标题冲突会阻止确认；没有稳定标识符时要求标准化标题、年份和完整作者署名集合相符。PDF 文件头确认但论文对应关系不足时保留 unverified。版本来自来源明确标注，缺失为 unknown；同一 URL 的版本声明冲突也保留 unknown，各来源声明仍保存在 provenance。

## 输出与统计

输出模型 `PaperPDFResult` 保留 title、doi、ownership_status、ownership_evidence、pdf_candidates、best_pdf_url、pdf_source、access_status、validation_status、version_type、error_details 和各 Provider 状态。每个候选保留来源论文 ID、元数据 URL/DOI、版本、许可证（缺失保持 null）、最终重定向 URL、HTTP 状态、MIME、实际读取字节数、文件头确认标记和身份/验证证据。不同版本的 URL 全部保留，同一 URL 的多来源证据合并；best_pdf_url 只从已验证候选中选取。

`statistics` 单独报告归档总条目、实际处理、有效 PDF 论文、仅未验证链接、未找到、归属跳过、数量限制导致的未处理、检索不完整及不同已验证 URL 数量。各来源贡献按每篇论文的唯一候选 URL 计数，同一个 URL 可由多个来源贡献，因此来源数量不可简单相加；还报告查询状态和错误次数。归档总数或处理数量不代表教授全部论文/可用 PDF 的数量。

有 Provider 查询失败时 CLI 返回 2，但仍保存已取得结果和完整错误信息；数量限制明确标记未处理条目。`not_found` 只表示本次处理未确认有效或待验证 PDF 候选，须结合来源失败状态查看，不能作为论文不存在公开 PDF 的证明。

## 限制

轻量验证确认的是“文件头为 PDF，公开来源元数据将此资源与论文关联”。不解析 PDF 全文，无法独立证明完整文件可解析、内部内容完全一致或法律上的再分发权限；许可证原样保留。已有链接可能失效，公开接口可能限流或缺少作者版本，检索不能保证覆盖所有公开版本。

接口依据：[OpenAlex Locations](https://help.openalex.org/data/locations/)、[Semantic Scholar 官方 API](https://api.semanticscholar.org/api-docs/graph)、[Unpaywall 数据字段](https://unpaywall.org/data-format)、[Unpaywall API 邮箱要求](https://data.unpaywall.org/products/api)、[arXiv API](https://info.arxiv.org/help/api/user-manual.html)。

本次小规模真实测试及访问限制见 [测试报告](../reports/pdf_url_sample.md)；全部候选、HTTP 文件头观察和未处理状态见 URL JSON（已移出仓库，位于 `paper-test-must/samples/hinton/hinton_papers.pdf_urls.sample.json`）。在归档顺序的前 5 篇已确认归属论文中，1 个 URL 同时通过文件头及论文元数据核对；该样本不能用于推算全部条目的 PDF 覆盖率。

PDF 内容验证、文档类型识别、来源冷却和相同范围复测见 [PDF 内容验证与来源完整性](pdf_validation.md)。本轮 20 篇待审核及全部 47 份现有文件的离线复验、完整联网复测命令和限制见 [验证报告](../reports/performance/pdf_validation.md)。后续 100 篇真实运行的来源完整率下降诊断、重定向缓存修复和独立 `search_sufficiently_satisfied` 指标见 [搜索完整性报告](../reports/performance/pdf_search_completion.md)。该指标确认实际取得验证全文，与所有来源是否完成查询分开统计。
