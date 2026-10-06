# 本地 TCP 风格拥塞控制模拟器

一个**完全运行在内存中**的确定性 TCP-Reno 风格拥塞控制模拟器。所有连接、
packet、ACK、时间和网络状态都由进程内的虚拟网络模拟器维护：**不访问真实
网络、不读取操作系统 TCP 状态、不调用任何外部服务、不使用真实 sleep**。

## 目录结构

```
tcp_sim/
  clock.py       # VirtualClock：虚拟时钟 + FIFO 稳定的事件堆
  sender.py      # Sender：cwnd / ssthresh / in-flight / dup-ACK 状态机
  network.py     # Network（丢包/ACK 丢失/乱序）+ Receiver（累积 ACK）
  simulation.py  # Simulation：事件驱动主循环、时间线记录、吞吐量统计
  __main__.py    # python -m tcp_sim 演示
tests/
  test_congestion.py
```

## 虚拟时钟

`VirtualClock` 用最小堆保存 `(触发时间, 插入序号, 回调)`：

- 时间只随事件推进，**单调不减**；相同时间戳的事件按 **FIFO** 顺序执行；
- 调度负延迟事件会抛 `ValueError`；
- 所有随机性（丢包、乱序抖动）来自 `random.Random(seed)`，因此
  **固定输入必然得到固定输出**（测试中对同一配置跑两次并比较完整时间线）。

## 状态机

```
                cwnd >= ssthresh              3 个重复 ACK
SLOW_START ───────────────────────► CONGESTION_AVOIDANCE ──────────► FAST_RECOVERY
    ▲                                      │                              │
    │             timeout                  │         新 ACK（覆盖恢复点）  │
    └──────────────────────────────────────┴──────────────────────────────┘
```

- `SLOW_START`：初始状态（或 timeout 后重新进入）。
- `CONGESTION_AVOIDANCE`：`cwnd >= ssthresh` 后进入。
- `FAST_RECOVERY`：收到第 3 个重复 ACK 时进入；新的累积 ACK 到达后退出，
  回到 `CONGESTION_AVOIDANCE`。
- `timeout`：任何状态下 RTO 超时都回到 `SLOW_START`。

## 窗口公式（窗口单位为“段”，1 段 = 1 packet）

| 事件 | 更新 |
|---|---|
| 慢启动中每个新 ACK | `cwnd += newly`（每 ACK 一段 +1，每 RTT 翻倍） |
| 拥塞避免中每个新 ACK | `cwnd += newly / cwnd`（每 RTT 约 +1 段） |
| 第 3 个重复 ACK（快速重传） | `ssthresh = max(cwnd/2, 2)`；`cwnd = ssthresh + 3` |
| 快速恢复中每个额外重复 ACK | `cwnd += 1`（inflate） |
| 快速恢复中收到新 ACK | `cwnd = ssthresh`（deflate），退出快速恢复 |
| 超时 | `ssthresh = max(cwnd/2, 2)`；`cwnd = 1`；go-back-N 重传；`RTO *= 2`（上限 16 倍），重新进入慢启动 |

其他约定：

- in-flight = `next_seq - send_base`；只有 `inflight < floor(cwnd)` 才发新段；
- 接收方使用**累积 ACK** 并缓存乱序段，丢失段补齐后一次性推进；
- 快速重传只重发 `send_base` 一段；超时为 go-back-N；
- RTO 默认为 `2 * RTT`，每个 ACK 到达都会刷新重传定时器（世代号使旧
  定时器失效），成功的新 ACK 会把 RTO 退避重置回基准值。

## 丢包 / 损伤模型

`Network` 支持以下可配置项（全部确定性）：

- `loss`：数据包丢失概率；
- `ack_loss`：ACK 丢失概率（累积 ACK 使后续 ACK 能覆盖丢失的 ACK）；
- `reorder`：对每个包以给定概率附加 `uniform(0, RTT/2)` 的额外延迟，
  从而产生乱序；
- `drop_packets={seq,...}` / `drop_acks={ack,...}`：确定性丢弃某段的首次
  传输 / 某 ACK 的首次发送，用于构造精确的测试场景。

## 输出

`Simulation.timeline` 记录每个关键事件：`(time, event, cwnd, ssthresh,
inflight, state)`；`sim.summary()` 给出完成时间、吞吐量（段 / 虚拟时间
单位）、重传次数、超时次数和网络统计。运行演示：

```bash
python -m tcp_sim
```

## 测试

```bash
python -m pytest tests/ -v -s
```

`-s` 让每个测试在终端打印关键窗口变化。覆盖场景：

- `test_no_loss_slow_start_then_congestion_avoidance`：无丢包，慢启动
  1→2→4→8 指数增长，到达 ssthresh 后转加性增长；
- `test_timeout_retransmits_and_reenters_slow_start`：丢包触发超时，
  `cwnd=1` 并重新进入慢启动；
- `test_timeout_backoff_on_total_blackout`：100% 丢包下 RTO 指数退避
  （2→4→8→16…）；
- `test_duplicate_acks_counted_before_fast_retransmit`：第 1、2 个重复
  ACK 不动作，第 3 个才触发快速重传；
- `test_fast_retransmit_and_recovery_window_math`：验证
  `ssthresh=cwnd/2`、`cwnd=ssthresh+3`、inflate 到 14、deflate 回
  ssthresh 的完整数值链；
- `test_high_loss_still_completes_with_many_retransmits`：30% 丢包仍完成
  且产生多次重传；
- `test_reordering_completes_and_is_deterministic`：60% 乱序概率下完成，
  且两次运行时间线完全一致；
- `test_ack_loss_only_recovers_via_remaining_acks`：ACK 丢失由后续累积
  ACK 覆盖，零超时零重传；
- `test_virtual_clock_zero_delay_and_monotonicity` /
  `test_virtual_clock_fifo_at_identical_timestamps`：虚拟时钟零延迟、
  单调性与同时戳 FIFO 边界；
- `test_simulation_uses_no_wall_clock`：禁止 `time.sleep`，上千虚拟时间
  单位的仿真在极短真实时间内完成。
