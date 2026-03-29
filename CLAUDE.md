# FreeCAD MCP TechDraw Extension 專案

## 專案概覽

- **版本**：0.1.16
- **Python**：>=3.12
- **套件管理**：uv + hatchling
- **入口點**：`freecad-mcp` CLI → `freecad_mcp.server:main`
- **協議**：MCP (Model Context Protocol) over stdio，內部透過 XML-RPC 與 FreeCAD 通訊
- **授權**：MIT

## 檔案結構

```
freecad-mcp/
├── pyproject.toml                          # 專案設定、依賴、入口點
├── tech_draw_test_template.svg             # TechDraw 自訂測試模板（A4 Landscape）
├── sample/
│   ├── test.stp                            # 測試用 3D STP 檔（11×1×16mm 小型零件）
│   └── test.png                            # TechDraw 投影參考截圖（驗證基準）
├── src/freecad_mcp/
│   ├── server.py                           # MCP Server 主程式
│   ├── __init__.py
│   └── py.typed
├── addon/FreeCADMCP/                       # FreeCAD Addon（複製到 FreeCAD Mod 目錄）
│   ├── InitGui.py                          # Workbench 註冊 + auto-start RPC
│   └── rpc_server/
│       ├── rpc_server.py                   # RPC Server 主程式（FreeCADRPC class）
│       ├── serialize.py                    # FreeCAD 物件 → dict 序列化
│       └── parts_library.py               # 零件庫操作
└── examples/                               # ADK / LangChain 整合範例
```

## 新增工具

新增 MCP 工具請使用 `freecad-mcp-tool-builder` skill（`.claude/skills/`），
該 skill 包含完整的四層架構模式、程式碼模板與 checklist。

簡要說明：每個新工具需要修改 `rpc_server.py`（RPC 端）與 `server.py`（MCP 端），共四層：
`_xxx_gui()` → public RPC method → `FreeCADConnection` wrapper → `@mcp.tool()`

## 已實作的 MCP 工具

### 基本工具

| 工具                       | 說明                                     |
| -------------------------- | ---------------------------------------- |
| `create_document`          | 建立新文件                               |
| `create_object`            | 建立物件（Part/Draft/PartDesign/Fem 等） |
| `edit_object`              | 編輯物件屬性                             |
| `delete_object`            | 刪除物件                                 |
| `execute_code`             | 執行任意 Python 程式碼（見下方使用限制） |
| `get_view`                 | 截取 3D 視圖截圖                         |
| `get_objects`              | 列出文件中所有物件                       |
| `get_object`               | 取得單一物件詳細資訊                     |
| `list_documents`           | 列出開啟的文件                           |
| `insert_part_from_library` | 從零件庫插入零件                         |
| `get_parts_list`           | 列出零件庫清單                           |

### TechDraw 工具

| 工具                   | 說明                                                       |
| ---------------------- | ---------------------------------------------------------- |
| `create_techdraw_page` | 建立 TechDraw 圖紙頁面（A0–A4 × Landscape/Portrait）       |
| `add_projection_group` | 建立多視圖投影群組（DrawProjGroup），支援第一角/第三角投影 |
| `add_techdraw_view`    | 建立單一 2D 投影視圖（DrawViewPart）                       |

### Prompt

| 名稱                      | 說明                                       |
| ------------------------- | ------------------------------------------ |
| `asset_creation_strategy` | 建立資產的策略指引（含 TechDraw 工作流程） |

## `execute_code` 使用限制（重要）

`execute_code` 是用於**修改核心代碼前的測試與驗證**，不應跳過既有 MCP 工具而直接作為替代方案使用。

### 禁止用法

- **禁止用 `execute_code` 重寫已有 MCP 工具的功能**：例如不可手動寫 SVG→PNG 截圖代碼來取代 `get_techdraw_screenshot`，因為這會繞過 `rpc_server.py` 中的修復邏輯（如 `_fix_techdraw_svg_template_scale`），導致截圖結果錯誤。
- **禁止用 `execute_code` 直接操作 TechDraw**：應使用 `create_techdraw_page`、`add_projection_group`、`add_techdraw_view` 等專用 MCP 工具，這些工具內建了正確的參數處理（如 `ScaleType = "Custom"`）和自動截圖。
- **禁止在 `execute_code` 中直接呼叫 RPC 實例方法**：會導致線程衝突或遞迴，可能造成 FreeCAD 崩潰。

### 正確用法

- **測試與驗證**：在修改 `rpc_server.py` 或 `server.py` 前，先用 `execute_code` 小範圍測試 FreeCAD API 行為
- **查詢資訊**：取得 BoundBox、物件屬性、模組狀態等輔助資訊
- **匯入檔案**：`Import.open(stp_path)` 等尚無對應 MCP 工具的操作
- **最終驗證截圖**：在所有 MCP 工具操作完成後，用 `execute_code` 做最終的 SVG→PNG 驗證時，**必須包含 `_fix_techdraw_svg_template_scale` 等同邏輯**，不可省略

