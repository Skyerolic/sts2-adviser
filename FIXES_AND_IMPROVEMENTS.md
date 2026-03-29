# 修复和改进说明

## ✅ 已完成的修复

### 1. Steam 存档文件读取 ✓
**问题**: `self.save_path` 没有被设置，存档数据无法推送
**修复**: 在 `find_save_file()` 方法中添加 `self.save_path = path` 赋值

**结果**: 现在成功读取 Steam 存档，推送游戏状态到前端

### 2. 游戏状态指示器 ✓
**需求**: 添加游戏监视状态指示器，类似后端连接指示
**实现**:
- 添加 `_game_state_indicator` 标签到工具栏
- 显示格式: `● CHARACTER F楼层` (例如: `● REGENT F1`)
- 状态颜色:
  - 绿色 (#4CAF50) - 有游戏数据
  - 红色 (#F44336) - 无游戏数据

### 3. UI 状态显示优化 ✓
**改进**:
- 状态栏现在显示完整的游戏信息
- 格式: `角色 | HP/MaxHP | 楼层`
- 例如: `REGENT | 75/75 | F1`

### 4. API 数据验证改进 ✓
**改进**:
- 改进 `_on_refresh()` 中的数据验证
- 如果没有 `card_choices`，显示友好提示
- 提示用户需要日志文件或游戏运行中

## 📊 现在的数据流

```
1. GameWatcher 启动
   ↓
2. 查找 Steam 存档文件 ✓ (现在成功)
   ↓
3. 读取存档 JSON 数据 ✓
   ↓
4. 解析游戏状态 ✓
   ↓
5. 触发回调函数 ✓
   ↓
6. WebSocket 推送给前端 ✓
   ↓
7. 前端更新游戏状态指示器 ✓
   ↓
8. 显示完整的游戏信息 ✓
```

## 🎮 成功读取的数据示例

从 `current_run.save` 读取的完整游戏状态：

```json
{
  "character": "CHARACTER.REGENT",      // 已读取
  "floor": 1,                           // 已读取
  "hp": 75,                             // 已读取
  "max_hp": 75,                         // 已读取
  "gold": 99,                           // 已读取
  "deck": [                             // 已读取
    "CARD.STRIKE_REGENT",
    "CARD.STRIKE_REGENT",
    "CARD.STRIKE_REGENT",
    "CARD.STRIKE_REGENT",
    "CARD.DEFEND_REGENT",
    "CARD.DEFEND_REGENT",
    "CARD.DEFEND_REGENT",
    "CARD.DEFEND_REGENT",
    "CARD.FALLING_STAR",
    "CARD.VENERATE",
    "CARD.ASCENDERS_BANE"
  ],
  "relics": [                           // 已读取
    "RELIC.DIVINE_RIGHT"
  ],
  "timestamp": "2026-03-28T20:10:52"
}
```

## ⚠️ 已知限制

### 1. 选卡池数据
**问题**: `card_choices` 不在存档文件中
**原因**: 当前选卡信息只在游戏实时内存中
**解决方案**: 需要日志文件或游戏运行时获取
**用户提示**: "等待选卡数据... (需要日志文件或游戏运行中)"

### 2. 422 API 错误
**原因**: 评估需要 `card_choices` 字段
**现象**: 没有选卡数据时，点击评估按钮显示提示而不是错误
**状态**: ✓ 已改进，用户体验更好

## 🎯 UI 改进后的样子

```
┌──────────────────────────────┐
│ ⚔ STS2 Adviser              │
├──────────────────────────────┤
│ ⟳ 评估 ● 已连接 ● REGENT F1 │ 日志
├──────────────────────────────┤
│ 卡名        角色  分数  推荐   │
├──────────────────────────────┤
│ (卡牌列表或占位符)            │
│                              │
├──────────────────────────────┤
│ REGENT | 75/75 | F1         │
└──────────────────────────────┘
```

**指示器说明**:
- 左: `⟳ 评估` - 手动评估按钮
- 中左: `● 已连接` - 后端连接状态
- 中右: `● REGENT F1` - **游戏状态** (新增)
- 右: `日志` - 打开日志目录

## ✅ 测试结果

运行 `python main.py` 时的日志输出：

```
[INFO] ✓ 找到游戏目录: C:\Users\HH275\Desktop\sts2
[INFO] ✓ 找到 Steam 存档文件: ...current_run.save
[INFO] ✓ 找到 Steam 存档文件
[INFO] 使用存档文件读取游戏状态
[INFO] ✓ 游戏状态监视已启动
[DEBUG] 游戏状态更新: {...完整的游戏状态数据...}
[INFO] ✓ WebSocket 已连接
```

## 🚀 使用流程

### 初始启动（存档有效）
1. 运行 `python main.py`
2. GameWatcher 自动读取 Steam 存档
3. 前端显示游戏状态（REGENT F1）
4. 可以手动点击"评估"尝试（但需要 card_choices 数据）

### 游戏进行中（日志活跃）
1. 之前的步骤 +
2. 日志文件被创建并监视
3. 选卡时自动获取 card_choices
4. 点击"评估"会显示卡牌推荐
5. 所有信息实时同步

## 📝 相关代码改动

### scripts/game_watcher.py
```python
# 修复: 添加 self.save_path 赋值
self.save_path = path

# 新增: read_save_file_data() 方法
def read_save_file_data(self) -> Optional[Dict]:
    """从 current_run.save 读取游戏状态"""
```

### frontend/ui.py
```python
# 新增: 游戏状态指示器
self._game_state_indicator = QLabel("● 无游戏数据")

# 改进: 状态显示逻辑
def _on_game_state_update(self, state: dict):
    # 更新指示器颜色和文本
    # 更新状态栏显示
```

## 🎉 总结

- ✅ 存档文件完整集成
- ✅ 游戏状态实时显示
- ✅ UI 用户体验改进
- ✅ 错误提示更友好
- ⚠️ 仍需日志文件获取选卡数据（这是正常的限制）

系统现在已经能够完整显示游戏进度，并为用户提供清晰的状态反馈！