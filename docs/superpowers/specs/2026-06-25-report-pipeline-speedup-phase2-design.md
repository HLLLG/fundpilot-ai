# 日报 / 推荐报告管线提速 · 阶段 2 设计：LLM 流式输出 + 前端骨架卡

**版本：** 2026-06-25
**作者：** hegl + Claude (Opus 4.7)
**前置阶段：** 阶段 1（`docs/superpowers/specs/2026-06-25-report-pipeline-speedup-phase1-design.md`）已完成；本期独立可执行。
**核心目标：** 把用户感知耗时从「等 70~95s 黑盒」降到「**1~3s 首字节 + 渐进显示**」。后端总耗时不变，但 UX 体感降一个数量级。

---

## 1. 背景与实测数据

阶段 1 实测（fast 模式 / 3 持仓 / 真实 DeepSeek V4 Flash + AkShare）：

| 阶段 | 冷启动 | 热启动 |
|---|---|---|
| `fund_data`（NAV+诊断 ×3 并发） | 5.6s | 5.2s |
| `news_prefetch`（3 主题 F3 并发） | 3.2s | 3.5s |
| `news_summarize`（Flash 摘要） | 0.4s | 0.4s |
| **LLM 主调用（DeepSeek V4 Flash）** | **69s** | **46~56s** ← 不可缩减 |
| post_judge / save | <0.1s + 1.8~3.4s | <0.1s + 1.8s |
| **总耗时** | **95s** | **70~78s** |

LLM 主调用 60~70% 的占比已是**模型推理速度的物理下限**——deepseek-v4-flash 输出 ~5000 token × 100~150 tok/s ≈ 50s。Deep 模式（deepseek-v4-pro）输出更长，时间还会增加。

**问题不在「让 LLM 更快」，而在「用户必须看着空白页等 70s」。** 阶段 2 用流式输出 + 前端骨架卡解决这个。

---

## 2. 目标

**用户感知（North Star）：**
- 首字节时间（time to first byte，TTFB）：从 70s → **<3s**（数据装配阶段结束 + LLM 第一个 chunk 到达）
- 报告骨架可见时间：从 70s → **<5s**（先显示「正在分析持仓 A...」「正在分析持仓 B...」三个空卡片）
- 完整报告可见时间：保持 70s 不变（LLM 总输出时间不变）
- 用户能在生成过程中**离开页面**、看到**可视化进度**、并知道「分析中」≠「卡死」

**技术：**
- 后端：DeepSeek chat completion 改用 `stream=true`，逐 chunk 经 SSE 推到前端
- 前端：`ReportPanel` 在等待时展示骨架卡（每只持仓一个 placeholder），流式收到的字段填充进对应卡片
- 兼容性：旧的 `GET /api/jobs/{id}` 轮询路径**保留**作为兜底（非流式连接、超时回退）

---

## 3. 不做（明确范围）

- ❌ 不改 LLM 输出格式（仍是完整 JSON：title / summary / fund_recommendations / caveats）
- ❌ 不改 prompt / 不动 `analysis_facts` schema
- ❌ 不动后端各阶段计算（fund_data / news_prefetch / news_summarize / judge / save 保持现状）
- ❌ 不引入新的 LLM 调用（fast 模式仍 1 次主调用，deep 模式仍 1 次主调用 + 1 次 judge）
- ❌ 不重做 JobStatusFloat（保留作为兜底；新增 streaming UI 与之并存）

---

## 4. 架构

### 4.1 新增 SSE 端点

```
POST /api/analyze/stream
  body: AnalysisRequest（同 /api/analyze/async）
  response: SSE 流，事件类型：
    - { "type": "stage", "stage": "fund_data", "label": "正在拉取净值..." }
    - { "type": "stage", "stage": "news_prefetch", "label": "..." }
    - { "type": "stage", "stage": "generating", "label": "AI 分析中..." }
    - { "type": "skeleton", "fund_codes": ["519674", "015945", "161725"] }
    - { "type": "token", "content": "...JSON 片段..." }
    - { "type": "report_partial", "fund_code": "519674", "patch": {...} }   # 解析出完整字段时
    - { "type": "done", "report_id": "...", "report": {...完整 Report...} }
    - { "type": "error", "message": "..." }
```

