# PDF Downloader

输入为 PDF Resolver 输出的 `{ "papers": [...] }` JSON。只下载候选 `validation_status=verified` 且 `identity_status=verified` 的 HTTP(S) URL；普通网页、孤立的 best_pdf_url 或未确认链接不作为下载依据。默认仅处理已确认的论文归属，`--include-needs-review` 不改变原有 Ownership Validator 状态。

```bash
python3 -m pip install -e '.[download]'
python3 -m paper_author_searcher.cli.download \
  --input papers.pdf_urls.json --output-dir downloaded_papers \
  --max-downloads 3 --timeout 15 --retries 2 --max-file-mb 50
```

```bash
# 明确允许待审核归属；候选 URL 仍须已验证
python3 -m paper_author_searcher.cli.download \
  --input papers.pdf_urls.json --output-dir downloaded_papers \
  --include-needs-review
```

已安装项目时也可运行 `pdf-download`。使用本项目 `.venv` 时，将 `python3` 替换为 `.venv/bin/python`；当前环境需要的可信 CA 配置可继续使用 `SSL_CERT_FILE=/etc/ssl/cert.pem`。下载依赖（`pypdf`、`pdfminer.six`）只在 `download` extra 中，原有其他阶段不必安装。

## 传输安全

HTTP 流式写入临时 `.part` 文件，默认限制单文件 128 MiB，逐块计算 SHA-256，核对实际大小和完整响应。重定向和 DNS 结果逐一检查，禁止本地/内网目标、凭据 URL 与非配置端口；连接固定到已经检查的公网 IP，同时保留原主机名的 TLS 验证。下载器不使用环境代理，不处理登录或验证码。

文件头、EOF/cross-reference 及所有页面内容流通过严格 PDF 解析后才保存为 `.pdf`。文件格式与论文内容身份分别记录：内容中的 DOI/标题一致，或没有 DOI 时独特标题与完整作者署名一致，才确认身份；证据不足进入 `needs_review/`，明确的不同论文进入 `quarantine/`。程序不执行 PDF 的 JavaScript、动作或嵌入文件。

文件名清理跨平台非法字符并带稳定论文哈希，按字节截断长标题，采用独占文件名预留避免覆盖。每篇论文完成后原子更新 `manifest.json`，保留所有输入条目、失败、跳过、候选尝试与历史结果。重跑会重新检查文件 SHA-256 和内容解析，并跳过完整文件；不同 URL 的同内容 PDF 共用一个本地文件，仍逐论文核对身份。没有实现 Range 分段续传，中断的未完成下载从零重新开始。

## 数量限制与退出码

`--max-downloads` 限制本次尝试的论文条目数，已完成文件的复用不占此额度。候选失败会继续其他已验证候选和其他论文。CLI 返回 0 表示本次无失败/内容待审核，2 表示存在失败或内容待审核且 manifest 已保存，130 表示用户中断；即使返回 0，也需查看归属、URL 和数量限制导致的跳过记录。

新 PDF Resolver 报告保留 authors/year/source/source_paper_id/external_ids。旧报告缺失的作者、年份不推测补齐，文件名使用 `unknown_year`。实际下载与重跑结果见 [真实测试报告](../reports/pdf_download_sample.md)。

## 模块职责

- `transport/http.py` 复用原有请求/重试机制，流式传输、响应大小及完整性校验。
- `transport/network_safety.py` 对每个请求和重定向检查 DNS，固定连接到已检查的公网地址；原 Host/SNI 和 TLS 验证保留。环境代理关闭，默认只允许 80/443 端口。
- `fulltext/pdf_file_validation.py` 在带超时的子进程里运行严格 pypdf 解析，区分文件结构和论文身份；不执行 PDF 中的代码。
- `fulltext/pdf_downloader.py` 管理归属门控、候选回退、文件命名、内容去重、重跑复用及原子 manifest。
- `cli/download.py` 处理参数和输出，不调用 Professor Resolver、Paper Collector 或 PDF Providers。

## 配置

`DownloadConfig` 集中管理文件上限（默认 128 MiB）、块大小（64 KiB）、单次传输总时限（300 秒）、重定向、允许端口、文件名长度（180 UTF-8 字节）、解析超时（30 秒）、页数及解压后页面内容上限。下载过程中使用有界读取，慢速响应也会检查总时限；HTTP 单次阻塞仍受 `HttpConfig.timeout` 约束。重试从头覆盖当前临时文件，不拼接未经确认的响应片段。

PDF 解析子进程在支持 `resource.RLIMIT_AS` 的环境限制地址空间（默认 768 MiB）；其他环境保留文件/页面/内容限制与子进程超时。子进程隔离用于限制解析资源，不是执行不可信代码的沙箱。项目只调用 PDF 解析与文本提取接口，不运行文档动作、脚本或嵌入程序。

