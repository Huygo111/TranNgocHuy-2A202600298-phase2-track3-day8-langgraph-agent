# Hướng dẫn từng bước - Day 08 LangGraph Agent Lab

Tài liệu này tóm tắt cách làm bài lab từ đầu đến cuối. Mục tiêu là hoàn thiện một workflow LangGraph cho support-ticket agent có routing, retry loop, HITL approval, persistence, metrics và report.

## 1. Mục tiêu cần đạt

Sau khi làm xong, repo cần đạt các yêu cầu sau:

- `make test` pass.
- `make run-scenarios` chạy đủ các scenario trong `data/sample/scenarios.jsonl`.
- `outputs/metrics.json` được sinh ra và đúng schema `MetricsReport`.
- `make grade-local` validate metrics thành công.
- `reports/lab_report.md` được điền đầy đủ.
- Có thể giải thích ít nhất một route bình thường và một failure mode trong demo.

Nếu muốn đạt điểm cao hơn, nên làm thêm ít nhất một extension: SQLite persistence, crash-resume, state history replay, real HITL interrupt, parallel fan-out, hoặc export graph diagram.

## 2. Hiểu flow cần xây

Graph đích cần có luồng chính:

```text
START -> intake -> classify -> route

route simple       -> answer -> finalize -> END
route tool         -> tool -> evaluate -> answer -> finalize -> END
route tool retry   -> tool -> evaluate -> retry -> tool -> evaluate -> ...
route missing_info -> clarify -> finalize -> END
route risky        -> risky_action -> approval -> tool -> evaluate -> answer -> finalize -> END
route error        -> retry -> tool -> evaluate -> retry -> ...
route max retry    -> retry -> dead_letter -> finalize -> END
```

Điểm quan trọng:

- Mọi route phải kết thúc ở `finalize -> END`.
- Retry loop phải bị giới hạn bởi `max_attempts`.
- Route `risky` phải đi qua approval trước khi chạy tool/action.
- Không hard-code theo `scenario_id`; phải route dựa trên query và state logic.

## 3. Cài đặt và kiểm tra ban đầu

Chạy các lệnh sau từ thư mục gốc repo:

```bash
pip install -e '.[dev]'
make test
```

Nếu dùng Windows PowerShell và gặp lỗi quote với extras, có thể dùng:

```powershell
pip install -e ".[dev]"
pytest
```

## 4. Hoàn thiện state schema

File cần xem: `src/langgraph_agent_lab/state.py`.

Cần đảm bảo state gọn, serializable và có reducer đúng cho các trường append-only:

- Append-only: `messages`, `tool_results`, `errors`, `events`.
- Overwrite: `route`, `risk_level`, `attempt`, `max_attempts`, `final_answer`, `pending_question`, `proposed_action`, `approval`, `evaluation_result`.

`evaluation_result` là trường quan trọng cho retry loop. `evaluate_node` sẽ set giá trị:

- `"success"` nếu tool result hợp lệ.
- `"needs_retry"` nếu tool result lỗi.

`initial_state()` cần set đầy đủ giá trị mặc định, đặc biệt:

- `attempt = 0`
- `max_attempts = scenario.max_attempts`
- `evaluation_result = None`
- các list append-only bắt đầu bằng `[]`

## 5. Hoàn thiện node logic

File cần sửa: `src/langgraph_agent_lab/nodes.py`.

Mỗi node nên return partial state update, không mutate trực tiếp input `state`.

### 5.1. `intake_node`

Mục tiêu:

- Strip query.
- Ghi audit event.
- Có thể thêm normalize nhẹ nếu cần.

Kết quả tối thiểu:

- Update `query`.
- Append `messages`.
- Append `events` với node `intake`.

### 5.2. `classify_node`

Mục tiêu: route query bằng keyword heuristics, không match exact scenario.

Thứ tự ưu tiên nên dùng:

1. `risky`
2. `tool`
3. `missing_info`
4. `error`
5. `simple`

Keyword gợi ý:

| Route | Keyword / điều kiện |
|---|---|
| `risky` | `refund`, `delete`, `send`, `cancel`, `remove`, `revoke` |
| `tool` | `status`, `order`, `lookup`, `check`, `track`, `find`, `search` |
| `missing_info` | query ngắn, mơ hồ, ví dụ ít hơn 5 từ và có từ `it` |
| `error` | `timeout`, `fail`, `failure`, `error`, `crash`, `unavailable` |
| `simple` | default |

Lưu ý:

- Nên strip punctuation khi check word như `it?`.
- Route risky có priority cao nhất để tránh query có cả risky và tool keyword.
- Set `risk_level = "high"` cho risky, còn lại có thể để `"low"` hoặc `"unknown"` tùy logic.

### 5.3. `ask_clarification_node`

Mục tiêu:

- Không hallucinate khi query thiếu thông tin.
- Set `pending_question`.
- Set `final_answer` bằng câu hỏi clarify để metrics tính success.
- Append event node `clarify`.

### 5.4. `tool_node`

Mục tiêu:

- Mô phỏng tool call cho route `tool`, `risky`, và `error`.
- Append `tool_results`.
- Append event node `tool`.

Cho route `error`, cần mô phỏng transient failure để retry loop hoạt động. Ví dụ:

- Nếu `route == "error"` và `attempt < 2`, trả result có chuỗi `ERROR`.
- Nếu đã retry đủ, trả result thành công.

Với scenario `S07_dead_letter`, `max_attempts = 1` sẽ làm workflow hết retry sớm và vào `dead_letter`.

### 5.5. `risky_action_node`

Mục tiêu:

- Tạo `proposed_action`.
- Append event node `risky_action`.
- Không thực hiện action thật trước approval.

### 5.6. `approval_node`

Mục tiêu:

- Default mock approval để tests và CLI chạy offline.
- Nếu `LANGGRAPH_INTERRUPT=true`, có thể dùng `interrupt()` để demo HITL thật.

Kết quả tối thiểu:

- Set `approval` thành dict có `approved`.
- Append event node `approval`.

### 5.7. `retry_or_fallback_node`

Mục tiêu:

- Tăng `attempt` lên 1.
- Append lỗi vào `errors`.
- Append event node `retry`.

Routing sau node này sẽ quyết định quay lại `tool` hay vào `dead_letter`.

### 5.8. `evaluate_node`

Mục tiêu:

- Đọc tool result mới nhất.
- Nếu có lỗi, set `evaluation_result = "needs_retry"`.
- Nếu hợp lệ, set `evaluation_result = "success"`.
- Append event node `evaluate`.

### 5.9. `answer_node`

Mục tiêu:

- Tạo `final_answer`.
- Nếu có `tool_results`, câu trả lời nên grounded vào tool result mới nhất.
- Nếu không có tool result, trả safe mock answer cho route `simple`.
- Append event node `answer`.

### 5.10. `dead_letter_node`

Mục tiêu:

- Set `final_answer` nói request không hoàn tất sau max retry và đã được log cho manual review.
- Append event node `dead_letter`.

### 5.11. `finalize_node`

Mục tiêu:

- Append event node `finalize`.
- Không cần sửa nhiều nếu đã có event.

## 6. Hoàn thiện routing

File cần sửa: `src/langgraph_agent_lab/routing.py`.

### 6.1. `route_after_classify`

Map route sang node tiếp theo:

| Route | Next node |
|---|---|
| `simple` | `answer` |
| `tool` | `tool` |
| `missing_info` | `clarify` |
| `risky` | `risky_action` |
| `error` | `retry` |

Nếu route không hợp lệ, fallback an toàn về `answer` hoặc `clarify`. Nên có test cho unknown route nếu muốn chặt hơn.

### 6.2. `route_after_evaluate`

Logic:

- Nếu `evaluation_result == "needs_retry"` thì return `"retry"`.
- Ngược lại return `"answer"`.

Đây là done-check tạo retry loop.

### 6.3. `route_after_retry`

Logic:

- Nếu `attempt >= max_attempts`, return `"dead_letter"`.
- Ngược lại return `"tool"`.