理由：
- `stage` 事件 = 现有 `JOB_STAGES` 直接搬过来，前端骨架卡用它显示进度
- `skeleton` 事件 = 在 LLM 开始流之前，告诉前端有几只持仓，每只先建一张空卡
- `token` 事件 = 原始 chunk 透传给前端缓冲（用于"正在生成中..."光标）
- `report_partial` 事件 = 后端在累积的 chunk 里 best-effort 解析 JSON，每当某只持仓的字段集齐时，推送该持仓的 patch
- `done` 事件 = 最终保存完毕，附完整 Report（兼容旧 onComplete）

### 4.2 流式 LLM 调用

复用 `report_chat._iter_stream_completion` 的模式：

```python
# apps/api/app/services/deepseek_streaming.py（新增）

def stream_chat_completion(
    *,
    messages: list[dict],
    model: str,
    max_tokens: int,
    response_format: dict | None = None,
) -> Iterator[str]:
    """逐 chunk yield content 文本。复用 report_chat._iter_stream_completion 的解析逻辑。"""
    settings = get_settings()
    payload = _build_chat_payload(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        tools=None,                        # tool-calling 在流式模式下不开（fast 模式本就不开）
        response_format=response_format,   # JSON mode 仍可，下文讨论
    )
    payload["stream"] = True

    with httpx.stream(
        "POST",
        deepseek_chat_url(settings),
        headers=deepseek_request_headers(settings),
        json=payload,
        timeout=deepseek_timeout(settings),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            chunk = _parse_stream_line(line)
            if chunk:
                yield chunk
```

**`response_format={"type": "json_object"}` 在 streaming 下能用吗？**
- DeepSeek 兼容 OpenAI API：streaming + json_object **能用**，模型仍只输出合法 JSON 文本，只是分块到达。
- 但 chunk 边界**不保证**对齐 JSON 字段——前端拿到的可能是 `{"title": "持仓盘`、`点", "fund_recommend` 这种切碎的字符串。
- 我们在**后端**做 incremental JSON 解析（见 4.3），前端收 `report_partial` 干净的字段，不直接拼 token。

### 4.3 后端增量解析

```python
# apps/api/app/services/streaming_json_parser.py（新增）

class StreamingReportParser:
    """累积 LLM 流式输出的 JSON chunks，在字段就绪时 emit 增量 patch。
    
    用 ijson 或简单状态机做 incremental parse；当 fund_recommendations[i]
    完整闭合后，emit {"fund_code": "...", "patch": {完整持仓对象}}。
    """
    
    def __init__(self) -> None:
        self._buffer = ""
        self._emitted_fund_codes: set[str] = set()
        self._title_emitted = False
        self._summary_emitted = False
    
    def feed(self, chunk: str) -> Iterator[dict]:
        """喂一个 chunk，yield 0 或多个 partial 事件。"""
        self._buffer += chunk
        # 1. 尝试提取 title（顶层字符串字段，第一个就绪）
        if not self._title_emitted:
            title = _try_extract_top_level_string(self._buffer, "title")
            if title is not None:
                self._title_emitted = True
                yield {"type": "report_partial", "field": "title", "value": title}
        # 2. summary 同理
        # 3. fund_recommendations 数组逐元素闭合检测：用一个 brace-depth 计数器
        for patch in self._extract_completed_fund_recs():
            code = patch.get("fund_code")
            if code and code not in self._emitted_fund_codes:
                self._emitted_fund_codes.add(code)
                yield {"type": "report_partial", "field": "fund_recommendation", "value": patch}
        # 4. caveats 数组在 `]` 闭合时一次性 emit
    
    def finalize(self, full_text: str) -> dict:
        """流结束后，最终全文 + 已有解析合并，返回完整 parsed dict（fallback 用现有 _parse_model_json）。"""
        ...
```

