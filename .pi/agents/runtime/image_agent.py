#!/usr/bin/env python3
"""ImageAgent — 图片生成（纯生成，不关心存储和配置）"""
import os, sys, json, time
import requests

ONE_API_URL = os.environ.get("IMAGE_API_URL", "http://172.17.0.1:3000/v1/images/generations")
ONE_API_KEY = os.environ.get("IMAGE_API_KEY", "sk-22BhHx41WDRZfujO9d14Dc28C7F2404b8773F9056b734358")
MODEL = "gpt-image-2"

INDUSTRY_PROMPTS = {
    "power": "Professional industrial photography: renewable energy hardware, solar farm mounting bolts, wind turbine fasteners, galvanized steel finish, natural sunlight, outdoor setting, ultra-realistic, 8K, no text",
    "agriculture": "Professional industrial photography: agricultural machinery hardware, tractor bolts, harvester parts, green accents, farm field background, natural daylight, ultra-realistic, 8K, no text",
    "industrial": "Professional industrial photography: heavy machinery bolts, steel factory components, warm industrial lighting, brushed metal, ultra-realistic, 8K, no text",
    "mining": "Professional industrial photography: mining equipment bolts, wear-resistant hardware, rugged rocky terrain, harsh sunlight, heavy-duty steel, ultra-realistic, 8K, no text",
    "municipal": "Professional industrial photography: urban infrastructure hardware, bridge bolts, water pipe fittings, concrete and steel, city backdrop, clean daylight, ultra-realistic, 8K, no text",
}

PROCESS_PROMPTS = {
    "cnc-machining": "5-axis CNC machine cutting metal part, coolant spray, bright workshop, metallic reflections, ultra-realistic, 8K, no text",
    "casting": "Molten metal pouring into sand molds, dramatic glow, foundry atmosphere, sparks and heat waves, photorealistic, 8K, no text",
    "forging": "Red-hot steel hammered by industrial press, dramatic lighting, sparks flying, powerful industrial scene, photorealistic, 8K, no text",
    "mim-parts": "Precision MIM parts on inspection tray, microscope view, clean lab environment, scientific product photography, 8K, no text",
}

def generate(prompt: str, size: str = "1024x1024", quality: str = "standard") -> dict:
    """生成一张图片，返回 {status, url, revised_prompt}"""
    payload = {"model": MODEL, "prompt": prompt, "n": 1, "size": size, "quality": quality}
    try:
        r = requests.post(ONE_API_URL, json=payload,
                         headers={"Authorization": f"Bearer {ONE_API_KEY}"}, timeout=180)
        r.raise_for_status()
        data = r.json()
        if "data" in data and data["data"]:
            img = data["data"][0]
            return {"status": "ok", "url": img["url"], "revised_prompt": img.get("revised_prompt", prompt)}
        return {"status": "fail", "error": str(data.get("error", "unknown"))}
    except Exception as e:
        return {"status": "fail", "error": str(e)[:200]}

def generate_industry_card(industry: str, scene: str = "", size="1024x1024") -> dict:
    prompt = INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS["power"])
    if scene:
        prompt = f"{scene.replace('-',' ')} hardware: {prompt}"
    return generate(prompt, size)

def generate_hero(industry: str) -> dict:
    prompt = f"Wide cinematic establishing shot: {INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS['power'])}"
    return generate(prompt, "1792x1024", "hd")

def generate_process(process: str) -> dict:
    prompt = PROCESS_PROMPTS.get(process, f"{process} industrial process, professional photography, 8K, no text")
    return generate(prompt, size="1792x1024")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--type", choices=["card","hero","process","raw"], default="card")
    p.add_argument("--industry", default="power")
    p.add_argument("--scene", default="")
    p.add_argument("--prompt", default="")
    p.add_argument("--size", default="1024x1024")
    args = p.parse_args()

    if args.type == "raw":
        result = generate(args.prompt, args.size)
    elif args.type == "hero":
        result = generate_hero(args.industry)
    elif args.type == "process":
        result = generate_process(args.scene or args.industry)
    else:
        result = generate_industry_card(args.industry, args.scene, args.size)

    print(json.dumps(result, ensure_ascii=False, indent=2))
