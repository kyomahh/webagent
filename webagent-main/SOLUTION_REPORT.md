# WebAgent 图片实时保存解决方案 - 完整分析报告

## 一、问题背景

### 用户反馈
执行一个测试用例时，图片保存有延迟问题：
- **期望**: 边执行日志输出，边保存图片（实时）
- **现象**: 所有日志输出完成后，最后才会一下子生成图片文件

### 影响范围
- 用户看不到实时的测试执行进度和截图
- 无法在执行过程中及时了解页面状态
- 报告生成在最后才进行，用户体验差

---

## 二、根本原因分析

### 架构现状
项目使用两种执行引擎：
1. **旧版本**: `PlaywrightExecutionTool` - 每步立即保存截图（在 `execution_impl.py` 第 723 行）
2. **当前版本**: `BrowserUseExecutionTool` - 批量保存截图（在 `execution_browser_use_impl.py` 第 1272 行）

### 问题根源
在 `execution_browser_use_impl.py` 的执行流程中：

```
┌─ execute() 调用 ─────────────────────────────────────────┐
│                                                            │
│  1. agent.run(max_steps)  ← 执行所有步骤               │
│     ├─ browser_use 内部生成截图（存在 /tmp 内存中）     │
│     ├─ 将截图传给 LLM 做决策                           │
│     └─ 最后返回执行历史 (history)                      │
│                                                            │
│  2. _copy_history_screenshots_to_output()                │
│     ├─ 从 history 对象提取截图路径                      │
│     └─ 复制到 output_dir ❌ 这里才首次保存              │
│                                                            │
│  3. _collect_browser_use_evidence_files()                │
│     ├─ 扫描 /tmp/browser_use_agent_*/ 临时目录          │
│     └─ 复制所有 PDF/PNG 到 output_dir ❌ 还是最后保存   │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 关键问题点

| 阶段       | 位置             | 现象                             |
| ---------- | ---------------- | -------------------------------- |
| 执行中     | browser_use 内部 | 截图只存在内存中，用于 LLM 决策  |
| 执行中     | 日志输出         | 日志实时输出，但没有图片         |
| **执行后** | 第 1272 行       | **第一次从临时目录批量复制文件** |
| **执行后** | 第 1345 行       | **第二次扫描 /tmp 目录复制文件** |

这就是为什么日志先全部输出完，最后才看到图片 ✗

---

## 三、解决方案设计

### 方案对比

| 方案                    | 优点                       | 缺点                              | 选择       |
| ----------------------- | -------------------------- | --------------------------------- | ---------- |
| **A. 同步保存**         | 最简洁，无线程复杂性       | 可能阻塞执行，影响 LLM 响应延迟   | ✗          |
| **B. Hook browser_use** | 逻辑清晰，时序准确         | 需要深度理解 browser_use 内部 API | ✗          |
| **C. 后台异步监听**     | 不影响执行性能，实时效果好 | 需要线程管理                      | **✓ 选择** |

### 实现方案 (C. 后台异步监听)

#### 核心逻辑
```python
执行 Agent 之前 ──┐
                 ├─→ 启动后台监听线程
                 │    每 0.5 秒扫描 /tmp/browser_use_agent_*/
                 │    发现新文件自动复制到 output_dir
                 │
执行 Agent       │    (主线程正常执行)
(主线程)         │
                 │
执行完成 ────────┤
                 └─→ 停止监听线程，收集已保存的文件列表
```

#### 实现位置
**文件**: `tools/impl/execution_browser_use_impl.py`

**新增方法**:
1. `_start_real_time_evidence_monitor()` - 第 1468-1521 行
   - 启动后台线程，每 0.5 秒扫描一次
   - 自动发现并复制新证据文件
   - 返回监听状态字典（包含线程对象）

2. `_stop_real_time_evidence_monitor()` - 第 1523-1543 行
   - 停止后台线程
   - 等待线程完成（最多 5 秒）
   - 返回已保存的文件列表

**修改的方法**:
- `_execute_with_browser_use_agent()` - 第 301-357 行
  - 在 `agent.run()` 前启动监听
  - 在 `agent.run()` 后停止监听
  - 合并实时监听和最后收集的文件列表

#### 工作时序
```
时间轴:
├─ t0: 启动监听线程
├─ t0-t1: 执行 agent.run()
│         └─ 后台线程每 0.5s 扫描一次，发现新文件立即复制 ✓ 实时
├─ t1: agent.run() 完成
├─ t1+: 停止监听线程
└─ t1+: 最后一次收集遗留文件

