import os
import re
import sys
import json
import shutil
import hashlib
import zipfile
import subprocess
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_WORKERS = 8

# ==========================================
# 환경 설정
# ==========================================
GDRIVE_SHARED_DIR = "G:/내 드라이브/Unreal7th_PaidAssets"

CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DOWNLOAD_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "download_config.json")
UPLOADER_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".uploader_cache.json")
DOWNLOAD_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

LOCAL_PAID_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../Content/PaidAssets"))
MANIFEST_NAME = "UpdatedFileList.json"
NOTES_NAME = "UpdatedNotes.txt"
ZIP_NAME = "PaidAssets_latest.zip"

# config.json 로드 (UTF-8 → cp949 fallback)
if os.path.exists(CONFIG_FILE_PATH):
    config_data = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with open(CONFIG_FILE_PATH, "r", encoding=enc) as f:
                config_data = json.load(f)
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except Exception as e:
            print(f"[-] 설정 파일(config.json) 로드 실패: {e}")
            break
    if config_data and "gdrive_path" in config_data:
        GDRIVE_SHARED_DIR = config_data["gdrive_path"].strip()


def calculate_md5(file_path):
    """파일 내용 기반 MD5 해시."""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        print(f"[-] 해시 계산 실패 ({file_path}): {e}")
        return None


def get_local_mtime_size_map():
    """업로더 PC의 파일 실시간 수정시간/크기 맵."""
    meta_map = {}
    if not os.path.exists(LOCAL_PAID_DIR):
        os.makedirs(LOCAL_PAID_DIR, exist_ok=True)
        return meta_map

    for root, dirs, files in os.walk(LOCAL_PAID_DIR):
        for file in files:
            if file in [MANIFEST_NAME, NOTES_NAME]:
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, LOCAL_PAID_DIR).replace("\\", "/")
            try:
                size = os.path.getsize(full_path)
                mtime = os.path.getmtime(full_path)
                meta_map[rel_path] = f"{size}_{mtime}"
            except Exception:
                pass
    return meta_map


def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def is_fingerprint_equal(fp1, fp2):
    try:
        size1, mtime1 = fp1.split('_')
        size2, mtime2 = fp2.split('_')
        if size1 != size2:
            return False
        return abs(float(mtime1) - float(mtime2)) <= 2.0
    except Exception:
        return False


