# TCP 风格拥塞控制模拟器（纯内存 · 确定性 · 虚拟时钟）

一个本地 TCP Reno 风格拥塞控制模拟器。所有连接、packet、ACK、时间和网络
状态都仅由内存中的确定性网络模拟器维护——**不访问真实网络、操作系统 TCP
状态或任何外部服务**，也没有任何真实 `sleep`：时间完全由事件堆驱动的虚拟
时钟推进。固定输入（配置 + 随机种子）必然得到逐字节一致的结果。

## 目录结构

- `tcp_sim/sender.py` — 发送方：`cwnd`、`ssthresh`、in-flight 集合、dup ACK 计数、状态机
- `tcp_sim/network.py` — 网络模型：RTT 时延、丢包、ACK 丢失、乱序（带种子 PRNG）
- `tcp_sim/receiver.py` — 累积 ACK 接收方（带乱序缓冲，类似真实 TCP）
- `tcp_sim/simulation.py` — 事件驱动引擎：虚拟时钟、RTO 定时器、日志与吞吐量统计
- `tcp_sim/demo.py` — 演示 CLI
- `tests/test_tcp_sim.py` — 自动化测试（纯终端运行，打印关键窗口变化）

## 状态机

```
                 cwnd >= ssthresh
 SLOW_START ───────────────────────► CONGESTION_AVOIDANCE
     ▲                                      │
     │                                      │ 3 个重复 ACK
     │                                      ▼
     │            新 ACK（deflate）     FAST_RECOVERY
     │          ┌─────────────────────────────┘
     │          │
     └──────────┴────────── timeout ──────────┘
                  （cwnd=1，可配置恢复状态）
```

- **SLOW_START**：每个新 ACK 使 `cwnd += newly_acked`（每 RTT 翻倍）；
  `cwnd >= ssthresh` 时进入 CONGESTION_AVOIDANCE。
- **CONGESTION_AVOIDANCE**：每个新 ACK 使 `cwnd += newly_acked / cwnd`
  （每 RTT 约 +1 MSS，线性增长）。
- **FAST_RECOVERY**：3 个重复 ACK 触发快速重传后进入；期间每个重复 ACK
  使 `cwnd += 1`（inflate）；收到新 ACK 时 `cwnd = ssthresh`（deflate）
  并回到 CONGESTION_AVOIDANCE。

## 窗口公式

| 事件 | ssthresh | cwnd |
|---|---|---|
| 慢启动中新 ACK | 不变 | `cwnd += newly_acked` |
| 拥塞避免中新 ACK | 不变 | `cwnd += newly_acked / cwnd` |
| 第 3 个重复 ACK（快速重传） | `max(cwnd/2, 2)` | `ssthresh + 3`（inflate） |
| 快速恢复中重复 ACK | 不变 | `cwnd += 1`（inflate） |
| 快速恢复中新 ACK | 不变 | `cwnd = ssthresh`（deflate） |
| 超时（RTO） | `max(cwnd/2, 2)` | `1`，重新进入慢启动（或配置的恢复状态） |

RTO = `rto_multiplier × RTT`（下限 `min_rto`），连续超时按 2 倍指数退避，
收到新 ACK 后复位。有效发送窗口为 `floor(cwnd)`，in-flight 为未确认包数。

## 丢包 / 网络模型

`NetworkConfig` 全部可选参数：

- `one_way_delay`：单向时延（虚拟时间单位），RTT = 2 × 单向时延
- `loss_rate`：数据包随机丢失概率（ seeded PRNG ）
- `ack_loss_rate`：ACK 随机丢失概率
- `reorder_rate` / `reorder_extra_delay`：数据包被额外延迟的概率与幅度
- `drop_seqs` / `drop_acks`：确定性丢弃指定序号（仅首次传输生效，
  重传可送达），用于精确复现 timeout / fast-retransmit 场景
- `seed`：随机种子——相同配置与种子的两次运行产生完全相同的事件轨迹

接收方使用累积 ACK 并缓冲乱序包；缺口之后的包触发重复 ACK，与真实 TCP
行为一致。

## 运行演示

```bash
python3 -m tcp_sim.demo clean     # 无丢包：慢启动 → 拥塞避免
python3 -m tcp_sim.demo timeout   # 尾部丢包：RTO 超时 → cwnd=1 → 慢启动
python3 -m tcp_sim.demo fastrtx   # 中途丢包：3 重复 ACK → 快速重传/恢复
python3 -m tcp_sim.demo lossy     # 高丢包 + ACK 丢失
python3 -m tcp_sim.demo reorder   # 高比例乱序
```

每行输出一个事件：虚拟时间、`cwnd`、`ssthresh`、状态、in-flight、
累计重传次数、吞吐量（已确认包数 / 虚拟时间）以及状态转换说明。

## 运行测试

```bash
cd <本目录>
python3 -m unittest discover -s tests -v
```

测试全部在终端直接运行、毫秒级完成，不依赖真实网络。每个用例会打印其
断言所依据的关键窗口变化。覆盖场景：

- `TestNoLossGrowth` — 无丢包下慢启动指数增长并平滑切入拥塞避免
- `TestTimeout` — 超时后 `cwnd=1`、`ssthresh=cwnd/2`、重新进入慢启动
  （或指定的恢复状态）
- `TestDuplicateAcksAndFastRetransmit` — 重复 ACK 计数、3 次触发快速重传、
  inflate/deflate 的完整快速恢复过程
- `TestHighLoss` — 高丢包下窗口被反复压小、传输最终完成；同种子轨迹逐点一致
- `TestReorder` — 乱序产生重复 ACK（甚至伪快速重传）后仍能完成传输
- `TestVirtualClock` — 零 RTT 边界（所有事件同时间戳仍确定有序）、
  虚拟时钟不消耗真实时间、RTO 精确等于 `rto_multiplier × RTT`
