import sys
import os
import unittest

# 将项目根目录添加到 python path 以便导入模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_api.llm_client import LLMClient

class TestLLMClient(unittest.TestCase):
    def setUp(self):
        try:
            self.client = LLMClient()
            print("\nLLMClient initialized successfully.")
        except ValueError as e:
            print(f"\nSkipping tests: {e}")
            self.skipTest(str(e))

    def test_chat_once(self):
        print("\nTesting chat_once...")
        prompt = "Hello, say 'test successful' if you can hear me."
        response = self.client.chat_once(prompt)
        print(f"Response: {response}")
        self.assertIsInstance(response, str)
        self.assertTrue(len(response) > 0)
        self.assertNotIn("Error in chat_once", response)

    def test_chat_multi_turn(self):
        print("\nTesting chat_multi_turn...")
        messages = [
            {"role": "system", "content": "You are a math tutor."},
            {"role": "user", "content": "What is 1 + 1?"},
            {"role": "assistant", "content": "1 + 1 equals 2."},
            {"role": "user", "content": "Multiply that by 5."}
        ]
        response = self.client.chat_multi_turn(messages)
        print(f"Response: {response}")
        self.assertIsInstance(response, str)
        self.assertTrue(len(response) > 0)
        self.assertNotIn("Error in chat_multi_turn", response)
        # 简单验证回复中是否包含正确答案 "10" 
        # 注意：这取决于具体的 LLM，可能不是完全可靠的断言，但对于测试连通性足够了
        self.assertTrue(any(x in response for x in ["10", "ten"]))

if __name__ == '__main__':
    unittest.main()
