# -*- coding: utf-8 -*-
"""트래블디토 인스타그램 캡션 자동 생성기 (Streamlit).

실행:  streamlit run app.py
"""

import os
import tempfile

# 일부 클라우드 컨테이너는 로케일이 UTF-8이 아니라(C/POSIX/ASCII) 한글 처리 중
# "'ascii' codec can't encode characters" 오류가 날 수 있어 앱 시작 시 강제 설정.
import locale
for _loc in ("C.UTF-8", "en_US.UTF-8"):
    try:
        locale.setlocale(locale.LC_ALL, _loc)
        break
    except locale.Error:
        continue

import streamlit as st
import streamlit.components.v1 as components

import pipeline
from style_prompt import DEFAULT_STYLE_PROMPT

st.set_page_config(page_title="트래블디토 캡션 생성기", page_icon="✈️", layout="wide")

# ---------------------------------------------------------------- 키/설정

def get_secret(name: str) -> str:
    value = ""
    try:
        if name in st.secrets:
            value = str(st.secrets[name]).strip()
    except Exception:
        pass
    value = value or os.environ.get(name, "")
    # secrets.toml의 자리표시자(한글 안내문)만 걸러내고, 그 외 값은 그대로 사용
    if any("가" <= ch <= "힣" for ch in value):
        return ""
    return value


with st.sidebar:
    st.header("⚙️ 설정")

    provider = st.radio(
        "AI 엔진",
        ["Google Gemini — 키 1개로 전부 (무료 사용량 큼)", "Claude + Groq"],
        index=0,
    )
    use_gemini = provider.startswith("Google")

    google_key = anthropic_key = groq_key = ""
    if use_gemini:
        google_key = get_secret("GOOGLE_API_KEY") or st.text_input(
            "Google API 키", type="password",
            help="aistudio.google.com/apikey 에서 무료 발급. "
                 "음성 인식과 캡션 생성을 이 키 하나로 처리합니다.",
        )
        model_label = st.radio(
            "캡션 생성 모델",
            ["기본 (Gemini Flash) — 무료 사용량 큼", "고품질 (Gemini Pro)"],
            index=0,
        )
        model = "gemini-flash-latest" if model_label.startswith("기본") else "gemini-pro-latest"
        use_web_search = st.checkbox(
            "🔍 웹 검색으로 정보 보강 (권장)", value=True,
            help="가격·예약처·준비물 같은 실전 정보를 구글 검색으로 조사해서 캡션에 반영합니다.",
        )
    else:
        use_web_search = False
        anthropic_key = get_secret("ANTHROPIC_API_KEY") or st.text_input(
            "Anthropic API 키", type="password",
            help="console.anthropic.com 에서 발급 (캡션 생성용)",
        )
        groq_key = get_secret("GROQ_API_KEY") or st.text_input(
            "Groq API 키", type="password",
            help="console.groq.com 에서 무료 발급 (음성→대본 변환용). "
                 "유튜브 자막이 있는 영상만 쓸 거면 없어도 됩니다.",
        )
        model_label = st.radio(
            "캡션 생성 모델",
            ["고품질 (Sonnet) — 권장", "절약 (Haiku) — 약 1/3 비용"],
            index=0,
        )
        model = "claude-sonnet-5" if model_label.startswith("고품질") else "claude-haiku-4-5-20251001"

    st.divider()

    caption_mode = st.radio(
        "캡션 방향",
        ["영상 내용 요약", "주제 관련 꿀정보 위주"],
        help="'꿀정보 위주'는 대본 반복 대신, 같은 주제에서 저장할 가치가 있는 추가 팁 중심으로 씁니다.",
    )

    use_frames = st.checkbox(
        "🖼️ 키프레임 분석 (영상 파일만, 비용 소폭 추가)",
        value=False,
        help="영상에서 장면 4장을 뽑아 화면 분위기까지 캡션에 반영합니다.",
    )

    with st.expander("✍️ 말투 규칙 편집"):
        style_prompt = st.text_area(
            "시스템 프롬프트", value=DEFAULT_STYLE_PROMPT, height=400,
            label_visibility="collapsed",
        )

# ---------------------------------------------------------------- 본문

