import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

# 加载环境变量
load_dotenv()

class LLMClient:
    def __init__(self, log_path: Optional[str | Path] = None):
        """
        初始化 LLM 客户端
        从环境变量中读取 API Key 和 Base URL
        """
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        self.model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
        
        if not api_key:
            raise ValueError("Environment variable OPENAI_API_KEY is not set")
            
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.log_path = Path(log_path) if log_path else Path("log") / "llm.log"

    def chat_once(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful assistant.",
        log_label: Optional[str] = None,
    ) -> str:
        """
        单次对话方法
        
        Args:
            prompt (str): 用户的输入
            system_prompt (str): 系统提示词，用于设定 AI 的角色
            
        Returns:
            str: AI 的回复内容
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages
            )
            output = response.choices[0].message.content
            self._log_llm_call(messages, output, label=log_label)
            return output
        except Exception as e:
            error_text = f"Error in chat_once: {str(e)}"
            self._log_llm_call(messages, error_text, label=log_label, error=True)
            return error_text

    def chat_multi_turn(
        self, messages: List[Dict[str, str]], log_label: Optional[str] = None
    ) -> str:
        """
        多次对话方法 (支持上下文)
        
        Args:
            messages (List[Dict[str, str]]): 历史消息列表，格式如 [{"role": "user", "content": "..."}]
            
        Returns:
            str: AI 的最新回复
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages
            )
            output = response.choices[0].message.content
            self._log_llm_call(messages, output, label=log_label)
            return output
        except Exception as e:
            error_text = f"Error in chat_multi_turn: {str(e)}"
            self._log_llm_call(messages, error_text, label=log_label, error=True)
            return error_text

    def _log_llm_call(
        self,
        messages: List[Dict[str, str]],
        output: str,
        label: Optional[str] = None,
        error: bool = False,
    ) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().isoformat(timespec="seconds")
            lines = [f"---{timestamp}---", f"MODEL: {self.model}"]
            if label:
                lines.append(f"TYPE: {label}")
            if error:
                lines.append("STATUS: error")
            lines.append("MESSAGES:")
            for message in messages:
                role = str(message.get("role", "unknown")).upper()
                content = str(message.get("content", ""))
                lines.append(f"{role}: {content}")
            lines.append("OUTPUT:")
            lines.append(str(output))
            entry = "\n".join(lines) + "\n"
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        except Exception:
            return

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
