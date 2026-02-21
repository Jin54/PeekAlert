"""
PeekAlert 아이콘 생성 스크립트
build.bat 실행 전에 먼저 이 스크립트로 icon.ico 를 생성합니다.
"""
from PIL import Image, ImageDraw

def make_icon():
    sizes = [16, 32,48, 64, 128, 256]
    images = []
    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad = size // 16
        # 배경 원
        d.ellipse([pad, pad, size-pad, size-pad], fill="#5865f2")
        # 눈 모양 (타원 2개)
        eye_w = size * 0.55
        eye_h = size * 0.32
        cx, cy = size / 2, size / 2
        d.ellipse([cx - eye_w/2, cy - eye_h/2,
                   cx + eye_w/2, cy + eye_h/2], fill="white")
        # 동공
        pu = size * 0.12
        d.ellipse([cx - pu, cy - pu, cx + pu, cy + pu], fill="#5865f2")
        images.append(img)

    images[0].save(
        "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:]
    )
    print("[OK] icon.ico 생성 완료!")

if __name__ == "__main__":
    make_icon()