**实现策略：**
- 用现有 `deepseek_client._extract_first_json_object` 同款的 brace-depth 状态机
- 不引入新依赖（不用 ijson，标准库够用）
- 增量提取 4 个字段：`title`、`summary`、`fund_recommendations[]`（逐元素）、`caveats[]`（数组结束时整体）
- 失败时不 emit partial，等 `finalize` 用现有 `_parse_model_json` 兜底——保证健壮性

### 4.4 SSE 端点实现

```python
# apps/api/app/main.py 新增端点

@app.post("/api/analyze/stream")
def analyze_stream(request: AnalysisRequest) -> StreamingResponse:
    user_id = require_request_user_id()
    
    def event_stream() -> Iterator[str]:
        for payload in stream_analysis(request, user_id=user_id):
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

```python
# apps/api/app/services/analyze_streaming.py（新增）

def stream_analysis(request: AnalysisRequest, *, user_id: int) -> Iterator[dict]:
    """阶段 2 主入口：把 run_analysis 拆成可流式产出 SSE 事件的版本。"""
    set_request_user_id(user_id)
    try:
        # 1. 复用 run_analysis 的前半段（同步执行，每个阶段发 stage 事件）
        yield {"type": "stage", "stage": "fund_data", "label": "正在拉取净值..."}
        resolved = FundProfileService().resolve_holdings(request.holdings)
        enriched_req = request.model_copy(update={"holdings": resolved})
        risk = evaluate_portfolio_risk(enriched_req.holdings, enriched_req.profile)
        snapshots, nav_trends = FundDataService().get_snapshots_with_nav_trends(enriched_req.holdings)
        
        yield {"type": "stage", "stage": "news_prefetch", "label": "..."}
        runtime = resolve_analysis_runtime(get_settings(), enriched_req.analysis_mode)
        news_service = NewsService()
        market_news = news_service.prefetch_for_holdings(enriched_req.holdings, max_topics=runtime.news_max_topics)
        
        yield {"type": "stage", "stage": "news_summarize", "label": "..."}
        topic_briefs = _build_topic_briefs(market_news, get_settings())
        
        # 2. 装配 bundle + 前端骨架卡
        bundle = prepare_analysis_bundle(enriched_req, risk, snapshots, market_news, topic_briefs, nav_trends, analysis_mode=runtime.mode)
        yield {
            "type": "skeleton",
            "fund_codes": [h.fund_code for h in enriched_req.holdings],
            "fund_names": [h.fund_name for h in enriched_req.holdings],
        }
        
        # 3. 流式调 LLM，增量解析推送 partial
        yield {"type": "stage", "stage": "generating", "label": "AI 分析中（流式）..."}
        messages = _build_messages_for_streaming(enriched_req, risk, snapshots, market_news, topic_briefs, nav_trends, runtime, bundle)
        parser = StreamingReportParser()
        all_chunks: list[str] = []
        
        for chunk in stream_chat_completion(
            messages=messages,
            model=runtime.model,
            max_tokens=get_settings().deepseek_max_tokens_report,
            response_format={"type": "json_object"},
        ):
            all_chunks.append(chunk)
            for partial in parser.feed(chunk):
                yield partial
        
        # 4. 流结束，复用现有 finalize（judge + finalize_recommendations + save）
        full_text = "".join(all_chunks)
        parsed = _parse_model_json(full_text)  # 现有 fallback 解析
        
        yield {"type": "stage", "stage": "judging", "label": "正在审校..."}
        parsed, judge_meta = judge_parsed_report(parsed, enriched_req, risk, snapshots, runtime, facts=bundle.facts)
        # ... 复用 deepseek_client.generate_report 中 progress("judging") 之后的全部逻辑 ...
        report = _build_final_report(parsed, enriched_req, risk, snapshots, market_news, topic_briefs, nav_trends, bundle, judge_meta, runtime)
        
        yield {"type": "stage", "stage": "saving", "label": "正在保存..."}
        save_report(report)
        
        yield {"type": "done", "report_id": report.id, "report": report.model_dump(mode="json")}
    except Exception as exc:
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        reset_request_user_id(...)
```

> **重构责任：** 把 `deepseek_client.generate_report` 中 `progress("judging")` 之后到 `return Report(...)` 之前的所有逻辑（fallback / finalize / caveats / build_pipeline_metadata）抽出为 `_build_final_report(...)` 纯函数，供同步和流式两条路径复用。

### 4.5 前端：流式消费

`apps/web/src/lib/api.ts` 新增：

```typescript
export interface StreamingReportEvents {
  onStage?: (stage: string, label: string) => void;
  onSkeleton?: (fundCodes: string[], fundNames: string[]) => void;
  onPartial?: (field: "title" | "summary" | "fund_recommendation" | "caveats", value: unknown) => void;
  onDone?: (report: Report) => void;
  onError?: (message: string) => void;
}

