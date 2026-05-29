"""Yaxiio 多模型验证脚本 — 验证 Claude/GPT/DeepSeek/Gemini 均可正常工作"""
import os, sys, json, time

MODELS = [
    {"name": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "env_key": "DEEPSEEK_API_KEY"},
    {"name": "claude-3-5-sonnet-20241022", "base_url": "https://api.anthropic.com/v1", "env_key": "ANTHROPIC_API_KEY"},
    {"name": "gpt-4o", "base_url": "https://api.openai.com/v1", "env_key": "OPENAI_API_KEY"},
    {"name": "gemini-1.5-pro", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "env_key": "GEMINI_API_KEY"},
]

def test_model(name, base_url, api_key):
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=name,
            messages=[{"role": "user", "content": "Say 'Yaxiio' in one word."}],
            max_tokens=20, temperature=0,
        )
        return True, resp.choices[0].message.content[:50]
    except Exception as e:
        return False, str(e)[:120]

if __name__ == "__main__":
    print("=" * 50)
    print("Yaxiio Multi-Model Verification")
    print("=" * 50)
    
    available = []
    for m in MODELS:
        key = os.environ.get(m["env_key"], "")
        if not key:
            print(f"  ⏭️  {m['name']}: 跳过 (未设置 {m['env_key']})")
            continue
        print(f"  🔍 {m['name']}...", end=" ", flush=True)
        ok, msg = test_model(m["name"], m["base_url"], key)
        if ok:
            print(f"✅ {msg}")
            available.append(m["name"])
        else:
            print(f"❌ {msg}")

    print(f"\n结果: {len(available)}/{len(MODELS)} 模型可用")
    if available:
        print(f"可用: {', '.join(available)}")
    print("\n设置方法:")
    print("  export DEEPSEEK_API_KEY=sk-...")
    print("  export ANTHROPIC_API_KEY=sk-ant-...")
    print("  export OPENAI_API_KEY=sk-...")
    print("  export GEMINI_API_KEY=...")
