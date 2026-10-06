"""
Auto poster: Instagram + Facebook Page.
- Folder fih ghir images  -> post (carousel ila kan >1)
- Folder fih images + video -> images = post, video = reel
- caption.txt = caption + hashtags
"""
import base64
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

GRAPH = "https://graph.facebook.com/v21.0"
ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
DONE = ROOT / "done"

IMG_EXT = {".jpg", ".jpeg", ".png"}
VID_EXT = {".mp4", ".mov"}

TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")
IG_USER_ID = os.environ.get("IG_USER_ID", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("BRANCH", "main")
DRY = os.environ.get("DRY_RUN", "0") == "1"

AI_KEY = os.environ.get("AI_API_KEY", "")
AI_BASE = os.environ.get("AI_BASE_URL", "https://api.z.ai/api/paas/v4").rstrip("/")
AI_MODELS = [m.strip() for m in os.environ.get("AI_MODEL", "glm-4.6v-flash").split(",") if m.strip()]
CAPTION_LANG = os.environ.get("CAPTION_LANG", "English")
BRAND_HINT = os.environ.get("BRAND_HINT", "Lidpet, a brand for pet lovers (pet apparel and accessories)")


def nat_key(p):
    """Natural sort: 2 < 10, 001 < 002, img (2) < img (10)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def raw_url(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{quote(rel)}"


def api(method, path, **params):
    params["access_token"] = TOKEN
    r = requests.request(method, f"{GRAPH}/{path}",
                         data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=120)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"{path}: {json.dumps(data)}")
    return data


def wait_ready(container_id, tries=60, delay=5):
    for _ in range(tries):
        s = api("GET", container_id, fields="status_code,status")
        code = s.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise RuntimeError(f"container {container_id} error: {s}")
        time.sleep(delay)
    raise RuntimeError(f"container {container_id} timeout")


# ---------- Instagram ----------
def ig_post(images, caption):
    if len(images) == 1:
        c = api("POST", f"{IG_USER_ID}/media", image_url=raw_url(images[0]), caption=caption)
        cid = c["id"]
    else:
        kids = []
        for img in images[:10]:
            k = api("POST", f"{IG_USER_ID}/media", image_url=raw_url(img), is_carousel_item="true")
            kids.append(k["id"])
        for k in kids:
            wait_ready(k)
        c = api("POST", f"{IG_USER_ID}/media", media_type="CAROUSEL",
                children=",".join(kids), caption=caption)
        cid = c["id"]
    wait_ready(cid)
    return api("POST", f"{IG_USER_ID}/media_publish", creation_id=cid)


def ig_reel(video, caption):
    c = api("POST", f"{IG_USER_ID}/media", media_type="REELS",
            video_url=raw_url(video), caption=caption, share_to_feed="true")
    wait_ready(c["id"], tries=120, delay=5)
    return api("POST", f"{IG_USER_ID}/media_publish", creation_id=c["id"])


# ---------- Facebook ----------
def fb_post(images, caption):
    ids = []
    for img in images:
        p = api("POST", f"{FB_PAGE_ID}/photos", url=raw_url(img), published="false")
        ids.append(p["id"])
    params = {"message": caption}
    for i, pid in enumerate(ids):
        params[f"attached_media[{i}]"] = json.dumps({"media_fbid": pid})
    return api("POST", f"{FB_PAGE_ID}/feed", **params)


def fb_video(video, caption):
    return api("POST", f"{FB_PAGE_ID}/videos", file_url=raw_url(video), description=caption)



# ---------- Auto caption (OpenAI-compatible vision API: Zhipu GLM free by default) ----------
def _img_b64(path: Path) -> str:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((1024, 1024))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def generate_caption(images):
    if not AI_KEY:
        return ""
    prompt = (
        f"You write social media captions for {BRAND_HINT}. "
        "Look at the attached image(s) and write: (1) a short, warm, engaging caption of 2-3 sentences "
        "with at most 2 emojis, (2) a blank line, (3) 12-15 relevant hashtags on a single line. "
        f"Language: {CAPTION_LANG}. Output only the final text, no intro, no markdown, no quotes."
    )
    b64s = []
    for img in images[:3]:
        try:
            b64s.append(_img_b64(img))
        except Exception as e:
            print(f"  [warn] ma9drtch n9ra {img.name}: {e}", file=sys.stderr)

    # attempts: (nbr images, avec prefix data URI wla la)
    attempts = [(len(b64s), True), (len(b64s), False)]
    if len(b64s) > 1:
        attempts += [(1, True), (1, False)]

    for model in AI_MODELS:
        for n, prefixed in attempts:
            content = [{"type": "text", "text": prompt}]
            for b in b64s[:n]:
                url = f"data:image/jpeg;base64,{b}" if prefixed else b
                content.append({"type": "image_url", "image_url": {"url": url}})
            try:
                r = requests.post(
                    f"{AI_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": content}]},
                    timeout=120)
                data = r.json()
                if r.status_code >= 400 or "error" in data:
                    raise RuntimeError(json.dumps(data)[:300])
                text = data["choices"][0]["message"]["content"].strip()
                if text:
                    print(f"  [ok  ] caption generee b {model}")
                    return text
            except Exception as e:
                print(f"  [warn] {model} (images={n}, prefix={prefixed}) fshl: {e}", file=sys.stderr)
    return ""


# ---------- Main ----------
def load_status(folder):
    f = folder / "status.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save_status(folder, st):
    (folder / "status.json").write_text(json.dumps(st, indent=2))


def run_step(folder, st, key, fn, *args):
    if st.get(key):
        print(f"  [skip] {key} deja tposta")
        return True
    print(f"  [run ] {key}")
    if DRY:
        return True
    try:
        res = fn(*args)
        st[key] = True
        save_status(folder, st)
        print(f"  [ok  ] {key}: {res}")
        return True
    except Exception as e:
        print(f"  [FAIL] {key}: {e}", file=sys.stderr)
        return False


def main():
    folders = sorted((p for p in POSTS.iterdir() if p.is_dir()), key=nat_key)
    if not folders:
        print("Ma kayn ta folder f posts/")
        return
    folder = folders[0]
    print(f"Folder: {folder.name}")

    files = sorted(folder.iterdir(), key=nat_key)
    images = [f for f in files if f.suffix.lower() in IMG_EXT]
    videos = [f for f in files if f.suffix.lower() in VID_EXT]
    cap_file = folder / "caption.txt"
    caption = cap_file.read_text(encoding="utf-8").strip() if cap_file.exists() else ""
    if not caption:
        print("  Ma kayn caption.txt, kanwlldha mn AI...")
        caption = generate_caption(images)
        if not caption:
            print("Ma9drtch nwlld caption: zid AI_API_KEY wla caption.txt", file=sys.stderr)
            sys.exit(1)
        print("  Caption:\n" + caption)
        if not DRY:
            cap_file.write_text(caption, encoding="utf-8")

    if not images and not videos:
        print("Folder khawi, kanmoviih l done/")
        if not DRY:
            shutil.move(str(folder), str(DONE / folder.name))
        return

    st = load_status(folder)
    ok = True

    if images:
        if IG_USER_ID:
            ok &= run_step(folder, st, "instagram_post", ig_post, images, caption)
        if FB_PAGE_ID:
            ok &= run_step(folder, st, "facebook_post", fb_post, images, caption)
    if videos:
        v = videos[0]
        if IG_USER_ID:
            ok &= run_step(folder, st, "instagram_reel", ig_reel, v, caption)
        if FB_PAGE_ID:
            ok &= run_step(folder, st, "facebook_video", fb_video, v, caption)

    if DRY:
        print("DRY RUN: walou tpposta.")
        return
    if ok:
        (folder / "status.json").unlink(missing_ok=True)
        shutil.move(str(folder), str(DONE / folder.name))
        print(f"Kml: {folder.name} -> done/")
    else:
        print("Chi platform fshl; ghadi n3awdo f run jay (status.json kaybqa).")
        sys.exit(1)


if __name__ == "__main__":
    main()
