# 分层评测集与上线门槛

`paper-benchmark` 把"随便给一个研究者，系统能找全、找准他的论文吗"变成可重复、可自动执行的测量：对评测集中每个人运行与生产相同的检索流程，按资历、领域、姓名类型分层统计召回率、精度和全文获取率，再按配置的门槛给出是否可以上线。

数据集说明见 [data/benchmark/v1/README.md](../data/benchmark/v1/README.md)。

## 流程

```bash
python3 -m pip install -e '.[download]'

# 0. 检查 roster、快照与真值状态（离线；CI 每次提交都会跑）
paper-benchmark validate

# 1. 抓取 ORCID works，并为缺少标识符的条目记录 Crossref / arXiv 标题查询候选（联网）
paper-benchmark fetch-sources            # 可加 --person ID 重复使用

# 2. 由快照离线构建待审核真值 truth/<id>.pending.json
paper-benchmark build-truth

# 3. 人工审核：导出表格 → 填写 decision → 导入
paper-benchmark review-export --person cs_huan_zhang --output review.csv
paper-benchmark review-import --person cs_huan_zhang --sheet review.csv \
  --reviewer '你的名字' --complete-positive-list

# 4. 对每个人运行检索系统（联网，耗时长；可断点续跑）
paper-benchmark run --run-id 2026-10-18

# 5. 分层评分并执行门槛（离线）
paper-benchmark score --run-id 2026-10-18 --enforce-gate
```

### 离线重放

改了身份解析或归属规则后，可以先离线验证效果，不花 API 费用，也不发出任何网络请求：

```bash
paper-benchmark run --replay-from calibrated-with_hints-20261018 --input-profile with_hints --run-id replay-x
paper-benchmark score --run-id replay-x
```

重放时：
- 所有元数据请求都从 `run.http_cache_dir` 读取；缓存里没有的请求直接失败，不会联网。主页、POST、下载请求一律拒绝。
- 锚点使用原 run 保存的结果。
- 只跑到 metadata 阶段，因为 PDF 阶段需要的页面和文件不在缓存里。

改动锚点发现（主页解析）后，无法离线验证，必须联网重跑。

原 run 用过本地 DBLP 索引（`output/dblp/dblp.sqlite`）时，重放也要设置 `PAPER_SEARCH__DBLP_DUMP__INDEX_PATH` 指向它，否则 DBLP 请求在缓存里找不到，结果会和原 run 不一样。

### 判定一致性门禁（重构和提速用）

重构、提速这类改动不应该改变任何判定。门禁用离线重放检查这一点：用旧代码和新代码重放同一个已录制的 run，逐人比较身份、run 状态和每篇论文的归属状态，任何差异都算失败。论文按合并进它的来源记录识别，所以去重结果变化也会被发现。

```bash
# 1. 在可信代码（通常是 main）上生成基线，只需一次
PAPER_SEARCH__DBLP_DUMP__INDEX_PATH=output/dblp/dblp.sqlite \
  paper-benchmark run --replay-from career-chg --input-profile minimal --run-id replay-main
paper-benchmark decisions --run-id replay-main --output baseline.decisions.json

# 2. 在要检查的分支上：重放、比较、打印耗时，完全一致才返回 0
python scripts/ci/replay_gate.py --replay-from career-chg --baseline baseline.decisions.json
```

- `paper-benchmark decisions`：把一个 run 的判定存成小 JSON（32 人约 1.5 MB），基线不必保留整个 run 目录。
- `paper-benchmark compare-decisions --baseline A --candidate B`：A、B 可以是 run ID 或 decisions 文件；列出每个人的身份变化、verified 新增与丢失、其他状态变化，有差异时退出码为 1。
- `replay_gate.py` 默认使用 `output/dblp/dblp.sqlite`（存在时），跑完会删除重放出的 run 目录（约 0.6–0.9 GB），用 `--keep` 保留。

录制缓存有几 GB，只在录制它的机器上，所以这个门禁不在 GitHub 托管的 runner 上跑，而是在装有录制数据的自托管 runner 上，对每个 PR 运行（`.github/workflows/replay-gate.yml`）。有意改变判定的改动（比如提高召回）门禁一定会失败，这时用 `paper-benchmark score` 给两个 run 打分，确认差异是改进再合并。

