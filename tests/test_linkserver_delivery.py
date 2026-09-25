import httpx

from bot.services.delivery import choose_mode, make_zip
from bot.services.linkserver import LinkServer, sign, verify

MB = 1024 * 1024


def test_sign_verify_expiry_and_tamper():
    token = sign("a/b.mp4", "key", 60, now=1000)
    assert verify(token, "key", now=1030) == "a/b.mp4"
    assert verify(token, "key", now=2000) is None  # expired
    assert verify(token, "other-key", now=1030) is None
    payload, mac = token.split(".")
    assert verify(payload + "x." + mac, "key", now=1030) is None
    assert verify("garbage", "key") is None


async def test_link_server_serves_and_protects(tmp_path, unused_tcp_port):
    root = tmp_path / "downloads"
    (root / "job1").mkdir(parents=True)
    f = root / "job1" / "video file.mp4"
    f.write_bytes(b"x" * 5000)
    (tmp_path / "secret.txt").write_text("nope")
    server = LinkServer(root, "k", f"http://127.0.0.1:{unused_tcp_port}", "127.0.0.1", unused_tcp_port, 1)
    await server.start()
    try:
        url = server.link_for(f)
        assert "video%20file.mp4" in url
        async with httpx.AsyncClient() as client:
            res = await client.get(url)
            assert res.status_code == 200 and len(res.content) == 5000
            assert "attachment" in res.headers["content-disposition"]
            bad = await client.get(url.replace("/f/", "/f/x"))
            assert bad.status_code == 403
            evil = sign("../secret.txt", "k", 60)
            assert (await client.get(f"http://127.0.0.1:{unused_tcp_port}/f/{evil}/s")).status_code == 404
            expired = sign("job1/video file.mp4", "k", -5)
            assert (await client.get(f"http://127.0.0.1:{unused_tcp_port}/f/{expired}/x")).status_code == 403
            assert (await client.get(f"http://127.0.0.1:{unused_tcp_port}/healthz")).text == "ok"
    finally:
        await server.stop()


def test_choose_mode_matrix():
    lim = 50 * MB
    assert choose_mode(10 * MB, lim, "auto", links=True, s3=True) == "telegram"
    assert choose_mode(10 * MB, lim, "link", links=True, s3=False) == "link"  # user prefers links
    assert choose_mode(10 * MB, lim, "link", links=False, s3=False) == "telegram"  # links not configured
    assert choose_mode(90 * MB, lim, "auto", links=False, s3=False) == "split"
    assert choose_mode(90 * MB, lim, "auto", links=True, s3=False) == "link"
    assert choose_mode(90 * MB, lim, "auto", links=True, s3=True) == "s3"
    assert choose_mode(90 * MB, lim, "split", links=True, s3=True) == "split"
    assert choose_mode(90 * MB, lim, "telegram", links=True, s3=False) == "link"  # can't fit; best fallback
    assert choose_mode(90 * MB, lim, "s3", links=True, s3=False) == "link"


def test_make_zip(tmp_path):
    files = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        p.write_bytes(bytes([i]) * 100)
        files.append(p)
    import zipfile

    z = make_zip(files, tmp_path / "out.zip")
    with zipfile.ZipFile(z) as zf:
        assert sorted(zf.namelist()) == ["0.png", "1.png", "2.png"]
        assert zf.testzip() is None