## 截圖機制注意事項

- **3D 視圖**：透過 `saveImage()` 截取，不支援 TechDraw / Spreadsheet 視圖
- **TechDraw 截圖**：使用 `TechDrawGui.exportPageAsSvg()` → `QSvgRenderer` → PNG 的 SVG 轉換方案
- **SVG 模板 scale 修正**：`TechDrawGui.exportPageAsSvg()` 會對模板群組加上 `transform="scale(10, 10)"`，假設模板座標為 mm 單位。但自訂模板（如 `tech_draw_test_template.svg`，viewBox `0 0 3000 2121`，座標已在 ~3000 範圍）會被放大 10 倍超出 viewBox，導致只截到左上角。`_fix_techdraw_svg_template_scale()` 靜態方法會在 SVG 匯出後偵測此情況並移除多餘的 scale transform。偵測邏輯：若模板群組內第一個座標值 > viewBox 寬度的 50%，則移除 scale(10)。此修正對 FreeCAD 內建模板（mm 座標 ~0-297）不會生效，僅影響座標系已放大的自訂模板。
- **`execute_code` 無 TechDraw 截圖**：`execute_code` 僅使用 3D 視圖截圖（`get_active_screenshot`），不嘗試 TechDraw 截圖。TechDraw 截圖由專用 MCP 工具（`create_techdraw_page`、`add_projection_group`、`add_techdraw_view`）內建處理。
- **舊 API 已移除**：`page.PageResult` 與 `TechDraw.writeSVGPage()` 在 FreeCAD 1.0 不存在，必須用 `TechDrawGui.exportPageAsSvg()`

## 設計決策

- MCP 工具用扁平參數，內部打包為 `options` dict 傳給 RPC
- TechDraw 最小範圍：不含標註 (dimension)、剖面圖 (section view)，留待後續擴充
- `--only-text-feedback` 旗標：停用所有截圖回傳（僅回傳文字）
- `--host` 旗標：指定 RPC server 位址（支援遠端連線）

## 注意事項

- 新增/修改 `rpc_server.py` 後需重新載入 FreeCAD Addon（或重啟 FreeCAD）
- 新增/修改 `server.py` 後需重啟 MCP server，新工具才會出現在 Claude 工具列表
- `QtSvg` import 有 `HAS_QT_SVG` fallback 保護，若不可用則 TechDraw 截圖回傳 None

## FreeCAD 1.0 相容性注意

- `page.PageResult` 屬性已移除，TechDraw 截圖改用 `TechDrawGui.exportPageAsSvg()`
- `TechDraw.writeSVGPage()` 已移除，同樣改用 `TechDrawGui.exportPageAsSvg()`
- FreeCAD 1.0 內建的 Qt binding 是 `PySide2`（非 `PySide6`），addon 中使用 `from PySide import ...` 即可
- TechDraw 模板檔名為 `A3_Landscape_blank.svg` 格式（非 `A3_Landscape.svg`），`TECHDRAW_TEMPLATES` 已對應更新

## 測試資源與驗證基準

### TechDraw 模板

- **專案自訂模板**：`tech_draw_test_template.svg`（A4 Landscape，297×210mm）
  - 使用方式：`create_techdraw_page` 的 `template` 參數傳入絕對路徑
    ```
    C:/Users/user/Desktop/freecad-mcp/tech_draw_test_template.svg
    ```
- **FreeCAD 預設模板**：使用者已在 FreeCAD 中設定好預設 template，不指定模板也會載入預設模板
- **完全空白頁面**是最後的選項，正常情況下不應出現

### 測試用 3D 檔案

- **路徑**：`sample/test.stp`
- **物件尺寸**：11×1×16mm（非常小的零件）
- **匯入方式**：透過 `execute_code` 執行 `Import.open("C:/Users/user/Desktop/freecad-mcp/sample/test.stp")`
- **匯入後物件名稱**：`Part__Feature`（TypeId: `Part::Feature`）
- **匯入後文件名稱**：`Unnamed`

### 驗證參考截圖

- **路徑**：`sample/test.png`
- **內容**：test.stp 的 TechDraw 投影結果（Front + Right + Isometric）
- **驗證重點**（優先順序）：
  1. **Scale 正確**：各視圖比例因子合理，投影結果大小適當
  2. **投影方式正確**：Third Angle（第三角）投影
  3. **投影結果正確**：各視圖形狀與參考一致
- **非驗證項目**：視圖的絕對位置（X, Y 座標）不需要完全一致

## TechDraw 操作最佳實踐（重要）

### 標準工作流程

建立 TechDraw 2D 工程圖的完整步驟：

1. **匯入 3D 模型**：`execute_code` → `Import.open(stp_path)`
2. **查詢物件尺寸**：`execute_code` → 取得 `Shape.BoundBox`（XLength, YLength, ZLength）
3. **建立 TechDraw 頁面**：`create_techdraw_page`（使用自訂模板或預設模板）
4. **確認頁面尺寸**：從 `page.Template.Width/Height` 取得可用繪圖區域
5. **計算合適的 Scale**：根據物件 BoundBox 與頁面尺寸計算
6. **加入投影群組**：`add_projection_group`（Front + Right 等）
7. **加入等角視圖**：`add_techdraw_view`（Isometric 方向）
8. **驗證結果**：`execute_code` 匯出 SVG→PNG 截圖，與參考圖比對

