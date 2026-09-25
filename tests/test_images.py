from bot.services.images import ImageService, extract_images_from_html, page_title
from tests.conftest import SITE_HTML


def test_extract_images_from_html_finds_every_source():
    found = extract_images_from_html(SITE_HTML, "https://site.test/gallery/")
    by_url = {f.url: f for f in found}
    urls = set(by_url)
    assert "https://site.test/img/big.png" in urls  # largest srcset candidate
    assert by_url["https://site.test/img/big.png"].width == 800
    assert "https://site.test/img/lazy.png" in urls  # data-src beats the data: placeholder
    assert "https://site.test/img/pic.png" in urls  # <picture><source>
    assert "https://site.test/img/og.png" in urls and by_url["https://site.test/img/og.png"].source == "meta"
    assert by_url["https://site.test/img/icon.png"].source == "icon"
    assert "https://site.test/img/ld.png" in urls  # JSON-LD
    assert "https://site.test/img/bg.png" in urls  # <style> url()
    assert "https://site.test/img/inline.png" in urls  # style attribute
    assert "https://site.test/img/linked.jpg" in urls  # <a href> to an image
    assert not any(u.startswith(("javascript:", "data:")) for u in urls)
    assert page_title(SITE_HTML) == "Fixture gallery"


def test_base_href_is_respected():
    html = '<html><head><base href="https://cdn.test/assets/"></head><body><img src="a.png"></body></html>'
    assert [f.url for f in extract_images_from_html(html, "https://site.test/page")] == [
        "https://cdn.test/assets/a.png"
    ]


async def test_find_and_download_from_live_page(settings, web, tmp_path):
    svc = ImageService(settings)
    found, title = await svc.find(f"{web}/gallery.html")
    assert title == "Fixture gallery" and len(found) >= 10
    saved = await svc.download(found, tmp_path / "out", referer=f"{web}/gallery.html")
    names = " ".join(s.url for s in saved)
    # The hotlink-protected image only works because the page is sent as Referer.
    assert "/hotlink/protected.png" in names
    assert all(s.width > 0 and s.path.exists() for s in saved)
    big = await svc.download(found, tmp_path / "big", referer=f"{web}/gallery.html", min_width=500)
    assert big and all(s.width >= 500 for s in big)
    limited = await svc.download(found, tmp_path / "lim", referer=f"{web}/gallery.html", limit=3)
    assert len(limited) == 3


async def test_direct_image_link(settings, web):
    found, _ = await ImageService(settings).find(f"{web}/img/big.png")
    assert [f.source for f in found] == ["direct"]