def is_unreal_running():
    """Windows tasklist로 UE 에디터 프로세스 감지."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq UnrealEditor.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if "UnrealEditor.exe" in result.stdout:
            return True
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq UE4Editor.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if "UE4Editor.exe" in result.stdout:
            return True
    except Exception:
        pass
    return False


def ensure_gdown():
    """gdown 임포트, 없으면 안내."""
    try:
        import gdown  # noqa: F401
        return True
    except ImportError:
        print("[-] gdown 패키지가 설치되어 있지 않습니다.")
        print("    Setup.bat을 먼저 실행하거나 직접 설치하세요: py -m pip install --upgrade gdown")
        return False


def extract_drive_file_id(url):
    """다양한 형태의 Google Drive 공유 URL에서 file ID만 추출."""
    patterns = [
        r"/file/d/([A-Za-z0-9_-]{20,})",
        r"[?&]id=([A-Za-z0-9_-]{20,})",
        r"/d/([A-Za-z0-9_-]{20,})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def build_zip(zip_path):
    """Content/PaidAssets 전체를 zip으로 묶기 (manifest/notes 포함, 무압축).

    GDrive의 공유 경로(.shortcut-targets-by-id/...)에서는 atomic rename이
    불안정해서, 로컬 임시 폴더에 zip을 만든 뒤 shutil.copy2로 전송한다.
    """
    import time

    local_tmp_dir = os.path.join(os.path.dirname(__file__), ".cache")
    os.makedirs(local_tmp_dir, exist_ok=True)
    local_zip = os.path.join(local_tmp_dir, os.path.basename(zip_path))

    if os.path.exists(local_zip):
        try:
            os.remove(local_zip)
        except Exception:
            pass

    file_list = []
    for root, dirs, files in os.walk(LOCAL_PAID_DIR):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, LOCAL_PAID_DIR).replace("\\", "/")
            file_list.append((full, rel))

    total = len(file_list)
    print(f"[*] zip 패키징 시작 ({total}개 파일, 무압축 모드, 로컬 빌드)...")
    with zipfile.ZipFile(local_zip, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for i, (full, rel) in enumerate(file_list, 1):
            zf.write(full, arcname=rel)
            if i % 50 == 0 or i == total:
                print(f"    [{i}/{total}] {rel}")

    local_size_mb = os.path.getsize(local_zip) / (1024 * 1024)
    print(f"[*] 로컬 zip 완료: {local_size_mb:.1f} MB → GDrive 경로로 전송 중...")

    try:
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)
        shutil.copy2(local_zip, zip_path)
    except Exception as e:
        print(f"[-] GDrive 경로 복사 실패: {e}")
        print(f"    로컬 zip은 남겨둡니다: {local_zip}")
        raise

    # GDrive Desktop이 즉시 가상화/숨김 처리하는 경우가 있어 짧게 재시도
    size_mb = None
    for _ in range(6):
        try:
            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            break
        except FileNotFoundError:
            time.sleep(0.5)

    # 로컬 임시 zip 정리
    try:
        os.remove(local_zip)
    except Exception:
        pass

    if size_mb is not None:
        print(f"[+] zip 전송 완료: {zip_path} ({size_mb:.1f} MB)")
    else:
        print(f"[+] zip 전송 완료(파일 크기 확인 불가, GDrive 동기화 상태 확인 권장): {zip_path}")


def run_upload():
    print(f"[+] 구글 드라이브 타겟 경로: {GDRIVE_SHARED_DIR}")
    print("[+] 유료 에셋 업로드 프로세스를 시작합니다. (Git 마스터 권한)")

    try:
        os.makedirs(GDRIVE_SHARED_DIR, exist_ok=True)
    except Exception:
        pass

    if not os.path.exists(GDRIVE_SHARED_DIR):
        print(f"[-] 에러: 구글 드라이브 경로를 찾을 수 없습니다: {GDRIVE_SHARED_DIR}")
        return

    current_meta_map = get_local_mtime_size_map()
    gdrive_manifest_path = os.path.join(GDRIVE_SHARED_DIR, MANIFEST_NAME)
    local_manifest_path = os.path.join(LOCAL_PAID_DIR, MANIFEST_NAME)

    remote_manifest = load_json(gdrive_manifest_path)
    if not remote_manifest:
        remote_manifest = {"version": 0, "files": {}}
    remote_files = remote_manifest.get("files", {})

    uploader_cache = load_json(UPLOADER_CACHE_PATH)
    files_changed = []
    updated_files = {}

    # 1단계: 캐시 일치 여부로 빠르게 분류
    files_to_hash = []
    for rel_path, meta_val in current_meta_map.items():
        cache_hit = (
            rel_path in uploader_cache
            and is_fingerprint_equal(uploader_cache[rel_path], meta_val)
            and rel_path in remote_files
        )
        if cache_hit:
            updated_files[rel_path] = remote_files[rel_path]
        else:
            files_to_hash.append(rel_path)

    # 2단계: 변경 의심 파일들 MD5 병렬 계산
    if files_to_hash:
        print(f"[*] 해시 검증 진행 중 ({len(files_to_hash)}개 파일, {MAX_WORKERS} threads)...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(calculate_md5, os.path.join(LOCAL_PAID_DIR, rp)): rp
                for rp in files_to_hash
            }
            for fut in as_completed(futures):
                rel_path = futures[fut]
                md5_val = fut.result()
                if not md5_val:
                    continue
                if rel_path not in remote_files or remote_files[rel_path] != md5_val:
                    print(f"[*] 실제 변경 확인: {rel_path}")
                    files_changed.append(rel_path)
                else:
                    print(f"[*] 메타데이터만 변경, 내용 동일 (스킵): {rel_path}")
                updated_files[rel_path] = md5_val

    files_deleted = [p for p in remote_files if p not in current_meta_map]
    gdrive_zip_path = os.path.join(GDRIVE_SHARED_DIR, ZIP_NAME)

    if not files_changed and not files_deleted:
        # 일반: 정말로 동기화 끝남
        if os.path.exists(gdrive_zip_path):
            print("[*] 변경된 에셋이 없습니다. 업로드를 종료합니다.")
            return
        # 복구: 매니페스트는 최신인데 GDrive zip이 누락 → 이전 업로드의 zip 단계 실패
        print("[!] 변경된 에셋은 없지만 GDrive에 PaidAssets_latest.zip이 없습니다.")
        print("    이전 업로드에서 zip 단계가 누락된 것 같습니다. 같은 매니페스트로 zip을 재빌드합니다.")
        try:
            build_zip(gdrive_zip_path)
            print(f"[+] zip 재빌드 완료 (Ver {remote_manifest.get('version', '?')} 유지)")
        except Exception as e:
            print(f"[-] zip 재빌드 실패: {e}")
        return

    print(f"[*] 변경 사항 -> 추가/수정: {len(files_changed)}개, 삭제: {len(files_deleted)}개")

    print("\n" + "=" * 50)
    print(" 이번 업로드의 변경 사항(릴리즈 노트)을 입력하세요.")
    print("=" * 50)
    note_input = input(">> 입력: ").strip()
    if not note_input:
        note_input = "정기 에셋 업데이트"

    # 변경된 개별 파일을 GDrive로 차분 업로드 (병렬) — 브라우징/백업 용도
    def _upload_one(rel_path):
        src = os.path.join(LOCAL_PAID_DIR, rel_path)
        dst = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return rel_path

    if files_changed:
        total = len(files_changed)
        print(f"[*] 개별 파일 GDrive 업로드 ({total}개, {MAX_WORKERS} threads)...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(_upload_one, rp): rp for rp in files_changed}
            for i, fut in enumerate(as_completed(futures), 1):
                rp = futures[fut]
                try:
                    fut.result()
                    print(f" -> [{i}/{total}] 업로드 완료: {rp}")
                except Exception as e:
                    print(f" [-] [{i}/{total}] 업로드 실패 ({rp}): {e}")

    # GDrive에서 로컬에 없어진 파일 청소
    for rel_path in files_deleted:
        dst = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        if os.path.exists(dst):
            try:
                os.remove(dst)
                print(f" -> [드라이브 파일 삭제] {rel_path}")
            except Exception as e:
                print(f" [-] 삭제 실패 ({rel_path}): {e}")

    new_version = remote_manifest.get("version", 0) + 1
    new_manifest = {
        "version": new_version,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": updated_files
    }

    # 매니페스트/노트를 로컬과 GDrive 양쪽 갱신 (zip에도 포함되어야 하므로 로컬 먼저)
    save_json(local_manifest_path, new_manifest)
    save_json(gdrive_manifest_path, new_manifest)
    save_json(UPLOADER_CACHE_PATH, current_meta_map)

    local_notes_path = os.path.join(LOCAL_PAID_DIR, NOTES_NAME)
    gdrive_notes_path = os.path.join(GDRIVE_SHARED_DIR, NOTES_NAME)

    log_entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Version {new_version}]\n"
    log_entry += f" [변경 사항] {note_input}\n"
    if files_changed:
        log_entry += " [추가/수정된 파일 목록]\n"
        for f in files_changed:
            log_entry += f"   └── {f}\n"
    if files_deleted:
        log_entry += " [삭제된 파일 목록]\n"
        for f in files_deleted:
            log_entry += f"   └── {f}\n"
    log_entry += "=" * 50 + "\n\n"

    for path in [local_notes_path, gdrive_notes_path]:
        existing_content = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing_content = f.read()
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(log_entry + existing_content)

    # zip 패키징 후 GDrive에 덮어쓰기 (Desktop이 새 리비전으로 동기화 → 공유 URL 유지)
    build_zip(gdrive_zip_path)

    print(f"\n[+] 성공: 버전 {new_version} 업로드 완료!")
    print()
    print("=" * 60)
    print(" [NEXT] 팀에 공유")
    print("=" * 60)
    print(" 1. GDrive Desktop이 zip을 클라우드로 업로드할 때까지 대기")
    print("    (drive.google.com에서 PaidAssets_latest.zip 동기화 확인)")
    print(" 2. 최초 1회만: zip 우클릭 → 공유 → '링크가 있는 모든 사용자: 뷰어'")
    print("                    → 링크 복사 → 팀에 공유")
    print(" 3. 이후 릴리즈는 같은 URL이 새 버전을 가리킵니다 (재공유 불필요)")
    print("=" * 60)


def run_download():
    print("[+] 유료 에셋 다운로드 (zip 방식)")

    if not ensure_gdown():
        return
    import gdown

    download_cfg = load_json(DOWNLOAD_CONFIG_PATH)
    saved_url = download_cfg.get("zip_url", "").strip()

    print()
    if saved_url:
        print(f"[저장된 URL] {saved_url}")
        print("엔터: 그대로 사용 / 새 URL 입력 시 갱신")
    else:
        print("[알림] 마스터로부터 받은 zip 공유 URL을 입력하세요.")
        print("예시: https://drive.google.com/file/d/XXXXXXXXX/view?usp=sharing")
    user_input = input(">> URL: ").strip().strip('"').strip("'")

    zip_url = user_input if user_input else saved_url
    if not zip_url:
        print("[-] URL이 비어 있습니다. 종료합니다.")
        return
    if "drive.google.com" not in zip_url:
        print("[-] 유효한 Google Drive URL이 아닙니다. 종료합니다.")
        return

    if zip_url != saved_url:
        save_json(DOWNLOAD_CONFIG_PATH, {"zip_url": zip_url})
        print(f"[+] URL 저장됨: {DOWNLOAD_CONFIG_PATH}")

    if is_unreal_running():
        print("[-] Unreal Editor가 실행 중입니다. 종료 후 다시 시도하세요.")
        return

    os.makedirs(DOWNLOAD_CACHE_DIR, exist_ok=True)
    zip_cache_path = os.path.join(DOWNLOAD_CACHE_DIR, ZIP_NAME)
    if os.path.exists(zip_cache_path):
        try:
            os.remove(zip_cache_path)
        except Exception:
            pass

    file_id = extract_drive_file_id(zip_url)
    if not file_id:
        print("[-] URL에서 파일 ID를 추출할 수 없습니다. 형식을 확인하세요.")
        print("    지원 형식 예: https://drive.google.com/file/d/<FILE_ID>/view?usp=sharing")
        return
    direct_url = f"https://drive.google.com/uc?id={file_id}"

    print(f"\n[*] zip 다운로드 시작... (file_id: {file_id})")
    try:
        result = gdown.download(direct_url, zip_cache_path, quiet=False)
        if not result or not os.path.exists(zip_cache_path):
            print("[-] 다운로드 실패. URL과 공유 설정('링크 있는 모든 사용자')을 확인하세요.")
            return
    except Exception as e:
        print(f"[-] 다운로드 중 오류: {e}")
        return

    # zip 유효성 검사
    try:
        with zipfile.ZipFile(zip_cache_path, 'r') as zf:
            bad = zf.testzip()
            if bad is not None:
                print(f"[-] 다운로드된 zip이 손상됨: {bad}")
                return
    except zipfile.BadZipFile:
        print("[-] 다운로드된 파일이 유효한 zip이 아닙니다. URL이 HTML 페이지를 가리키고 있을 수 있습니다.")
        return

    # zip 안의 매니페스트만 먼저 읽어 차분 적용 계획 수립
    try:
        with zipfile.ZipFile(zip_cache_path, 'r') as zf:
            zip_names = set(zf.namelist())
            if MANIFEST_NAME not in zip_names:
                print(f"[-] zip 내부에 {MANIFEST_NAME}이 없습니다. 마스터 업로드를 확인하세요.")
                return
            with zf.open(MANIFEST_NAME) as mf:
                remote_manifest = json.loads(mf.read().decode('utf-8'))
    except Exception as e:
        print(f"[-] 매니페스트 읽기 실패: {e}")
        return

    remote_files = remote_manifest.get("files", {})

    os.makedirs(LOCAL_PAID_DIR, exist_ok=True)
    local_manifest_path = os.path.join(LOCAL_PAID_DIR, MANIFEST_NAME)
    local_manifest = load_json(local_manifest_path)
    local_files = local_manifest.get("files", {})

    # 1) 분류: 매니페스트 신뢰로 빠른 스킵 vs 실제 MD5 검증 vs 신규 추출
    files_to_extract = []
    files_to_verify = []
    for rel_path, remote_md5 in remote_files.items():
        abs_path = os.path.join(LOCAL_PAID_DIR, rel_path)
        if not os.path.exists(abs_path):
            files_to_extract.append(rel_path)
            continue
        if local_files.get(rel_path) == remote_md5:
            continue  # 매니페스트 일치 → 스킵
        files_to_verify.append(rel_path)

    # 2) 의심 파일만 MD5 병렬 검증
    if files_to_verify:
        print(f"[*] 무결성 검증 중 ({len(files_to_verify)}개 파일, {MAX_WORKERS} threads)...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(calculate_md5, os.path.join(LOCAL_PAID_DIR, rp)): rp
                for rp in files_to_verify
            }
            for fut in as_completed(futures):
                rel_path = futures[fut]
                actual = fut.result()
                if actual != remote_files[rel_path]:
                    files_to_extract.append(rel_path)

    # 3) 매니페스트에 없는 로컬 파일 = 고립 파일
    files_to_delete = []
    for root, dirs, files in os.walk(LOCAL_PAID_DIR):
        for f in files:
            if f in [MANIFEST_NAME, NOTES_NAME]:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, LOCAL_PAID_DIR).replace("\\", "/")
            if rel not in remote_files:
                files_to_delete.append(rel)

    if not files_to_extract and not files_to_delete:
        print("[*] 이미 최신 상태입니다 (변경된 파일 없음).")
    else:
        print(f"[*] 적용 -> 추가/덮어쓰기: {len(files_to_extract)}개, 삭제: {len(files_to_delete)}개")

    # 4) 변경된 파일만 zip에서 개별 추출
    if files_to_extract:
        with zipfile.ZipFile(zip_cache_path, 'r') as zf:
            for i, rel_path in enumerate(files_to_extract, 1):
                if rel_path not in zip_names:
                    print(f" [-] zip에 없음 (스킵): {rel_path}")
                    continue
                try:
                    zf.extract(rel_path, LOCAL_PAID_DIR)
                    print(f" -> [{i}/{len(files_to_extract)}] 추출: {rel_path}")
                except Exception as e:
                    print(f" [-] 추출 실패 ({rel_path}): {e}")

    # 5) 고립 파일 삭제
    for rel_path in files_to_delete:
        full = os.path.join(LOCAL_PAID_DIR, rel_path)
        try:
            os.remove(full)
            print(f" -> [삭제] {rel_path}")
        except Exception as e:
            print(f" [-] 삭제 실패 ({rel_path}): {e}")

    # 6) 빈 디렉터리 정리
    for root, dirs, files in os.walk(LOCAL_PAID_DIR, topdown=False):
        for d in dirs:
            full = os.path.join(root, d)
            try:
                if not os.listdir(full):
                    os.rmdir(full)
            except Exception:
                pass

    # 7) 매니페스트/노트 동기화 (항상 zip 기준으로 최신화)
    try:
        with zipfile.ZipFile(zip_cache_path, 'r') as zf:
            zf.extract(MANIFEST_NAME, LOCAL_PAID_DIR)
            if NOTES_NAME in zip_names:
                zf.extract(NOTES_NAME, LOCAL_PAID_DIR)
    except Exception as e:
        print(f"[-] 매니페스트/노트 갱신 실패: {e}")

    try:
        os.remove(zip_cache_path)
    except Exception:
        pass

    version = remote_manifest.get("version", "?")
    file_count = len(remote_files)
    print(f"\n[+] 동기화 완료: Ver {version} ({file_count}개 파일)")


def run_setup():
    """초기 설정: 역할 선택, gdown 설치, GDrive 경로(마스터), git clean 가드."""
    print("=" * 55)
    print("    유료 에셋 동기화 시스템 초기 설정")
    print("=" * 55)
    print()
    print("역할을 선택하세요:")
    print("  1) 마스터 (Git 관리자, 업로드 권한)")
    print("  2) 다운로더 (팀원, 다운로드만)")
    role = input(">> 입력 (1/2): ").strip()

    is_master = role == "1"

    if is_master:
        print()
        print("-" * 55)
        print("본인의 PC에 마운트된 구글 드라이브 공유 폴더 경로를 입력하세요.")
        print("(윈도우 탐색기에서 복사한 경로를 그대로 붙여넣으셔도 됩니다.)")
        print()
        default_path = r"G:\내 드라이브\Unreal7th_PaidAssets"
        print(f"기본값 (엔터 클릭 시): {default_path}")
        print("-" * 55)

        user_input = input(">> 경로 입력: ").strip().strip('"').strip("'")
        if not user_input:
            user_input = default_path

        config = {"gdrive_path": user_input}
        try:
            with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            print(f"\n[+] config.json 저장 완료: {CONFIG_FILE_PATH}")
        except Exception as e:
            print(f"[-] config.json 저장 실패: {e}")
            return
    else:
        print("\n[*] 다운로더 모드: GDrive Desktop 경로 입력은 건너뜁니다.")
        print("    (DownloadAssets.bat 최초 실행 시 마스터에게 받은 URL을 입력하세요)")

    print()
    print("-" * 55)
    print("[Dependency] gdown 패키지 설치 중...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "gdown"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            print("[Dependency] gdown 설치/업데이트 완료!")
        else:
            print(f"[Dependency] gdown 설치 실패: {result.stderr.strip()}")
            print("    수동 설치: py -m pip install --upgrade gdown")
    except Exception as e:
        print(f"[Dependency] gdown 설치 중 오류: {e}")
    print("-" * 55)

    print()
    print("-" * 55)
    print("[Git Guard] git clean 시 유료 에셋 폴더 삭제 방지 가드를 등록합니다...")
    try:
        result = subprocess.run(
            ["git", "config", "clean.exclude", "Content/PaidAssets"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            print("[Git Guard] 가드 등록 완료!")
        else:
            print(f"[Git Guard] 가드 등록 실패: {result.stderr.strip()}")
    except FileNotFoundError:
        print("[Git Guard] git 명령을 찾을 수 없습니다. Git 설치 여부를 확인하세요.")
    except Exception as e:
        print(f"[Git Guard] 가드 등록 실패: {e}")
    print("-" * 55)

    print()
    print("=" * 55)
    print("[+] 설정이 성공적으로 완료되었습니다!")
    if is_master:
        print("    이제 UploadAssets.bat으로 업로드하세요.")
    else:
        print("    이제 DownloadAssets.bat으로 마스터에게 받은 URL을 입력하세요.")
    print("=" * 55)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    if sys.argv[1] == "--upload":
        run_upload()
    elif sys.argv[1] == "--download":
        run_download()
    elif sys.argv[1] == "--setup":
        run_setup()