### 常見錯誤與避免方式

#### 1. anchor_direction 方向選擇錯誤

**錯誤**：使用 `[0, 0, 1]`（俯視方向）作為 Front 視圖的 anchor_direction，導致 Front 視圖顯示的是 XY 平面而非 XZ 平面。

**正確做法**：
- **先觀察 3D 物件的 BoundBox**，判斷哪個面是「正面」
- 大多數情況下，Front 視圖的預設方向 `[0, -1, 0]` 是正確的（從 +Y 方向看向原點，顯示 XZ 平面）
- 若物件主要面在 XY 平面，才考慮使用 `[0, 0, 1]`
- **不確定時，使用預設值** `[0, -1, 0]`

#### 2. 視圖位置超出頁面範圍

**錯誤**：將視圖的 X 座標設為 340mm，但 A4 Landscape 頁面寬度僅 297mm，導致視圖不可見。

**正確做法**：
- 建立頁面後，先確認頁面尺寸（A4 Landscape = 297×210mm）
- 所有視圖的 X, Y 座標必須在頁面範圍內
- 留出足夠邊距，考慮視圖本身的寬高（= 物件尺寸 × Scale）

#### 3. ScaleType 未設為 Custom（重要）

**問題**：TechDraw 視圖的 `ScaleType` 預設為 `"Page"`，表示使用頁面的預設 Scale（通常為 1.0）。即使透過 MCP 工具傳入 `scale` 參數，若 `ScaleType` 仍為 `"Page"`，Scale 值不會生效，視圖永遠以 Scale=1.0 顯示。

**正確做法**（已在 `rpc_server.py` 中修正）：
- 在設定 `Scale` 值之前，必須先將 `ScaleType` 設為 `"Custom"`
- `_add_projection_group_gui()` 和 `_add_techdraw_view_gui()` 中的順序：
  ```python
  view.ScaleType = "Custom"  # 必須先設定
  view.Scale = scale          # 之後設定才會生效
  ```
- FreeCAD `ScaleType` 可用值：`"Page"`（使用頁面預設）、`"Automatic"`（自動計算）、`"Custom"`（使用手動指定值）

#### 4. Scale 計算不當

**問題**：未先查詢物件尺寸就設定 Scale，導致視圖過大或過小。

**正確做法**：
- **必須先查詢 BoundBox**：`obj.Shape.BoundBox`
- 根據物件最大面的尺寸與頁面可用空間計算合適的 Scale
- 參考公式：`scale = 可用空間 / (物件尺寸 × 視圖數量的空間分配)`
- 對於 sample/test.stp（11×1×16mm），Scale=7 的投影群組 + Scale=5 的等角視圖是合適的

#### 5. TechDraw 截圖時機

**問題**：MCP 工具回傳的截圖可能是空白或不完整的，因為 TechDraw 視圖可能尚未完成渲染。

**正確做法**：
- 在所有視圖建立完成後，先呼叫 `doc.recompute()`
- 使用 `execute_code` 手動匯出 SVG→PNG 來進行最終驗證
- MCP 工具自帶的截圖僅供初步參考

### Scale 計算指引

對於 A4 Landscape 頁面（可用區域約 260×180mm）：

| 物件最大面尺寸 | 建議 Scale 範圍 | 說明                     |
| -------------- | --------------- | ------------------------ |
| < 20mm         | 5–10            | 小型零件，需放大         |
| 20–50mm        | 2–5             | 中型零件                 |
| 50–100mm       | 1–2             | 大型零件                 |
| > 100mm        | 0.5–1           | 特大零件，可能需縮小     |

### 位置佈局建議（A4 Landscape）

| 視圖類型                 | 建議位置 (X, Y) | 說明                           |
| ------------------------ | --------------- | ------------------------------ |
| ProjGroup（Front+Right） | (100–130, 120–150) | 偏左上方，留空間給等角視圖   |
| Isometric（單獨視圖）    | (200–240, 50–80)   | 右下區域（TechDraw Y 軸朝上）|

> **注意**：TechDraw 的 Y 座標系統是從頁面底部向上的，Y=0 是底邊，Y=210 是 A4 的頂邊。

## 變更邊界限制（重要）

- 本專案是從 https://github.com/neka-nat/freecad-mcp 的穩定版本 fork 而來 (./source-code)。
- 主要目標是在既有穩定功能上新增（或擴充）TechDraw 相關 MCP tools。
- 在正常情況下，不允許修改原始框架與既有工具行為，避免引入不必要的崩潰風險。
- 若確實需要調整原始框架或既有工具，必須先明確說明必要性、風險與影響範圍，並取得同意後再進行。
