# uTiki v1.5 採用與授權紀錄

## 採用決策

| 欄位 | 決策 |
|---|---|
| 採用人 | Repository 維護者 OooPeople |
| 採用日期 | 2026-10-16（Asia/Taipei） |
| Repository | `OooPeople/tickets_hunter` |
| 追蹤 Issue | [#17](https://github.com/OooPeople/tickets_hunter/issues/17) |
| 實作 branch | `refactor/17-utiki-root-v1-5` |
| 實作基準 | `f307f7caba054fb40aab807a4629c891e69fb200` |
| Upstream 基準 | `bouob/tickets_hunter@9e2d1061b48706076943b1059616347c389df809` |
| 正式計劃 | [uTiki 低延遲重構 v1.5](../utiki-root-refactor-plan-v1.5.md) |
| 計劃 SHA-256 | `c8fcc741a37db3d404cefd73cfcda6731a24a922f42522777fbdbc3eeddf6328` |
| 實作授權 | 依 v1.5 的 P0→P8 順序持續實作、驗證與階段審查 |
| 審查責任 | Codex 主代理整合；高風險階段由獨立 subagent 複審 |

維護者已在本次對話明確採用目前這份 v1.5，並授權開始修改。階段 gate
由實作者依計劃自行審查；只有會改變產品政策、擴大法律／安全邊界，或無法由
現有證據裁決的重大選擇才需要再次請示。

計劃原文保留交付時的 `PROPOSED / REVIEW REQUIRED` 狀態與原始位元組，
本文件是 repository 內使該提案生效的採用紀錄；不得修改計劃原文後仍沿用上列
digest。

## 已採用的架構與 breaking 決策

1. ADR-01 採方案 A：主入口直接使用新的
   `platforms.utiki.bootstrap.create_engine(...)`；只有真實 current-core
   契約不相符時才建立模組外薄適配，不預建頂層 integration framework。
2. ADR-02 採嚴格唯一設定：正式 runtime 只接受 canonical `utiki` 行為 section；
   legacy-only、mixed-equal、mixed-complementary 與 mixed-conflict 均先經獨立
   offline upgrader。帳號、Cookie 與既有 CTBC 外部契約不是 legacy 行為 mirror。
3. ADR-03 採 v1.5 發布順序：P3a 第一次外部化 production JS 時同步完成打包驗證；
   P6b 指定真站 smoke 先於 P6c 最終 legacy 刪除；P8 重新驗證最終成品。
4. 最終 runtime 不保留舊 facade、API alias、legacy model／decoder／workflow、
   設定 fallback／mirror或 private test bridge。
5. 其他平台只允許為 current-core caller、共用規則或建置所需的最小變更；不得
   藉本 Issue 重構其他平台。

## 本次授權不包含

- 真實售票網站連線、登入、刷新、選票、送出或交易操作。
- 自動付款、提高刷新頻率、繞過票務平台風控或增加驗證規避能力。
- GitHub Release、公開發行、tag、正式部署或自動發布。
- 刪除 P6a 候選中的最後 legacy 實作。P6c 仍須先通過 P6b 並取得對應候選的
  真站證據與 final-delete 覆核。

以上工作若成為當前 gate，狀態須明確標為 `LIVE_BLOCKED`、
`FINAL_DELETE_BLOCKED` 或 `RELEASE_BLOCKED`，不能以離線測試代填。

## Required catalog 核准

v1.5 固定的 153 個 ID 已細分為 341 個 obligation，並由獨立 verification
subagent 逐項對照計劃，而不是由現有測試反推。正式 catalog 為
`ci/utiki-required-cases.json`，狀態 `APPROVED`，核准 SHA-256：

`d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1`

完整 review、PROPOSED→APPROVED 的唯一位元組變更與 reviewer 紀錄見
[catalog-approval.md](catalog-approval.md)。任何規格變更均須重新取得獨立 review
及新 digest，execution manifest、JUnit 或 run metadata 不得自行改寫 required 集合。

發布 target manifest亦由同一獨立 reviewer對照 repository現況與 v1.5 target契約核准；
`ci/utiki-release-targets.json` 的固定 SHA-256 為
`9cc875e624070c67c251514b97d973cc1f08b764687578573e1f9ad16052ce85`。核准範圍與變更規則見
[release-targets.md](release-targets.md)。

## 修改與工作目錄保護

- 開始時 `main` 與 `origin/main` 同為實作基準，工作目錄乾淨。
- 不使用 `git reset --hard`、`git clean` 或會覆寫使用者工作的操作。
- 每個階段先記錄 base/candidate，修改過的 Python 檔需逐檔編譯；聚焦測試、
  完整 suite、browser、build、live 各自如實陳述。
- `src/settings.json`、profile、Cookie、token、webhook 與完整個人資料不得
  進入程式碼、測試、Issue、artifact 或 log。
