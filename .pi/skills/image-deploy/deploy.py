#!/usr/bin/env python3
"""ImageDeploy Skill — 下载 → 转webp → 上传OSS → 更新Redis"""
import os, sys, json, time, tempfile, subprocess, hashlib
import requests

OSS_BASE = "oss://lightingmetal-deploy/images/site"
CDN_BASE = "https://lighting-metal.oss-cn-hongkong.aliyuncs.com/images/site"
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASS", "Yaxiio2026")

def download(url: str) -> str:
    """下载图片到临时文件"""
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    suffix = ".png" if ".png" in url.split("?")[0] else ".webp"
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return path

def to_webp(local_path: str) -> str:
    """转 webp（如果安装了 Pillow）"""
    if not local_path.endswith(".webp"):
        try:
            from PIL import Image
            img = Image.open(local_path)
            webp_path = local_path.rsplit(".", 1)[0] + ".webp"
            img.save(webp_path, "webp", quality=85)
            os.unlink(local_path)
            return webp_path
        except ImportError:
            pass
    return local_path

def upload_oss(local_path: str, oss_path: str) -> str | None:
    """上传 OSS，返回 CDN URL"""
    full_oss = f"{OSS_BASE}/{oss_path}"
    try:
        proc = subprocess.run(
            ["ossutil64", "cp", local_path, full_oss, "-f"],
            capture_output=True, text=True, timeout=60
        )
        if proc.returncode == 0:
            return f"{CDN_BASE}/{oss_path}"
        print(f"[Deploy] OSS fail: {proc.stderr[:100]}", file=sys.stderr)
    except Exception as e:
        print(f"[Deploy] OSS error: {e}", file=sys.stderr)
    return None

def update_mongo(page: str, field: str, cdn_url: str, langs: list = None):
    """更新 MongoDB page_content 图片字段 → 后续会 sync 到 HK Redis"""
    if langs is None:
        langs = ["en", "zh", "ru", "ar", "es"]
    try:
        from pymongo import MongoClient
        c = MongoClient("mongodb://172.17.0.1:27017", serverSelectionTimeoutMS=3000)
        db = c.lightingmetal
        col = db.page_content
        for lang in langs:
            doc_path = f"/{lang}/{page}" if not page.startswith(lang) else page
            doc = col.find_one({"path": {"\$regex": page, "\$options": "i"}, "lang": lang})
            if doc:
                col.update_one({"_id": doc["_id"]}, {"\$set": {f"content.{field}": cdn_url}})
                print(f"[Deploy] MongoDB {lang}:{page}.{field} = {cdn_url}")
        c.close()
    except Exception as e:
        print(f"[Deploy] Mongo error: {e}", file=sys.stderr)

def deploy(image_url: str, category: str, industry: str = "power",
           scene: str = "", langs: list = None) -> dict:
    """主流程：下载→转换→上传→配置"""
    print(f"[Deploy] {category}/{industry}/{scene or 'default'}")
    
    # 1. 下载
    try:
        local = download(image_url)
    except Exception as e:
        return {"status": "fail", "step": "download", "error": str(e)[:100]}
    
    # 2. 转 webp
    local = to_webp(local)
    
    # 3. 构建 OSS 路径
    if category == "hero":
        oss_path = f"hero/hero-{industry}.webp"
        redis_field = f"heroImg"
    elif category == "card":
        oss_path = f"card/card-{industry}-{scene or 'default'}.webp"
        redis_field = f"scene{scene}Img" if scene else "cardImg"
    elif category == "scene":
        oss_path = f"scene/scene-{industry}-{scene}.webp"
        redis_field = f"sceneImg"
    elif category == "process":
        oss_path = f"process/process-{scene or industry}.webp"
        redis_field = f"heroImg"
    else:
        h = hashlib.md5(image_url.encode()).hexdigest()[:8]
        oss_path = f"misc/img-{h}.webp"
        redis_field = "img"
    
    # 4. 上传
    cdn_url = upload_oss(local, oss_path)
    os.unlink(local)
    
    if not cdn_url:
        return {"status": "fail", "step": "upload", "error": "OSS failed"}
    
    # 5. 更新 MongoDB → sync 到 HK Redis
    update_mongo(industry, redis_field, cdn_url, langs)
    
    return {"status": "ok", "url": cdn_url, "oss_path": oss_path}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="Image URL from ImageAgent")
    p.add_argument("--category", choices=["hero","card","scene","process"], default="card")
    p.add_argument("--industry", default="power")
    p.add_argument("--scene", default="")
    p.add_argument("--langs", default="en,zh,ru,ar,es")
    args = p.parse_args()
    
    langs = [l.strip() for l in args.langs.split(",")]
    result = deploy(args.url, args.category, args.industry, args.scene, langs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
