import json
import os
import time
from datetime import datetime
from zhipuai import ZhipuAI

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")


def save_data(data, filename):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filepath


def load_data(filename):
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def log_compliance(task_type, prompt, result):
    os.makedirs(LOG_DIR, exist_ok=True)
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "task_type": task_type,
        "prompt": prompt[:2000],
        "result": str(result)[:2000],
    }
    log_file = os.path.join(LOG_DIR, "compliance_log.jsonl")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


_client_cache = None


def get_llm_client():
    global _client_cache
    if _client_cache is None:
        api_key = os.environ.get("ZHIPUAI_API_KEY", "")
        _client_cache = ZhipuAI(api_key=api_key)
    return _client_cache


def call_llm(prompt, task_type="unknown", model="glm-4.6v", max_retries=5, base_delay=3):
    client = get_llm_client()
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            result = response.choices[0].message.content
            log_compliance(task_type, prompt, result)
            return result
        except Exception as e:
            err_code = getattr(e, "code", None) or str(e)
            if "429" in err_code or "1302" in err_code or "速率" in str(e):
                delay = base_delay * (attempt + 1)
                print(f"  ⏳ API速率限制，等待 {delay}s 后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(delay)
            elif attempt < max_retries:
                print(f"  ⚠️ 调用失败: {e}，重试中 ({attempt + 1}/{max_retries})...")
                time.sleep(base_delay)
            else:
                raise
    raise RuntimeError(f"LLM调用失败，已重试{max_retries}次")