st.title("✈️ 트래블디토 인스타 캡션 생성기")
st.caption("영상을 넣으면 → 대본을 추출하고 → 트래블디토 말투로 저장 각 캡션을 만들어 드립니다 :)")

tab_file, tab_youtube, tab_text = st.tabs(
    ["📁 영상 파일 업로드", "🔗 유튜브 링크", "📝 대본 직접 입력"]
)

source = None  # ("file", 경로) | ("youtube", url) | ("text", 대본)

with tab_file:
    uploaded = st.file_uploader(
        "업로드 전 오프라인 영상도 OK! (mp4, mov, mkv, webm, m4a, mp3)",
        type=["mp4", "mov", "mkv", "webm", "avi", "m4a", "mp3", "wav"],
    )
    if uploaded is not None:
        source = ("file", uploaded)

with tab_youtube:
    yt_url = st.text_input("유튜브 영상 링크 (Shorts 포함)", placeholder="https://youtu.be/...")
    if yt_url.strip():
        source = ("youtube", yt_url.strip())
    st.caption("💡 자막이 있는 영상은 STT 비용 없이 자막을 그대로 사용합니다 (0원).")

with tab_text:
    manual_text = st.text_area(
        "대본/블로그 글/식당 정보를 붙여넣어도 됩니다", height=200,
        placeholder="이미 대본이 있다면 여기에 붙여넣는 게 가장 빠르고 저렴해요!",
    )
    if manual_text.strip():
        source = ("text", manual_text.strip())

extra_info = st.text_input(
    "➕ 추가 요청사항 (선택)",
    placeholder="예: 식당 이름은 '○○식당', 위치 강조해줘 / 라오스 환율 정보 넣어줘",
)

generate = st.button("🚀 캡션 생성하기", type="primary", use_container_width=True)

# ---------------------------------------------------------------- 실행

def fail(msg: str):
    st.error(msg)
    st.stop()


