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
import smtplib

load_dotenv()

def fuzz_logic(repo_url: str,max_len: int, time_budget:int,email:str) -> str:
    send_email_to_user(file_path="harness_generator/output", email_to=email, subject="天衡-您的模糊测试进程已启动！",target_url=repo_url)
    repospec = RepoSpec(
        url=f"{repo_url}"
    )
    generator = NonOssFuzzHarnessGenerator(
    repo_spec= repospec,
    ai_key_path=Path("./.env"),
    max_len=max_len,
    time_budget_per_target = time_budget)
    generator.generate()
    # send_email_to_user(file_path=repospec,email_to=email, subject="Fuzzing Task Completed")
    return "Fuzzing completed."


def send_email_to_user(file_path: str, email_to: str, subject: str,target_url:str) -> str:
    """
    读取文件 -> 调用 DeepSeek 生成总结(未做) -> 发送简短邮件通知任务完成。
    附加任何文件。
    """
    file_path = Path(file_path)
    file_path = Path(file_path/"crash_analysis.md")
    # 发件人邮箱地址
    sendAddress = '2074484142@qq.com'
    # 发件人授权码
    password = 'bitaremetiwfedcf'
    # 连接服务器
    server = smtplib.SMTP_SSL('smtp.qq.com', 465)
    # 登录邮箱
    loginResult = server.login(sendAddress, password)

    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    # 正文
    content =f"""
    您好！
    您的模糊测试链接已收到： {target_url}
    模糊测试正在运行，请稍后查看结果。
    任务完成后，您可以在附件中找到模糊测试的结果总结。
    祝您使用愉快！
    """
    # 定义一个可以添加正文的邮件消息对象
    msg = MIMEText(content, 'plain', 'utf-8')

    # 发件人昵称和地址
    msg['From'] = 'bohuju<2074484142@qq.com>'
    # 收件人昵称和地址
    msg['To'] = f'<{email_to}>'
    # 邮件主题
    msg['Subject'] = subject

    from_addr = '2074484142@qq.com'
    to_addrs = email_to

    server.sendmail(from_addr, to_addrs, msg.as_string())

if __name__ == "__main__":
    # 测试用例
    # repo_url = "https://github.com/syoyo/tinyexr.git"
    # fuzz_logic(repo_url)
    send_email_to_user(file_path="test.txt", email_to="yfdwx5@163.com", subject="天衡-您的模糊测试进程已启动！")