Lưu ý: `retry_or_fallback_node` tăng `attempt` trước khi routing này được gọi.

### 6.4. `route_after_approval`

Logic:

- Nếu `approval.approved == True`, return `"tool"`.
- Nếu bị reject, return `"clarify"` hoặc route fallback an toàn.

## 7. Kiểm tra graph wiring

File cần xem: `src/langgraph_agent_lab/graph.py`.

Graph cần có các node:

- `intake`
- `classify`
- `answer`
- `tool`
- `evaluate`
- `clarify`
- `risky_action`
- `approval`
- `retry`
- `dead_letter`
- `finalize`

Edges cần đảm bảo:

- `START -> intake -> classify`
- `classify` dùng conditional edges qua các route.
- `tool -> evaluate`
- `evaluate` conditional qua `answer` hoặc `retry`.
- `retry` conditional qua `tool` hoặc `dead_letter`.
- `risky_action -> approval`, approval conditional qua `tool` hoặc `clarify`.
- `answer`, `clarify`, `dead_letter` đều vào `finalize`.
- `finalize -> END`.

Không cần sửa graph nếu skeleton đã đúng. Chỉ sửa khi node name hoặc behavior thay đổi.

## 8. Persistence và recovery

File cần sửa: `src/langgraph_agent_lab/persistence.py`.

Bản tối thiểu:

- `kind == "memory"` trả `MemorySaver()`.
- `kind == "none"` trả `None`.

Extension SQLite:

- Cài dependency:

```bash
pip install -e '.[dev,sqlite]'
```

- Dùng `SqliteSaver` với connection sqlite. Theo README, với `langgraph-checkpoint-sqlite` 3.x nên dùng pattern `SqliteSaver(conn=sqlite3.connect(...))`, tránh dùng API trả context manager nếu nó không phù hợp với compile.
- Cập nhật `configs/lab.yaml`:

```yaml
checkpointer: sqlite
database_url: checkpoints.db
```

Cần có bằng chứng trong report:

- Mỗi run có `thread_id`.
- Có sử dụng checkpointer.
- Có state history hoặc crash-resume nếu làm extension.

## 9. Metrics

File cần xem: `src/langgraph_agent_lab/metrics.py`.

`outputs/metrics.json` phải validate với `MetricsReport`, gồm:

- `total_scenarios`
- `success_rate`
- `avg_nodes_visited`
- `total_retries`
- `total_interrupts`
- `resume_success`
- `scenario_metrics`

Mỗi scenario metric cần có:

- `scenario_id`
- `success`
- `expected_route`
- `actual_route`
- `nodes_visited`
- `retry_count`
- `interrupt_count`
- `approval_required`
- `approval_observed`
- `latency_ms`
- `errors`

Mặc định code tính nodes/retry/interrupt dựa trên `events`, vì vậy mỗi node quan trọng phải append event đúng `node` name.

## 10. Chạy scenarios và validate

Sau khi sửa code:

```bash
make test
make run-scenarios
make grade-local
```

Nếu dùng PowerShell và `make` không khả dụng, chạy trực tiếp:

```powershell
pytest
python -m langgraph_agent_lab.cli run-scenarios --config configs/lab.yaml --output outputs/metrics.json
python -m langgraph_agent_lab.cli validate-metrics --metrics outputs/metrics.json
```

Sau khi chạy, mở `outputs/metrics.json` để kiểm tra:

- Tổng scenario tối thiểu 6, repo mẫu có 7.
- `success_rate` nên đạt 1.0 với sample scenarios.
- `S04_risky` và `S06_delete` có `approval_observed = true`.
- `S05_error` có retry.
- `S07_dead_letter` vào error route và kết thúc qua dead letter khi hết attempt.

## 11. Làm bonus extension

Phần bonus không bắt buộc để lab chạy, nhưng giúp nhắm mức điểm cao hơn. Nên làm ít nhất một bonus và ghi evidence vào `reports/lab_report.md`.

### 11.1. Bonus SQLite persistence + state history

