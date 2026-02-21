"""
PeekAlert 빌드 스크립트
실행: python build.py
결과: dist/PeekAlert.exe
"""
import subprocess, sys, os

def run(cmd, desc):
    print(f"\n[...] {desc}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"[실패] {desc}")
        sys.exit(1)
    print(f"[OK] {desc}")

print("=" * 50)
print("  PeekAlert - EXE 빌드")
print("=" * 50)

# 1. PyInstaller 설치
run("pip install pyinstaller pystray Pillow -q", "PyInstaller 설치")

# 2. 아이콘 생성
run("python make_icon.py", "아이콘 생성")

# 3. 이전 빌드 정리
for path in ["build", "PeekAlert.spec"]:
    if os.path.exists(path):
        subprocess.run(f'rmdir /s /q "{path}"' if os.path.isdir(path) else f'del /f "{path}"', shell=True)

# 4. PyInstaller 빌드
has_icon = os.path.exists("icon.ico")
icon_arg = "--icon icon.ico --add-data icon.ico;." if has_icon else ""

cmd = (
    "pyinstaller"
    " --onefile"
    " --noconsole"
    " --name PeekAlert"
    f" {icon_arg}"
    " --hidden-import pystray._win32"
    " --hidden-import PIL._tkinter_finder"
    " peekalert.py"
)
run(cmd, "EXE 빌드 (1~2분 소요)")

# 5. 결과
exe = os.path.join("dist", "PeekAlert.exe")
if os.path.exists(exe):
    size_mb = os.path.getsize(exe) / 1024 / 1024
    print()
    print("=" * 50)
    print("  빌드 성공!")
    print(f"  위치: dist\\PeekAlert.exe ({size_mb:.1f} MB)")
    print("=" * 50)
    print()
    print("  사용법:")
    print("  - dist\\PeekAlert.exe 를 원하는 폴더로 복사")
    print("  - 실행하면 트레이 아이콘 생성")
    print("  - 시작 프로그램 등록: Win+R → shell:startup")
    print("       → PeekAlert.exe 바로가기 붙여넣기")
    os.startfile("dist")
else:
    print("[오류] EXE 생성 실패")
    sys.exit(1)
