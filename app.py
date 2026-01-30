import streamlit as st
import os
import subprocess
import google.generativeai as genai
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import mimetypes
import tempfile

st.set_page_config(page_title="Gemini AI Auto Tagger", layout="wide")

# --- 関数定義 ---

def get_tags_from_gemini(api_key, file_path, model_name="gemini-2.5-flash-lite"):
    """Gemini APIを使用してタグを生成する"""
    try:
        genai.configure(api_key=api_key)
        
        mime_type, _ = mimetypes.guess_type(file_path)
        sample_file = genai.upload_file(file_path, mime_type=mime_type)
        
        while sample_file.state.name == "PROCESSING":
            time.sleep(1)
            sample_file = genai.get_file(sample_file.name)

        if sample_file.state.name == "FAILED":
            return None, "Upload processing failed"

        model = genai.GenerativeModel(model_name)
        
        prompt = """
        このファイルの内容を表す検索用キーワードを5個〜10個生成してください。
        日本語の単語のみをカンマ区切りで並べてください。
        （例: イラスト, 青空, 猫, 水彩画, 笑顔）
        """
        
        response = model.generate_content([prompt, sample_file])
        return response.text.strip(), None
        
    except Exception as e:
        return None, str(e)

def write_tags_securely(file_path, tags):
    """
    【文字化け対策版】
    コマンドライン引数を使わず、UTF-8の一時引数ファイル(-@)を経由して
    ExifToolに命令を渡すことで、Windowsでの日本語文字化けを完全回避する。
    """
    args_file_path = None
    try:
        # 1. UTF-8で指示書（引数ファイル）を作成
        # delete=Falseにして、close後にExifToolに読ませる
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8') as f:
            f.write("-overwrite_original\n")
            f.write("-m\n")          # 軽微な警告を無視
            f.write("-charset\nutf8\n") # 内部処理をUTF-8で行う宣言
            f.write("-sep\n, \n")    # 区切り文字定義
            
            # 各種タグへの書き込み指示
            f.write(f"-XPKeywords={tags}\n")
            f.write(f"-Subject={tags}\n")
            f.write(f"-Keywords={tags}\n")
            
            # 対象ファイルパス
            f.write(f"{file_path}\n")
            
            args_file_path = f.name

        # 2. ExifToolに指示書を渡して実行
        command = ["exiftool", "-@", args_file_path]
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            encoding='utf-8', 
            startupinfo=startupinfo
        )
        
        # 3. 実行結果の判定
        if result.returncode == 0:
            return True, "Success"
        else:
            return False, result.stderr

    except Exception as e:
        return False, str(e)
    finally:
        # 4. 一時ファイルの掃除
        if args_file_path and os.path.exists(args_file_path):
            try:
                os.remove(args_file_path)
            except:
                pass

def process_single_file(api_key, file_path):
    """スレッド処理用ラッパー"""
    filename = os.path.basename(file_path)
    tags, err = get_tags_from_gemini(api_key, file_path)
    
    if err:
        return filename, False, f"AI Error: {err}", None

    success, write_err = write_tags_securely(file_path, tags)
    if success:
        return filename, True, tags, None
    else:
        return filename, False, f"Write Error: {write_err}", None

def remove_all_tags_in_folder(folder_path):
    """指定フォルダ以下の全ファイルのタグを一括削除する"""
    # ExifToolはフォルダ指定で一括処理できるため、Pythonループより圧倒的に速い
    try:
        # 再帰的に(-r)、タグを空にする
        command = [
            "exiftool",
            "-r", # サブフォルダも含む
            "-overwrite_original",
            "-m",
            "-XPKeywords=", 
            "-Subject=", 
            "-Keywords=",
            folder_path
        ]
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            startupinfo=startupinfo
        )
        
        if result.returncode == 0:
            return True, result.stdout # 成功時は「XX image files updated」などのログが返る
        else:
            return False, result.stderr
            
    except Exception as e:
        return False, str(e)

# --- UI構築 ---

st.title("🤖 Gemini AI Auto Tagger v2")
st.markdown("文字化け対策済み | 一括削除機能付き")

# サイドバー設定
with st.sidebar:
    st.header("共通設定")
    api_key = st.text_input("Gemini API Key", type="password")
    target_folder = st.text_input("対象フォルダパス", value="./images")
    st.info("フォルダパスは絶対パスまたは相対パスで入力してください。")

# タブで機能を切り替え
tab1, tab2 = st.tabs(["🏷️ 自動タグ付け", "🗑️ タグ一括削除"])

# --- タブ1: 自動タグ付け ---
# --- タブ1: 自動タグ付け ---
with tab1:
    concurrency = st.slider("同時処理数 (スレッド)", 1, 10, 4)
    
    if st.button("タグ付け開始", type="primary"):
        if not api_key:
            st.error("APIキーを入力してください。")
            st.stop()
        if not os.path.exists(target_folder):
            st.error("フォルダが見つかりません。")
            st.stop()

        extensions = ('.jpg', '.jpeg', '.png', '.gif', '.mp4')
        files = [os.path.join(target_folder, f) for f in os.listdir(target_folder) if f.lower().endswith(extensions)]
        
        if not files:
            st.warning("対象ファイルがありません。")
            st.stop()

        progress_bar = st.progress(0)
        status_text = st.empty()
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.subheader("処理ログ")
            log_area = st.empty()
        with col2:
            st.subheader("プレビュー")
            preview_area = st.empty()

        logs = []
        processed_count = 0
        total_files = len(files)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process_single_file, api_key, f): f for f in files}
            
            for future in as_completed(futures):
                filename, success, message, _ = future.result()
                file_full_path = futures[future]
                
                processed_count += 1
                progress_bar.progress(processed_count / total_files)
                status_text.text(f"Processing: {processed_count}/{total_files}")

                if success:
                    log_msg = f"✅ {filename}: {message}"
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        try:
                            preview_area.image(file_full_path, caption=f"Tags: {message}", use_container_width=True)
                        except: pass
                else:
                    log_msg = f"❌ {filename}: {message}"
                
                logs.insert(0, log_msg)
                
                # 【ここを修正しました】 keyを削除し、disabled=Trueを追加
                log_area.text_area("Log", "\n".join(logs[:20]), height=300, disabled=True)

        st.success("完了しました！")

# --- タブ2: タグ削除 ---
with tab2:
    st.header("タグの一括削除")
    st.warning("指定したフォルダ（およびその中のフォルダ全て）の画像のタグを全て消去します。この操作は元に戻せません。")
    
    if st.button("すべてのタグを削除する", type="secondary"):
        if not os.path.exists(target_folder):
            st.error("フォルダが見つかりません。")
            st.stop()
            
        with st.spinner("ExifToolで一括削除を実行中..."):
            success, msg = remove_all_tags_in_folder(target_folder)
            
        if success:
            st.success("削除完了！")
            st.text_area("詳細ログ", msg, height=200)
        else:
            st.error("エラーが発生しました")
            st.text_area("エラー詳細", msg, height=200)