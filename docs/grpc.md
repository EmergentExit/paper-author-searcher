# gRPC API（v1）

除了 [REST API](api.md)，搜索服务也提供 gRPC 接口。两者共用同一个任务库、同一套请求校验和同一份结果视图：同一个 API Key 在 REST 上发起的搜索，在 gRPC 上也查得到，反过来也一样。

- 接口文件：[`proto/paper_author_searcher/rpc/v1/paper_search.proto`](../proto/paper_author_searcher/rpc/v1/paper_search.proto)，包名 `paper_searcher.v1`，服务名 `PaperSearch`。
- 公网地址：`grpc.paper-searcher.fyi:443`，走 TLS，证书由 Cloudflare 提供，客户端用系统自带的根证书即可。
- 健康检查：标准的 `grpc.health.v1.Health`（服务名为空或 `paper_searcher.v1.PaperSearch`）。

## 鉴权与幂等

每次调用都要带 metadata：`authorization: Bearer <API Key>`。Key 和 REST 通用，由 `paper-search-api create-key --name <名字>` 生成。

`CreateSearch` 和 `ConfirmSearch` 可以带 `idempotency_key`（1–255 个字符）。网络出错后用同一个 key 和同一个请求重试，返回的是第一次创建的那个搜索，不会重复搜索。

## 调用

| 方法 | 作用 |
|---|---|
| `CreateSearch` | 发起搜索，返回状态为 `SEARCH_STATUS_QUEUED` 的搜索 |
| `GetSearch` | 查看状态、进度；成功后带结果。`include_needs_review=true` 时也返回待确认的论文 |
| `ConfirmSearch` | 搜索停在 `NEEDS_CONFIRMATION` 时，从 `candidates[].selections` 里选一个人，搜索会继续 |
| `CancelSearch` | 结束排队中、运行中或等待确认的搜索 |
| `GetAgentResult` | cold-email agent 可以引用的论文（`papers`）和不可引用的（`withheld`），见 [输出契约](agent-contract.md) |
| `DownloadPaperPdf` | 下载结果里 `full_text.status = FILE` 的论文 PDF，分块流式返回 |

一次搜索通常要几分钟。请每隔几秒调用一次 `GetSearch` 轮询，直到状态变成 `SUCCEEDED`、`FAILED`、`NEEDS_CONFIRMATION` 或 `CANCELLED`。不要用一个长时间不返回的调用去等结果：经过 Cloudflare 的单次调用最多约 100 秒。

## 错误

使用标准 gRPC 状态码。trailing metadata 里的 `error-code` 是服务自己的错误码（和 REST 的 `error.code` 一致）。

| 状态码 | 情况 |
|---|---|
| `UNAUTHENTICATED` | 没带 Key，或 Key 无效 |
| `INVALID_ARGUMENT` | 请求不符合 search-request 规则（详情在状态消息里），候选人选错，或幂等 key 被换了请求复用 |
| `NOT_FOUND` | 没有这个搜索（别的 Key 的搜索也算作不存在），或没有这篇 PDF |
| `FAILED_PRECONDITION` | 搜索当前的状态不允许这个操作，例如已结束的搜索不能取消 |
| `ABORTED` | 同一个幂等 key 的第一次请求还没处理完 |
| `RESOURCE_EXHAUSTED` | 超过同时进行的搜索数或每小时搜索数；`retry-after-seconds` 说明多久后可以重试 |
| `INTERNAL` | 服务端出错 |

## 示例

用 [grpcurl](https://github.com/fullstorydev/grpcurl)：

```bash
grpcurl -proto proto/paper_author_searcher/rpc/v1/paper_search.proto \
  -H "authorization: Bearer $PAPER_SEARCH_KEY" \
  -d '{"search": {"name": "Alice Gao", "university": "University of Waterloo", "mode": "MODE_PDF_URLS"}}' \
  grpc.paper-searcher.fyi:443 paper_searcher.v1.PaperSearch/CreateSearch
```

Python（先 `pip install grpcio grpcio-tools`，再用 `python -m grpc_tools.protoc` 按 proto 文件生成代码）：

```python
import time

import grpc
from paper_author_searcher.rpc.v1 import paper_search_pb2 as pb, paper_search_pb2_grpc as pb_grpc

channel = grpc.secure_channel('grpc.paper-searcher.fyi:443', grpc.ssl_channel_credentials())
stub = pb_grpc.PaperSearchStub(channel)
auth = (('authorization', 'Bearer ' + key),)

search = stub.CreateSearch(
    pb.CreateSearchRequest(search=pb.SearchRequest(name='Alice Gao', university='University of Waterloo')),
    metadata=auth,
)
while search.status in (pb.SEARCH_STATUS_QUEUED, pb.SEARCH_STATUS_RUNNING):
    time.sleep(5)
    search = stub.GetSearch(pb.GetSearchRequest(search_id=search.id), metadata=auth)
agent = stub.GetAgentResult(pb.GetAgentResultRequest(search_id=search.id), metadata=auth)
```

## 维护

- 改了 `.proto` 后，运行 `python scripts/ci/generate_grpc.py` 重新生成 `src/paper_author_searcher/rpc/v1/` 下的代码，并一起提交。CI 会检查生成代码和 proto 是否一致。
- 新增字段只能用新的字段编号，不能改动或复用已有编号，这样旧客户端不受影响。枚举值要用 `rest_value` 写明对应的 REST 取值。
- 启动 gRPC 服务需要安装 grpc 依赖：`pip install '.[grpc]'`。启动命令是 `paper-search-api serve-grpc`，`--check` 只检查一个已在运行的服务是否正常应答。部署方法见 [部署与运维](deployment.md#grpc-与-cloudflare-tunnel)。
