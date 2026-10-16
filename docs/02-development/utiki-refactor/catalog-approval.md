# uTiki required catalog 核准紀錄

## 核准結果

| 欄位 | 值 |
|---|---|
| Issue | [#17](https://github.com/OooPeople/tickets_hunter/issues/17) |
| Branch | `refactor/17-utiki-root-v1-5` |
| Catalog | [`ci/utiki-required-cases.json`](../../../ci/utiki-required-cases.json) |
| Catalog revision | `1.5.0` |
| Plan SHA-256 | `c8fcc741a37db3d404cefd73cfcda6731a24a922f42522777fbdbc3eeddf6328` |
| APPROVED catalog SHA-256 | `d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1` |
| Case／obligation | 153／341 |
| Reviewer | 獨立 verification subagent `/root/verification_v13` |
| Review date | 2026-10-16（Asia/Taipei） |
| Decision | APPROVED |

## 審查方式

Reviewer 逐輪對照 v1.5 §16、§16.11、§17、§18.15、§19 與 §21.3，而不是從
pytest collection、JUnit、execution manifest 或現有測試數反推需求。最終核對包含：

- 153 個固定 ID、情境與 invariant 完整且無未知／重複；
- 341 個 obligation ID 唯一，全部為 `PASS_ONLY`；
- phase、job、layer、target、variant、evidence 與 gate profile 可互相對應；
- H06 與 H11 必備負向子案例完整；
- P1 production-origin、hard-egress、cold/warm/CDP/event-loop baseline 有正式義務；
- Windows／macOS native browser 與 performance 證據都要求獨立 isolation report；
- P6b live、P6c reviewed inheritance、P7/P8 runtime/resource delta 條件無自填 false 漏洞；
- catalog 內容沒有 test node ID、現有測試數或 execution mapping 痕跡。

## Digest 轉換

完成語意審查時，catalog 仍為 `PROPOSED`，SHA-256 為
`ff3024ec20c3b3d4be87dff73285b838927ec9f1ad67e1a3f3669523cfc08164`。
Reviewer 只授權把唯一一處 status 改為 `APPROVED`，其餘語意不變，第一個核准版本 digest為
`0bfe4f7ef663f7e234b80dfc8447c7bd6ac82fdfe1051fdd616c82f7227c6bf2`。pre-commit
`git diff --cached --check` 隨後發現檔尾多一個空白行；移除該單一 LF 後，新 digest為
`d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1`。獨立 reviewer驗證
兩份 JSON解析結果完全相等、補回 LF可精確還原舊 digest，並明確批准目前 normalized bytes；
沒有進行其他 catalog變更。

任何後續 case、obligation、variant、condition source、required gate、job、target、layer、
evidence 或 result policy 變更，均須記錄 old/new digest、差異理由並重新獨立審查；不得只
修改 execution manifest 或測試映射來縮減義務。
