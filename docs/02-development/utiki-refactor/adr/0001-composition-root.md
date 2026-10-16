# ADR-0001：uTiki composition root 採直接 bootstrap

- 狀態：已採用
- 日期：2026-10-16（Asia/Taipei）
- 追蹤：[Issue #17](https://github.com/OooPeople/tickets_hunter/issues/17)
- 實作分支：`refactor/17-utiki-root-v1-5`
- 實作基準：`f307f7caba054fb40aab807a4629c891e69fb200`
- Upstream 基準：`bouob/tickets_hunter@9e2d1061b48706076943b1059616347c389df809`
- 對應計劃：v1.5 §4、§12、§14、§19、§22

## 決策

採方案 A：`src/nodriver_tixcraft.py` 直接呼叫新的
`platforms.utiki.bootstrap.create_engine(...)`，以已驗證的 current policy 與窄
ports 建立唯一 engine。主入口保留其他平台既有路由，不建立頂層
`integrations/` framework，也不以 legacy facade 接到新 engine。

bootstrap 是新架構的正常工廠，不是相容層：

- 不接受或產生 legacy dict、舊 state 或舊 result shape。
- 不 re-export `nodriver_ctbc_*`、`nodriver_utiki_main` 或其他歷史 API。
- 不建立第二個 session registry、coordinator 或 composition root。
- 符合新 port 的既有 callable 由主入口直接注入，不再包一層。
- 只有 current-core interface 與新 port 的資料形狀確實不相符時，才可在
  `src/utiki_core_services.py` 建立小型外部適配；每個例外都必須登錄於
  `core-integration-inventory.md`。

方案 B（通用頂層 integration framework）在有第二個真實 consumer、獨立 host
contract、方案 A 無法簡潔滿足的證據前不採用。方案 C（舊 API facade 轉送）不採用。

## 現況證據

actual HEAD 的主入口直接 import 十個 uTiki/CTBC 控制符號，接點不只每輪 handler：

| 現有接點 | 基準位置 | 作用 |
|---|---|---|
| uTiki import block | `src/nodriver_tixcraft.py:73-84` | URL 判定、Cookie、reload、health、cleanup、主 handler |
| 啟動 Cookie | `src/nodriver_tixcraft.py:394-416` | 注入 Cookie 後觸發 shared reload |
| 定時刷新 | `src/nodriver_tixcraft.py:648-685` | 設定變更時 cancel，到點時 reload |
| challenge/document gate | `src/nodriver_tixcraft.py:799-918` | 讀取文件並可能交給 challenge handler 變更頁面 |
| stop／離站／handler 例外 | `src/nodriver_tixcraft.py:1073-1096,1133-1138,1241-1248` | 清理 tab runtime／driver |
| pause／reload event | `src/nodriver_tixcraft.py:1146-1161` | 暫停刷新與重設外層 gate |
| outer health | `src/nodriver_tixcraft.py:1184-1208` | 在 route 前執行 429／文件恢復 |
| 主 route | `src/nodriver_tixcraft.py:1241-1248` | 呼叫舊 `nodriver_utiki_main` alias |

舊 handler 內又執行一次 health check（`src/platforms/utiki/workflow.py:3494-3510`），
證明新接合不能只替換最後一個 route；startup、scheduled、health、pause、stop、離站與
challenge ownership 都必須接到同一 engine/session 仲裁。

基準沒有第二個真平台使用共通 host runtime，也沒有既存通用 integration contract。
`src/platforms/ctbc.py` 與 `platforms.utiki.__init__` 的 runtime alias 是歷史相容，
不是保留頂層框架的證據。

## Current-core 接合原則

可能需要接合的現行能力只有實際存在的服務：shared refresh coordinator、global
refresh policy、pause/quit control、credential source、native input setter、OCR/image
capture、notification/sound、clock/identity 及受限 filesystem 能力。逐項裁決見
`core-integration-inventory.md`。

預設處理如下：

1. notification、sound、pause/quit 等 callable 若符合 port，直接注入。
2. credential provider 不把完整 raw config/accounts 傳入 engine；只提供必要 tenant
   credential revision/material，並遵守秘密生命週期。
3. coordinator 若需要把既有 reservation/result shape 映射為 typed port，可建立一個
   外部薄適配；不得建立 mirror DB、queue 或 state。
4. native setter 由現行唯一來源提供 fragment/digest，不在新 adapters 複製 body。
5. bootstrap、application、domain 不 import main、外部 bridge 或 legacy workflow。

## 後果

正面結果是 composition root 只有一個、uTiki 業務仍留在 family package、主入口 diff
可以限制在真實接點。成本是主入口必須明確建立／持有 engine，並把所有外層 uTiki
effect 納入 handoff/revoke/cleanup，而不是維持多個舊函式入口。

這項決策不授權重排其他平台路由，也不授權建立全專案 scheduler。未遷移平台維持
既有 handler 節奏。

## 驗收

- 一個 tab/session 只有一個 engine 與 state writer。
- import graph 不含 `platforms.ctbc`、舊 workflow、舊 alias 或第二個 composition root。
- main 的每個 uTiki startup/scheduled/health/pause/stop/cleanup 接點都有新 contract。
- 符合既有 shape 的 callable 沒有多餘 wrapper；外部 bridge 僅含 inventory 核准項。
- 非 uTiki 假 host 只能驗證 port 中立性，不能用來反向建立通用 framework。
- 上游同步檢查依 `upstream-touchpoints.md` 執行。

## 授權邊界

本 ADR 已核准架構選擇，但沒有授權真實售票網站操作或公開發布。需要真站證據的 gate
維持 `LIVE_BLOCKED`；P6c 最終刪除與公開 release 分別維持
`FINAL_DELETE_BLOCKED`、`RELEASE_BLOCKED`，直到對應授權與證據完成。