可通过 CLI 设置数量、HTTP 超时、重试、文件上限与归属 opt-in；其他策略通过 `DownloadConfig` 注入。解析依赖 [pypdf 5.9.0](https://pypi.org/project/pypdf/5.9.0/) 支持 Python 3.8；用严格解析防止仅文件头相符的垃圾或截断文件被接受，参考 [PdfReader](https://pypdf.readthedocs.io/en/5.9.0/modules/PdfReader.html) 和 [文本提取说明](https://pypdf.readthedocs.io/en/5.9.0/user/extract-text.html)。

## 文件内容判定

`format_status` 为 valid/invalid/unverified/not_checked。valid 要求大小范围、文件头、尾部 startxref/EOF、严格 PDF 解析、正页数以及页面内容流解析均通过。无法完成解析的文件不记作成功，临时文件清理；格式错误和解析资源不足应结合错误详情区分。

`identity_status` 为 verified/needs_review/rejected/not_checked。确认依据是下载文件自己的标题与 DOI，或缺失 DOI 时独特标题加完整目标作者署名。只看到 DOI 引用或标题片段不足以自动确认，明确的标题元数据冲突保守审核。仅有不同 DOI 也可能是版本差异，不能单独拒绝。判断仅使用前两页文本和 PDF 标题元数据；扫描件、文本编码异常、封面或版本差异可能需要人工确认，不做 OCR，不把内容核验等同于人工 Ground Truth。

所有内容判定都保存在 `validation`：页数、提取的标题/DOI、证据、错误及文档动作观察。`ownership_status` 来自输入报告，PDF 身份确认不会替代教授归属验证。来源许可证缺失时保持 null，不推断再分发权限。

如果 URL 已经通过验证，其公开出版页同时提供与目标标题、年份和完整作者集合一致的 citation metadata，下载验证可以使用该页面的原始署名作为补充。仍须在 PDF 前部找到全部这些作者及独特标题；DOI 或标题元数据冲突仍保守审核。补充证据单独记录，不改写输入作者、不降低作者归属阈值，也不使用评估 Ground Truth 中的 PDF 地址。

## Manifest 与重跑

`manifest.json` 包含 schema_version、时间、本次 run_complete/statistics 和 papers。每个条目保留 title/authors/year/doi/source/pdf_url/local_filename/download_status/validation_status/ownership_status/file_size/sha256/error_details。额外保留文件格式/身份状态、实际最终 URL、PDF 来源版本与公开依据、候选尝试、来源证据和历史结果。失败 URL 的 HTTP 状态、实际请求 URL 和错误保存在 attempts；输入文件仍保持原样。

| download_status | 含义 |
| --- | --- |
| downloaded | 新保存的可解析 PDF；身份可为 verified 或 needs_review |
| skipped_existing | 文件 SHA-256、解析与对应关系重新检查后复用 |
| duplicate_content | 已有相同 SHA-256 的文件，仍单独核对当前论文身份 |
| rejected_content | 解析有效但明确属于其他论文，不计为成功 |
| failed | 所有候选均失败或文件不能通过解析 |
| skipped_ownership | 归属未放行或被拒绝 |
| skipped_unverified_url | 没有已验证候选 URL |
| skipped_limit | 本次数量限制，没有实际发起请求 |
| interrupted/not_processed | 未完成的条目；完整文件可在下次重跑恢复 |

统计针对本次输入条目，不代表教授论文总数；其中 downloaded_files 计当前新增的代表文件，verified_papers 计内容确认条目。复用、内容重复、待审核和失败另列。旧输入中不再出现的 manifest 条目也保留，因此 manifest 的总条目数可以大于本次输入数。

每次完成一个实际下载条目后写临时 manifest、fsync 并原子替换。文件也先校验临时内容，再独占预留文件名并原子移动；已有文件不覆盖。若在文件提交和 manifest 写入之间中断，可重新核验稳定文件名恢复结果。源文件 checksum 改变时不盲目信任，也不覆盖用户修改过的文件。损坏的 manifest 不静默替换。

本阶段支持重新运行时跳过完成文件，不实现 HTTP Range 分段续传。正常中断清理临时文件；硬终止进程可能留下隐藏 `.part`，这些文件永远不作为成功 PDF，重跑使用新临时文件。请对同一目录串行运行下载器；当前没有实现多进程 manifest 协同写入。

```text
downloaded_papers/
├── manifest.json
├── <year>_<title>_<stable_hash>.pdf
├── needs_review/      # 文件格式有效，论文对应关系未确认
└── quarantine/        # 明确的错误论文及其可追溯记录
```