export function streamAnalysis(
  request: AnalysisRequest,
  events: StreamingReportEvents,
  signal?: AbortSignal,
): Promise<void> {
  // 用 fetch + ReadableStream + EventSource polyfill，或直接 fetch + getReader 解析 SSE。
  // 不能用浏览器原生 EventSource：原生只支持 GET。
  // 推荐：用 @microsoft/fetch-event-source 或手写 SSE parser（很短）。
}
```

`apps/web/src/components/ReportPanel.tsx` 在等待时展示骨架：

```tsx
{state === "streaming" && (
  <div className="space-y-4">
    <StageIndicator stage={stage} label={stageLabel} />
    {fundCodes.length > 0 && (
      <>
        {fundCodes.map(code => (
          <FundRecommendationCard
            key={code}
            data={partialByCode[code] /* 已收到的字段 */}
            placeholder={!partialByCode[code]}   /* 还没收到任何字段时显示骨架 */
          />
        ))}
      </>
    )}
    {title && <h3>{title}</h3>}
    {summary && <p>{summary}</p>}
  </div>
)}
```

`Dashboard.tsx` 决策：默认走 `streamAnalysis`；如果 SSE 失败或网络层不支持，自动回退到现有的 `analyzeAsync` + `JobStatusFloat` 轮询路径。

### 4.6 兼容性与回退

- 保留 `POST /api/analyze/async` + `GET /api/jobs/{id}`：旧客户端不动；移动端小程序、未来批量任务都仍可用
- 默认前端走 `/api/analyze/stream`；连接 5s 内未收到任何 stage 事件 → 视为失败，前端无感切换到 async 轮询
- 报告**最终持久化**与 async 路径完全相同：经 `save_report` 进 `reports` 表。SSE 失败 / 客户端断线 → 后端任务继续（可选）或丢弃（更简单，本期建议丢弃）

---

## 5. 关键技术细节

### 5.1 增量 JSON 解析的边界

**问题：** DeepSeek 流式 + `response_format=json_object` 返回的 chunk 切割位置完全由 token 边界决定，可能切在任何字符上：

```
chunk 1: {"title":"持
chunk 2: 仓盘点","summary
chunk 3: ":"...","fund_recommendations":[{"fund_code":"5196
chunk 4: 74","fund_name":"银河
```

**解决：** 增量解析只在「我看到了 fund_recommendations 数组第 N 个对象的闭合 `}`」时触发 partial emit。状态机大致：

```python
class _BraceDepthScanner:
    """累积字符流，跟踪 brace 深度、string-in-flight，输出已闭合的 fund_recommendations 元素。"""
    
    def __init__(self):
        self.buf = ""
        self.in_recommendations = False  # 进入 fund_recommendations 数组
        self.rec_array_depth = 0          # 数组层深度（嵌套 [ ] 用）
        self.cur_obj_start = None         # 当前对象起始 buf 索引
        self.cur_brace_depth = 0
        self.in_string = False
        self.escaped = False
        self.completed: list[str] = []    # 已闭合的 JSON 对象字符串列表
    
    def feed(self, chunk: str) -> list[dict]:
        emitted: list[dict] = []
        # 简单遍历，注意 string escape 处理
        for i, c in enumerate(chunk, start=len(self.buf)):
            ... # 跟踪状态、维护 brace_depth
            # 当 cur_brace_depth 从 1→0 且 in_recommendations=True：完成一个对象
            obj_str = self.buf[self.cur_obj_start : i+1]
            try:
                emitted.append(json.loads(obj_str))
            except json.JSONDecodeError:
                pass  # 跳过解析失败的，等 finalize 兜底
        self.buf += chunk
        return emitted
