# TechDraw 操作問題記錄

> 記錄日期：2026-03-24
> 修復日期：2026-03-26
> 操作情境：透過 MCP RPC Server 對 STP 檔案建立 TechDraw 2D 工程圖

---

## 導覽指南

本文件分為兩個區塊：

- **更新日誌**：整理本次已解決的問題，快速查看修正結果。
- **已知問題詳細**：保留給未來新的、尚未解決的問題使用，記錄現象、原因、影響與處理進度。

---

## 更新日誌

### 2026-03-26

#### 1. TechDraw 專用 MCP 工具未載入

**解決方法**：改用本機 fork 版本啟動 MCP Server，確保載入最新 `server.py` 與 TechDraw 工具定義。

#### 2. TechDraw 模板路徑錯誤（`A3_Landscape.svg` 不存在）

**解決方法**：更新 `TECHDRAW_TEMPLATES` 對應檔名，改為 FreeCAD 1.0 可用的 `*_blank.svg` 模板。

#### 3. TechDraw 視圖無法截圖（`MDIViewPagePy` 不支援 `saveImage`）

**解決方法**：在 `server.py` 的 `execute_code` 加入 TechDraw 截圖 fallback，自動呼叫 `get_techdraw_screenshot()`。

#### 4. `TechDraw.DrawPage` 無 `PageResult` 屬性

**解決方法**：改用 `TechDrawGui.exportPageAsSvg()` 匯出 SVG，再執行 SVG → PNG 截圖流程。

#### 5. `TechDraw` 模組無 `writeSVGPage` 方法

**解決方法**：統一改用 FreeCAD 1.0 可用的 `TechDrawGui.exportPageAsSvg()` API。

#### 6. `PySide6` 不可用，應改用 `PySide2`

**解決方法**：手動 `execute_code` 腳本改用 `PySide2` import；addon 既有 `PySide` 寫法維持可用。

#### 7. DrawProjGroup Anchor 損壞警告

**解決方法**：在建立 ProjGroup 前先驗證 Page 的 Template 是否有效，避免損壞 Anchor。

---

## 已知問題詳細

目前沒有未解決問題。

後續若有新問題，建議依下列格式補充：

### 問題標題

**時間**

**現象**

**錯誤訊息**

```text
貼上錯誤訊息
```

**可能原因**

**影響**

**處理進度**

**備註**
