# Active Polling Pattern（主動輪詢機制）

**文件說明**：定義冷卻刷新期間的等待與輪詢標準模式
**最後更新**：2026-06-10

> ⚠️ **文件狀態**：Active Polling 為設計概念，尚未實作。一般平台使用 **Simple Wait 模式**；中信兄弟與味全龍的跨程序共享刷新屬下述例外。

---

## 目前實作：Simple Wait 標準模式

### 概述

一般平台在刷新重試時使用 Simple Wait 模式：等待設定的冷卻間隔後重載頁面。中信兄弟與味全龍日期頁改由跨程序協調器統一排程，詳見本節後方的例外說明。

```
[check] → 未找到目標 → [sleep interval] → [reload] → [check] → ...
```

> **重要**：所有平台的冷卻重載順序統一為 **Sleep → Reload**，
> 確保 reload 後取得的最新資料能立即被檢查，不被冷卻等待浪費。

### 標準範本

```python
# Simple Wait Pattern - 標準模板（所有平台統一）
interval = config_dict["advanced"].get("auto_reload_page_interval", 0)
if interval > 0:
    debug.log(f"[STAGE SELECT] Waiting {interval}s before reload...")
    await asyncio.sleep(interval)   # 1. 先等待冷卻

debug.log(f"[STAGE SELECT] Reloading page...")
try:
    await tab.reload()              # 2. 再重載（最新資料立即可用）
except Exception:
    pass
```

### 實際程式碼參照

#### Stage 4: 日期選擇

**檔案**：`src/platforms/tixcraft.py`
**函式**：`nodriver_tixcraft_date_auto_select()`

```python
# Auto refresh if no date was selected (for strict mode or sold out scenarios)
if not is_date_clicked:
    # Simple wait mode (consistent with TicketPlus/iBon/FamiTicket)
    interval = config_dict["advanced"].get("auto_reload_page_interval", 0)
    if interval > 0:
        debug.log(f"[DATE SELECT] Waiting {interval}s before reload...")
        await asyncio.sleep(interval)

    debug.log(f"[DATE SELECT] No date selected, reloading page...")
    try:
        await tab.reload()
    except Exception:
        pass
```

#### Stage 5: 區域選擇

**檔案**：`src/platforms/tixcraft.py`
**函式**：`nodriver_tixcraft_area_auto_select()`

```python
# Auto refresh if needed (simple wait mode, consistent with TicketPlus/iBon/FamiTicket)
if is_need_refresh:
    interval = config_dict["advanced"].get("auto_reload_page_interval", 0)
    if interval > 0:
        debug.log(f"[AREA SELECT] Waiting {interval}s before reload...")
        await asyncio.sleep(interval)

    debug.log(f"[AREA SELECT] Page reloading...")
    try:
        await tab.reload()
    except Exception:
        pass
```

### 新平台實作檢查清單

- [ ] 使用 `config_dict["advanced"]["auto_reload_page_interval"]` 取得間隔
- [ ] **先** `asyncio.sleep(interval)` **再** `tab.reload()`（Sleep → Reload 順序）
- [ ] 使用 `debug.log()` 輸出除錯訊息
- [ ] `tab.reload()` 包在 try/except 中
- [ ] 使用 `DebugLogger`，不使用 `if show_debug_message: print()`

### 職棒平台的跨程序例外

中信兄弟與味全龍的 `UTK0201_` 日期頁不在每個程序內各自執行 `Sleep → Reload`。啟用「職棒共用刷新保護」時，每個實例把重新載入需求加入同一個 SQLite FIFO，並將 `advanced.auto_reload_page_interval` 當成所有實例合計的最小間隔。這可避免四個 profiles 各自每秒重載一次，實際合計變成每秒四次。

日期頁找到可操作控制後會取消該實例尚未送出的重新載入，原生點擊只送出一次，並驗證是否進入預期的 `UTK0204_`／`UTK0205_` 或站方會員驗證視窗；沒有進展才重新排入日期頁佇列。進入購票流程後，DOM 點擊不經重新載入協調器，只有確實呼叫 `tab.reload()` 時才使用全域職棒最小間隔。所有階段仍在同一個 429 冷卻範圍內。

選用「開賣首輪加速」後，`refresh_datetime` 的正規化時間會建立一次性 campaign。預設兩秒窗口內，最多四個不同 profile 各取得一次首輪 reload 指令機會，指令間隔預設 `0.25` 秒；已取得額度的 profile 與一般刷新都要等窗口結束。SQLite 會持久保存 campaign、profile grant 與短期 dispatch lease，讓重啟、commit 重播或程序崩潰都不會補發額度。任何 429 都會在同一交易永久中止該 campaign，冷卻恢復後不會繼續剩餘的快速額度。

