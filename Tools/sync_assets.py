import os
import sys
import json
import shutil
import hashlib
import subprocess
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 동시 처리 스레드 수 (구글 드라이브 I/O 병렬화)
MAX_WORKERS = 8

# ==========================================
# 환경 설정
# ==========================================
GDRIVE_SHARED_DIR = "G:/내 드라이브/Unreal7th_PaidAssets"

# Setup이 생성한 config.json 로드 (UTF-8 → cp949 fallback: 구버전 bat이 만든 파일 호환)
CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "config.json")
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

LOCAL_PAID_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../Content/PaidAssets"))
MANIFEST_NAME = "UpdatedFileList.json"
NOTES_NAME = "UpdatedNotes.txt"

# 마스터(업로더)의 고속 스캔을 위한 로컬 전용 캐시 파일
UPLOADER_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".uploader_cache.json")


def calculate_md5(file_path):
    """파일의 실제 내용물을 기반으로 변하지 않는 고유 MD5 해시를 계산합니다."""
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
    """업로더 PC의 파일 실시간 수정시간/크기 맵을 만듭니다."""
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
    """두 지문을 비교하여 파일 크기가 같고 시차가 2초 이내면 동일 파일로 판정합니다."""
    try:
        size1, mtime1 = fp1.split('_')
        size2, mtime2 = fp2.split('_')
        if size1 != size2:
            return False
        return abs(float(mtime1) - float(mtime2)) <= 2.0
    except Exception:
        return False


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
    files_to_upload = []
    updated_remote_files = {}

    # 1단계: 캐시 일치 여부로 빠르게 분류 (해시 재계산 회피)
    files_to_hash = []
    for rel_path, meta_val in current_meta_map.items():
        cache_hit = (
            rel_path in uploader_cache
            and is_fingerprint_equal(uploader_cache[rel_path], meta_val)
            and rel_path in remote_files
        )
        if cache_hit:
            updated_remote_files[rel_path] = remote_files[rel_path]
        else:
            files_to_hash.append(rel_path)

    # 2단계: 변경 의심 파일들의 MD5를 병렬로 계산
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
                    print(f"[*] 실제 변경 확인 (업로드 대기): {rel_path}")
                    files_to_upload.append(rel_path)
                else:
                    print(f"[*] 메타데이터 변경되었으나 내용물이 일치함 (업로드 패스): {rel_path}")
                updated_remote_files[rel_path] = md5_val

    files_to_delete = [p for p in remote_files if p not in current_meta_map]

    if not files_to_upload and not files_to_delete:
        print("[*] 변경된 에셋이 없습니다. 업로드를 종료합니다.")
        return

    print(f"[*] 최종 변경사항 반영 -> 실제 업로드: {len(files_to_upload)}개, 삭제: {len(files_to_delete)}개")

    print("\n" + "="*50)
    print(" 이번 업로드의 변경 사항(릴리즈 노트)을 입력하세요.")
    print("="*50)
    note_input = input(">> 입력: ").strip()
    if not note_input:
        note_input = "정기 에셋 업데이트"

    # 차분 파일 업로드 (병렬)
    def _upload_one(rel_path):
        src = os.path.join(LOCAL_PAID_DIR, rel_path)
        dst = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return rel_path

    if files_to_upload:
        total = len(files_to_upload)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(_upload_one, rp): rp for rp in files_to_upload}
            for i, fut in enumerate(as_completed(futures), 1):
                rp = futures[fut]
                try:
                    fut.result()
                    print(f" -> [{i}/{total}] 업로드 완료: {rp}")
                except Exception as e:
                    print(f" [-] [{i}/{total}] 업로드 실패 ({rp}): {e}")

    # 구글 드라이브 삭제 파일 청소
    for rel_path in files_to_delete:
        dst = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        if os.path.exists(dst):
            os.remove(dst)
            print(f" -> [드라이브 파일 삭제] {rel_path}")

    # 버전 업데이트
    new_version = remote_manifest.get("version", 0) + 1
    new_manifest = {
        "version": new_version,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": updated_remote_files
    }
    
    save_json(gdrive_manifest_path, new_manifest)
    save_json(local_manifest_path, new_manifest)
    save_json(UPLOADER_CACHE_PATH, current_meta_map)

    # 내림차순 패치노트 생성
    local_notes_path = os.path.join(LOCAL_PAID_DIR, NOTES_NAME)
    gdrive_notes_path = os.path.join(GDRIVE_SHARED_DIR, NOTES_NAME)
    
    log_entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Version {new_version}]\n"
    log_entry += f" [변경 사항] {note_input}\n"
    if files_to_upload:
        log_entry += " [추가/수정된 파일 목록]\n"
        for f in files_to_upload: log_entry += f"   └── {f}\n"
    if files_to_delete:
        log_entry += " [삭제된 파일 목록]\n"
        for f in files_to_delete: log_entry += f"   └── {f}\n"
    log_entry += "="*50 + "\n\n"
    
    for path in [local_notes_path, gdrive_notes_path]:
        existing_content = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f: existing_content = f.read()
            except Exception: pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(log_entry + existing_content)

    print(f"\n[+] 성공: 버전 {new_version} 업로드 완료!")


