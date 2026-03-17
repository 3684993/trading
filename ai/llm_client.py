import json
import requests
import time
from typing import Dict, Optional
from core.logger import logger
from config.settings import settings


class LLMClient:
    def __init__(self, base_url: str = None, api_key: str = None, model: str = None):
        self.base_url = base_url or settings.openai_base_url or "http://127.0.0.1:1234/v1"
        self.api_key = api_key or settings.openai_api_key or "lm-studio"
        self.model = model or settings.openai_model or "local-model"
        self.max_retries = 3
        self.retry_delay = 5
        
        if not self.base_url.endswith("/chat/completions"):
            self.chat_url = f"{self.base_url}/chat/completions"
        else:
            self.chat_url = self.base_url
        
        logger.info(f"LLMClient initialized with base_url: {self.base_url}, model: {self.model}")
    
    def chat(self, messages: list, temperature: float = 0.7, max_tokens: int = 500, timeout: int = 180) -> Optional[str]:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                }
                
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                
                logger.info(f"LLM request attempt {attempt + 1}/{self.max_retries}")
                
                response = requests.post(
                    self.chat_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout
                )
                
                if response.status_code == 200:
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    logger.info(f"LLM response received, length: {len(content)}")
                    return content
                else:
                    last_error = f"HTTP {response.status_code}: {response.text}"
                    logger.error(f"LLM API error: {last_error}")
                    
            except requests.exceptions.Timeout:
                last_error = "Request timeout"
                logger.warning(f"LLM request timeout (attempt {attempt + 1})")
            except requests.exceptions.ConnectionError as e:
                last_error = str(e)
                logger.warning(f"LLM connection error (attempt {attempt + 1}): {e}")
            except Exception as e:
                last_error = str(e)
                logger.error(f"LLM request failed (attempt {attempt + 1}): {e}")
            
            if attempt < self.max_retries - 1:
                logger.info(f"Retrying in {self.retry_delay} seconds...")
                time.sleep(self.retry_delay)
        
        logger.error(f"LLM request failed after {self.max_retries} attempts: {last_error}")
        return None
    
    def chat_json(self, messages: list, temperature: float = 0.7, max_tokens: int = 4000) -> Optional[Dict]:
        try:
            response = self.chat(messages, temperature, max_tokens)
            
            if response:
                logger.debug(f"Raw LLM response (first 1000 chars): {response[:1000]}")
                
                # Handle <think\> tags from deepseek-r1 and similar reasoning models
                # These models output thinking tokens before the actual response
                if "<think" in response:
                    think_end = response.find("</think")
                    if think_end != -1:
                        response = response[think_end + 8:]
                        logger.debug(f"After removing think tag: {response[:500]}")
                
                if "```json" in response:
                    json_start = response.find("```json") + 7
                    json_end = response.find("```", json_start)
                    if json_end != -1:
                        json_str = response[json_start:json_end].strip()
                        logger.info(f"Found JSON code block, length: {len(json_str)}")
                        try:
                            result = json.loads(json_str)
                            logger.info(f"Successfully parsed JSON from code block")
                            return result
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error in code block: {e}")
                
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                
                if json_start != -1 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    logger.info(f"Found JSON object, length: {len(json_str)}")
                    try:
                        result = json.loads(json_str)
                        logger.info(f"Successfully parsed JSON from LLM response")
                        return result
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}, content: {json_str[:300]}")
                        return None
                else:
                    logger.warning(f"No JSON found in LLM response: {response[:500]}")
                    return None
            return None
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM JSON parsing failed: {e}")
            return None
    
    def test_connection(self) -> bool:
        try:
            messages = [{"role": "user", "content": "Hello"}]
            response = self.chat(messages, max_tokens=10)
            return response is not None
        except Exception as e:
            logger.error(f"LLM connection test failed: {e}")
            return False
