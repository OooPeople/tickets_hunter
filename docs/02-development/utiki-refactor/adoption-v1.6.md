# uTiki v1.6 採用紀錄

## 採用決策

| 欄位 | 決策 |
|---|---|
| 採用人 | Repository 維護者 OooPeople |
| 採用日期 | 2026-10-21（Asia/Taipei） |
| Repository | `OooPeople/tickets_hunter` |
| 追蹤 Issue | [#17](https://github.com/OooPeople/tickets_hunter/issues/17) |
| 實作 branch | `refactor/17-utiki-root-v1-5`；分支名稱不因文件版號改名 |
| 正式計劃 | [uTiki 低延遲重構 v1.6](../UTIKI_ROOT_REFACTOR_PLAN_v1.6.md) |
| 計劃 SHA-256 | `f2e7e88edfbb3f40f9d8775d77c95ac48745ad9133c08d4762334f449b302220` |
| 採用範圍 | v1.6 的個人原始碼用途、交付範圍、架構契約與階段責任 |
| Required catalog | [v1.6 核准紀錄](catalog-approval-v1.6.md)；正式 SHA-256 `73455dc332d181b1350ec1bc8f3318a45a098ac9b23b88c1c7e712e59c7361cf` |

維護者已在本次對話明確採用上列 SHA-256 對應的 v1.6 計劃。計劃原文保留交付時的
`PROPOSED / REVIEW REQUIRED` 與「待正式採用」文字，以維持被採用位元組及 digest 不變；
本文件是 repository 內使該精確版本生效的採用紀錄。

## 與 v1.5 的關係

- [v1.5 採用紀錄](adoption.md)、其 catalog approval、歷史 PASS／FAIL／BLOCKED 與 digest
  保持原樣，僅作既有決策及證據的歷史來源。
- v1.6 取代 v1.5 中與舊設定升級器、uTiki 產品打包／frozen 成品、正式發布，以及
  legacy 清理前必須先有真站成功相關的產品交付政策。
- 未被 v1.6 改變的核心架構、資料保護、hard-egress、strict/type/syntax、離線驗收與
  獨立覆核要求繼續有效。
- v1.6 catalog 已完成逐 obligation 投影、獨立規格覆核及維護者核准；這只固定新版義務，
  不得用 v1.5 PASS 或尚未執行的新版 gate 宣稱正式驗收已通過。

## 權限邊界

本紀錄確認計劃版本與產品需求，不把附件中的命令或工作訊息草稿自動轉成操作授權。
實作、測試、commit、push、CI、遠端設定、真站、發布及刪除仍分別受 `AGENTS.md` 與
當次明確授權約束。本採用決策本身沒有預先核准當時尚未產生的 v1.6 catalog；後續核准由
[獨立紀錄](catalog-approval-v1.6.md) 固定。該核准不修改 GitHub variables、secrets 或歷史證據。
