# Tickets Hunter PyInstaller 打包與驗證指南

**最後更新**：2026-10-15

本文件說明目前 repository 實際使用的兩個 PyInstaller bundle、本機 smoke gate 與 GitHub Release 流程。若文件與程式不一致，以下列檔案為準：

- `build_scripts/build_local.py`：跨平台本機建置與 smoke gate。
- `build_scripts/nodriver_tixcraft.spec`：主程式 bundle。
- `build_scripts/settings.spec`：設定介面 bundle。
- `.github/workflows/build-release.yml`：Windows 與 Apple Silicon macOS 發佈建置。

## 快速開始

在已安裝 runtime dependencies 與 PyInstaller 的開發環境中，從專案根目錄執行：

```bash
.venv/bin/python build_scripts/build_local.py --keep
```

Windows PowerShell 可改用：

```powershell
.venv\Scripts\python.exe build_scripts\build_local.py --keep
```

`--keep` 會保留 `build/` 供查看 PyInstaller warning；不需要時可省略。若已經建置並組裝完 `dist/tickets_hunter/`，可以只重跑驗證：

```bash
.venv/bin/python build_scripts/build_local.py --skip-build --keep
```

Windows 仍保留 `build_scripts/build_and_test.bat` 作為完整 ZIP 建置工具；開發中的快速可執行 gate 以 `build_local.py` 為準。

## 建置產物

目前只有兩個可執行程式：

| Spec | 產物 | 用途 |
|---|---|---|
| `nodriver_tixcraft.spec` | `nodriver_tixcraft` / `.exe` | Zendriver 搜票主程式 |
| `settings.spec` | `settings` / `.exe` | Tornado 網頁設定介面 |

組裝後的核心目錄：

```text
dist/tickets_hunter/
├── nodriver_tixcraft[.exe]
├── settings[.exe]
├── _internal/
├── assets/
└── www/
```

macOS 建置會另複製 `build_scripts/macos/` 內的使用者 launcher。`settings.json` 不打包，由程式首次執行時產生；不得將本機設定檔、Cookie 或帳密放入發佈物。

## 本機 gate 會驗證什麼

`build_local.py` 完成以下檢查：

1. Python 版本與 PyInstaller 可用，核心 runtime modules 可 import。
2. 以兩個 spec 從乾淨的 `build/` / `dist/` 重建 bundle。
3. 合併兩個 `_internal/`，並複製 `assets/` 與 `www/`。
4. 驗證兩個可執行檔、web UI 與 ddddocr ONNX model 存在。
5. 驗證 `torch`、`matplotlib` 與 `IPython` 沒有意外被打包。
6. 執行 frozen bot `--help`，確認全部 platform import chain 可載入。
7. 執行 frozen bot `--self-test`，確認 OCR model 實際可讀取測試圖片。

Phase 驗收時必須保留命令結果。只看 PyInstaller exit code、只檢查檔案存在，或只在已安裝 Python 的開發機手動點開，都不能取代上述 smoke gate。

## Spec 與 hidden imports

`nodriver_tixcraft.spec` 以 `src/` 為 import root，明確列出專案內的 platform modules。uTiki2 family 至少應包含：

```python
hiddenimports=[
    'platforms.utiki',
    'platforms.utiki.auth',
    'platforms.utiki.dom',
    'platforms.utiki.profiles',
    'platforms.utiki.selection',
    'platforms.utiki.bootstrap',
    'platforms.utiki.production_run',
]
```

新增 family submodule 時，要在同一 Phase 評估 hidden import，並重跑本機建置。不要把 pytest、coverage 或其他開發工具加入 runtime `requirement.txt` 或 spec；測試依賴屬於 `requirements-dev.txt`。

兩個 spec 都使用 onedir（`exclude_binaries=True`）與 `upx=False`：

- onedir 可直接檢查 native dependencies，也避免 onefile 每次啟動解壓。
- 發佈流程將兩個 `_internal/` 合併到同一目錄以減少重複。
- `ddddocr` 的 data files 由 `collect_data_files('ddddocr')` 收集。
- OCR trainer 可能讓開發 venv 內存在大型套件，spec 明確排除 `torch`、`torchvision`、`matplotlib`、`IPython` 與 `tkinter`。

## GitHub Release 流程

推送 `v*` tag 會觸發 `.github/workflows/build-release.yml`：

1. Windows 使用 Python 3.11.9 與 PyInstaller 6.22.3 建置兩個 `.exe`。
2. 組裝 `dist/tickets_hunter/` 後以 `build_local.py --skip-build --keep` 執行 layout、import 與 OCR smoke。
3. 產生 ZIP，並建立 draft GitHub Release。
4. Apple Silicon macOS job 在 Windows job 完成後建置及驗證對應 bundle，再上傳到同一個 draft release。

PyInstaller 不能跨平台編譯。macOS 本機成功只證明 macOS 產物；Windows 發布前仍需 Windows job 通過。一般測試 workflow 也不取代 release build。

建立 tag 前必須先：

1. 將同一版本號同步到 `src/settings.py`、`src/nodriver_common.py`、`src/www/settings.html` 與 `src/www/settings.js`。目前 workflow 不會把 tag 自動注入這些顯示值。
2. 在 `CHANGELOG.md` 新增對應版本 heading 與內容；workflow 會以它產生 Release notes。
3. 提交上述變更，並再跑測試與本機打包 gate。

確認後才建立並推送 tag：

```bash
git tag vYYYY.MM.DD
git push origin vYYYY.MM.DD
```

建置後請下載 ZIP，在未安裝開發環境的 Windows Sandbox／VM 與實體 Apple Silicon Mac 驗證。Release 預設為 draft，未完成手動驗證前不發佈。

## 常見問題

### `ModuleNotFoundError`

1. 先確認 source mode 中的 import 成功。
2. 查看 `build/<spec-name>/warn-<spec-name>.txt`。
3. 若屬動態 import，在對應 spec 加入精確 hidden import，不要廣泛 collect 整個無關 package。
4. 重跑 `build_local.py`，並確認 frozen `--help` 真正通過。

### OCR source mode 成功、frozen mode 失敗

確認 spec 同時包含 `ddddocr`、`onnxruntime.capi.onnxruntime_pybind11_state` 與 `collect_data_files('ddddocr')`，再檢查 `dist/tickets_hunter/_internal/` 下是否有 `.onnx` 檔。以 `--self-test` 結果為準。

### Bundle 異常過大

先檢查建置 venv 是否安裝 OCR trainer 依賴，再查看 `_internal/` 是否意外出現 `torch`、notebook 或 plotting stack。不要為了縮小產物刪除 runtime 實際使用的 native module。

### macOS 無法開啟

本機建置成功後 `build_local.py` 會嘗試移除 quarantine attribute。目前 release workflow 會產生 build provenance attestation，但未進行 Apple Developer ID 簽署或 notarization，因此下載產物仍可能被 Gatekeeper 阻擋。不要為此關閉系統整體安全機制；正式對外發布前應另行評估簽署與 notarization。
