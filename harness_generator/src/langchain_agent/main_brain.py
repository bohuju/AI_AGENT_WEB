
from __future__ import annotations
SYSTEM_PROMPT = """You are a helpful AI assistant that calls tools to get information when needed.
When you call a tool, you must use the exact function signature provided.
You must always call a tool when you have enough information to do so.
After calling a tool, wait for the tool's response before answering the user.
Use the tool responses to help you answer the user's questions.
When you respond, you must respond in the specified response format.
"""

from dataclasses import dataclass
from langchain.tools import tool, ToolRuntime
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
import json

from pathlib import Path
import subprocess

checkpointer = InMemorySaver()

@tool
def get_weather_for_location(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"


@dataclass
class Context:
    """Custom runtime context schema."""
    user_id: str

@tool
def get_user_location(runtime: ToolRuntime[Context]) -> str:
    """Retrieve user information based on user ID."""
    user_id = runtime.context.user_id
    return "Florida" if user_id == "1" else "SF"

@tool
def refuse_tool_call(reason: str) -> str:
    """Tool to refuse to call a tool."""
    return f"Refused to call tool: {reason}"

model = init_chat_model(
    "deepseek-chat",
    temperature=0.5,
    timeout=10,
    max_tokens=1000
)



# We use a dataclass here, but Pydantic models are also supported.
@dataclass
class ResponseFormat:
    """标准化的智能体响应格式"""
    response: str                   # 智能体最终的回答
    used_tools: list[str] | None = None  # 使用过的工具名称列表
    response_key: str | None = None  # 指定主要回答的字段名称
    def to_json(self) -> str:
        """转成 JSON 字符串，供 LangChain 输出使用"""
        return json.dumps({
            "response": self.response,
            "used_tools": self.used_tools or []
        }, ensure_ascii=False)

# agent = create_agent(
#     model=model,
#     system_prompt=SYSTEM_PROMPT,
#     tools=[get_user_location, get_weather_for_location],
#     context_schema=Context,
#     response_format=ResponseFormat,
#     checkpointer=checkpointer
# )

# # `thread_id` is a unique identifier for a given conversation.
# config = {"configurable": {"thread_id": "1"}}

# response = agent.invoke(
#     {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
#     config=config,
#     context=Context(user_id="1")
# )

# print(response['structured_response'])
# ResponseFormat(
#     punny_response="Florida is still having a 'sun-derful' day! The sunshine is playing 'ray-dio' hits all day long! I'd say it's the perfect weather for some 'solar-bration'! If you were hoping for rain, I'm afraid that idea is all 'washed up' - the forecast remains 'clear-ly' brilliant!",
#     weather_conditions="It's always sunny in Florida!"
# )

def create_agent_outside(input_text: str,mdoel:str = "deepseek-chat", temperature:float=0.5, timeout:int=10, max_tokens:int=1000):

    model = init_chat_model(
    "deepseek-chat",
    temperature=0.5,
    timeout=10,
    max_tokens=1000
)
    
    agent = create_agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[get_user_location, get_weather_for_location],
    context_schema=Context,
    response_format=ResponseFormat,
    checkpointer=checkpointer
    )

    config = {"configurable": {"thread_id": "1"}}

    response = agent.invoke(
    {"messages": [{"role": "user", "content": f'{input_text}'}]},
    config=config,
    context=Context(user_id="1")
)
    return response




if __name__ == "__main__":
    
    # `thread_id` is a unique identifier for a given conversation.
    config = {"configurable": {"thread_id": "1"}}

    response = agent.invoke(
        {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
        config=config,
        context=Context(user_id="1")
    )

    print(response['structured_response'])
    # ResponseFormat(
    #     punny_response="Florida is still having a 'sun-derful' day! The sunshine is playing 'ray-dio' hits all day long! I'd say it's the perfect weather for some 'solar-bration'! If you were hoping for rain, I'm afraid that idea is all 'washed up' - the forecast remains 'clear-ly' brilliant!",
    #     weather_conditions="It's always sunny in Florida!"
    # )
