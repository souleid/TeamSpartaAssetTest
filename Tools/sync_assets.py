import os
import sys
import json
import shutil
from datetime import datetime

# ==========================================
# 환경 설정 (팀의 구글 드라이브 공유 폴더 경로에 맞게 수정 필수!)
# ==========================================
GDRIVE_SHARED_DIR = "G:\내 드라이브\PaidAssets"

LOCAL_PAID_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../Content/PaidAssets"))
MANIFEST_NAME = "UpdatedFileList.json"
NOTES_NAME = "UpdatedNotes.txt"

def get_asset_fingerprint(file_path):
    """[성능 최적화] 파일 크기와 수정 시간을 조합해 0초 만에 고유 지문(Fingerprint)을 만듭니다."""
    try:
        size = os.path.getsize(file_path)
        mtime = os.path.getmtime(file_path)
        return f"{size}_{mtime}"
    except Exception as e:
        print(f"[-] 파일 메타데이터 조회 실패 ({file_path}): {e}")
        return None

def scan_local_assets():
    """로컬 PaidAssets 폴더를 스캔하여 에셋들의 고유 지문 맵을 생성합니다."""
    asset_map = {}
    if not os.path.exists(LOCAL_PAID_DIR):
        os.makedirs(LOCAL_PAID_DIR, exist_ok=True)
        return asset_map

    for root, dirs, files in os.walk(LOCAL_PAID_DIR):
        for file in files:
            if file in [MANIFEST_NAME, NOTES_NAME]:
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, LOCAL_PAID_DIR).replace("\\", "/")
            
            fingerprint = get_asset_fingerprint(full_path)
            if fingerprint:
                asset_map[rel_path] = fingerprint
    return asset_map

def load_json_manifest(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": 0, "files": {}}

def save_json_manifest(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def run_upload():
    print("[+] 유료 에셋 업로드 프로세스를 시작합니다. (Git 마스터 권한)")
    if not os.path.exists(GDRIVE_SHARED_DIR):
        print(f"[-] 에러: 구글 드라이브 경로를 찾을 수 없습니다.\n    경로를 확인하세요: {GDRIVE_SHARED_DIR}")
        return

    current_local_files = scan_local_assets()
    local_manifest_path = os.path.join(LOCAL_PAID_DIR, MANIFEST_NAME)
    gdrive_manifest_path = os.path.join(GDRIVE_SHARED_DIR, MANIFEST_NAME)
    
    old_manifest = load_json_manifest(local_manifest_path)
    old_files = old_manifest.get("files", {})

    files_to_upload = []
    for rel_path, current_fingerprint in current_local_files.items():
        if rel_path not in old_files or old_files[rel_path] != current_fingerprint:
            files_to_upload.append(rel_path)

    files_to_delete = [p for p in old_files if p not in current_local_files]

    if not files_to_upload and not files_to_delete:
        print("[*] 변경된 에셋이 없습니다. 업로드를 종료합니다.")
        return

    print(f"[*] 감지된 변경사항 -> 업로드 대상: {len(files_to_upload)}개, 삭제 대상: {len(files_to_delete)}개")

    print("\n" + "="*50)
    print(" 이번 업로드의 변경 사항(릴리즈 노트)을 입력하세요.")
    print("="*50)
    note_input = input(">> 입력: ").strip()
    if not note_input:
        note_input = "정기 에셋 업데이트"

    # 차분 업로드 진행
    for rel_path in files_to_upload:
        src = os.path.join(LOCAL_PAID_DIR, rel_path)
        dst = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print(f" -> [업로드 완료] {rel_path}")

    # 드라이브 청소 (로컬에서 지워진 파일 반영)
    for rel_path in files_to_delete:
        dst = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        if os.path.exists(dst):
            os.remove(dst)
            print(f" -> [드라이브 파일 삭제] {rel_path}")

    # 매니페스트 업데이트 및 저장
    new_version = old_manifest.get("version", 0) + 1
    new_manifest = {
        "version": new_version,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": current_local_files
    }
    
    save_json_manifest(local_manifest_path, new_manifest)
    save_json_manifest(gdrive_manifest_path, new_manifest)

    # 릴리즈 노트 누적 기록 처리
    local_notes_path = os.path.join(LOCAL_PAID_DIR, NOTES_NAME)
    gdrive_notes_path = os.path.join(GDRIVE_SHARED_DIR, NOTES_NAME)
    log_entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Version {new_version}]\n - {note_input}\n\n"
    
    for path in [local_notes_path, gdrive_notes_path]:
        with open(path, "a", encoding="utf-8") as f:
            f.write(log_entry)

    print(f"\n[+] 성공: 버전 {new_version} 업로드 완료!")

def run_download():
    print("[+] 유료 에셋 다운로드 및 동기화를 시작합니다. (팀원 권한)")
    gdrive_manifest_path = os.path.join(GDRIVE_SHARED_DIR, MANIFEST_NAME)
    
    if not os.path.exists(gdrive_manifest_path):
        print("[-] 에러: 구글 드라이브에 매니페스트가 없습니다. 마스터의 최초 업로드가 필요합니다.")
        return

    remote_manifest = load_json_manifest(gdrive_manifest_path)
    remote_files = remote_manifest.get("files", {})
    current_local_files = scan_local_assets()
    
    files_to_download = []
    for rel_path, remote_fingerprint in remote_files.items():
        if rel_path not in current_local_files or current_local_files[rel_path] != remote_fingerprint:
            files_to_download.append(rel_path)

    files_to_delete = [p for p in current_local_files if p not in remote_files]

    if not files_to_download and not files_to_delete:
        print(f"[*] 이미 최신 버전 상태입니다. (최신 버전: Ver {remote_manifest.get('version')})")
        return

    print(f"[*] 동기화 필요 -> 다운로드: {len(files_to_download)}개, 로컬 파일 정리: {len(files_to_delete)}개")

    # 차분 다운로드 진행
    for rel_path in files_to_download:
        src = os.path.join(GDRIVE_SHARED_DIR, rel_path)
        dst = os.path.join(LOCAL_PAID_DIR, rel_path)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            print(f" -> [다운로드 완료] {rel_path}")

    # 고립된 로컬 파일 삭제 (클린 싱크)
    for rel_path in files_to_delete:
        dst = os.path.join(LOCAL_PAID_DIR, rel_path)
        if os.path.exists(dst):
            os.remove(dst)
            print(f" -> [로컬 고립 파일 제거] {rel_path}")

    # 매니페스트 및 릴리즈 노트 최종 동기화
    shutil.copy2(gdrive_manifest_path, os.path.join(LOCAL_PAID_DIR, MANIFEST_NAME))
    gdrive_notes_path = os.path.join(GDRIVE_SHARED_DIR, NOTES_NAME)
    if os.path.exists(gdrive_notes_path):
        shutil.copy2(gdrive_notes_path, os.path.join(LOCAL_PAID_DIR, NOTES_NAME))

    print(f"\n[+] 동기화 완료: Ver {remote_manifest.get('version')} 상태로 최신화되었습니다.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sync_assets.py [--upload | --download]")
        sys.exit(1)
        
    mode = sys.argv[1]
    if mode == "--upload":
        run_upload()
    elif mode == "--download":
        run_download()