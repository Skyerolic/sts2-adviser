"""
vision/ — 基于截图+OCR的游戏状态识别模块

子模块：
  window_capture   — 找到STS2窗口并截图
  ocr_engine       — Windows内置OCR（中英文）
  screen_detector  — 判断当前是选卡/商店/其他界面
  card_extractor   — 按比例裁剪卡名区域
  card_normalizer  — OCR结果模糊匹配→card_id
  vision_bridge    — 整合以上模块，对外输出RunState
"""
