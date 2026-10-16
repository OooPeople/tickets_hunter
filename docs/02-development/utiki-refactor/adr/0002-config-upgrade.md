# ADR-0002：uTiki 設定採唯一 canonical 格式與嚴格離線升級

- 狀態：已採用
- 日期：2026-10-16（Asia/Taipei）
- 追蹤：[Issue #17](https://github.com/OooPeople/tickets_hunter/issues/17)
- 實作分支：`refactor/17-utiki-root-v1-5`
- 實作基準：`f307f7caba054fb40aab807a4629c891e69fb200`
- Upstream 基準：`bouob/tickets_hunter@9e2d1061b48706076943b1059616347c389df809`
- 對應計劃：v1.5 §11、§14、§19、§21、§22

## 決策

採嚴格一次性離線升級：正式 uTiki runtime 只接受 canonical `utiki` 行為 section，
且三鍵完整、型別和值合法；正常 load/save/hot reload 不解析、合併、鏡射或清理
legacy `ctbc` 行為 section。

本輪 current 契約不強加 `schema_version=2`，沿用三個正式鍵：

| 鍵 | 型別／合法值 | 缺鍵轉換預設 |
|---|---|---|
| `ticket_type_keyword` | string；明確空字串是有效值 | `全票` |
| `seat_sorting` | `display`、`row_seat`、`row_asc_aisle`、`row_asc_seat_desc`、`row_desc_seat_asc` | `row_seat` |
| `seat_selection_mode` | `manual`、`computer` | `manual` |

`accounts.ctbc_cookie`、`accounts.ctbc_account`、`accounts.ctbc_password`、真實 CTBC
domain/Cookie 名稱及其他 tenant 外部契約是 current data，不是 legacy 行為 mirror，
不得因名稱含 `ctbc` 而刪除或搬入 `utiki`。

## 基準現況

基準會系統性產生 mixed profile：

- `get_default_config()` 同時建立 `utiki` 與其 `ctbc` copy：
  `src/settings.py:208-215`。
- `migrate_config()` 先逐鍵從 legacy 補 canonical，之後無條件補兩個 section，最後
  把 canonical 三鍵寫回 `ctbc`：`src/settings.py:352-367,399-422`。
- settings UI 以 `utiki`→`ctbc` fallback 讀取，保存時雙寫：
  `src/www/settings.js:130-139,1819-1826`。
- bot initial load、settings load/save/profile create 與 hot reload 都會執行 migrator：
  `src/nodriver_common.py:180-193`、`src/settings.py:432-450,824-875,900-933`、
  `src/nodriver_tixcraft.py:740-783`。
- 舊 runtime 另有 `utiki`→`ctbc` fallback：
  `src/platforms/utiki/workflow.py:142-150`。

因此從本基準升級的既有使用者通常必須執行 converter；不能把 mixed-equal 當成
少數例外，也不能要求使用者重建 profile 取代正式升級路徑。

## 分類與處理

| 輸入 | 正式 uTiki load/save/hot reload | 獨立 upgrader |
|---|---|---|
| canonical-only，三鍵完整合法 | 接受 | 合法 no-op；輸出新檔時正規寫入 |
| canonical-only 缺鍵／型別或值非法 | validation error | 只依明示轉換表補缺鍵；非法型別拒絕 |
| legacy-only | `UPGRADE_REQUIRED` | 三鍵搬至 canonical，依表補缺鍵 |
| mixed-equal | `UPGRADE_REQUIRED` | 保留 canonical 三鍵並移除 mirror |
| mixed-complementary | `UPGRADE_REQUIRED` | 依存在的合法值合併；顯式空字串不當 missing |
| mixed-conflict | `UPGRADE_REQUIRED` | 預設非零；只接受明示 canonical/legacy policy |
| legacy section 有未知鍵或疑似秘密 | `UPGRADE_REQUIRED`，不顯示值 | 拒絕破壞性轉換，只列鍵名與原因 |
| duplicate key、root 非 object、毀損 JSON | common config error | 非零且來源不變 |
| 明確建立全新 profile／無來源檔 | 建立 canonical-only defaults | 不屬轉換 |

全檔 JSON parse 與 uTiki activation validation 分開。其他平台 profile 中未啟用的舊
棒球 section 作 opaque data 原樣保留；只有啟動 uTiki、保存 uTiki 行為、UI 切成
uTiki 或啟用其 worker 時要求 canonical schema。不得因未使用的 mirror 讓其他平台
整份 profile 無法讀寫。

## Upgrader 交付契約

唯一 conversion body 位於獨立 migration tool/package，由 source CLI 與 frozen CLI
共用；bot/settings 正常 import graph 不可達 converter。

必要行為：

1. strict JSON，拒絕 duplicate key、未知 schema、錯誤 root/type，且不把 bool 當 int。
2. 預設 dry-run 或輸出新檔；output 已存在預設拒絕。
3. in-place 必須明示、建立原檔備份，且不可與 output 同時使用。
4. conflict policy 預設 `error`，只有使用者明示 `canonical` 或 `legacy` 才選邊。
5. accounts 與非 uTiki 資料的解析後值完全相同；不得 trim 或輸出秘密值。
6. 寫入前後核對來源 identity/digest；同 filesystem temp、flush、fsync、atomic replace。
7. 多 profile 先全部驗證再寫入新目錄，逐檔記 source/output digest 與結果；不宣稱
   跨檔 atomic。
8. partial failure 不啟動 bot/settings，並提供原 binary＋原設定備份的回退指引。

公開升級必須交付 Windows x64 與 macOS arm64 獨立 frozen upgrader、SHA-256、tool
manifest 及無 system Python/source tree 的端到端驗證；詳細矩陣見
`release-targets.md`。

## Runtime 與 UI 後果

- defaults、load、save、profile create、UI 與 hot reload 全部改為 canonical-only。
- current validator 為純邊界；settings 可以使用 validator，但不得 import engine。
- settings UI 顯示 upgrade-required 與工具使用方式，不以新 defaults 掩蓋舊選擇。
- invalid hot reload 保留最後有效 policy 供既有 attempt 歸屬，但撤銷新的 uTiki
  purchase/navigation/input/ordinary-refresh 權限；不清掉已送出的 attempt。
- 現行 `code-boundaries.md` 對 settings project import 的限制必須在實作前按本 ADR
  精確更新，不能用廣域例外放行 engine 或平台模組。

## 驗收與回退

- mixed-equal/conflict 不可被 runtime 靜默接受、選邊或清理。
- converter failure 時來源 bytes 不變；秘密不出現在 log、manifest 或 artifact。
- settings loopback load/save 與 bot/settings frozen config self-test 只產生 canonical-only。
- 其他平台 profile 的 opaque sections 與非 uTiki 值保持不變。
- 回退使用配對的舊 binary/commit 與原設定備份，不在新 runtime 維持雙寫 fallback。

## 授權邊界

本 ADR 核准 breaking config policy，但不等於已完成可發布升級。兩平台 frozen upgrader、
checksum/manifest 及 no-Python E2E 未通過前維持 `RELEASE_BLOCKED`。本 ADR 不授權讀取、
提交或展示真實 `src/settings.json`、profile secrets，也不授權真站操作。