Đây là bonus dễ làm nhất vì repo đã có sẵn `persistence.py`, `thread_id` và LangGraph checkpointer.

Bước làm:

1. Cài dependency SQLite:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e ".[sqlite]"
   ```

2. Hoàn thiện `src/langgraph_agent_lab/persistence.py`:

   - `kind == "memory"` trả `MemorySaver()`.
   - `kind == "none"` trả `None`.
   - `kind == "sqlite"` tạo `sqlite3.connect(...)`, bật WAL, rồi trả `SqliteSaver(conn=conn)`.
   - Không dùng `SqliteSaver.from_conn_string()` nếu version đang cài trả context manager thay vì checkpointer trực tiếp.

3. Test nhanh checkpointer SQLite bằng state history:

   ```python
   from langgraph_agent_lab.graph import build_graph
   from langgraph_agent_lab.persistence import build_checkpointer
   from langgraph_agent_lab.state import Route, Scenario, initial_state

   checkpointer = build_checkpointer("sqlite", ":memory:")
   graph = build_graph(checkpointer=checkpointer)

   scenario = Scenario(
       id="sqlite-demo",
       query="Please lookup order status for order 12345",
       expected_route=Route.TOOL,
   )
   state = initial_state(scenario)
   config = {"configurable": {"thread_id": state["thread_id"]}}

   final_state = graph.invoke(state, config=config)
   history = list(graph.get_state_history(config))

   print(final_state["route"])
   print(len(history))
   print(history[0].values["events"][-1]["node"])
   ```

4. Expected evidence:

   - `final_state["route"] == "tool"`.
   - `len(history) > 0`.
   - checkpoint history có event cuối là `finalize`.

5. Nếu muốn dùng file SQLite thật, cập nhật `configs/lab.yaml`:

   ```yaml
   scenarios_path: data/sample/scenarios.jsonl
   checkpointer: sqlite
   database_url: outputs/checkpoints-bonus.db
   report_path: reports/lab_report.md
   ```

   Nếu môi trường chạy bị lỗi ghi SQLite file như `disk I/O error`, có thể dùng tạm `database_url: ":memory:"` để chứng minh `SqliteSaver` và `get_state_history()` hoạt động trong cùng process. Khi demo bonus persistence đúng nghĩa, nên dùng file DB như `outputs/checkpoints-bonus.db`.

6. Chạy lại:

   ```powershell
   .\.venv\Scripts\python.exe -m langgraph_agent_lab.cli run-scenarios --config configs/lab.yaml --output outputs/metrics.json
   .\.venv\Scripts\python.exe -m langgraph_agent_lab.cli validate-metrics --metrics outputs/metrics.json
   ```

Ghi vào report:

- Checkpointer đã dùng: SQLite.
- `thread_id` được truyền qua `configurable.thread_id`.
- Có gọi `get_state_history(config)` và thấy checkpoint history.

### 11.2. Bonus crash-resume

Mục tiêu là chứng minh workflow có thể tiếp tục từ cùng `thread_id`.

Cách làm đơn giản:

1. Dùng SQLite checkpointer thay vì memory.
2. Chạy graph với một `thread_id` cố định.
3. Tạo lại graph mới với cùng database file.
4. Gọi `graph.get_state(config)` hoặc `graph.get_state_history(config)` với cùng `thread_id`.
5. Nếu state/history vẫn đọc được sau khi tạo graph mới, có evidence recovery.
6. Trong CLI, nên kiểm tra crash-resume sau khi chạy scenario bằng cách tạo lại `build_graph(build_checkpointer("sqlite", database_url))` rồi gọi `get_state_history(config)` với `thread_id` đầu tiên.
7. Nếu dùng lại cùng SQLite DB nhiều lần, nên tạo `thread_id` duy nhất cho mỗi lần `run-scenarios` để không lẫn checkpoint cũ vào metrics. Ví dụ CLI có thể thêm hậu tố `run-{uuid}` vào `thread-{scenario_id}`.

Pseudo-code:

```python
config = {"configurable": {"thread_id": "thread-crash-demo"}}

