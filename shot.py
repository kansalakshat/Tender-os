import sys
import pathlib

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8000"

SHOTS = [
    ("hero", "/", 1440, 900, "light", 0),
    ("hero-dark", "/", 1440, 900, "dark", 0),
    ("mid", "/", 1440, 900, "light", 900),
    ("footer", "/", 1440, 900, "light", 2600),
    ("hero-375", "/", 375, 780, "light", 0),
    ("browse", "/browse", 1440, 900, "light", 0),
    ("browse-375", "/browse", 375, 780, "light", 0),
    ("login-dark", "/login", 1440, 900, "dark", 0),
]

with sync_playwright() as p:
    b = p.chromium.launch()
    for name, path, w, h, scheme, scroll in SHOTS:
        ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
        pg = ctx.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + path, wait_until="networkidle")
        pg.wait_for_timeout(1500)
        if scroll:
            pg.mouse.wheel(0, scroll)
            pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / f"{name}.png"))
        info = pg.evaluate("""() => ({
            over: document.documentElement.scrollWidth > window.innerWidth + 1,
            font: document.fonts.check('16px Geist'),
            gsap: !!window.gsap
        })""")
        print(f"{name:12s} {info} errors={errs or 'none'}")
        ctx.close()
    b.close()
