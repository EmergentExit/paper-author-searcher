# MCP 服务（给 AI 助手用）

MCP（Model Context Protocol）是让 AI 助手调用外部工具的标准协议。接上之后，Claude 这类助手可以直接替你搜一位研究者的论文：发起搜索、等结果、遇到同名的人先问你是哪一位，最后只引用确认过的论文。

MCP 服务和 [REST API](api.md)、[gRPC API](grpc.md) 共用同一个任务库、同一套 API Key、同一套请求校验和结果视图。同一个 Key 在哪个接口发起的搜索，在另外两个接口上都查得到。

- 地址：`https://paper-searcher.fyi/mcp`（Streamable HTTP，无状态，以 JSON 应答）。
- 实现：官方 MCP Python SDK（`mcp` 2.x），同时支持新版协议（2026-07-28）和握手时代的旧版本。
- 鉴权：请求头带 `Authorization: Bearer <API Key>`，Key 和 REST 通用。不支持 OAuth 登录，所以需要客户端能自己加请求头。

## 工具

| 工具 | 作用 |
|---|---|
| `start_search` | 发起搜索：姓名、现在的学校，可选院系、以前的学校、ORCID、个人主页、已知论文、模式。会立刻返回 `search_id` |
| `get_search_status` | 查看状态和下一步该做什么。`wait_seconds` 最多 50：搜索还在跑时，最多等这么久，结束或需要确认时提前返回 |
| `get_citable_papers` | 可以引用的论文，以及不可引用的论文（withheld），内容同 [输出契约](agent-contract.md) |
| `get_full_result` | 完整结果，包括每篇论文的证据和全文链接；用来查看，不用来引用 |
| `confirm_researcher` | 有同名者时，用户说明是哪一位后，传入那位候选人的 selection，搜索继续 |
| `cancel_search` | 结束搜索 |

每次返回都带 `next_step`，用一句话告诉助手下一步做什么。服务端也给助手提供了使用说明（instructions）：轮询要用 `wait_seconds`；遇到同名者必须先问用户，不能自己猜；只能引用 `get_citable_papers` 里的论文。

## 接入

Claude Code：

```bash
claude mcp add --transport http paper-search https://paper-searcher.fyi/mcp --header "Authorization: Bearer $PAPER_SEARCH_KEY"
```

其他能自己加请求头的 MCP 客户端，同样填地址和 `Authorization` 请求头即可。Python 程序可以用官方 SDK：

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

http = httpx2.AsyncClient(headers={'Authorization': f'Bearer {key}'}, timeout=90)
async with Client(streamable_http_client('https://paper-searcher.fyi/mcp', http_client=http)) as client:
    started = await client.call_tool('start_search', {'name': 'Alice Gao', 'university': 'University of Waterloo'})
```

## 运维

- 启动：`paper-search-api serve-mcp`。需要安装 mcp 依赖：`pip install '.[mcp]'`。Docker 里用 `--profile mcp` 启动，服务在 127.0.0.1:8090。
- 配置项：
  - `PAPER_SEARCH__SERVICE__MCP_PUBLIC_URL`：客户端访问的地址。
  - `PAPER_SEARCH__SERVICE__MCP_ALLOWED_HOSTS`：服务接受的 Host，用来防 DNS rebinding。
  - `MCP_MAXIMUM_WAIT_SECONDS`：一次状态调用最多等多久，要小于 Cloudflare 的约 100 秒单请求上限。
- Cloudflare Tunnel：在 `paper-searcher.fyi` 的规则之前，加一条按路径转发的规则：`path: ^/(mcp|\.well-known/oauth-protected-resource)`，转到 `http://127.0.0.1:8090`。这条规则不需要 TLS，也不需要 HTTP/2。