结果: 整个执行过程中文件实时被保存到 output_dir
```

---

## 四、代码实现细节

### 1. 启动监听
```python
# 在 execute() 之前
monitor_thread = self._start_real_time_evidence_monitor(
    scenario_id=scenario_id,
    start_time=run_started_at,
)
```

### 2. 后台工作
```python
# 每 0.5 秒循环
while monitor_state["running"]:
    for pattern in ["/tmp/browser_use_agent_*/browseruse_agent_data/*", ...]:
        for source_path in glob.glob(pattern):
            if is_new_evidence_file(source_path):
                copy_to_output_dir(source_path)  # ← 实时保存
```

### 3. 停止监听
```python
# 执行完成后
files = self._stop_real_time_evidence_monitor(monitor_thread)
# 合并文件列表
all_evidence_files = merge(step_files, collected_files, monitored_files)
```

---

## 五、改动清单

### 文件: `tools/impl/execution_browser_use_impl.py`

**第 1 处修改** - 导入 `time` 模块（第 18 行）
```python
import time
from datetime import datetime
```

**第 2 处修改** - 启动监听线程（第 326-330 行）
```python
monitor_thread = None
try:
    # ... 创建 agent ...
    monitor_thread = self._start_real_time_evidence_monitor(...)
```

**第 3 处修改** - 停止监听线程（第 346-347 行）
```python
monitored_files = self._stop_real_time_evidence_monitor(monitor_thread)
```

**第 4 处修改** - 合并文件列表（第 363-364 行）
```python
all_evidence_files = self._merge_evidence_paths(
    step_screenshot_files, evidence_files, monitored_files,
)
```

**第 5 处修改** - 异常处理中停止线程（第 378-379 行）
```python
except Exception as exc:
    if monitor_thread is not None:
        self._stop_real_time_evidence_monitor(monitor_thread)
```

**新增代码** - 两个新方法（第 1468-1543 行）
- `_start_real_time_evidence_monitor()` 
- `_stop_real_time_evidence_monitor()`

---

## 六、性能和风险分析

### 性能影响
| 指标       | 影响       | 说明                                     |
| ---------- | ---------- | ---------------------------------------- |
| 主线程 CPU | ✓ 无影响   | 监听在后台线程，采用 0.5s 循环间隔       |
| 内存       | ✓ 最小     | 仅存储监听到的文件路径列表               |
| 磁盘 I/O   | ≈ 相同     | 文件最终都要复制，只是分散到整个执行过程 |
| 执行时间   | ✓ 可能减少 | 文件并行复制，比最后批量复制更高效       |

### 风险控制
1. **线程安全**: 使用 `set` 追踪已复制文件，避免重复
2. **异常处理**: 线程内的异常不会传播，只在日志中记录
3. **清理**: 异常发生时主线程仍会停止监听线程
4. **超时**: 等待线程最多 5 秒，防止挂死

---

## 七、验证方法

### 单元测试
```bash
uv run python test_real_time_monitor.py
```
✓ 输出: `✅ 实时监听机制测试通过！`

### 集成测试
```bash
uv run python main.py --url https://demo.4gaboards.com/ --resume
```

**观察日志**:
- ✓ `[RealTimeMonitor] 实时保存证据文件: xxx.pdf`
- ✓ 在日志输出过程中看到实时保存的信息
- ✓ output/ 目录中的文件会逐步增加（而不是最后一下子出现）

### 可视化报告
```bash
uv run server.py  # 启动可视化服务
# 在执行过程中访问 http://localhost:8000
# 观察截图列表是否实时更新
```

---

## 八、预期效果

### Before（修改前）
```
[Executor] 执行步骤 1...
[Executor] 执行步骤 2...
[Executor] 执行步骤 3...
...
[Executor] 所有步骤执行完成！
[VerificationTool] 开始验证...
... (此时才出现大量截图文件)
```

### After（修改后）
```
[Executor] 执行步骤 1...
[RealTimeMonitor] 实时保存证据文件: step_1_xxx.png  ✓
[Executor] 执行步骤 2...
[RealTimeMonitor] 实时保存证据文件: step_2_xxx.png  ✓
[Executor] 执行步骤 3...
[RealTimeMonitor] 实时保存证据文件: step_3_xxx.png  ✓
...
[Executor] 所有步骤执行完成！
[VerificationTool] 开始验证...
```

---

## 九、后续优化方向

1. **可配置扫描间隔**: 添加环境变量 `REAL_TIME_MONITOR_INTERVAL` 控制扫描频率
2. **按优先级复制**: 优先复制 PDF/PNG，后复制其他文件
3. **监听统计**: 记录监听效率（多少文件通过实时监听复制 vs 最后收集）
4. **Playwright executor 适配**: 考虑为旧版本也添加类似机制

---

## 总结

✅ **问题已解决**: 图片现在会在整个执行过程中实时保存到 `output/` 目录
✅ **性能无损**: 使用后台线程，不影响主流程执行性能
✅ **风险可控**: 异常处理完善，线程安全有保障
✅ **易于验证**: 通过日志和文件时间戳可直观看到效果
