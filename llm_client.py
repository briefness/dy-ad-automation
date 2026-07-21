"""
LLM 客户端封装（文案个性化基础设施）

功能：
- 支持 OpenAI 兼容 API（DeepSeek / Moonshot / ChatAnywhere / 豆包 等）
- 文本生成 + JSON 模式
- 自动重试（指数退避）
- 超时控制
- 错误兜底（返回 None，不抛异常）

使用方式：
    from llm_client import generate_text, generate_json

    # 生成文本
    result = generate_text("写一段带货文案", system_prompt="你是专业的广告文案")

    # 生成 JSON
    data = generate_json("返回产品信息", system_prompt="输出 JSON 格式")
"""

import json
import time
from typing import List, Dict, Optional, Any

import requests

from config import (
    LLM_BASE_URL,
    LLM_API_KEY,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    LLM_TIMEOUT,
    LLM_MAX_RETRIES,
    LLM_ENABLED,
)


class LLMJSONError(RuntimeError):
    """Base class for a received response whose JSON contract is unusable."""

    def __init__(self, message: str, raw_text: str, finish_reason: str = ""):
        super().__init__(message)
        self.raw_text = raw_text
        self.finish_reason = finish_reason


class LLMJSONParseError(LLMJSONError):
    """The provider returned a complete response that is not valid JSON."""


class LLMJSONTruncatedError(LLMJSONError):
    """The provider stopped because the output token budget was exhausted."""


# ============================================================
# LLM 客户端
# ============================================================

