"""
frontend/__main__.py
用于支持 `python -m frontend` 启动方式

用法：
  python -m frontend      # 启动前端（仅 UI，后端需要单独启动）
  python -m frontend.main # 启动前端
"""

from .main import main

if __name__ == "__main__":
    main()
