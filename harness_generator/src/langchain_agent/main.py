# main.py
from __future__ import annotations
from fastapi import FastAPI, Body
from main_brain import create_agent_outside
from dataclasses import asdict
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fuzz_relative_functions import fuzz_logic

app = FastAPI(title="LangChain Agent API", version="1.0")

# 设置静态文件目录
current_dir = os.path.dirname(os.path.abspath(__file__))#获取当前绝对路径
static_dir = os.path.join(current_dir, "static")
print(static_dir)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

#创建线程池
executor = ThreadPoolExecutor(max_workers=5)

class chat_model(BaseModel):
    text: str
    model: str = "deepseek-chat"
    temperature: float = 0.5
    timeout: int = 10
    max_tokens: int = 1000

class fuzz_model(BaseModel):
    code_url: str
    email: str
    model: str = "deepseek-chat"
    temperature: float = 0.5
    timeout: int = 10
    max_tokens: int = 1000

@app.post("/chat_with_agent")#和智能体对话，测试用例
def chat(request: chat_model = Body(...)):
    """与智能体对话"""
    result = create_agent_outside(request.text, request.model)
    print(result)
    result = result['structured_response'].response
    if hasattr(result, "__dataclass_fields__"):
        result = asdict(result)
    print(result)
    return {"reply": result}

@app.post("/fuzz_code")#对代码仓库进行模糊测试(后续添加发送邮件功能)
def fuzz_code(request: fuzz_model = Body(...)):
    """对代码仓库进行模糊测试"""
    print("Received fuzzing request for URL:", request.code_url)
    fuzz_logic(request.code_url)
    return {"status": "Fuzzing report generated."}


@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(static_dir, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    # finla = create_agent_outside("what is the weather outside?")
    # print(finla)