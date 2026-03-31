# STS2 Adviser — 杀戮尖塔2 实时选牌导师

<p align="center">
  <img src="assets/preview.png" width="220" alt="STS2 Adviser 预览"/>
</p>

进入选卡界面时自动截图识别候选卡，读取游戏存档和日志获取当前角色、楼层、牌组、遗物等状态，综合套路契合度、卡牌固有价值、当前阶段（楼层）适配、套路完成度贡献、遗物协同加成五个维度评分，并与社区胜率/选取率数据交叉验证，给出推荐评分和理由，以置顶浮窗形式叠加在游戏上方。

## 快速开始

### 方式一：直接运行 EXE（推荐，无需安装 Python）

1. 前往 [Releases](https://github.com/Skyerolic/sts2-adviser/releases) 下载最新版 ZIP
2. 解压后双击 `sts2_adviser.exe`

### 方式二：从源码运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
python main.py
```

## 使用说明

### 自动模式（OCR）

启动后浮窗置顶显示在游戏上方。进入选卡界面时，助手自动截图识别三张候选卡并给出评分，无需任何操作。

**调整窗口大小**：拖拽浮窗右下角的缩放手柄可以自由调整大小，字体会随窗口等比缩放。

### 手动选牌模式

点击浮窗右侧的 **◀** 按钮展开选牌抽屉，窗口向右扩展，不占用主面板空间。

1. 抽屉顶部按攻击 / 技能 / 能力分组列出当前角色所有卡牌
2. 点击卡片选中（高亮），最多选 4 张候选卡
3. 点击底部 **⟳ 评估** 按钮，主面板显示评分结果
4. 再次点击 **▶** 收起抽屉

### 配置游戏日志路径

GameWatcher 读取游戏日志来获取角色、楼层、当前牌组信息，让评分更准确（尤其是阶段适配和完成度维度）。不配置时仅凭 OCR 结果评分。

**自动搜索**：启动时会自动尝试以下路径：
```
%AppData%\Roaming\SlayTheSpire2\
%AppData%\Local\SlayTheSpire2\saves\
C:\Program Files (x86)\Steam\steamapps\common\SlayTheSpire2\
```

**手动配置**：若自动搜索失败，在设置界面（齿轮图标）填入游戏日志路径，或运行诊断工具：
```bash
python diagnose_save_path.py
```
配置保存在 `~/.sts2-adviser/config.json`，重启后生效。

### OCR 识别效果不佳？

**首先尝试：将游戏窗口最大化。**
OCR 依赖游戏窗口截图的分辨率，窗口越大识别越准确。小窗口下卡名文字太小容易误读。

其他措施：
- 确认游戏语言设置为中文界面
- 确认游戏窗口标题包含 `Slay the Spire 2`
- 在选卡界面运行诊断工具，查看截图和 OCR 分段效果：
  ```bash
  python diagnose_ocr.py
  ```

## 模块说明

```
sts2-adviser/
├── main.py                     # 集成启动入口：同时启动后端服务和前端浮窗
│
├── backend/                    # FastAPI 后端，处理评估逻辑
│   ├── main.py                 # HTTP/WebSocket 服务器，协调 GameWatcher 和 VisionBridge
│   ├── evaluator.py            # 评估引擎：调用各维度评分，汇总结果和推荐理由
│   ├── scoring.py              # 五维度评分算法 + 社区数据交叉验证
│   ├── archetypes.py           # 套路库：每个角色的套路定义和核心卡列表
│   ├── archetype_inference.py  # 套路推断：对未在套路列表中的卡用关键词推断权重
│   └── models.py               # 数据模型：Card / RunState / ScoreBreakdown 等
│
├── frontend/                   # PyQt6 浮窗界面
│   ├── ui.py                   # 主窗口：置顶浮窗、拖拽、评分展示、侧边选牌抽屉
│   ├── card_locale.py          # 英文卡 ID → 中文名映射
│   └── styles.qss              # 深色主题样式（杀戮尖塔风格）
│
├── vision/                     # 视觉识别模块
│   ├── vision_bridge.py        # 总调度：定时轮询截图，驱动状态机，推送识别结果
│   ├── window_capture.py       # 用 PrintWindow API 截图（窗口被遮挡时仍有效）
│   ├── ocr_engine.py           # Windows WinRT OCR 封装，含 OpenCV/PIL 预处理
│   ├── screen_detector.py      # 判断当前界面类型（选卡 / 商店 / 其他）
│   └── card_normalizer.py      # OCR 结果后处理：误读修正 + fuzzy 白名单匹配
│
├── scripts/
│   ├── game_watcher.py         # 监视游戏日志文件，解析角色 / 楼层 / 牌组 / 遗物
│   └── config_manager.py       # 读写 ~/.sts2-adviser/config.json（日志路径、语言等）
│
├── data/
│   ├── cards.json              # 卡牌库：费用、稀有度、类型等元数据
│   ├── card_library.json       # 社区统计：每张卡的胜率和选取率
│   ├── card_locale_zh.json     # 中文本地化：英文 ID → 中文卡名
│   └── card_names_zh.json      # 中文卡名索引（OCR 匹配用）
│
├── diagnose_ocr.py             # 诊断工具：截图并输出 OCR 分段结果，排查识别问题
└── diagnose_save_path.py       # 诊断工具：自动搜索游戏存档和日志路径
```

### 数据流

```
游戏窗口
  │
  ├─ PrintWindow 截图 ──→ OCR 引擎 ──→ card_normalizer ──→ 卡名列表
  │                                                              │
  └─ 游戏日志文件 ──→ GameWatcher ──→ RunState（角色/楼层/牌组）  │
                                              │                  │
                                              └──────────────────┤
                                                                 ▼
                                                          evaluator（五维度评分）
                                                                 │
                                                                 ▼
                                                        前端浮窗展示结果
```

## 评分算法

### 五维度加权评分

每张候选卡按以下五个维度独立评分（均归一化到 0~1），加权合并后映射到 0~100 分：

> **计划加入（尚未实现）**：血量维度——当前血量/最大血量比例低时，对治疗/防御类卡加权；深度维度——基于当前牌组厚度动态调整卡牌价值（厚牌组下低价值卡的惩罚将更激进）。

| 维度 | 权重 | 评分逻辑 |
|------|------|----------|
| 套路契合度 | **40%** | 取该卡在所有匹配套路中的最高权重；无匹配返回 0（由固有价值兜底） |
| 卡牌固有价值 | **25%** | 稀有度基线（Rare 0.80 / Uncommon 0.60 / Common 0.45）+ 费用效率（0 费 +0.12，3 费以上 -0.05） |
| 阶段适配 | **15%** | 核心/使能卡后期分更高（早 0.75 → 晚 0.88）；过渡卡早期强（早 0.85 → 晚 0.15）；污染牌固定 0 |
| 完成度贡献 | **15%** | 拿这张后套路完成度 delta × 3（放大系数，因单张卡通常仅提升 5~10%） |
| 协同加成 | **5%** | 与当前遗物 / 卡组的标签重叠，每个匹配标签 +0.20，上限 1.0 |

**惩罚项**（直接从原始分扣除，不经权重）：
- **污染惩罚**：污染牌 −0.50，牌组每多一张折扣 0.015（上限抵扣 0.25）
- **厚牌组惩罚**：牌组 ≥ 20 张后，低价值牌每多一张 −0.01（上限 0.15）；核心/使能卡豁免

最终分档：

| 分数 | 推荐等级 |
|------|----------|
| 80~100 | 强烈推荐 |
| 65~79  | 推荐 |
| 50~64  | 可选 |
| 30~49  | 谨慎 |
| 0~29   | 跳过 |

### 社区数据交叉验证

社区胜率 / 选取率经 sigmoid 归一化后与算法分混合（最大权重 25%，另有 15% 补丁滞后折扣）：

| 比较结果 | 判定 | 处理方式 |
|----------|------|----------|
| delta ≤ 0.15 | AGREEMENT（同趋势） | 双方向上/向下各放大 5%，置信度 100% |
| 0.15 < delta ≤ 0.30 | SOFT_CONFLICT | 社区权重打 75%，折中混合 |
| delta > 0.30 | CONFLICT | 社区权重打 50%，算法分优先 |
| 无社区数据 | — | 直接使用算法分 |

## 系统要求

- **Windows 10 / 11**（依赖 Windows 内置 OCR）
- Python 3.10+
- 推荐安装 `opencv-python`（OCR 预处理质量更好）：
  ```bash
  pip install opencv-python
  ```

## 故障排查

**找不到游戏窗口**：确认游戏窗口标题包含 `Slay the Spire 2`

**OCR 识别率低**：先把游戏窗口最大化再试；或运行诊断工具：
```bash
python diagnose_ocr.py
```

**后端连接失败**：手动启动后端：
```bash
python -m uvicorn backend.main:app --port 8001
```

---

## 版本历史

### v1.0 Test（当前）
- **EXE 正式可用**：首个完整可独立运行的打包版本，无需安装 Python，解压即用
- **GameWatcher 修复**：`scripts/` 改为标准 Python 包，EXE 模式下角色/楼层/牌组信息现可正确加载，套路契合度评分更准确
- **路径修复（PyInstaller 6.x）**：用 `sys._MEIPASS` 解析 `_internal/` 目录，`data/`、`styles.qss`、日志路径在 EXE 模式下均正确
- **进程退出修复**：改用 `os._exit()` 关闭浮窗后立即终止全部进程（含 uvicorn 后端线程）
- **UI 调整**：初始窗口高度增大 1.5 倍（600×750），右下角缩放手柄改为金色可见样式

### v0.99
- **EXE 打包路径兼容**：新增 `utils/paths.py`，统一所有模块的根目录解析（开发模式 vs PyInstaller frozen 模式），修复 EXE 运行时 `data/`、`logs/`、`styles.qss` 路径错位问题
- **依赖拆分**：`requirements-prod.txt`（仅生产依赖）与 `requirements.txt`（开发+测试）分离，用户安装更简洁
- **build_exe.bat 升级**：自动创建/复用 `.venv` 虚拟环境，仅安装生产依赖，打包后显示目录大小并检测 UPX
- **spec 补全**：补充 `rapidfuzz`、`psutil`、`mss`、`PIL`、`numpy`、`anyio._backends._trio`、`uvicorn.protocols.websockets.wsproto_impl` 等 hidden imports，打包覆盖率更完整

### v0.95
- **遗物协同系统完善**：新增 `relic_archetype_map.py` 遗物→套路适配度映射；补充 `data/relics.json` 遗物定义数据
- **社区数据补全**：`data/card_library.json` 覆盖全部可用卡牌的胜率和选取率统计
- **打包基础设施**：新增 `build_exe.bat` 和 `sts2_adviser.spec`，支持一键打包为无需安装 Python 的独立 EXE

### v0.9
- **代码质量清理**：全面替换 `print` 调试输出为结构化 `logging`，提升日志可读性
- **vision_bridge.py 精简**：移除冗余的独立 OCR 方法，统一为 `_extract_card_names_combined` 双策略（全图聚类 + 区域补全）
- **UI 修复**：
  - 抽屉展开/收起时窗口宽度动态伸缩（不再截断卡名）
  - 启动后自适应窗口高度（由 `_auto_fit_height` 在布局完成后调整）
  - 卡片按钮改为 `Expanding` 策略，宽度均匀分布

### v0.8
- **OCR 稳定性大幅提升**：
  - 白名单过滤策略替代黑名单（fuzzy 匹配自动过滤所有乱码，无需手动维护规则）
  - 全图 OCR 候选区 Y 范围精确收窄，排除卡牌类型标签行（攻击/技能）
  - OCR 并发锁，防止 WinRT RecognizeAsync 重叠调用
  - `win32gui` 不可用时自动降级为 ctypes 枚举窗口
- **OpenCV 预处理**：有 OpenCV 时使用 INTER_CUBIC 放大 + CLAHE + 高斯去噪 + 锐化；无 OpenCV 时 PIL 对比度增强回退
- **中文 OCR 误读修正表扩充**：覆盖煊融之拳、双重打击等高频卡名乱码
- **UI 重构**：
  - 字体整体放大 20%
  - 候选卡垂直布局（卡名 → 中文定位 → 分数 → 推荐 → 理由）
  - 手动选牌改为侧边抽屉（◀/▶ 控制展开/收起），展开时窗口向右扩展，不占用主面板空间
  - 推荐理由分色显示（绿色 / 橙红）
  - 卡牌选择面板改为 3 列网格，加宽至 340px，防止卡名截断

### v0.7
- 社区数据交叉验证层：算法评分与社区胜率 / 选取率联合决策
- sigmoid 归一化将社区统计转换为 0~1 评分
- AGREEMENT / SOFT_CONFLICT / CONFLICT 三档置信度调整
- 推荐理由新增社区数据相关说明

### v0.6
- 套路推断层（`archetype_inference.py`）：基于关键词 / 描述文本自动推断卡牌套路权重
- 覆盖铁甲人 / 沉默者 / 机器人 / 守望者共 11 个套路推断配置
- 不在精确卡牌列表中的卡也能获得推断权重，显著扩大套路覆盖面

### v0.5
- OCR 识别重写：双策略（全图聚类 + 区域补全）
- 评分引擎重构（archetype / value / phase / completion / synergy 五维度）
- 日志基础设施：评分 JSON 日志 + OCR 快照自动保存
- WebSocket 稳定性修复（UTF-8 编码 / asyncio 阻塞问题）

### v0.1 — v0.4
- 项目初始化，基础 FastAPI 后端 + PyQt6 浮窗
- Windows PrintWindow 截图模块
- Windows OCR 引擎封装
- 游戏日志文件监视器（GameWatcher）