if generate:
    if source is None:
        fail("영상 파일, 유튜브 링크, 대본 중 하나를 먼저 넣어주세요!")
    if use_gemini and not google_key:
        fail("사이드바에 Google API 키를 입력해 주세요. (aistudio.google.com/apikey 에서 무료 발급)")
    if not use_gemini and not anthropic_key:
        fail("사이드바에 Anthropic API 키를 입력해 주세요.")

    status = st.status("작업 중...", expanded=True)
    progress = lambda msg: status.write(msg)
    transcript, frames = "", None

    def stt(audio_path):
        """엔진에 맞는 음성 인식."""
        if use_gemini:
            return pipeline.transcribe_audio_gemini(audio_path, google_key, model="gemini-flash-latest", progress=progress)
        return pipeline.transcribe_audio(audio_path, groq_key, progress)

    try:
        kind, value = source

        if kind == "text":
            transcript = value

        elif kind == "youtube":
            progress("유튜브 자막 확인 중...")
            try:
                transcript = pipeline.fetch_youtube_transcript(value)
                progress("✅ 자막을 찾았습니다 — STT 비용 0원!")
            except Exception:
                progress("자막이 없는 영상이네요. 음성 인식으로 전환합니다.")
                if use_gemini:
                    try:
                        progress("Gemini가 유튜브 영상을 직접 분석 중...")
                        transcript = pipeline.gemini_youtube_transcript(value, google_key)
                    except Exception:
                        progress("직접 분석 실패 — 오디오를 내려받아 다시 시도합니다.")
                        with tempfile.TemporaryDirectory() as td:
                            audio = pipeline.download_youtube_audio(value, td, progress)
                            transcript = stt(audio)
                else:
                    if not groq_key:
                        status.update(state="error")
                        fail("자막이 없는 영상은 Groq API 키(무료)가 필요합니다. 사이드바에 입력해 주세요.")
                    with tempfile.TemporaryDirectory() as td:
                        audio = pipeline.download_youtube_audio(value, td, progress)
                        transcript = stt(audio)

        elif kind == "file":
            if not use_gemini and not groq_key:
                status.update(state="error")
                fail("영상 파일은 음성 인식을 위해 Groq API 키(무료)가 필요합니다. 사이드바에 입력해 주세요.")
            suffix = os.path.splitext(value.name)[1] or ".mp4"
            with tempfile.TemporaryDirectory() as td:
                video_path = os.path.join(td, "input" + suffix)
                with open(video_path, "wb") as f:
                    f.write(value.getbuffer())
                try:
                    progress("오디오 추출 중...")
                    audio = pipeline.extract_audio(video_path, td)
                    transcript = stt(audio)
                except Exception as e:
                    transcript = ""
                    progress(f"⚠️ 음성 인식 실패 ({type(e).__name__}: {str(e)[:200]}) — 화면 분석으로 전환합니다.")

                # 목소리가 없거나 너무 짧으면 → 영상 화면 직접 분석 (Gemini)
                if len(transcript.strip()) < 40:
                    if use_gemini:
                        progress("목소리가 감지되지 않아 화면 내용으로 전환합니다.")
                        transcript = pipeline.gemini_video_content(
                            video_path, google_key, progress=progress,
                        )
                    else:
                        status.update(state="error")
                        fail("목소리가 없는 영상은 화면 분석이 필요합니다. "
                             "사이드바에서 'Google Gemini' 엔진을 선택해 주세요.")
                elif use_frames:
                    progress("키프레임 추출 중...")
                    try:
                        frames = pipeline.extract_frames(video_path, td)
                    except Exception:
                        progress("⚠️ 키프레임 추출에 실패해 대본만으로 진행합니다.")

        if not transcript.strip():
            status.update(state="error")
            fail("대본을 추출하지 못했습니다. 다른 영상으로 시도해 보세요.")

        progress("트래블디토 말투로 캡션 작성 중... ✍️")
        if use_gemini:
            caption = pipeline.generate_caption_gemini(
                transcript=transcript,
                style_prompt=style_prompt,
                google_api_key=google_key,
                model=model,
                extra_info=extra_info,
                frames=frames,
                caption_mode=caption_mode,
                use_search=use_web_search,
            )
        else:
            caption = pipeline.generate_caption(
                transcript=transcript,
                style_prompt=style_prompt,
                anthropic_api_key=anthropic_key,
                model=model,
                extra_info=extra_info,
                frames=frames,
                caption_mode=caption_mode,
            )
        caption = pipeline.strip_indentation(caption)
        status.update(label="완료! 🎉", state="complete", expanded=False)
        st.session_state["caption"] = caption
        st.session_state["transcript"] = transcript
        st.session_state["frames"] = frames

    except Exception as e:
        import traceback
        status.update(state="error")
        st.error(f"오류가 발생했습니다: {e}")
        with st.expander("🔧 진단 정보 (오류 상세)"):
            st.code(traceback.format_exc(), language="python")
        st.stop()

# ---------------------------------------------------------------- 결과 표시 (생성 후 계속 유지)

def copy_button(text: str):
    """클립보드 복사 버튼 (iframe 안에서도 동작하도록 폴백 포함)."""
    import json

    payload = json.dumps(text)
    components.html(f"""
    <button id="copy" style="
        width: 100%; padding: 14px 0; border: none; border-radius: 8px;
        background: #FF4B4B; color: white; font-size: 17px; font-weight: 700;
        font-family: 'Malgun Gothic', sans-serif; cursor: pointer;">
        📋 캡션 복사하기
    </button>
    <script>
    const btn = document.getElementById('copy');
    const text = {payload};
    btn.addEventListener('click', async () => {{
        try {{
            await navigator.clipboard.writeText(text);
        }} catch (e) {{
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        }}
        btn.innerText = '✅ 복사 완료! 인스타에 붙여넣으세요';
        btn.style.background = '#21c354';
        setTimeout(() => {{
            btn.innerText = '📋 캡션 복사하기';
            btn.style.background = '#FF4B4B';
        }}, 2500);
    }});
    </script>
    """, height=58)


if st.session_state.get("caption"):
    st.subheader("📋 완성된 캡션")
    copy_button(st.session_state["caption"])
    st.code(st.session_state["caption"], language=None)

    with st.expander("📜 추출된 대본 보기"):
        st.write(st.session_state.get("transcript", ""))
    if st.session_state.get("frames"):
        with st.expander("🖼️ 분석에 사용한 키프레임"):
            saved_frames = st.session_state["frames"]
            cols = st.columns(len(saved_frames))
            for col, jpg in zip(cols, saved_frames):
                col.image(jpg)
