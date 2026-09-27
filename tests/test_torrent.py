"""Torrent support: .torrent parsing, file choice, aria2 progress, magnet detection and access (no network)."""

import hashlib

import pytest

from bot.config import Settings
from bot.handlers.torrent import torrent_source
from bot.services.torrent import TorrentError, bdecode, choose, parse_readout, parse_torrent


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b"e"


INFO = {
    "name": "Open Movies",
    "piece length": 262144,
    "pieces": b"\x00" * 20,
    "files": [
        {"length": 2_000, "path": ["readme.txt"]},
        {"length": 700_000_000, "path": ["Big Buck Bunny 1080p.mkv"]},
        {"length": 40_000_000, "path": ["Sample", "sample.mkv"]},
        {"length": 350_000_000, "path": ["extras", "Sintel.mp4"]},
        {"length": 90_000, "path": ["cover.jpg"]},
    ],
}


@pytest.fixture
def torrent_file(tmp_path):
    path = tmp_path / "movies.torrent"
    path.write_bytes(bencode({"announce": "udp://tracker.test:1337", "info": INFO}))
    return path


def test_parse_multi_file_torrent(torrent_file):
    info = parse_torrent(torrent_file)
    assert info.name == "Open Movies"
    assert info.info_hash == hashlib.sha1(bencode(INFO)).hexdigest()  # same hash as every BitTorrent client
    assert [f.index for f in info.files] == [1, 2, 3, 4, 5]  # aria2 --select-file numbering
    assert info.files[1].path == "Open Movies/Big Buck Bunny 1080p.mkv"
    assert [f.index for f in info.videos] == [2, 4]  # the "sample" clip and non-videos are left out


def test_choose_files(torrent_file):
    info = parse_torrent(torrent_file)
    assert [f.index for f in choose(info)] == [2]  # largest video
    assert [f.index for f in choose(info, "all")] == [2, 4]
    assert [f.index for f in choose(info, "4")] == [4]
    with pytest.raises(TorrentError):
        choose(info, "1")  # a text file
    with pytest.raises(TorrentError):
        choose(info, "99")


def test_single_file_and_broken_torrents(tmp_path):
    single = tmp_path / "one.torrent"
    single.write_bytes(bencode({"info": {"name": "clip.webm", "length": 5_000_000, "piece length": 1, "pieces": b""}}))
    info = parse_torrent(single)
    assert [(f.index, f.path) for f in info.files] == [(1, "clip.webm")]
    bad = tmp_path / "bad.torrent"
    bad.write_bytes(b"<html>not a torrent</html>")
    with pytest.raises(TorrentError):
        parse_torrent(bad)
    with pytest.raises(ValueError):
        bdecode(b"5:ab")  # string runs past the end


def test_aria2_progress_lines():
    line = "[#2089b0 36MiB/61MiB(58%) CN:12 SD:3 DL:3.8MiB ETA:6s]"
    stats = parse_readout(line)
    assert stats["downloaded"] == 36 * 1024**2 and stats["total"] == 61 * 1024**2
    assert stats["speed"] == int(3.8 * 1024**2) and stats["eta"] == 6.0
    assert parse_readout("[#a1 1.2GiB/4.0GiB(30%) CN:40 DL:12MiB ETA:3m54s]")["eta"] == 234.0
    assert parse_readout("[#a1 0B/0B CN:0 SD:0 DL:0B]") is None  # still fetching metadata: no percentage yet
    assert parse_readout("09/27 08:40:01 [NOTICE] Download complete") is None


def test_torrent_sources_in_messages():
    magnet = "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel"
    assert torrent_source(f"watch this {magnet} tonight") == magnet
    assert (
        torrent_source("https://archive.org/download/x/x_archive.torrent")
        == "https://archive.org/download/x/x_archive.torrent"
    )
    assert torrent_source("https://example.com/video.mp4") is None


def test_torrent_access_setting(tmp_path):
    def s(mode):
        return Settings(bot_token="1:x", data_dir=tmp_path, torrents=mode)

    assert s("admins").torrents_allowed(True) and not s("admins").torrents_allowed(False)
    assert s("all").torrents_allowed(False)
    assert not s("off").torrents_allowed(True)
    assert s("nonsense").torrents == "admins"
