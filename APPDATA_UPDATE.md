# AppData 路径更新说明

## 📍 更新内容

GameWatcher 已更新为支持正确的 STS2 AppData 存档位置。

## 🎯 关键路径

### Windows (现在支持 ✅)
```
C:\Users\{USERNAME}\AppData\Local\SlayTheSpire2\saves\
```

**包含内容：**
- 游戏日志文件（`.log` 文件）
- 存档数据文件
- 运行状态信息

### 搜索优先级

GameWatcher 现在按以下顺序搜索日志文件：

1. **Windows AppData** (最优先)
   ```
   %APPDATA%\Local\SlayTheSpire2\saves\
   %APPDATA%\Local\SlayTheSpire2\logs\
   ```

2. **游戏安装目录**
   ```
   C:\Users\{USERNAME}\Desktop\sts2\logs\
   C:\Users\{USERNAME}\Desktop\sts2\user://logs\
   ```

3. **Linux/Mac**
   ```
   ~/.local/share/SlayTheSpire2/logs/
   ~/.config/godot/app_userdata/SlayTheSpire2/logs/
   ```

## 🔍 GameWatcher 现在会：

1. ✅ 自动检测 AppData 中的 SlayTheSpire2 目录
2. ✅ 在 saves/ 目录中查找日志文件
3. ✅ 找到最新的日志文件（按修改时间）
4. ✅ 实时监视日志变化
5. ✅ 解析游戏状态并通过 WebSocket 推送

## 🚀 使用流程

### 首次设置
1. 运行 `python main.py`
2. 启动 STS2 游戏
3. GameWatcher 会自动：
   - 在 AppData 中创建 `SlayTheSpire2\saves\` 目录
   - 生成和监视日志文件
   - 实时推送游戏状态

### 日志文件位置

游戏运行时，日志会写入：
```
C:\Users\{USERNAME}\AppData\Local\SlayTheSpire2\saves\
```

日志文件通常命名为：
- `game_YYYYMMDD_HHMMSS.log`
- 或类似格式

### 调试技巧

如果 GameWatcher 找不到日志，可以：

1. **确认游戏已启动**
   ```bash
   tasklist | findstr SlayTheSpire
   ```

2. **检查 AppData 目录**
   ```bash
   explorer %APPDATA%\Local\SlayTheSpire2\
   ```

3. **查看 GameWatcher 日志**
   ```bash
   tail -f game_watcher.log
   ```

4. **手动指定游戏路径**（可选）
   ```bash
   set STS2_PATH=C:\path\to\sts2\game
   python main.py
   ```

## 📊 日志输出示例

启动后应该看到：
```
[GameWatcher] 正在查找 STS2 安装目录...
[GameWatcher] ✓ 找到游戏目录: C:\Users\HH275\Desktop\sts2

当游戏启动后：
[GameWatcher] ✓ 找到活跃日志: C:\Users\HH275\AppData\Local\SlayTheSpire2\saves\game_20260328_183000.log
[GameWatcher] ✓ 游戏状态监视已启动
[WebSocket] 广播消息: 游戏状态更新
```

## ✅ 验证

运行游戏时，应该看到：

```
✓ 后端就绪
✓ 游戏状态监视已启动
✓ WebSocket 已连接
✓ 游戏监视: 已连接游戏监视
```

（不会再有 "找不到游戏日志文件" 的错误）

## 🔐 数据安全

- 所有日志读取都是**只读**的
- **不修改**游戏文件
- **不上传**任何数据
- WebSocket 仅限本地连接

---

**更新完成！现在 GameWatcher 能正确找到和监视 AppData 中的游戏日志了。** ✅
