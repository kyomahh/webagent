import os
import time
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Web Test Agent Visualizer Backend")

# ⚡️ 关键配置：允许跨域请求
# 因为 React 前端一般运行在 5173 端口，FastAPI 运行在 8000 端口，必须开启 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 课设阶段可以直接放开，允许所有源访问
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 📂 路径配置：统一指定到项目根目录下的 output 文件夹
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "tmpoutput")
LOG_PATH = os.path.join(OUTPUT_DIR, "log.txt")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")

# 🛠️ 初始化：确保 output 及其子文件夹存在
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
if not os.path.exists(LOG_PATH):
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("=== System Initialized ===\n")

# 🖼️ 静态资源托管
# 将整个 output 目录挂载到 /static 路由下
# 这样前端就可以直接通过 http://localhost:8000/static/screenshots/xxxx.png 访问到图片
app.mount("/static", StaticFiles(directory=OUTPUT_DIR), name="static")


# 🤖 模拟 Agent 运行的后台任务
def run_agent_in_background():
    """
    这个函数会在后台线程运行，不会卡死后端的 HTTP 响应。
    在这里写往 output 里写日志和扔图片的逻辑。
    后续你们可以把它改成：subprocess.run(["python", "your_agent.py"])
    """
    # 模拟重新开始测试，先清空日志
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%X')}] 🚀 Agent 测试任务启动...\n")

    # 模拟测试步骤
    for i in range(1, 4):
        time.sleep(3)  # 模拟测试执行的耗时

        # 模拟写入新日志
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%X')}] 🔍 正在执行第 {i} 个测试用例...\n")
            f.write(f"[{time.strftime('%X')}] 📸 成功捕获当前页面截图。\n")

        # 模拟生成一个空图片文件（真实情况下是你们的 Agent 截图保存到这里）
        simulated_img = os.path.join(SCREENSHOT_DIR, f"step_{i}_{int(time.time())}.png")
        with open(simulated_img, "wb") as f:
            f.write(b"")  # 占位空图片

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n[{time.strftime('%X')}] 🎉 所有测试用例执行完毕！\n")


# ---------------- 🚀 API 接口设计 ----------------


@app.post("/api/start-test")
async def start_test(background_tasks: BackgroundTasks):
    """
    1. 触发测试接口
    使用 BackgroundTasks 让 Agent 在后台默默跑，后端立刻给前端返回成功响应。
    """
    background_tasks.add_task(run_agent_in_background)
    return {"status": "success", "message": "Agent pipeline started successfully."}


@app.get("/api/logs")
async def get_logs(offset: int = 0):
    """
    2. 日志读取接口（支持增量轮询）
    前端传一个 offset（读到了多少字节），后端只返回那之后的“新日志”
    """
    if not os.path.exists(LOG_PATH):
        return {"content": "", "offset": 0}

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        f.seek(offset)  # 移动指针到上次读过的地方
        content = f.read()  # 读取新产生的内容
        new_offset = f.tell()  # 记录当前读到了哪里

    return {"content": content, "offset": new_offset}


@app.get("/api/screenshots")
async def get_screenshots():
    """
    3. 获取截图列表接口
    扫描文件夹，把里面所有的图片文件名打包返回
    """
    if not os.path.exists(SCREENSHOT_DIR):
        return {"screenshots": []}

    files = os.listdir(SCREENSHOT_DIR)
    # 过滤出图片格式
    images = [f for f in files if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    # 按文件名排序，确保前端按时间/步骤顺序展示
    images.sort()

    # 拼接出前端可以直接访问的 URL 路径
    urls = [f"/static/screenshots/{img}" for img in images]
    return {"screenshots": urls}


if __name__ == "__main__":
    import uvicorn

    # 这里的 "server:app" 中，server指的是文件名 server.py，app指的是里面的 FastAPI 实例名
    # reload=True 表示只要你修改了代码保存，服务器就会自动热重启，非常适合开发！
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