graph1 = build_graph(checkpointer=build_checkpointer("sqlite", "outputs/checkpoints-bonus.db"))
graph1.invoke(initial_state(scenario), config=config)

graph2 = build_graph(checkpointer=build_checkpointer("sqlite", "outputs/checkpoints-bonus.db"))
history = list(graph2.get_state_history(config))

assert history
```

Nếu làm bonus này, nên cập nhật metrics/report:

- `resume_success = true` nếu demo đọc lại state/history thành công.
- Report ghi rõ cùng `thread_id` và cùng SQLite database file.
- `database_url: ":memory:"` không chứng minh được crash-resume giữa hai graph/process khác nhau, vì mỗi connection có database riêng. Muốn chứng minh crash-resume thật, cần dùng file DB.

### 11.3. Bonus time-travel replay

Mục tiêu là dùng checkpoint history để xem lại trạng thái ở các bước trước.

Bước làm:

1. Sau khi chạy graph, gọi:

   ```python
   history = list(graph.get_state_history(config))
   ```

2. In các checkpoint:

   ```python
   for checkpoint in history:
       values = checkpoint.values
       print(values.get("route"), values.get("attempt"), values.get("events", [])[-1]["node"])
   ```

3. Chọn một checkpoint trước `finalize` để phân tích retry hoặc approval path.

Ghi vào report:

- Có bao nhiêu checkpoint.
- Checkpoint nào cho thấy route/retry/approval.
- Vì sao state history hữu ích khi debug production workflow.

Nếu muốn tự động hóa trong CLI:

- Sau khi chạy scenarios bằng SQLite file, tạo lại graph mới với cùng DB.
- Gọi `get_state_history(config)` với `thread_id` đầu tiên của run.
- Chuyển history thành timeline gồm `step`, `node`, `route`, `attempt`, `events_count`.
- Ghi timeline ra `outputs/time_travel_replay.json`.
- Chèn một bảng ngắn vào `reports/lab_report.md`.

Expected evidence:

- `outputs/time_travel_replay.json` tồn tại và có nhiều step.
- Report có bảng `Time-travel replay`.
- Step cuối trong timeline thường là `finalize`, chứng minh có thể xem lại trạng thái workflow đã hoàn tất.

### 11.4. Bonus real HITL interrupt

Repo đã có mock approval để tests chạy offline. Nếu muốn demo HITL thật:

1. Cách nhanh nhất là dùng CLI demo:

   ```powershell
   .\.venv\Scripts\python.exe -m langgraph_agent_lab.cli hitl-demo --output outputs/hitl_demo.json --approved
   ```

2. Command này sẽ:

   - Tự bật `LANGGRAPH_INTERRUPT=true`.
   - Chạy risky scenario có query `Refund this customer and send confirmation email`.
   - Dừng tại `approval_node` bằng `interrupt(...)`.
   - Resume graph bằng `Command(resume=...)`.
   - Ghi evidence vào `outputs/hitl_demo.json`.

3. Có thể demo reject path:

   ```powershell
   .\.venv\Scripts\python.exe -m langgraph_agent_lab.cli hitl-demo --output outputs/hitl_demo_rejected.json --rejected --comment "needs more context"
   ```

4. Nếu tự viết code resume, decision payload có dạng:

   ```python
   {"approved": True, "reviewer": "human", "comment": "approved for demo"}
   ```

Ghi vào report:

- Risky route đã pause tại approval.
- Human decision được truyền vào `Command(resume=...)`.
- `outputs/hitl_demo.json` có `interrupts`, `decision`, `approval`, `final_answer`, `nodes_visited`.
- Nếu bị reject thì route fallback về `clarify`.

### 11.5. Bonus export graph diagram

Mục tiêu là đưa sơ đồ graph vào report.

Chạy command:

```powershell
.\.venv\Scripts\python.exe -m langgraph_agent_lab.cli export-graph --output outputs/graph_diagram.mmd
```

Command này gọi:

```python
from langgraph_agent_lab.graph import build_graph