class LLMClient:
    """
    通用 LLM 客户端，兼容 OpenAI API 格式。

    支持 DeepSeek、Moonshot、ChatAnywhere、豆包等所有 OpenAI 兼容接口。

    Args:
        base_url: API 基础地址（需包含 /v1 等路径前缀）
        api_key: API 密钥
        model: 模型名称
        timeout: 请求超时时间（秒）
        max_retries: 网络错误最大重试次数
    """

    def __init__(
        self,
        base_url: str = LLM_BASE_URL,
        api_key: str = LLM_API_KEY,
        model: str = LLM_MODEL,
        timeout: int = LLM_TIMEOUT,
        max_retries: int = LLM_MAX_RETRIES,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    # ----------------------------------------------------------
    # 核心方法
    # ----------------------------------------------------------

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Optional[str]:
        """
        发送聊天请求，返回生成的文本。

        Args:
            messages: 消息列表，格式如 [{"role": "user", "content": "..."}]
            temperature: 采样温度，默认使用配置值
            max_tokens: 最大生成 token 数，默认使用配置值
            stream: 是否流式输出（当前仅支持 False）

        Returns:
            生成的文本字符串，失败返回 None
        """
        if not self.api_key:
            print("⚠️  LLM API Key 未配置，跳过调用")
            return None

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
            "max_tokens": max_tokens if max_tokens is not None else LLM_MAX_TOKENS,
            "stream": stream,
        }

        # 指数退避重试
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                # 提取回复文本
                choices = data.get("choices", [])
                if not choices:
                    print("⚠️  LLM 返回为空（choices 为空）")
                    return None

                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    print("⚠️  LLM 返回内容为空")
                    return None

                return content.strip()

            except requests.exceptions.Timeout as e:
                last_error = f"请求超时：{e}"
            except requests.exceptions.ConnectionError as e:
                last_error = f"连接错误：{e}"
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response is not None else 0
                if status_code == 429:
                    # 限流：解析 Retry-After 头，默认等 60s 后重试
                    retry_after = 60
                    try:
                        retry_after = int(e.response.headers.get("Retry-After", 60))
                    except (ValueError, AttributeError):
                        pass
                    wait_sec = max(retry_after, 2 ** attempt)
                    print(f"⚠️  LLM 429 限流，{wait_sec}s 后重试（{attempt + 1}/{self.max_retries + 1}）")
                    time.sleep(wait_sec)
                    last_error = f"429 限流：{e}"
                else:
                    # 其他 HTTP 错误（鉴权/参数问题）不重试
                    print(f"❌ LLM HTTP 错误：{e}")
                    try:
                        err_detail = resp.json()
                        print(f"   错误详情：{err_detail}")
                    except Exception:
                        pass
                    return None
            except Exception as e:
                last_error = f"未知错误：{e}"

            # 重试逻辑
            if attempt < self.max_retries:
                wait_sec = 2 ** attempt  # 指数退避：1s, 2s, 4s...
                print(f"⚠️  LLM 调用失败（第 {attempt + 1} 次），{wait_sec}s 后重试...  {last_error}")
                time.sleep(wait_sec)

        # 所有重试都失败
        print(f"❌ LLM 调用失败（已重试 {self.max_retries} 次）：{last_error}")
        return None

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        raise_on_parse_error: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        发送聊天请求，返回解析后的 JSON 字典。

        优先使用模型的 JSON 模式（response_format），如果不支持则自动降级为
        从普通文本中提取 JSON。

        Args:
            messages: 消息列表
            temperature: 采样温度
            max_tokens: 最大生成 token 数

        Returns:
            解析后的 JSON 字典，失败返回 None
        """
        # 方式一：尝试用 response_format 请求 JSON 模式
        json_messages = list(messages)
        # 在 system prompt 中注入 JSON 格式要求（兼容所有模型）
        has_system = any(m.get("role") == "system" for m in json_messages)
        json_instruction = "请严格以 JSON 格式输出，不要包含任何额外的文字、解释或 markdown 代码块。"

        if has_system:
            for i, m in enumerate(json_messages):
                if m.get("role") == "system":
                    json_messages[i] = {
                        "role": "system",
                        "content": m["content"] + "\n\n" + json_instruction,
                    }
                    break
        else:
            json_messages.insert(0, {
                "role": "system",
                "content": json_instruction,
            })

        # P1 修复：真正使用 response_format 参数（OpenAI 兼容标准）
        # 先尝试带 response_format 调用，如果模型不支持会报错，再降级
        text = None
        finish_reason = ""
        response_format_unsupported = False
        if self.api_key:
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "model": self.model,
                "messages": json_messages,
                "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
                "max_tokens": max_tokens if max_tokens is not None else LLM_MAX_TOKENS,
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            last_error = None
            for attempt in range(self.max_retries + 1):
                try:
                    resp = self._session.post(
                        url, json=payload, headers=headers, timeout=self.timeout,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    text = choice["message"]["content"]
                    finish_reason = str(choice.get("finish_reason") or "")
                    break
                except requests.exceptions.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else 0
                    detail = exc.response.text.lower() if exc.response is not None else ""
                    if status in {400, 404, 422} and (
                        "response_format" in detail
                        and any(term in detail for term in ("not support", "unsupported", "unknown"))
                    ):
                        response_format_unsupported = True
                        break
                    last_error = f"HTTP 错误：{exc}"
                except requests.exceptions.Timeout as exc:
                    last_error = f"请求超时：{exc}"
                except requests.exceptions.ConnectionError as exc:
                    last_error = f"连接错误：{exc}"
                except Exception as exc:
                    last_error = f"JSON 请求失败：{exc}"
                if attempt < self.max_retries:
                    wait_sec = 2 ** attempt
                    print(
                        f"⚠️  LLM JSON 调用失败（第 {attempt + 1} 次），"
                        f"{wait_sec}s 后重试...  {last_error}"
                    )
                    time.sleep(wait_sec)
            if text is None and not response_format_unsupported:
                print(f"❌ LLM JSON 调用失败（已重试 {self.max_retries} 次）：{last_error}")
                return None

        if text is None and response_format_unsupported:
            text = self.chat(json_messages, temperature=temperature, max_tokens=max_tokens)
        if text is None:
            return None

        # 尝试解析 JSON
        try:
            return self._extract_json(text)
        except Exception as e:
            print(f"❌ LLM JSON 解析失败：{e}")
            print(f"   原始输出前 200 字：{text[:200]}")
            if raise_on_parse_error:
                error_type = LLMJSONTruncatedError if finish_reason == "length" else LLMJSONParseError
                raise error_type(str(e), text, finish_reason) from e
            return None

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        """
        从模型输出中提取 JSON 对象。

        支持以下情况：
        - 纯 JSON 字符串
        - 被 markdown 代码块包裹（```json ... ```）
        - JSON 前后有说明文字

        Args:
            text: 模型输出文本

        Returns:
            解析后的 JSON 字典

        Raises:
            json.JSONDecodeError: 无法解析时抛出
        """
        text = text.strip()

        # 去除 markdown 代码块
        if text.startswith("```"):
            # 去掉开头的 ```json 或 ```
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            # 去掉结尾的 ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取第一个 { 到最后一个 } 之间的内容
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
            return json.loads(candidate)

        # 尝试提取第一个 [ 到最后一个 ] 之间的内容（JSON 数组）
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
            return {"data": json.loads(candidate)}

        # 全部失败，抛出异常
        raise json.JSONDecodeError("无法从文本中提取 JSON", text, 0)


# ============================================================
# 单例 & 便捷函数
# ============================================================

_default_client: Optional[LLMClient] = None


def get_default_client() -> Optional[LLMClient]:
    """
    获取默认 LLM 客户端（从 config 读取配置，懒加载单例）。

    Returns:
        LLMClient 实例，如果 LLM 未启用或 API Key 为空返回 None
    """
    global _default_client

    # 运行时读取 config，确保 --no-llm 等运行时修改生效
    # （不能用模块顶部 from config import LLM_ENABLED，因为那是值拷贝，
    #  运行时修改 config.LLM_ENABLED 不会反映到已导入的变量上）
    import config as _cfg

    if not _cfg.LLM_ENABLED:
        return None

    if _default_client is None:
        if not _cfg.LLM_API_KEY:
            print("💡 LLM API Key 未配置，LLM 功能将不可用（自动降级为模板模式）")
            return None
        _default_client = LLMClient(
            base_url=_cfg.LLM_BASE_URL,
            api_key=_cfg.LLM_API_KEY,
            model=_cfg.LLM_MODEL,
            timeout=_cfg.LLM_TIMEOUT,
            max_retries=_cfg.LLM_MAX_RETRIES,
        )
        print(f"✅ LLM 客户端已初始化：{_cfg.LLM_MODEL} @ {_cfg.LLM_BASE_URL}")

    return _default_client


def generate_text(
    prompt: str,
    system_prompt: Optional[str] = None,
    **kwargs,
) -> Optional[str]:
    """
    便捷函数：生成文本。

    Args:
        prompt: 用户提示词
        system_prompt: 系统提示词（可选）
        **kwargs: 透传给 LLMClient.chat 的参数（temperature, max_tokens 等）

    Returns:
        生成的文本，失败或未启用返回 None
    """
    client = get_default_client()
    if client is None:
        return None

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    return client.chat(messages, **kwargs)


def generate_json(
    prompt: str,
    system_prompt: Optional[str] = None,
    **kwargs,
) -> Optional[Dict[str, Any]]:
    """
    便捷函数：生成 JSON。

    Args:
        prompt: 用户提示词
        system_prompt: 系统提示词（可选）
        **kwargs: 透传给 LLMClient.chat_json 的参数

    Returns:
        解析后的 JSON 字典，失败或未启用返回 None
    """
    client = get_default_client()
    if client is None:
        return None

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    return client.chat_json(messages, **kwargs)


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 LLM 客户端自测")
    print("=" * 60)

    # ---- 测试 1：未配置 API Key 时的错误处理 ----
    print("\n📝 测试 1：未配置 API Key 的错误处理")
    client_no_key = LLMClient(api_key="", base_url="https://api.deepseek.com/v1", model="deepseek-chat")
    result = client_no_key.chat([{"role": "user", "content": "你好"}])
    assert result is None, "❌ 未配置 API Key 时应返回 None"
    print("   ✅ 通过：未配置 API Key 时返回 None，无异常抛出")

    # ---- 测试 2：JSON 提取 - 纯 JSON ----
    print("\n📝 测试 2：JSON 提取 - 纯 JSON 字符串")
    json_text = '{"name": "测试产品", "price": 99, "tags": ["a", "b"]}'
    parsed = LLMClient._extract_json(json_text)
    assert parsed["name"] == "测试产品" and parsed["price"] == 99
    print("   ✅ 通过：纯 JSON 字符串解析正确")

    # ---- 测试 3：JSON 提取 - markdown 代码块包裹 ----
    print("\n📝 测试 3：JSON 提取 - markdown 代码块包裹")
    md_json = """```json
{
    "title": "好物推荐",
    "hooks": [
        {"type": "question", "text": "你是不是也...？"}
    ]
}
```"""
    parsed = LLMClient._extract_json(md_json)
    assert parsed["title"] == "好物推荐"
    assert len(parsed["hooks"]) == 1
    print("   ✅ 通过：markdown 代码块中的 JSON 解析正确")

    # ---- 测试 4：JSON 提取 - 前后有说明文字 ----
    print("\n📝 测试 4：JSON 提取 - 前后有说明文字")
    noisy_json = """好的，这是你要的产品信息：

