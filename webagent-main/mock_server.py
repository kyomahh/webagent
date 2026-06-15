import os
import time
import shutil
import asyncio
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Mock Web Test Agent Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 📂 目录配置：使用 tmpoutput
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmpoutput")
LOG_PATH = os.path.join(TMP_DIR, "log.txt")
SCREENSHOT_DIR = os.path.join(TMP_DIR, "screenshots")
BASE_IMG = os.path.join(TMP_DIR, "a.png")  # 你的种子图片

# 🛠️ 初始化目录和基础文件
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
if not os.path.exists(BASE_IMG):
    # 如果你忘了放 a.png，帮你随便建一个空的防崩
    with open(BASE_IMG, "wb") as f:
        f.write(b"")
if not os.path.exists(LOG_PATH):
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("=== Mock System Initialized ===\n")

# 挂载静态目录
app.mount("/static", StaticFiles(directory=TMP_DIR), name="static")


# 🤖 核心逻辑：Mock Agent 的异步任务
async def mock_agent_run(selected_cases: list):
    # 1. 每次启动测试前，清空旧日志和旧截图
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(
            f"[{time.strftime('%X')}] 🚀 Mock Agent 初始化，准备执行选中的 {len(selected_cases)} 个测试用例...\n"
        )

    for f_name in os.listdir(SCREENSHOT_DIR):
        file_path = os.path.join(SCREENSHOT_DIR, f_name)
        if os.path.isfile(file_path):
            os.remove(file_path)

    # 2. 循环遍历前端勾选的测试用例
    for idx, case_id in enumerate(selected_cases):
        await asyncio.sleep(2)  # 模拟 Agent 打开浏览器、跑脚本的耗时

        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%X')}] ⚙️ 正在执行用例 ID: {case_id}...\n")

        await asyncio.sleep(1)  # 模拟截图耗时

        # 复制 a.png 作为当前步骤的截图
        new_img_name = f"step_{idx + 1}_case_{case_id}_{int(time.time())}.png"
        new_img_path = os.path.join(SCREENSHOT_DIR, new_img_name)
        if os.path.exists(BASE_IMG):
            shutil.copy(BASE_IMG, new_img_path)

        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%X')}] 📸 成功捕获截图: {new_img_name}\n")

    await asyncio.sleep(1)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n[{time.strftime('%X')}] 🎉 所有测试用例执行完毕！\n")


# ---------------- 🚀 API 接口设计 ----------------


@app.get("/api/cases")
async def get_cases():
    """动态获取测试用例列表"""
    return [
        {"id": "101", "name": "登录页账号密码校验", "type": "UI"},
        {"id": "102", "name": "商品详情页加载速度测试", "type": "Performance"},
        {"id": "103", "name": "支付全流程断言", "type": "E2E"},
        {"id": "104", "name": "深色模式渲染对比", "type": "UI"},
        {"id": "105", "name": "异常网络状态重试机制", "type": "Network"},
    ]


@app.post("/api/start-test")
async def start_test(request: Request, background_tasks: BackgroundTasks):
    """触发测试，接收前端传来的用例 ID 数组"""
    data = await request.json()
    selected_cases = data.get("cases", [])

    # 将任务丢入后台异步执行，不阻塞主线程
    background_tasks.add_task(mock_agent_run, selected_cases)
    return {"status": "success", "message": f"Started {len(selected_cases)} cases."}


@app.get("/api/logs")
async def get_logs(offset: int = 0):
    if not os.path.exists(LOG_PATH):
        return {"content": "", "offset": 0}
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        f.seek(offset)
        content = f.read()
        new_offset = f.tell()
    return {"content": content, "offset": new_offset}


@app.get("/api/screenshots")
async def get_screenshots():
    if not os.path.exists(SCREENSHOT_DIR):
        return {"screenshots": []}
    files = os.listdir(SCREENSHOT_DIR)
    images = [f for f in files if f.lower().endswith((".png", ".jpg"))]
    images.sort()
    # 注意：URL 路径拼接要对得上 tmpoutput 挂载的 /static
    urls = [f"/static/screenshots/{img}" for img in images]
    return {"screenshots": urls}


if __name__ == "__main__":
    import uvicorn

    # 启动命令: uv run mock_server.py
    uvicorn.run("mock_server:app", host="127.0.0.1", port=8000, reload=True)