def run_download():
    print(f"[+] 구글 드라이브 타겟 경로: {GDRIVE_SHARED_DIR}")
    print("[+] 유료 에셋 다운로드 및 동기화를 시작합니다. (팀원 권한)")
    
    gdrive_manifest_path = os.path.join(GDRIVE_SHARED_DIR, MANIFEST_NAME)
    local_manifest_path = os.path.join(LOCAL_PAID_DIR, MANIFEST_NAME)
    
    if not os.path.exists(gdrive_manifest_path):
        print("[-] 에러: 구글 드라이브에 매니페스트 파일이 존재하지 않습니다.\n    마스터의 최초 업로드가 필요합니다.")
        return

    remote_manifest = load_json(gdrive_manifest_path)
    remote_files = remote_manifest.get("files", {})
    local_manifest = load_json(local_manifest_path)
    local_files = local_manifest.get("files", {})

    missing_physical_files = []
    for rel_path in remote_files:
        if not os.path.exists(os.path.join(LOCAL_PAID_DIR, rel_path)):
            missing_physical_files.append(rel_path)

    if remote_manifest.get("version") == local_manifest.get("version") and not missing_physical_files:
        print(f"[*] 이미 최신 버전 상태입니다. (현재 버전: Ver {local_manifest.get('version')})")
        return

    files_to_download = []
    for rel_path, remote_md5 in remote_files.items():
        if rel_path not in local_files or local_files[rel_path] != remote_md5 or rel_path in missing_physical_files:
            files_to_download.append(rel_path)

    files_to_delete = [p for p in local_files if p not in remote_files]

    print(f"[*] 동기화 진행 -> 다운로드 필요: {len(files_to_download)}개, 로컬 파일 정리: {len(files_to_delete)}개")

    # 차분 다운로드 (병렬)
    def _download_one(rel_path):
        src = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        dst = os.path.join(LOCAL_PAID_DIR, rel_path)
        if not os.path.exists(src):
            return rel_path, False
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return rel_path, True

    if files_to_download:
        total = len(files_to_download)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(_download_one, rp): rp for rp in files_to_download}
            for i, fut in enumerate(as_completed(futures), 1):
                rp = futures[fut]
                try:
                    _, ok = fut.result()
                    if ok:
                        print(f" -> [{i}/{total}] 다운로드 완료: {rp}")
                    else:
                        print(f" [-] [{i}/{total}] 대기: 클라우드 동기화 중 - {rp}")
                except Exception as e:
                    print(f" [-] [{i}/{total}] 다운로드 실패 ({rp}): {e}")

    # 고립 파일 제거
    for rel_path in files_to_delete:
        dst = os.path.join(LOCAL_PAID_DIR, rel_path)
        if os.path.exists(dst):
            os.remove(dst)
            print(f" -> [로컬 고립 파일 제거] {rel_path}")

    # 매니페스트 최종 동기화
    save_json(local_manifest_path, remote_manifest)
    gdrive_notes_path = os.path.join(GDRIVE_SHARED_DIR, NOTES_NAME)
    if os.path.exists(gdrive_notes_path):
        shutil.copy2(gdrive_notes_path, os.path.join(LOCAL_PAID_DIR, NOTES_NAME))

    print(f"\n[+] 동기화 완료: Ver {remote_manifest.get('version')} 상태로 최신화되었습니다.")


def run_setup():
    """초기 설정: 구글 드라이브 경로를 받아 config.json에 UTF-8로 저장하고 git clean 가드 등록."""
    print("=" * 55)
    print("    유료 에셋 동기화 시스템 초기 설정")
    print("=" * 55)
    print()
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
    print(f"    설정된 경로: {user_input}")
    print("    이제 UploadAssets.bat 또는 DownloadAssets.bat을 사용하세요.")
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