graph = build_graph()
print(graph.get_graph().draw_mermaid())
```

Nếu chạy `run-scenarios`, CLI cũng có thể ghi `outputs/graph_diagram.mmd` và chèn Mermaid vào `reports/lab_report.md`.

Ghi vào report:

- Mermaid diagram.
- File evidence: `outputs/graph_diagram.mmd`.
- Giải thích các conditional edges quan trọng: `classify`, `evaluate`, `approval`, `retry`.

### 11.6. Bonus parallel fan-out/fan-in

Đây là bonus khó hơn, chỉ nên làm sau khi core đã ổn.

Ý tưởng:

- Thêm hai mock tool độc lập, ví dụ `order_tool` và `account_tool`.
- Dùng LangGraph `Send()` để fan-out song song.
- Merge kết quả vào `tool_results` bằng append-only reducer.
- `evaluate_node` đọc toàn bộ evidence và quyết định success/retry.

Cần chú ý:

- `tool_results` phải là append-only reducer.
- Mỗi tool node phải append event riêng để metrics thấy node đã chạy.
- Không làm fan-out nếu nó làm route/retry core khó debug.

## 12. Viết report

File output cần có: `reports/lab_report.md`.

Có thể dùng template trong `reports/lab_report_template.md`. Report cần gồm:

1. Team / student
   - Tên
   - Repo/commit
   - Date

2. Architecture
   - Mô tả node, edge, conditional route.
   - Giải thích vì sao retry loop nằm sau `evaluate`.
   - Giải thích route risky cần approval.

3. State schema
   - Lập bảng field, reducer, lý do.
   - Nếu field append-only thì nói rõ dùng reducer `add`.

4. Scenario results
   - Paste các metric chính từ `outputs/metrics.json`.
   - Nên có bảng: scenario, expected route, actual route, success, retries, interrupts.

5. Failure analysis
   - Ít nhất hai failure mode:
     - Tool transient failure và retry.
     - Risky action không có approval hoặc bị reject.
   - Có thể thêm max retry -> dead letter.

6. Persistence / recovery evidence
   - Nếu chỉ dùng memory, giải thích thread_id và MemorySaver.
   - Nếu làm SQLite/time travel/crash-resume, đưa evidence cụ thể.

7. Extension work
   - Mô tả extension đã làm.
   - Nếu export graph diagram, dán Mermaid hoặc ảnh.

8. Improvement plan
   - Nếu có thêm một ngày, ưu tiên productionize gì: structured tools, real approval UI, observability, typed errors, persistence bền vững.

## 13. Rubric checklist

Dùng checklist này trước khi nộp:

- [ ] State schema đúng typed state và reducer cho append-only fields.
- [ ] `classify_node` route đúng 5 route chính.
- [ ] Không hard-code scenario ID.
- [ ] Route risky đi qua approval.
- [ ] Retry loop bị bound bởi `max_attempts`.
- [ ] Dead-letter path hoạt động khi hết retry.
- [ ] Mọi route đều terminate ở `finalize -> END`.
- [ ] `make test` pass.
- [ ] `make run-scenarios` sinh `outputs/metrics.json`.
- [ ] `make grade-local` pass.
- [ ] `reports/lab_report.md` đã điền đầy đủ.
- [ ] Có giải thích metrics và failure modes.
- [ ] Có ít nhất một extension nếu muốn nhắm mức 90+.

## 14. Lỗi thường gặp

- Check keyword bằng substring quá rộng: ví dụ `it` match trong `item`. Nên tách word và strip punctuation.
- Đặt priority sai: risky keyword phải được check trước tool keyword.
- Quên append event ở node mới, làm metrics `nodes_visited`, `retry_count`, `interrupt_count` sai.
- Retry loop không bound bằng `max_attempts`, làm graph có nguy cơ loop vô hạn.
- Quên edge `dead_letter -> finalize` hoặc `clarify -> finalize`.
- Dùng SQLite checkpointer API không đúng version đang cài.
- Report chỉ paste số liệu nhưng không giải thích vì sao metrics có giá trị đó.