```

**精确边界：** 当扫描到 `"fund_recommendations":[` 后进入 `in_recommendations` 状态；扫描到与之配对的 `]` 时退出。期间每个顶层 `{...}` 闭合即为一只持仓。

**测试覆盖（必须）：**
- chunk 切在字符串中间
- chunk 切在 brace 上
- chunk 切在 `\"` 转义符上（in_string + escaped 状态）
- 整个 fund_recommendations 数组在一个 chunk 内到达
- title 在 fund_recommendations 之前 / 之后
- 无效 JSON 不抛异常，只是不 emit

### 5.2 不流式时的回退

LLM 不支持 stream（极小概率）或网络中断时：

```python
try:
    for chunk in stream_chat_completion(...):
        ...
except (httpx.StreamError, httpx.ReadTimeout) as exc:
    # 已累积的 chunks 仍可拼接做最后一次解析
    if all_chunks:
        parsed = _parse_model_json("".join(all_chunks))
        yield {"type": "stage", "stage": "salvage", "label": "流式中断，已收集部分内容..."}
        # 进入正常 finalize 路径，但 caveats 加一条「流式中断，部分字段可能缺失」
    else:
        # 完全没收到，回退到非流式 chat completion 一次性出 JSON
        yield {"type": "stage", "stage": "fallback_non_stream", "label": "..."}
        parsed = _non_stream_chat_fallback(...)
```

### 5.3 LLM Timeout 与重试

- 现有 `deepseek_timeout(settings)` = 300s read timeout
- 流式连接的 timeout 应该是 **chunk 间最大间隔**（如 30s），而不是总耗时
- 用 `httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)`：30s 内未收到任何 chunk 视为断线
- 重试：不在流式路径里做（用户已经看到部分内容），失败就 salvage

### 5.4 报告 ID

现有 `save_report` 完成后 report 对象才有 `id`。流式期间前端不知道 id——`done` 事件返回 id 即可，前端没有立即跳转需求（用户就在 ReportPanel 里看）。

---

## 6. 测试策略

### 6.1 后端单测

新增 `apps/api/tests/test_streaming_json_parser.py`：

```python
def test_parser_emits_fund_rec_when_object_closes():
    parser = StreamingReportParser()
    chunks = [
        '{"title":"t","fund_recommendations":[',
        '{"fund_code":"519674","fund_name":"x","action":"观察"',
        ',"points":["p1"]},{"fund_code":"015945"',
        ',"fund_name":"y","action":"减仓评估","points":["p2"]}]}',
    ]
    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    fund_events = [e for e in events if e.get("field") == "fund_recommendation"]
    assert len(fund_events) == 2
    assert fund_events[0]["value"]["fund_code"] == "519674"
    assert fund_events[1]["value"]["fund_code"] == "015945"

def test_parser_handles_string_escapes():
    """chunk 切在 \" 中间不应误判 string 闭合。"""
    parser = StreamingReportParser()
    chunks = [
        '{"fund_recommendations":[{"fund_code":"x","fund_name":"a\\',
        '"b"}]}',
    ]
    events: list = []
    for c in chunks:
        events.extend(parser.feed(c))
    # 不应崩；可以不 emit（解析失败时）也可以 emit 字符串 a"b
    # 主要验证不抛异常

def test_parser_partial_chunks_no_premature_emit():
    """每个 chunk 都不完整时，不应 emit fund_recommendation。"""
    parser = StreamingReportParser()
    events = list(parser.feed('{"fund_recommendations":[{'))
    assert not [e for e in events if e.get("field") == "fund_recommendation"]
```

