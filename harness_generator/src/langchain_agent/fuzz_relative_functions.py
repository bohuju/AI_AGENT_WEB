from __future__ import annotations
import os
import smtplib
from email.mime.text import MIMEText
from pydantic import BaseModel
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import sys
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 获取父文件夹路径 dirname:获取目录名(路径去掉文件名)，嵌套两次获取父文件夹
sys.path.append(parent_dir)  # 添加父文件夹路径
from fuzz_unharnessed_repo import NonOssFuzzHarnessGenerator, RepoSpec
from pathlib import Path

load_dotenv()

def fuzz_logic(repo_url: str) -> str:
    repospec = RepoSpec(
        url=f"{repo_url}"
    )
    generator = NonOssFuzzHarnessGenerator(
    repo_spec= repospec,
    ai_key_path=Path("./.env"),)
    generator.generate()
    return "Fuzzing completed."


def send_email_to_user(file_path: str, email_to: str, subject: str) -> str:
    """
    读取文件 -> 调用 DeepSeek 生成总结 -> 发送简短邮件通知任务完成。
    不附加任何文件。
    """
    if not os.path.exists(file_path):
        return f"文件不存在: {file_path}"

    # Step 1: 读取文件内容
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Step 2: 用 DeepSeek 生成简短总结（例如生成一句任务报告）
    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        temperature=0.3,
        max_tokens=500,
    )

    prompt = f"""你是一名任务总结助手。
请阅读以下文件，生成一句话总结该任务的完成情况，语气积极、简洁：
{content[:4000]}
"""
    summary = llm.invoke(prompt).content.strip()

    # Step 3: 发送邮件（只发一句话）
    sender_email = os.getenv("SMTP_USER")
    sender_pass = os.getenv("SMTP_PASS")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))

    msg = MIMEText(f"您好，您的任务已成功完成。\n\nAI总结：{summary}")
    msg["From"] = sender_email
    msg["To"] = email_to
    msg["Subject"] = subject

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender_email, sender_pass)
            server.send_message(msg)
        return f"✅ 邮件已发送至 {email_to}。AI 总结：{summary}"
    except Exception as e:
        return f"❌ 邮件发送失败: {str(e)}"

if __name__ == "__main__":
    # 测试用例
    repo_url = "https://github.com/syoyo/tinyexr.git"
    fuzz_logic(repo_url)