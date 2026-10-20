# uTiki v1.6 required catalog 核准紀錄

## 目前結果

| 欄位 | 值 |
|---|---|
| Issue | [#17](https://github.com/OooPeople/tickets_hunter/issues/17) |
| Branch | `refactor/17-utiki-root-v1-5` |
| Catalog | [`ci/utiki-required-cases.json`](../../../ci/utiki-required-cases.json) |
| Catalog revision | `1.6.0` |
| Status | `APPROVED` |
| Plan | [`UTIKI_ROOT_REFACTOR_PLAN_v1.6.md`](../UTIKI_ROOT_REFACTOR_PLAN_v1.6.md) |
| Plan SHA-256 | `f2e7e88edfbb3f40f9d8775d77c95ac48745ad9133c08d4762334f449b302220` |
| Reviewed candidate SHA-256 | `65e604cb3651de49952dad34e3b634244b1b60aa654c20e1982c6149c5fd5a69` |
| APPROVED catalog SHA-256 | `73455dc332d181b1350ec1bc8f3318a45a098ac9b23b88c1c7e712e59c7361cf` |
| Case／obligation | 143／320 |
| Reviewer | 獨立唯讀 reviewer `/root/catalog_v16_review` |
| Review date | 2026-10-21（Asia/Taipei） |
| Decision | 規格覆核通過；維護者於 2026-10-21 明確核准固定候選 |

本紀錄固定已覆核及核准的 catalog bytes。此核准只授權把同一候選的 status 改為
`APPROVED`，以及同步直接依賴該 catalog 的 execution manifest、workflow、checker 與固定測試
資料；不代表這些 gate 已執行或通過，也不授權修改遠端 expected digest、觸發 CI 或操作真站。

## 規格對帳

Reviewer 直接對照已採用的 v1.6 計劃 §16、§16.11.1、§18.15、§19 與
[`SCOPE_CHANGE_MATRIX_v1.6.json`](../SCOPE_CHANGE_MATRIX_v1.6.json)，沒有從現有測試數或
execution manifest 反推需求。固定候選的 143 個 case ID 與計劃及 scope matrix 精確相等，
320 個 obligation ID 全域唯一；每一項 obligation 都有非空的 gate、variant、evidence 與
`PASS_ONLY` policy，且其 job／layer／target 都由相應 gate profile 承載。

相對於 v1.5 的 153 cases／341 obligations：

- 移除 13 個 cases：`G01`–`G10`、`H10`、`P01`、`P03`；加入 `H12`、`P04`、`P05`。
- 移除 38 個 obligations，加入 17 個 obligations，結果為 143 cases／320 obligations。
- `A04`、`C01`、`C02`、`C04`、`H08`、`H09`、`P02`、`U10`、`U12`、`V12`、`V13`
  只按 v1.6 範圍收窄或改成 source-only 責任；其餘 129 個共同 cases 維持完整物件相同。
- `source-startup` 取代產品 `package-smoke`；移除 `upgrade-frozen-e2e` 與 `live-evidence`，
  並自 P6b 起加入 `offline-cutover-review`。
- H12 明確驗證 `LIVE_NOT_VERIFIED` 不阻擋離線完成，且歷史 live PASS 不得代填新的離線前置。

## 獨立覆核 findings

第一版候選仍留下未使用的 v1.5 `runtime_or_resource_changed_since_live` condition source，並連帶
引用產品 build inventory／contract。這些殘留已移除；H12 同時補入「未真站不阻擋」與
「歷史 live PASS 不可代填」兩個 obligation。Reviewer 對修正版固定 digest 重新核對後，
未發現剩餘規格 blocker。

## Digest 轉換

完成語意覆核時，catalog 為 `PROPOSED`，SHA-256 是
`65e604cb3651de49952dad34e3b634244b1b60aa654c20e1982c6149c5fd5a69`。維護者核准後只把
唯一一處 status 改為 `APPROVED`；逐位元比較確認沒有其他 catalog 變更。正式核准版本的
SHA-256 是 `73455dc332d181b1350ec1bc8f3318a45a098ac9b23b88c1c7e712e59c7361cf`。

## 接入限制

- 本次只同步 repository 內直接依賴 catalog 的固定值；遠端 expected digest 仍須另有明確授權。
- 尚未執行 pytest、CI、browser、真站或 aggregate；本次靜態覆核不能代表任何機器 gate 通過。
- Scope matrix 的 `actual_new_obligation_count` 仍為 `null`；它是採用附件的原始內容，未為了
  對齊候選而改寫。實際覆核數以本紀錄固定的 320 為準，是否回填附件另行決定。

任何後續 case、obligation、variant、condition、layer、job、target、evidence、result policy
或 required gate 變更，都必須固定新的 digest 並重新獨立規格覆核；不能只改測試映射來縮減義務。