### 耗时

每份 workflow `report.json` 都有 `timings`：总耗时、各阶段与关键步骤（身份候选搜索、锚点发现、论文查询、按姓名搜索、去重、归属判定等）的秒数，以及本次运行按 host 统计的 HTTP 调用数、失败数和秒数。`paper-benchmark timings --run-id X` 汇总整个 run，并列出最慢的人；`index.json` 里每人还有 `elapsed_seconds`。重放时请求都走缓存，HTTP 秒数不代表网络耗时，要看网络耗时需比较联网 run。

需要可信 CA 时沿用 `SSL_CERT_FILE=/etc/ssl/cert.pem`。`CROSSREF_MAILTO` 可选，用于 Crossref polite pool；其余 API key 与主流程相同。

## 配置（`data/benchmark/v1/benchmark.json`）

| 字段 | 作用 |
|---|---|
| `year_window` | 默认时间窗；roster 中个人可用 `year_start`/`year_end` 覆盖 |
| `dimensions` | 分层维度及允许值；每人必须在每个维度有一个值 |
| `publication_types` | 计入真值的出版类型；未知类型是否计入 |
| `publication_types.non_publication_output` | 系统输出中不是出版物的 `paper_type`（数据集、书评、学位论文、会议摘要），评分时既不算命中也不算误检 |
| `reported_metrics` | 报告的指标，取自现有 `PerformanceEvaluator` 的指标名 |
| `gate.rules` | 门槛：指标 + 最小值（0–1）；可选 `aggregation`（`macro`/`micro`）覆盖默认口径，同一指标可以同时要求两种口径 |
| `gate.minimum_truth_positives` | 按指标设置每人至少多少篇真值；不够的人不进入该指标的分层汇总（报告里会列出），避免一两篇论文让宏平均大幅波动 |
| `gate.aggregation` | `macro`（每人等权平均，默认）或 `micro`（汇总分子分母） |
| `gate.minimum_people_per_stratum` | 每个分层至少有多少人有可计算的值，否则该层判失败 |
| `gate.require_human_review` | 有人未经人工审核时，达标也只给 `not_certifiable` |
| `provisional_closed_world` | 未审核时把来源并集当完整列表计算精度（是精度下界） |
| `version_grouping_year_tolerance` / `metadata_year_tolerance` | 版本归并、元数据匹配允许的年份差 |
| `identity.minimum_truth_share` | `identity_precision` 判定"认对了人"所需的真值覆盖比例 |
| `run` | 传给检索流程的参数（与 `paper-search` 同名） |
| `sources` | ORCID / Crossref / arXiv 地址、批量大小、请求间隔 |

当前门槛：每个分层 `identity_precision = 1.00`（不能认错人），`verified_paper_recall ≥ 0.80`、`author_match_precision ≥ 0.95`，宏平均（每人等权）和汇总口径（按论文汇总）都要达到；召回类指标只统计真值至少 3 篇的人；每层至少 2 人。全文获取率（`actual_download_coverage`）只报告，不设门槛。

## 指标口径

来源为 `manual` 的真值条目是从公开页面抄录的，作者名单常被缩写或截断，所以这类条目不要求作者集合完全一致：标题相同或相近（可省略副标题、容许错字）、年份相差不超过 2 年、至少一位作者相符，就算匹配；DOI 不同视为同一论文的其他版本。其他来源的条目仍按原规则严格匹配。

指标由现有 `PerformanceEvaluator` 计算，基准层只做逐人调用和分层聚合：

- `identity_resolution_rate`：系统自己确认了身份的人所占比例。
- `identity_precision`：在系统确认了身份的人里，确认对了的比例。选中的身份所收集的论文至少覆盖本人真值的 `identity.minimum_truth_share`（默认 20%）才算对。认成同名者比不确认更糟，所以门槛要求每个分层都是 100%。
- `verified_paper_recall`：真值论文中被系统判为 verified 的比例。**门槛用的召回率。**
- `paper_discovery_recall`：真值论文中被系统检索到的比例（无论归属状态），用于区分"没搜到"和"搜到了但不敢确认"。
- `author_match_precision`：系统 verified 的论文里确实属于本人的比例。需要真值标注 `not_belongs`，或审核者确认 `complete_positive_list`。
- `actual_download_coverage`：真值论文中下载到、且格式与内容身份都校验通过的全文比例（需 `run.mode=full`）。

