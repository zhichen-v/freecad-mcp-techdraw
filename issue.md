# TechDraw 操作問題記錄

> 記錄日期：2026-03-24
> 操作情境：透過 MCP RPC Server 對 STP 檔案建立 TechDraw 2D 工程圖

---

## 問題一：TechDraw 專用 MCP 工具未載入

**現象**
使用 `ToolSearch` 搜尋 `create_techdraw_page`、`add_projection_group`、`add_techdraw_view` 時，回傳「No matching deferred tools found」。

**根本原因**
這三個工具雖然在 `server.py` 中已有實作（行號 ~602、~636、~702），但 MCP Server 啟動後工具清單中並未出現。可能原因：
- MCP Server 尚未重啟以載入最新的 `server.py`
- 工具定義有 import error 導致部分工具未被註冊

**影響**
所有 TechDraw 操作退而求其次改用 `execute_code` 直接執行 Python，繞過了 MCP 工具層。

**建議修復**
- 確認 MCP Server 已重啟且工具清單包含三個 TechDraw 工具
- 在 `server.py` 加入工具載入的錯誤捕捉與日誌，方便診斷哪個工具未被註冊

---

## 問題二：TechDraw 模板路徑錯誤（`A3_Landscape.svg` 不存在）

**時間**：22:50:06

**錯誤訊息**
```
Error executing Python code: {'sErrMsg': 'Could not read the new template file', ...}
```

**原因**
CLAUDE.md 的 `TECHDRAW_TEMPLATES` 常數中記錄的快捷名對應檔案為 `A3_Landscape.svg`，但實際 FreeCAD 1.0 安裝中該檔名不存在。

**實際可用模板檔案（FreeCAD 1.0）**
```
A3_Landscape_blank.svg        ← 正確名稱
A3_Landscape_ISO5457_minimal.svg
A3_Landscape_ISO5457_advanced.svg
A3_Landscape_TD.svg
A3_Landscape_m52.svg
```

**影響**
第一次建立 Page + Template 物件時失敗，但物件殘留在文件中（`Page`、`Template`），需要手動清除。

**建議修復**
更新 `rpc_server.py` 中的 `TECHDRAW_TEMPLATES` 常數，將 `A3_Landscape` → `A3_Landscape_blank`（其他尺寸同理），或改為動態掃描模板目錄。

---

## 問題三：TechDraw 視圖無法截圖（`MDIViewPagePy` 不支援 `saveImage`）

**時間**：22:50:29

**錯誤訊息（RPC Server log）**
```
View type: MDIViewPagePy, Has saveImage: False
Current view does not support screenshots
```

**原因**
當 FreeCAD 的 Active View 是 TechDraw 頁面（`MDIViewPagePy`）時，`saveImage()` 方法不存在，RPC Server 的 `get_active_screenshot()` 回傳 `None`，`execute_code` 工具因此無法自動附上截圖。

這個問題在 CLAUDE.md 中已有描述（TechDraw 截圖需走 SVG→PNG 路線），但 `execute_code` 工具並未整合此 fallback。

**變通方案（本次使用）**
```python
TechDrawGui.exportPageAsSvg(page, svg_path)
# 再用 PySide2.QtSvg.QSvgRenderer 轉為 PNG
```

**建議修復**
在 `rpc_server.py` 的 `execute_code` 回應中，當 active view 為 `MDIViewPagePy` 時，自動呼叫 `get_techdraw_screenshot()` 作為截圖 fallback。

---

## 問題四：`TechDraw.DrawPage` 無 `PageResult` 屬性

**時間**：22:51:06

**錯誤訊息**
```
Error executing Python code: 'TechDraw.DrawPage' object has no attribute 'PageResult'
```

**原因**
`PageResult` 是舊版 FreeCAD（<=0.21）的屬性，FreeCAD 1.0 已移除。目前 CLAUDE.md 中記錄的截圖方案第一步「`page.PageResult` → 取得 SVG 暫存檔路徑」在 FreeCAD 1.0 上已失效。

**建議修復**
更新 `rpc_server.py` 的 `get_techdraw_screenshot()` 實作，改用：
```python
TechDrawGui.exportPageAsSvg(page, tmp_path)
```
取代 `page.PageResult`。

---

## 問題五：`TechDraw` 模組無 `writeSVGPage` 方法

**時間**：22:51:46

**錯誤訊息**
```
Error executing Python code: module 'TechDraw' has no attribute 'writeSVGPage'
```

**原因**
嘗試使用 `TechDraw.writeSVGPage()` 匯出 SVG（舊版 API），FreeCAD 1.0 已不存在此方法。

