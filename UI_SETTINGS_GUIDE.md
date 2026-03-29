# UI三状态指示器和路径设置实现指南

## ✅ 已完成的功能

### 1. 三个连接状态指示器

工具栏现在显示三个状态指示器：

```
[⟳ 评估] [● 后端] [● 游戏] [● 日志] [⚙ 设置]
```

| 指示器 | 绿色 (已连接) | 红色 (未连接) |
|--------|--------------|--------------|
| **● 后端** | FastAPI 服务正常运行 | FastAPI 无法连接 |
| **● 游戏** | 存档文件被成功读取 | 找不到或无法读取存档文件 |
| **● 日志** | 日志文件正在被监视 | 日志文件未找到或未被监视 |

### 2. 路径设置对话框

点击工具栏中的"⚙ 设置"按钮，弹出路径设置对话框：

- **存档路径**：手动指定 `current_run.save` 文件位置
- **日志路径**：手动指定 `godot.log` 文件位置
- **浏览按钮**：打开文件选择器快速选择
- **保存/取消**：保存设置或放弃修改

设置保存到 `~/.sts2-adviser/config.json`，下次启动自动加载。

### 3. 实时日志状态推送

GameWatcher 在以下情况下触发日志状态回调：
- 成功找到并开始监视日志文件 → 日志指示器变绿
- 无法找到日志文件 → 日志指示器变红
- 日志文件停止更新 → 日志指示器变红（可选扩展）

## 📁 实现细节

### 新增文件

**`scripts/config_manager.py`**
```python
load_config()         # 从 ~/.sts2-adviser/config.json 读取配置
save_config(config)   # 保存配置到文件
get_save_path()       # 获取存档路径配置
set_save_path(path)   # 设置存档路径配置
get_log_path()        # 获取日志路径配置
set_log_path(path)    # 设置日志路径配置
```

### 修改的文件

#### `scripts/game_watcher.py`
- `__init__` 添加 `custom_save_path` 和 `custom_log_path` 参数
- `find_active_log_file()` 优先使用自定义日志路径
- `find_save_file()` 优先使用自定义存档路径
- 添加 `log_status_callbacks: List[Callable]`
- 添加 `on_log_status_change(callback)` 注册方法
- 添加 `trigger_log_status(active, path)` 触发回调
- `watch_logs()` 在开始/停止监视时触发日志状态回调

#### `backend/main.py`
- 导入 `config_manager` 模块
- `ConnectionManager.start_game_watcher()` 从配置文件读取自定义路径
- 添加 `on_log_status_update` 回调处理
- 新增 `POST /api/config` 端点
- WebSocket 广播 `{"type": "log_status", "data": {...}}` 消息

#### `frontend/ui.py`
- `GameStateWatcher` 新增 `log_status_updated` 信号
- `on_message()` 处理 `log_status` 类型消息
- 工具栏布局修改：
  - `_backend_indicator` 标签改为"● 后端"
  - `_game_state_indicator` 改为 `_game_indicator`（显示"● 游戏"）
  - 新增 `_log_indicator`（显示"● 日志"）
  - 新增 `_on_settings` 按钮
- 新增 `PathSettingsDialog` 类：
  - 存档路径输入框 + 浏览按钮
  - 日志路径输入框 + 浏览按钮
  - 保存按钮 → 调用 `POST /api/config`
  - 取消按钮
- 新增 `_on_log_status_update()` 处理日志状态消息
- 新增 `_on_settings()` 打开设置对话框

## 🚀 使用流程

### 首次运行（自动路径检测）

1. 运行 `python main.py`
2. 工具栏显示三个指示器，初始状态为红色
3. GameWatcher 自动搜索常见位置：
   - Windows AppData/Roaming → AppData/Local → 游戏安装目录
   - 找到存档文件 → `_game_indicator` 变绿
   - 找到日志文件 → `_log_indicator` 变绿

### 自定义路径设置

1. 点击工具栏中的"⚙ 设置"按钮
2. 在对话框中输入或浏览选择：
   - 存档路径：`C:\Users\...\AppData\Roaming\SlayTheSpire2\steam\...\current_run.save`
   - 日志路径：`C:\Users\...\AppData\Roaming\SlayTheSpire2\logs\godot.log`
3. 点击"保存"
4. GameWatcher 重启，使用新路径
5. 配置保存到 `~/.sts2-adviser/config.json`

### 游戏运行时

1. 启动 STS2 游戏
2. `_game_indicator` 保持绿色（存档被持续读取）
3. 进行选卡操作时，日志被写入 → `_log_indicator` 保持绿色
4. UI 显示游戏进度和选卡推荐

## 🔌 WebSocket 消息格式

### 日志状态消息

```json
{
  "type": "log_status",
  "data": {
    "active": true,
    "path": "/path/to/godot.log",
    "timestamp": "2026-03-28T20:25:54.123456"
  }
}
```

## 📋 API 端点

### POST /api/config

更新配置并重启 GameWatcher

**请求体：**
```json
{
  "save_path": "/path/to/current_run.save",
  "log_path": "/path/to/godot.log"
}
```

**响应：**
```json
{
  "status": "success",
  "message": "配置已更新，监视器已重启",
  "config": {
    "save_path": "...",
    "log_path": "..."
  }
}
```

## 🔧 配置文件位置

`~/.sts2-adviser/config.json`

示例：
```json
{
  "save_path": "C:\\Users\\HH275\\AppData\\Roaming\\SlayTheSpire2\\steam\\76561198105647177\\profile1\\saves\\current_run.save",
  "log_path": "C:\\Users\\HH275\\AppData\\Roaming\\SlayTheSpire2\\logs\\godot.log"
}
```

## ✅ 验证检查清单

- [ ] 启动 `python main.py`，所有指示器都显示
- [ ] 后端指示器为绿色（FastAPI 运行中）
- [ ] 不运行游戏时，游戏指示器为红色
- [ ] 不启动游戏时，日志指示器为红色
- [ ] 启动 STS2 游戏，游戏指示器变绿
- [ ] 游戏开始监视日志，日志指示器变绿
- [ ] 点击"⚙ 设置"，弹出路径设置对话框
- [ ] 可以输入或浏览选择路径
- [ ] 保存设置后，GameWatcher 重启使用新路径
- [ ] 配置文件 `~/.sts2-adviser/config.json` 被创建和更新

## 🎨 UI 外观

```
┌────────────────────────────────────────┐
│ ⚔ STS2 Adviser             [×]        │
├────────────────────────────────────────┤
│ [⟳ 评估] [● 后端] [● 游戏] [● 日志] [⚙] │ ← 工具栏
├────────────────────────────────────────┤
│ 卡名         角色   分数  推荐         │
│ ──────────────────────────────────────│
│ (卡牌列表...)                          │
│                                        │
├────────────────────────────────────────┤
│ REGENT | 75/75 | F1                   │ ← 状态栏
└────────────────────────────────────────┘
```

## 📝 注意事项

- 配置文件位于用户主目录下 `~/.sts2-adviser/`，确保目录权限可写
- 路径必须指向实际存在的文件，否则设置时会提示错误
- GameWatcher 收到新配置后会立即重启，不需要重启整个应用
- 日志状态通过 WebSocket 实时推送，状态更新延迟 <100ms

---

**最后更新时间**: 2026-03-28 20:25:54
