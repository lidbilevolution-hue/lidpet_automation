"""
Auto poster: Instagram + Facebook Page.
- Folder fih ghir images  -> post (carousel ila kan >1)
- Folder fih images + video -> images = post, video = reel
- caption.txt = caption + hashtags
"""
import json
import os
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
    folders = sorted(p for p in POSTS.iterdir() if p.is_dir())
    if not folders:
        print("Ma kayn ta folder f posts/")
        return
    folder = folders[0]
    print(f"Folder: {folder.name}")

    files = sorted(folder.iterdir())
    images = [f for f in files if f.suffix.lower() in IMG_EXT]
    videos = [f for f in files if f.suffix.lower() in VID_EXT]
    cap_file = folder / "caption.txt"
    caption = cap_file.read_text(encoding="utf-8").strip() if cap_file.exists() else ""

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