**正確 API（FreeCAD 1.0）**
```python
import TechDrawGui
TechDrawGui.exportPageAsSvg(page, output_path)
TechDrawGui.exportPageAsPdf(page, output_path)
```

---

## 問題六：`PySide6` 不可用，應改用 `PySide2`

**時間**：22:53:36

**錯誤訊息**
```
Error executing Python code: No module named 'PySide6'
```

**原因**
FreeCAD 1.0 內建的 Qt binding 是 `PySide2`，不是 `PySide6`。

**正確 import**
```python
from PySide2.QtSvg import QSvgRenderer
from PySide2.QtGui import QImage, QPainter
from PySide2.QtCore import Qt
```

---

## 問題七：DrawProjGroup Anchor 損壞警告

**時間**：22:52:50

**RPC Server log**
```
Warning - DPG (ProjGroup/ProjGroup) may be corrupt - Anchor deleted
```

**原因**
第一次建立 `ProjGroup` 時（在殘留的 `Page` 上操作），因 Template 未正確設定，`addProjection("Front")` 的 Anchor 在後續 `removeObject` 時被刪除，導致 FreeCAD 內部狀態損壞。

**影響**
僅影響第一次失敗的 ProjGroup，已透過清除所有 TechDraw 物件後重建解決。

**建議修復**
在 `rpc_server.py` 的 `add_projection_group()` 實作中，建立 ProjGroup 前先驗證 Page 的 Template 已正確設定。

---

## 成功的變通流程（本次實際執行）

```python
# 1. 匯入 STP
import Import
Import.open("path/to/test.stp")

# 2. 建立 TechDraw 頁面
template_path = os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates", "A3_Landscape_blank.svg")
page = doc.addObject("TechDraw::DrawPage", "TechDrawPage")
tpl = doc.addObject("TechDraw::DrawSVGTemplate", "PageTemplate")
tpl.Template = template_path
page.Template = tpl

# 3. 建立 Projection Group (Front + Right, scale=7)
proj_group = doc.addObject("TechDraw::DrawProjGroup", "ProjGroup")
page.addView(proj_group)
proj_group.Source = [source]
proj_group.ScaleType = 2
proj_group.Scale = 7.0
proj_group.addProjection("Front")
proj_group.Anchor.Direction = FreeCAD.Vector(0, 0, 1)
proj_group.addProjection("Right")
proj_group.X, proj_group.Y = 140.0, 140.0

# 4. 建立 Isometric view (scale=5)
iso_view = doc.addObject("TechDraw::DrawViewPart", "IsoView")
page.addView(iso_view)
iso_view.Source = [source]
iso_view.ScaleType = 2
iso_view.Scale = 5.0
iso_view.Direction = FreeCAD.Vector(-0.577, -0.577, 0.577)
iso_view.X, iso_view.Y = 340.0, 220.0

# 5. 匯出截圖 (SVG → PNG via PySide2)
import TechDrawGui
from PySide2.QtSvg import QSvgRenderer
from PySide2.QtGui import QImage, QPainter
from PySide2.QtCore import Qt

TechDrawGui.exportPageAsSvg(page, svg_path)
renderer = QSvgRenderer(svg_path)
image = QImage(1920, height, QImage.Format_ARGB32)
image.fill(Qt.white)
painter = QPainter(image)
renderer.render(painter)
painter.end()
image.save(png_path)
```

---
## 問題一更新（2026-03-24）

### 根因確認
- `claude_mcp_settings.json` 原本使用：
  - `uvx freecad-mcp`
- 這種啟動方式會優先使用已發布套件版本（非本機 fork 專案），因此本地 `server.py` 新增的工具可能不會被載入，導致 `ToolSearch` 找不到 TechDraw 相關 tools。

### 另外發現的阻塞點
- 本機專案 `pyproject.toml` 設定 `readme = "README.md"`，但 repo 當時沒有 `README.md`，造成本地 source build 失敗，進一步影響本機版本啟動。

### 已完成修正
- 已在專案根目錄補上 `README.md`（讓本地 build 可通過）。
- 已更新 `C:\Users\user\.claude\claude_mcp_settings.json` 的 `freecad` 設定為本機 source 啟動：

```json
{
  "command": "C:\\Users\\user\\.local\\bin\\uvx.exe",
  "args": [
    "--from",
    "C:\\Users\\user\\Desktop\\freecad-mcp",
    "freecad-mcp"
  ]
}
```

### 驗證結果
- `uvx --from C:\Users\user\Desktop\freecad-mcp freecad-mcp --help` 可正常執行，且可看到從本機專案 build 成功。
- 結論：問題一已確認並修正為「改用本機 fork 版本啟動 MCP server」。