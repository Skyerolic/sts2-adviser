# STS2 Adviser — 杀戮尖塔2 实时选卡助手

完全外置，无需修改游戏文件。通过截图 OCR 自动识别选卡界面，结合社区数据与套路分析给出推荐评分。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
python main.py
```

## 使用说明

1. 启动 STS2 Adviser，浮窗置顶显示在游戏上方
2. 进入游戏选卡界面，助手自动识别三张候选卡并给出评分
3. 如需手动选牌评估，点击右侧 **◀** 按钮展开选牌面板

### OCR 识别效果不佳？

**首先尝试：将游戏窗口最大化。**
OCR 依赖游戏窗口截图的分辨率，窗口越大识别越准确。
小窗口或分辨率过低时，卡名文字太小容易误读。

其他措施：
- 确认游戏语言设置（中文界面）
- 确认游戏窗口标题包含 `Slay the Spire 2`
- 在选卡界面运行诊断工具，查看截图效果：
  ```bash
  python diagnose_ocr.py
  ```

## 项目结构

```
sts2-adviser/
├── backend/              # FastAPI + WebSocket 后端
│   ├── main.py           # 服务器入口，管理 GameWatcher / VisionBridge
│   ├── evaluator.py      # 卡牌评估引擎
│   ├── archetypes.py     # 套路库定义
│   ├── archetype_inference.py  # 套路推断层（关键词匹配）
│   ├── models.py         # 数据模型
│   └── scoring.py        # 评分引擎（含社区数据交叉验证）
│
├── frontend/             # PyQt6 浮窗 UI
│   ├── ui.py             # 主界面（置顶、可拖拽、侧边选牌抽屉）
│   ├── card_locale.py    # 中文卡名本地化
│   └── styles.qss        # 深色主题样式表
│
├── vision/               # OCR 视觉识别
│   ├── window_capture.py # PrintWindow 截图（不受遮挡影响）
│   ├── ocr_engine.py     # Windows 内置 OCR（含 OpenCV 预处理）
│   ├── screen_detector.py# 界面类型检测（选卡 / 商店 / 其他）
│   ├── card_normalizer.py# 卡名模糊匹配（fuzzy 白名单过滤）
│   └── vision_bridge.py  # 整合模块，状态机驱动轮询
│
├── scripts/
│   ├── game_watcher.py   # 日志文件监视器（备用数据源）
│   └── config_manager.py # 路径配置管理
│
├── data/
│   ├── cards.json            # 卡牌库（英文元数据）
│   ├── card_library.json     # 社区统计数据（胜率 / 选取率）
│   ├── card_locale_zh.json   # 中文本地化
│   └── card_names_zh.json    # 中文卡名索引
│
├── diagnose_ocr.py       # 诊断工具：截图 + 分段 OCR 输出
├── diagnose_save_path.py # 诊断工具：游戏存档路径查找
└── main.py               # 集成启动脚本
```

## 数据来源

系统有两个并行数据源：

| 来源 | 原理 | 说明 |
|------|------|------|
| VisionBridge | PrintWindow 截图 + Windows OCR | 主数据源，自动检测选卡界面 |
| GameWatcher | 解析游戏日志文件 | 备用数据源，提供角色 / 楼层 / 牌组信息 |

## 评分算法

### 五维度加权评分

每张候选卡按以下五个维度独立评分（均归一化到 0~1），加权合并后映射到 0~100 分：

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

### v0.8（当前）
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
