import asyncio
import httpx
import time

async def send_request(client, request_id, url):
    """发送一个模糊测试请求"""
    payload = {
        "code_url": f"https://github.com/syoyo/tinyexr.git",
        "email": f"test{request_id}@example.com",
        "model": "deepseek-chat",
        "temperature": 0.5,
        "timeout": 10,
        "max_tokens": 1024,
        "time_budget": 10,  # 改短以便快速测试
        "rounds": 5
    }
    
    print(f"[{time.strftime('%H:%M:%S')}] 请求 {request_id} 发送...")
    start = time.time()
    
    response = await client.post(url, json=payload)
    elapsed = time.time() - start
    
    print(f"[{time.strftime('%H:%M:%S')}] 请求 {request_id} 完成 (耗时 {elapsed:.2f}秒)")
    return response.json()

async def test_concurrent():
    """并发测试 3 个请求"""
    async with httpx.AsyncClient() as client:
        url = "http://127.0.0.1:8000/fuzz_code"
        
        print("开始并发测试...")
        start_time = time.time()
        
        # 同时发送 3 个请求
        tasks = [
            send_request(client, 1, url),
            send_request(client, 2, url),
            send_request(client, 3, url),
        ]
        
        results = await asyncio.gather(*tasks)
        
        total_time = time.time() - start_time
        print(f"\n总耗时: {total_time:.2f}秒")
        print(f"结果: {results}")

if __name__ == "__main__":
    asyncio.run(test_concurrent())