检索失败、身份需要人工确认而中止的人，召回按 0 计（系统没有交付结果），不会被排除在分母之外。

## 真值规则

- 每条真值是一个出版物：同一 DOI / arXiv ID / PMID，或规范化标题相同且年份相差不超过容差的预印本与正式版合并为一条，找到任一版本即算找到。
- 主页上的会议简称（如 "NeurIPS 2025"）保存在 `listed_venue`，不放入 `venue`，避免与数据库全称不一致导致匹配失败。
- 无标识符的条目额外生成 ±1 年的元数据别名，兼容数据库按 arXiv 年份或出版年份记录的差异。
- Vancouver 格式署名（`Trinidad MI`）统一为 `M. I. Trinidad`，与首字母兼容匹配一致。
- 署名中找不到目标姓名的条目 `proposed_ownership=uncertain` 并标记 `target_name_not_found_in_byline`。

## 审核表

`review-export` 生成的 CSV 中 `decision` 填 `belongs` / `not_belongs` / `uncertain`：

- `uncertain` 表示不计分（例如范围外的海报、无法确认的版本）。
- 审核者发现来源漏掉的论文，可新增一行：`evaluation_id` 留空，填 `decision=belongs`、`title`、`year`、`authors`（`; ` 分隔）、可选 `dois` / `external_ids`、`reviewer_note`。
- 只有全部条目都有决定，才会写成正式真值 `truth/<id>.json`；`--allow-pending` 只保存进度。
- `--complete-positive-list` 只在审核者确认窗口内论文已列全时使用，它使未在真值中的 verified 输出成为可计算的误检。
- 复核 AI 审核：`review-export --prefill-from assistant_reviewed` 会把 AI 的决定预先填好，人工只需修改不同意的行，再用默认的 `--review-state human_reviewed` 导入。导入后整份真值记为人工审核，所以每一行都要真正看过。

## 门槛结果与退出码

| 状态 | 退出码 | 含义 |
|---|---:|---|
| `passed` | 0 | 所有分层、所有规则达标，且全部真值经人工审核 |
| `failed` | 1 | 至少一个分层未达标、指标不可用或人数不足 |
| `not_certifiable` | 3 | 数值达标，但有人仍是 provisional 真值 |

`score` 不加 `--enforce-gate` 时总是返回 0，只写报告：`reports/benchmark/<run-id>.scores.json` 与 `.md`。

## CI

- `.github/workflows/ci.yml`：每次 push / PR 运行全部离线单元测试和 `paper-benchmark validate`。
- 判定一致性门禁（`scripts/ci/replay_gate.py`）依赖本地录制缓存，在本地运行，见上文"判定一致性门禁"。
- `.github/workflows/benchmark.yml`：只能手动触发，联网运行完整评测并执行门槛。有意义的分数需要数据源密钥和本地 DBLP、书目库索引，要先在仓库 secrets 和工作流里配好，否则大部分数据源会拒绝或限流，分数不反映代码质量；未审核导致的 `not_certifiable` 平时只给 warning，手动触发时勾选 `release` 则视为失败。报告作为 artifact 上传。

联网评测依赖外部 API 的可用性和限流，同一代码两次运行的数字可能有小幅波动；比较版本时应使用同一套真值。

## 扩展

- 新的书目格式：实现 `TruthSource` 协议（`kind` + `load(person, spec, snapshot_dir)`），注册到 `TruthSourceRegistry`。
- 新的被测系统：实现 `SearchSystem` 协议（`name` + `search(person, output_dir)`，返回工作流报告），传给 `BenchmarkRunner`。
- 新的分层维度或门槛：只改 `benchmark.json` 和 roster，不改代码。
- 采集辅助脚本在 `scripts/benchmark/`：`lines_to_snapshot.py`（人工整理行 → 快照）、`citation_lines.py`、`acl_citations.py`、`stanford_profile.py`、`page_bib.py`、`orcid_candidates.py`。