新增 `apps/api/tests/test_analyze_streaming.py`：

```python
def test_stream_analysis_emits_skeleton_and_done(monkeypatch):
    """端到端：mock LLM streaming，验证事件顺序与最终 report。"""
    # mock stream_chat_completion 返回预设 chunks
    def fake_stream(*, messages, model, max_tokens, response_format=None):
        yield '{"title":"t","summary":"s","fund_recommendations":['
        yield '{"fund_code":"519674","fund_name":"x","action":"观察","points":["p"]}'
        yield '],"caveats":["c"]}'
    
    monkeypatch.setattr("app.services.analyze_streaming.stream_chat_completion", fake_stream)
    # ... 准备 request ...
    events = list(stream_analysis(request, user_id=1))
    types = [e["type"] for e in events]
    assert "skeleton" in types
    assert "report_partial" in types
    assert types[-1] == "done"

def test_stream_analysis_handles_llm_failure_with_salvage(monkeypatch):
    """LLM 中途断流，已收 chunks 仍能 salvage。"""
    def failing_stream(**kwargs):
        yield '{"title":"t","fund_recommendations":[{"fund_code":"519674"'
        raise httpx.ReadError("connection lost")
    
    monkeypatch.setattr("app.services.analyze_streaming.stream_chat_completion", failing_stream)
    events = list(stream_analysis(request, user_id=1))
    # 应该有 fallback 事件或 error 事件，不应抛出未捕获异常
    assert events[-1]["type"] in {"done", "error"}
```

### 6.2 端到端测试

更新 `apps/api/scripts/smoke_run_analysis.py` 加 `--stream` 选项，对比：

```bash
# 现有
./venv/Scripts/python.exe scripts/smoke_run_analysis.py --mode fast --label baseline

# 新
./venv/Scripts/python.exe scripts/smoke_run_analysis.py --mode fast --label stream --stream
```

输出对比：
- 首字节时间（time to first byte，对应第一个 `stage` / `skeleton` / `partial` 事件）
- 首只持仓 partial 时间（用户首次看到完整的一条建议）
- 总耗时（与 baseline 接近，不应明显劣化）

### 6.3 前端测试

`apps/web/src/components/ReportPanel.test.tsx`（vitest）：
- mock streamAnalysis 推 stage / skeleton / partial / done 事件
- 验证 stage label 显示
- 验证 skeleton 卡片渲染（数量等于 fund_codes 长度）
- 验证 partial 到达时卡片渲染对应字段
- 验证 done 事件后切到完整 Report 视图

---

## 7. 文件清单

### 后端新增

| 路径 | 责任 | 行数估算 |
|---|---|---|
| `apps/api/app/services/streaming_json_parser.py` | `StreamingReportParser`（brace-depth 状态机 + 字段提取） | ~150 |
| `apps/api/app/services/deepseek_streaming.py` | `stream_chat_completion` 流式 LLM 调用 | ~50 |
| `apps/api/app/services/analyze_streaming.py` | `stream_analysis` 端到端 SSE 生成器 | ~200 |
| `apps/api/tests/test_streaming_json_parser.py` | 解析器单测 | ~120 |
| `apps/api/tests/test_analyze_streaming.py` | 端到端单测（mock LLM） | ~80 |

### 后端修改

| 路径 | 改动 |
|---|---|
| `apps/api/app/main.py` | 新增 `POST /api/analyze/stream` 端点（参考既有 `POST /api/reports/{id}/chat`） |
| `apps/api/app/services/deepseek_client.py` | 抽出 `_build_final_report()` 纯函数（供 sync 和 stream 复用） |
| `apps/api/scripts/smoke_run_analysis.py` | 加 `--stream` 选项 |

### 前端新增

