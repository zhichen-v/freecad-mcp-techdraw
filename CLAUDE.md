# FreeCAD MCP Techdraw Extension 專案

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
│   ├── __init__.py                         # 空
│   ├── py.typed                            # PEP 561 型別標記
│   └── server.py                           # MCP Server 主程式（~840 行）
│                                           #   - FreeCADConnection class（XML-RPC client wrapper）
│                                           #   - @mcp.tool() 工具定義（14 個）
│                                           #   - @mcp.prompt() 提示定義（1 個）
│                                           #   - add_screenshot_if_available() 截圖輔助
│                                           #   - main() CLI 入口（--only-text-feedback, --host）
├── addon/FreeCADMCP/                       # FreeCAD Addon（複製到 FreeCAD Mod 目錄）
│   ├── Init.py                             # 空（FreeCAD 要求）
│   ├── InitGui.py                          # Workbench 註冊 + auto-start RPC
│   └── rpc_server/
│       ├── __init__.py                     # re-export rpc_server
│       ├── rpc_server.py                   # RPC Server 主程式（~1020 行）
│       │                                   #   - FreeCADRPC class（所有 RPC 方法）
│       │                                   #   - FilteredXMLRPCServer（IP 過濾）
│       │                                   #   - GUI Command classes（5 個 toolbar 按鈕）
│       │                                   #   - start/stop_rpc_server()
│       │                                   #   - process_gui_tasks()（QTimer 驅動的 queue 消費）
│       ├── serialize.py                    # FreeCAD 物件 → dict 序列化
│       │                                   #   - serialize_value/shape/view_object/object
│       └── parts_library.py               # 零件庫操作
│                                           #   - insert_part_from_library()
│                                           #   - get_parts_list()（@cache）
└── examples/
    ├── adk/                                # Google ADK 整合範例
    │   ├── .env                            # API key 設定
    │   ├── __init__.py
    │   └── agent.py
    └── langchain/
        └── react.py                        # LangChain ReAct 範例

```

## 通訊流程

```
Claude ─(stdio)→ MCP Server (server.py)
                    │
                    ├─ FreeCADConnection.method()    ← XML-RPC client wrapper
                    │         │
                    │         ▼
                    │  xmlrpc.client.ServerProxy ─(HTTP)→ FreeCADRPC (rpc_server.py)
                    │                                          │
                    │                                          ├─ public method: 放入 rpc_request_queue
                    │                                          │
                    │                                          ▼
                    │                                   process_gui_tasks() ← QTimer 每 50ms 觸發
                    │                                          │
                    │                                          ├─ _xxx_gui(): 在 FreeCAD 主執行緒執行
                    │                                          │
                    │                                          ▼
                    │                                   rpc_response_queue.put(result)
                    │                                          │
                    ▼                                          ▼
              回傳 TextContent / ImageContent          XML-RPC response
