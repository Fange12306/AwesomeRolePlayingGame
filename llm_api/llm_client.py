import os
from dotenv import load_dotenv
from openai import OpenAI
from typing import List, Dict, Union

# 加载环境变量
load_dotenv()

class LLMClient:
    def __init__(self):
        """
        初始化 LLM 客户端
        从环境变量中读取 API Key 和 Base URL
        """
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        self.model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
        
        if not api_key:
            raise ValueError("Environment variable OPENAI_API_KEY is not set")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )

    def chat_once(self, prompt: str, system_prompt: str = "You are a helpful assistant.") -> str:
        """
        单次对话方法
        
        Args:
            prompt (str): 用户的输入
            system_prompt (str): 系统提示词，用于设定 AI 的角色
            
        Returns:
            str: AI 的回复内容
        """
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )
            
            return response.choices[0].message.content
        except Exception as e:
            return f"Error in chat_once: {str(e)}"

    def chat_multi_turn(self, messages: List[Dict[str, str]]) -> str:
        """
        多次对话方法 (支持上下文)
        
        Args:
            messages (List[Dict[str, str]]): 历史消息列表，格式如 [{"role": "user", "content": "..."}]
            
        Returns:
            str: AI 的最新回复
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )
            
            return response.choices[0].message.content
        except Exception as e:
            return f"Error in chat_multi_turn: {str(e)}"

if __name__ == "__main__":
    print("正在运行 LLMClient 直接测试...")
    try:
        client = LLMClient()
        print("LLMClient 初始化成功。")
        print("正在尝试单次对话...")
        response = client.chat_once("Hello! Just say 'I am working'.")
        print(f"收到回复: {response}")
    except Exception as e:
        print(f"运行时发生错误: {e}")
        print("请检查 .env 文件配置是否正确。")
