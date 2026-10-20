# v1.6 文件層級檢查結果

**檢查對象：文件，而非 tickets_hunter 程式碼。**
**最終結果：19 項通過。這不是獨立程式審查、pytest、mypy、CI、build 或真站驗收。**

## 檔案識別

| 項目 | 結果 |
|---|---|
| 唯一底稿 | `sources/UTIKI_ROOT_REFACTOR_PLAN_v1.5.md` |
| v1.5 | 2,679 行、232,652 bytes；SHA-256 `c8fcc741a37db3d404cefd73cfcda6731a24a922f42522777fbdbc3eeddf6328` |
| v1.6 | 2,646 行、234,526 bytes |
| v1.6 SHA-256 | `f2e7e88edfbb3f40f9d8775d77c95ac48745ad9133c08d4762334f449b302220` |
| Git diff stat | 1 file changed, 508 insertions(+), 541 deletions(-) |
| 淨行數 | −33 行；版面與來源說明也包含於差異，非程式碼變更量 |
| 章節／固定節號 | 24 個主要章節，196 個唯一編號標題，60 個直接節號參照均可定位 |
| active case | 143 個；153 − 13 ＋ 3；實際新 obligation 數尚待 catalog 投影核對 |
| 狀態 | PROPOSED / REVIEW REQUIRED |

## 實際執行的文件檢查

文件檢查由此模型工作目錄中的 Python 文件處理程式執行，讀取本包 Markdown／JSON、原始底稿與差異；沒有匯入或執行專案模組。

| # | 檢查 | 結果 |
|---|---|---|
| 01 | 唯一底稿與原始位元組 | PASS |
| 02 | 24章完整且順序唯一 | PASS |
| 03 | 節號不重複 | PASS |
| 04 | 章節參照可解析 | PASS |
| 05 | 有效案例143且唯一 | PASS |
| 06 | 情境矩陣／固定清單／處置JSON一致 | PASS |
| 07 | 153減13加3且不重用撤回ID | PASS |
| 08 | 89項核心案例逐列保留 | PASS |
| 09 | 核心章節5-9/12-13保留 | PASS |
| 10 | 自主規範有限診斷與固定覆核保留 | PASS |
| 11 | 新文件UTF8/LF/檔尾與無行尾空白 | PASS |
| 12 | Markdown程式區塊成對與表格連續 | PASS |
| 13 | 繁體中文指定殘留掃描 | PASS |
| 14 | 計劃JSON範例可解析 | PASS |
| 15 | 可交付相對連結存在 | PASS |
| 16 | 已取消交付無殘留執行命令 | PASS |
| 17 | unified diff可精確重建完整v1.6 | PASS |
| 18 | git diff --no-index --check | PASS |
| 19 | 差異行數算術一致 | PASS |

詳細輸出、計數、hash、控制案例退出碼與 diff 重建結果在 `DOCUMENT_CHECK_DETAILS_v1.6.json`。

核心保留檢查：§5／§6／§9／§12／§13 與 v1.5 逐字相同；§7 僅將「升級前」改為「切換原始碼版本前」，§8 僅將「發布說明」改為「使用說明」後相同。D/N/S/R/L/X/T/I 共89項核心案例逐列相同。自主規範v1.1的§7有限診斷與§10固定覆核逐字保留。

繁體掃描檢查一份指定簡體／非台灣用語清單與全文人工自檢；這不是保證已用語言學工具驗盡所有罕見字。歷史附件位元組原樣保存，不因其語言或舊政策改寫。

## 實際 Git／patch 命令及退出碼

以下 `BASE`／`NEW`／`DIFF` 指本包文件，不是 repository source：

```bash
git diff --no-index --no-ext-diff --no-color --unified=3 "$BASE" "$NEW"
git diff --no-index --stat "$BASE" "$NEW"
git diff --no-index --numstat "$BASE" "$NEW"
git diff --no-index --check "$BASE" "$NEW"
patch --batch --output "$TEMP/reconstructed.md" "$TEMP/base.md" "$DIFF"
```

前三個 Git 命令退出碼為1，表示兩文件有差異。`--check` 同樣在 `--no-index` 模式回1，但 stdout／stderr 均空，沒有空白錯誤。另用文件專用控制樣本確認：正常不同文件回1且無診斷；故意新增行尾空白回3並指出 `trailing whitespace`。因此沒有把本例的差異退出碼誤報成檢查失敗，也沒有掩蓋空白診斷。

`patch` 退出碼為0，重建輸出與完整v1.6位元組完全相同。補丁檔的 +/− 行與 Git numstat 一致：508／541。這是文件差異重建，沒有對 tickets_hunter 套用 patch。

JSON檢查包含完整計劃的兩個JSON示例，以及本包新增JSON檔可解析。示例仍明標非完整catalog；可解析不等於正式核准。

相對連結只核對交付檔案可定位及明示的repository安裝位置。R/E歷史外部連結未重新上網驗證，不能宣稱全部網址目前可用。

## 範圍與來源自檢

已檢視當前政策段落與命令：無要求執行upgrader、PyInstaller／frozen或發布的命令；產品相關詞只在撤回說明、歷史來源或不可變模型用語中保留。`live audit`／domain `frozen=True` 與真站測試／產品打包不同，未誤刪。

新的required集合、退役ID／新ID、P6b離線覆核、P6c清理、P8 source結案、設定只重填uTiki、保留P1及其他平台保護，在計劃、摘要與Codex訊息對齊。這是文件一致性自檢，不是獨立reviewer已核准的宣稱。

`SOURCE_MANIFEST_v1.6.json`記錄實際附入來源及原始SHA-256。R01／早期清理規則只封存，不假裝本輪全文重讀。唯一計劃底稿v1.5與自主規範v1.0已全文讀取；需求確認直接來自本次對話。

## 沒有執行的驗證或操作

沒有存取或修改目前tickets_hunter repository、Codex工作樹、Issue／PR／branch／遠端變數。沒有執行專案py_compile、pytest、mypy、architecture gate、browser、Docker/test-image build、產品build、benchmark、設定轉換／重設、真站、CI、發布或合併。沒有另請獨立reviewer驗收本文件；仍需採用與必要範圍審查。

## 壓縮包及雜湊

最終包另以ZIP CRC與逐檔解壓位元組比對，並核對`SHA256SUMS`；結果見`BUNDLE_CHECK_v1.6.json`（存於ZIP旁）。`SHA256SUMS`不含自身，ZIP與旁附檢查結果不建立循環自我雜湊。這些校驗不等於catalog內容核准或發布簽章。