| 路径 | 责任 |
|---|---|
| `apps/web/src/lib/streamApi.ts` | `streamAnalysis()` SSE 客户端（fetch + ReadableStream 解析） |
| `apps/web/src/components/ReportSkeleton.tsx` | 流式渲染时的骨架卡组件 |

### 前端修改

| 路径 | 改动 |
|---|---|
| `apps/web/src/lib/api.ts` | export `streamAnalysis` |
| `apps/web/src/components/Dashboard.tsx` | 默认走 `streamAnalysis`，失败回退 `analyzeAsync` |
| `apps/web/src/components/ReportPanel.tsx` | 新增 streaming 状态分支，渲染骨架 + partial |

---

## 8. 实施顺序（写 plan 时再细化）

按依赖递增：

1. **后端 streaming_json_parser 单测 + 实现**（纯函数，最易测）
2. **后端 deepseek_streaming.stream_chat_completion**（复用 report_chat 模式，最小新代码）
3. **后端 deepseek_client._build_final_report 抽取**（重构，不改行为，跑全量回归）
4. **后端 analyze_streaming.stream_analysis 端到端实现 + 单测**
5. **后端 POST /api/analyze/stream 端点**
6. **smoke 脚本加 --stream，实测对比**
7. **前端 streamApi.ts**（SSE 客户端，约 80 行）
8. **前端 ReportSkeleton 组件 + ReportPanel 集成**
9. **前端 Dashboard 切换默认路径 + 失败回退**
10. **端到端联调 + Playwright 冒烟（可选）**

每步独立可测、可单独 PR。前 6 步纯后端（中断在第 6 步也是可发布的——后端已有 streaming API，前端继续走旧路径）。

---

## 9. 预期收益

| 指标 | 阶段 1 后 | 阶段 2 后 |
|---|---|---|
| 首字节时间（首个 stage 事件） | n/a | **<3s**（fund_data 阶段开始即推） |
| 首只持仓建议可见时间 | 70s | **~10s**（LLM 开始流 + 第一只持仓 JSON 闭合） |
| 用户「黑盒等待」时间 | 70s | **<3s** |
| 总耗时（后端） | 70~78s | **70~78s**（不变） |
| 用户感知 | "卡住了？" | "正在生成，能看到进度" |

> 注：实际 LLM 输出顺序由模型决定。fund_recommendations 通常在 title/summary 之后，所以「首只持仓可见」实际上是「LLM 开始输出 + 装配 + summary + 第一只 close brace」累计 ~10s。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM streaming 在 deep 模式 + tool calling 下复杂 | 本期 streaming 路径**仅支持 fast 模式**（tool calling 关闭）；deep 模式继续走 async 轮询。Spec 明确写出该限制 |
| 增量 JSON 解析 bug 导致 partial 丢失 | parser 失败时不 emit，等 finalize 用 `_parse_model_json` 兜底；最终 done 事件包含完整 Report |
| SSE 在企业网络/代理后被截断 | 前端 5s 内无任何事件即回退 async 路径；返回 `X-Accel-Buffering: no` 头禁用 nginx 缓冲 |
| 客户端断线后服务端任务继续浪费资源 | 本期不做后台续跑：客户端断线 → 服务端 generator 关闭 → LLM 调用取消（httpx 自动 cancel）。已生成的部分丢弃 |
| 旧客户端兼容 | `POST /api/analyze/async` + `GET /api/jobs/{id}` **保留**；小程序、batch 任务、流式失败回退均走该路径 |

---

## 11. 后续阶段（不在本 spec 内）

- **阶段 3：** UI 精修——参考阶段 1 spec 附录 F5 的竞品 takeaway，做"思考过程摘要"侧栏、可取消、浏览器通知、生成完成后红点等。本期阶段 2 只做核心骨架渲染
- **荐基侧流式：** `/api/fund-discovery/stream` 同款实现。本期不做，但 streaming_json_parser 可直接复用——只需把字段映射从 `fund_recommendations` 改为 `recommendations`
- **deep 模式 streaming：** 等 fast 模式上线稳定后再处理（tool calling 的流式实现需要状态机更复杂）
