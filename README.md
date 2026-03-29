# STS2 Adviser - 杀戮尖塔2 实时选卡助手

完全外置，无需修改游戏文件。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动（Windows 一键）
start_with_game_monitor.bat

# 或手动
python main.py
```

## 项目结构

```
sts2-adviser/
├── backend/              # FastAPI + WebSocket 后端
│   ├── main.py           # 服务器入口，管理 GameWatcher / VisionBridge
│   ├── evaluator.py      # 卡牌评估引擎
│   ├── archetypes.py     # 套路库定义
│   ├── models.py         # 数据模型
│   └── scoring.py        # 评分规则
│
├── frontend/             # PyQt6 浮窗 UI
│   ├── ui.py             # 主界面（置顶、可拖拽）
│   └── styles.qss        # 样式表
│
├── vision/               # OCR 视觉识别（v0.2）
│   ├── window_capture.py # PrintWindow 截图（不受遮挡影响）
│   ├── ocr_engine.py     # Windows 内置 OCR 封装（含坐标）
│   ├── screen_detector.py# 界面类型检测（选卡 / 商店 / 其他）
│   ├── card_extractor.py # 动态定位卡名区域（OCR 坐标驱动）
│   ├── card_normalizer.py# 卡名模糊匹配
│   └── vision_bridge.py  # 整合模块，与 GameWatcher 接口兼容
│
├── scripts/
│   └── game_watcher.py   # 日志文件监视器（v0.1 数据源）
│
├── data/
│   ├── cards.json            # 卡牌库（英文）
│   └── card_locale_zh.json   # 中文本地化
│
├── diagnose_ocr.py       # 诊断工具：截图 + 分段 OCR 输出
├── main.py               # 集成启动脚本
└── requirements.txt
```

## 数据来源

系统有两个并行数据源：

| 来源 | 原理 | WebSocket 类型 |
|------|------|---------------|
| GameWatcher | 解析游戏日志文件 | `game_state` |
| VisionBridge | PrintWindow 截图 + Windows OCR | `vision_state` |

选卡界面识别后自动触发评估，结果显示在浮窗列表里。

## 系统要求

- Windows 10/11（Windows OCR 依赖）
- Python 3.10+
- STS2 游戏已安装

## 故障排查

**找不到游戏窗口**：确认游戏窗口标题包含 `Slay the Spire 2`

**OCR 无结果**：运行诊断工具（在选卡界面时）：
```bash
python diagnose_ocr.py
```

**后端连接失败**：确认后端已启动，端口默认 8001
```bash
python -m main
```
