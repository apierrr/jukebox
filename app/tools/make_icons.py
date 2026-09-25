"""Génère les icônes PWA (PNG) sans dépendance : dégradé + triangle « lecture »."""
import struct, sys, zlib
from pathlib import Path


def png(width, height, rows):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render(size):
    ss = 2  # sur-échantillonnage pour l'anticrénelage
    n = size * ss
    # triangle de lecture centré, légèrement décalé à droite
    ax, ay = n * 0.36, n * 0.27
    bx, by = n * 0.36, n * 0.73
    cx, cy = n * 0.76, n * 0.50

    def inside(px, py):
        d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
        d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
        d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
        neg = d1 < 0 or d2 < 0 or d3 < 0
        pos = d1 > 0 or d2 > 0 or d3 > 0
        return not (neg and pos)

    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            t = (x + y) / (2 * size)  # dégradé diagonal violet -> rose
            bg = (int(96 + (255 - 96) * t), int(74 + (92 - 74) * t), int(255 + (138 - 255) * t))
            cov = sum(inside(x * ss + i + 0.5, y * ss + j + 0.5) for i in range(ss) for j in range(ss)) / (ss * ss)
            r = int(bg[0] + (255 - bg[0]) * cov)
            g = int(bg[1] + (255 - bg[1]) * cov)
            b = int(bg[2] + (255 - bg[2]) * cov)
            row += bytes((r, g, b, 255))
        rows.append(row)
    return png(size, size, rows)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "icons")
    out.mkdir(parents=True, exist_ok=True)
    for s in (192, 512):
        (out / f"icon-{s}.png").write_bytes(render(s))
    print("icônes écrites dans", out)
