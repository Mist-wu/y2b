import subprocess

def upload(video_path: str, title: str, desc: str, tags: list, tid: int, user_cookie: str):
    cmd = [
        "biliup", "upload",
        video_path,
        "--title", title,
        "--desc", desc,
        "--tag", ",".join(tags),
        "--tid", str(tid),
    ]
    subprocess.run(cmd, check=True)
    return "BV_FAKE_ID"