{
    "product": "面膜",
    "selling_point": "补水保湿"
}

希望对你有帮助！"""
    parsed = LLMClient._extract_json(noisy_json)
    assert parsed["product"] == "面膜"
    assert parsed["selling_point"] == "补水保湿"
    print("   ✅ 通过：含说明文字的 JSON 提取正确")

    # ---- 测试 5：JSON 解析失败的处理 ----
    print("\n📝 测试 5：chat_json 解析失败的错误处理")
    # 用 mock 方式测试：直接调用 _extract_json 传无效 JSON
    try:
        LLMClient._extract_json("这根本不是 JSON")
        print("   ❌ 失败：无效 JSON 应抛出异常")
    except json.JSONDecodeError:
        print("   ✅ 通过：无效 JSON 正确抛出 JSONDecodeError")

    # ---- 测试 6：便捷函数在未配置时返回 None ----
    print("\n📝 测试 6：便捷函数在 API Key 为空时返回 None")
    # 临时覆盖全局配置模拟未配置状态
    import config
    original_key = config.LLM_API_KEY
    original_enabled = config.LLM_ENABLED
    config.LLM_API_KEY = ""
    config.LLM_ENABLED = True

    # 重置单例（直接修改模块级变量）
    globals()["_default_client"] = None

    result = generate_text("测试")
    assert result is None, "❌ API Key 为空时 generate_text 应返回 None"

    result_json = generate_json("测试")
    assert result_json is None, "❌ API Key 为空时 generate_json 应返回 None"

    # 恢复配置
    config.LLM_API_KEY = original_key
    config.LLM_ENABLED = original_enabled
    globals()["_default_client"] = None
    print("   ✅ 通过：便捷函数在未配置时优雅返回 None")

    # ---- 测试 7：LLM_ENABLED = False 时 ----
    print("\n📝 测试 7：LLM_ENABLED = False 时禁用")
    config.LLM_ENABLED = False
    globals()["_default_client"] = None
    client = get_default_client()
    assert client is None, "❌ LLM 禁用时应返回 None"
    config.LLM_ENABLED = original_enabled
    globals()["_default_client"] = None
    print("   ✅ 通过：LLM_ENABLED=False 时正确禁用")

    print("\n" + "=" * 60)
    print("🎉 所有自测通过！")
    print("=" * 60)