```

## 新增工具的模式

每個新工具需要修改兩個檔案，三層架構：

1. **rpc_server.py** — `FreeCADRPC` class 中新增：
   - Public method：將 lambda 放入 `rpc_request_queue`，從 `rpc_response_queue` 取結果
   - Private `_xxx_gui` method：在 FreeCAD 主執行緒執行實際操作

2. **server.py** — 新增：
   - `FreeCADConnection` class wrapper method（透過 `self.server` 呼叫 RPC）
   - `@mcp.tool()` decorated function（MCP 工具定義）

## 已實作的 MCP 工具

### 基本工具

| 工具                       | server.py 行號 | 說明                                     |
| -------------------------- | -------------- | ---------------------------------------- |
| `create_document`          | ~174           | 建立新文件                               |
| `create_object`            | ~210           | 建立物件（Part/Draft/PartDesign/Fem 等） |
| `edit_object`              | ~357           | 編輯物件屬性                             |
| `delete_object`            | ~394           | 刪除物件                                 |
| `execute_code`             | ~427           | 執行任意 Python 程式碼                   |
| `get_view`                 | ~459           | 截取 3D 視圖截圖                         |
| `get_objects`              | ~523           | 列出文件中所有物件                       |
| `get_object`               | ~548           | 取得單一物件詳細資訊                     |
| `list_documents`           | ~590           | 列出開啟的文件                           |
| `insert_part_from_library` | ~491           | 從零件庫插入零件                         |
| `get_parts_list`           | ~574           | 列出零件庫清單                           |

### TechDraw 工具

| 工具                   | server.py 行號 | 說明                                                       |
| ---------------------- | -------------- | ---------------------------------------------------------- |
| `create_techdraw_page` | ~602           | 建立 TechDraw 圖紙頁面（A0–A4 × Landscape/Portrait）       |
| `add_projection_group` | ~636           | 建立多視圖投影群組（DrawProjGroup），支援第一角/第三角投影 |
| `add_techdraw_view`    | ~702           | 建立單一 2D 投影視圖（DrawViewPart）                       |

### Prompt

| 名稱                      | server.py 行號 | 說明                                       |
| ------------------------- | -------------- | ------------------------------------------ |
| `asset_creation_strategy` | ~759           | 建立資產的策略指引（含 TechDraw 工作流程） |

## 截圖機制

### 3D 視圖截圖

- 透過 `FreeCADGui.ActiveDocument.ActiveView.saveImage()` 截取
- 支援 Isometric/Front/Top/Right/Back/Left/Bottom/Dimetric/Trimetric 視角
- 不支援的視圖型別（TechDraw、Spreadsheet）由 `get_active_screenshot()` 回傳 None

### TechDraw 截圖（SVG → PNG）

TechDraw 的 `MDIViewPage` 沒有 `saveImage()` 方法，改用以下方案：

1. `TechDrawGui.exportPageAsSvg(page, tmp_path)` → 匯出 SVG 至暫存檔
2. `QSvgRenderer` 載入 SVG
3. `QImage` + `QPainter` 渲染為 PNG（預設寬度 1920px，等比例計算高度）
4. 讀取 PNG → base64 回傳，清除暫存 SVG

> **注意**：舊版 FreeCAD（<=0.21）使用 `page.PageResult` 取得 SVG 路徑，但此屬性在 FreeCAD 1.0 已移除。
> 同樣，`TechDraw.writeSVGPage()` 在 FreeCAD 1.0 也已不存在，須使用 `TechDrawGui.exportPageAsSvg()`。

相關程式碼：

- `rpc_server.py`: `get_techdraw_screenshot()` + `_get_techdraw_screenshot_gui()`
- `server.py`: `FreeCADConnection.get_techdraw_screenshot()`, 三個 TechDraw 工具成功後自動呼叫

### `execute_code` 的 TechDraw 截圖 Fallback

當 `execute_code` 工具執行後，若 active view 為 TechDraw 頁面（`MDIViewPagePy`），3D 截圖會回傳 `None`。
此時 `server.py` 中的 `_try_techdraw_screenshot_fallback()` 會自動偵測當前 TechDraw 頁面，
透過 `get_techdraw_screenshot()` 取得 SVG→PNG 截圖作為 fallback。

相關程式碼：

- `server.py`: `_try_techdraw_screenshot_fallback()` helper function

優點：不依賴視窗前景、解析度可控、無額外依賴（Qt 原生 SVG 支援）

## TechDraw 實作細節

### TECHDRAW_TEMPLATES 常數（rpc_server.py）

快捷名 → SVG 檔名對應，模板位於：
`{FreeCAD.getResourceDir()}/Mod/TechDraw/Templates/`

快捷名格式：`A0_Landscape`、`A1_Portrait`、`A2_Landscape` … `A4_Portrait`

### 模板路徑解析邏輯（`_resolve_template_path`）

1. 若為絕對路徑且存在 → 直接使用
2. 查快捷名字典 → 組合完整路徑
3. 找不到 → 回傳可用快捷名清單（供錯誤訊息使用）

### DrawProjGroup 建立順序

0. 驗證 Page 的 Template 已正確設定（避免 Anchor 損壞）
1. `addProjection("Front")` 必須第一個呼叫（建立 Anchor）
2. 設定 `anchor.Direction` 與 `anchor.RotationVector`
3. 依序呼叫其他 `addProjection()`，跳過重複的 "Front"

有效投影值：`Front`, `Left`, `Right`, `Top`, `Bottom`, `Rear`, `FrontTopLeft`, `FrontTopRight`, `FrontBottomLeft`, `FrontBottomRight`

### 常用方向向量

| 視圖      | Direction               |
| --------- | ----------------------- |
| Front     | (0, -1, 0)              |
| Top       | (0, 0, 1)               |
| Right     | (1, 0, 0)               |
| Isometric | (-0.577, -0.577, 0.577) |

## RPC Server 架構（rpc_server.py）

### 執行緒模型

- XML-RPC server 在獨立 daemon thread 執行
- GUI 操作必須在主執行緒：public method 將 lambda 放入 `rpc_request_queue`
- `process_gui_tasks()` 由 `QTimer` 每 50ms 觸發，從 queue 取出並執行
- 結果透過 `rpc_response_queue` 回傳

### GUI Commands（Toolbar 按鈕）

| Command class                    | 說明                                 |
| -------------------------------- | ------------------------------------ |
| `StartRPCServerCommand`          | 啟動 RPC Server                      |
| `StopRPCServerCommand`           | 停止 RPC Server                      |
| `ToggleAutoStartCommand`         | 切換 FreeCAD 啟動時自動啟動 RPC      |
| `ToggleRemoteConnectionsCommand` | 切換遠端連線（0.0.0.0 vs localhost） |
| `ConfigureAllowedIPsCommand`     | 設定允許的 IP 白名單                 |

### 設定檔

- 路徑：`{FreeCAD.getUserAppDataDir()}/freecad_mcp_settings.json`
- 欄位：`auto_start_rpc`, `remote_enabled`, `allowed_ips`
- 透過 `load_settings()` / `save_settings()` 操作

### 物件建立輔助

- `Object` dataclass：封裝 `obj_type`, `obj_name`, `properties`
- `set_object_property()`：處理各類 FreeCAD 屬性設定（Placement, Vector, Color, list 等）

## 設計決策

- MCP 工具用扁平參數，內部打包為 `options` dict 傳給 RPC
- TechDraw 最小範圍：不含標註 (dimension)、剖面圖 (section view)，留待後續擴充
- `--only-text-feedback` 旗標：停用所有截圖回傳（僅回傳文字）
- `--host` 旗標：指定 RPC server 位址（支援遠端連線）

## 注意事項

- 新增/修改 `rpc_server.py` 後需重新載入 FreeCAD Addon（或重啟 FreeCAD）
- 新增/修改 `server.py` 後需重啟 MCP server，新工具才會出現在 Claude 工具列表
- `FreeCADConnection.server` 是 `xmlrpc.client.ServerProxy`，wrapper method 直接呼叫 `self.server.方法名()`
- `QtSvg` import 有 `HAS_QT_SVG` fallback 保護，若不可用則 TechDraw 截圖回傳 None

## FreeCAD 1.0 相容性注意

- `page.PageResult` 屬性已移除，TechDraw 截圖改用 `TechDrawGui.exportPageAsSvg()`
- `TechDraw.writeSVGPage()` 已移除，同樣改用 `TechDrawGui.exportPageAsSvg()`
- FreeCAD 1.0 內建的 Qt binding 是 `PySide2`（非 `PySide6`），addon 中使用 `from PySide import ...` 即可
- TechDraw 模板檔名為 `A3_Landscape_blank.svg` 格式（非 `A3_Landscape.svg`），`TECHDRAW_TEMPLATES` 已對應更新

## 測試資源與驗證基準

### TechDraw 模板

- **專案自訂模板**：`tech_draw_test_template.svg`（位於專案根目錄）
  - 格式：A4 Landscape（297×210mm）
  - 使用方式：`create_techdraw_page` 的 `template` 參數傳入絕對路徑
    ```
    C:/Users/user/Desktop/freecad-mcp/tech_draw_test_template.svg
    ```
- **FreeCAD 預設模板**：使用者已在 FreeCAD 中設定好預設 template，建立 TechDraw 頁面時即使不指定模板也會載入預設模板，而不是建立完全空白的頁面
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

#### 3. Scale 計算不當

**問題**：未先查詢物件尺寸就設定 Scale，導致視圖過大或過小。

**正確做法**：
- **必須先查詢 BoundBox**：`obj.Shape.BoundBox`
- 根據物件最大面的尺寸與頁面可用空間計算合適的 Scale
- 參考公式：`scale = 可用空間 / (物件尺寸 × 視圖數量的空間分配)`
- 對於 sample/test.stp（11×1×16mm），Scale=7 的投影群組 + Scale=5 的等角視圖是合適的

#### 4. TechDraw 截圖時機

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