協調器以 `last_dispatch_at + max(上一筆間隔, 下一筆間隔)` 檢查快慢模式交界，避免最後一筆首輪 reload 之後過早進入一般輪詢。reload 命令完成後仍須等待新的 `performance.timeOrigin`；這個分頁層級的文件確認不會占用全域 dispatch lease，因此慢速頁面不會阻塞其他 profile 的首輪命令。文件確認最長等待 10 秒，逾時後會回到受保護的一般共享重試，且不會退還首輪額度。

---

## 設計概念：Active Polling Pattern（待實作）

> 以下描述的 Active Polling 機制為**設計概念**，目前尚未在任何平台實作。
> 保留作為未來最佳化方向的參考。

### 問題背景

在搶票過程中，當頁面需要刷新重試時，通常會有一個冷卻間隔（`auto_reload_page_interval`）。Simple Wait 使用單一 `sleep()` 等待整個間隔，導致：

- **錯過快速出現的票**：票可能在等待期間出現，但 bot 在睡眠中
- **反應延遲**：必須等完整個間隔才能偵測
- **時機流失**：熱門票券可能在幾秒內售罄

### 設計方案

**Active Polling Pattern**：將單一長等待拆分為多次短輪詢，每次輪詢檢查目標元素是否出現。

```
Simple Wait：  [sleep 8s]          → [reload] → [check]
Active Polling：[0.2s check] × 40  → [reload] → [check]
                     ↑
                冷卻期間持續偵測，發現元素立即處理
```

### 設計原則

| 原則 | 說明 |
|------|------|
| **總時間不變** | 維持使用者設定的 `auto_reload_page_interval` |
| **輪詢間隔** | 固定 0.2 秒（快速反應，最小化錯過機會） |
| **早期發現早期處理** | 偵測到目標立即中斷等待，執行後續邏輯 |
| **自動計算** | 輪詢次數根據設定自動計算：`poll_count = interval * 5` |

### 標準公式

```python
poll_count = int(interval * 5)  # interval 秒 / 0.2 秒 = 輪詢次數
```

| 設定秒數 | 輪詢次數 | 說明 |
|----------|----------|------|
| 5s | 25 次 | 每 0.2s 檢查 |
| 8s | 40 次 | 每 0.2s 檢查（預設） |
| 10s | 50 次 | 每 0.2s 檢查 |
| 15s | 75 次 | 每 0.2s 檢查 |

### 概念範本

```python
# Active Polling Pattern - 概念模板（待實作）
interval = config_dict["advanced"].get("auto_reload_page_interval", 0)
if interval > 0:
    debug.log(f"[STAGE SELECT] Waiting up to {interval}s with active polling...")

    # Poll every 0.2s during the wait period
    poll_count = int(interval * 5)
    for poll_idx in range(poll_count):
        await asyncio.sleep(0.2)
        try:
            el = await tab.wait_for('TARGET_SELECTOR', timeout=0.1)
            if el:
                debug.log(f"[STAGE DELAYED] Elements detected after {(poll_idx + 1) * 0.2:.1f}s")
                break
        except:
            pass

    if not el:
        debug.log(f"[STAGE DELAYED] No elements detected during {interval}s polling")

# 輪詢結束後仍需 reload（維持 Sleep → Reload 順序）
try:
    await tab.reload()
except Exception:
    pass
```

### 預期效能

| 指標 | Simple Wait | Active Polling | 預期改善 |
|------|-------------|----------------|----------|
| 最快反應時間 | 等待整個 interval | 0.2s | 顯著 |
| 平均反應時間 | interval | ~interval/2 | ~2x |
| 錯過機會率 | 較高 | 較低 | 降低 |

### 平台選擇器對照表（參考用）

| 平台 | 階段 | 選擇器 | 說明 |
|------|------|--------|------|
| TixCraft | 日期 | `.btn-group .btn-primary` | 日期按鈕 |
| TixCraft | 區域 | `.zone a` | 區域連結 |
| iBon | 日期 | Shadow DOM (CDP) | 需使用 DOMSnapshot |
| KKTIX | 區域 | `.ticket-unit` | 票種區塊 |
| TicketPlus | 區域 | `.expansion-panel` | 展開面板 |

---

## 版本歷史

| 版本 | 日期 | 變更內容 |
|------|------|---------|
| v2.0 | 2026-02-13 | 重構文件：區分 Simple Wait（已實作）與 Active Polling（設計中） |
| | | 修正範例：`print()` → `debug.log()` |
| | | 更新程式碼參照（日期、區域） |
| v1.0 | 2025-12-17 | 初版：建立 Active Polling Pattern 文件 |

---

## 相關文件

- [04-date-selection.md](./04-date-selection.md) - 日期選擇機制
- [05-area-selection.md](./05-area-selection.md) - 區域選擇機制
- [12-error-handling.md](./12-error-handling.md) - 錯誤處理與重試
- [NoDriver API 指南](../06-api-reference/nodriver_api_guide.md) - wait_for 用法

---

**維護者**：Tickets Hunter 開發團隊
