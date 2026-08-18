# -*- coding: utf-8 -*-
"""영상 → 대본 → 캡션 파이프라인.

비용 절감 우선순위:
1) 유튜브 자막이 있으면 그대로 사용 (0원)
2) 없으면 오디오만 추출해 Groq Whisper로 전사 (무료 티어 / 시간당 약 $0.04)
3) 키프레임 분석은 선택 옵션 (Claude vision 호출이 늘어나므로)
"""

import base64
import json
import os
import re
import subprocess
import tempfile

import imageio_ffmpeg

# ---------------------------------------------------------------- ffmpeg

def _ffmpeg() -> str:
    """pip으로 설치된 ffmpeg 바이너리 경로 (별도 설치 불필요)."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list) -> None:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg 실행 실패: " + proc.stderr.decode("utf-8", errors="replace")[-800:]
        )


def get_duration_seconds(media_path: str) -> float:
    """ffmpeg 로그에서 길이 파싱 (ffprobe 없이 동작)."""
    proc = subprocess.run(
        [_ffmpeg(), "-i", media_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    text = proc.stderr.decode("utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        raise RuntimeError("영상 길이를 읽지 못했습니다. 파일이 손상되지 않았는지 확인해 주세요.")
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def extract_audio(video_path: str, out_dir: str) -> str:
    """음성만 24kHz 모노 mp3(64kbps)로 추출 — 인식률을 위해 화질보다 음질 우선."""
    out = os.path.join(out_dir, "audio.mp3")
    _run([
        _ffmpeg(), "-y", "-i", video_path, "-vn",
        "-ac", "1", "-ar", "24000", "-b:a", "64k", out,
    ])
    return out


def split_audio(audio_path: str, out_dir: str, chunk_sec: int = 1200) -> list:
    """Groq 업로드 한도(25MB)에 맞춰 20분 단위로 분할."""
    dur = get_duration_seconds(audio_path)
    if dur <= chunk_sec:
        return [audio_path]
    chunks = []
    start, idx = 0.0, 0
    while start < dur:
        out = os.path.join(out_dir, f"chunk_{idx:03d}.mp3")
        _run([
            _ffmpeg(), "-y", "-ss", str(start), "-t", str(chunk_sec),
            "-i", audio_path, "-acodec", "copy", out,
        ])
        chunks.append(out)
        start += chunk_sec
        idx += 1
    return chunks


def extract_frames(video_path: str, out_dir: str, n: int = 4) -> list:
    """영상에서 균등 간격으로 n장의 키프레임(JPEG bytes) 추출."""
    dur = get_duration_seconds(video_path)
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        out = os.path.join(out_dir, f"frame_{i}.jpg")
        _run([
            _ffmpeg(), "-y", "-ss", str(t), "-i", video_path,
            "-frames:v", "1", "-vf", "scale='min(720,iw)':-2", "-q:v", "5", out,
        ])
        with open(out, "rb") as f:
            frames.append(f.read())
    return frames

# ---------------------------------------------------------------- STT (Groq Whisper)

def transcribe_audio(audio_path: str, groq_api_key: str, progress=None) -> str:
    """Groq Whisper(large-v3-turbo)로 전사. 무료 티어로 대부분 커버됩니다."""
    from groq import Groq

    client = Groq(api_key=groq_api_key)
    with tempfile.TemporaryDirectory() as td:
        chunks = split_audio(audio_path, td)
        texts = []
        for i, chunk in enumerate(chunks):
            if progress:
                progress(f"음성 인식 중... ({i + 1}/{len(chunks)})")
            with open(chunk, "rb") as f:
                result = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk), f.read()),
                    model="whisper-large-v3-turbo",
                    response_format="text",
                )
            texts.append(result if isinstance(result, str) else result.text)
    return "\n".join(t.strip() for t in texts).strip()

# ---------------------------------------------------------------- 유튜브

def youtube_video_id(url: str) -> str:
    for pattern in (
        r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ):
        m = re.search(pattern, url.strip())
        if m:
            return m.group(1)
    raise ValueError("유튜브 링크 형식을 인식하지 못했습니다.")


def fetch_youtube_transcript(url: str) -> str:
    """유튜브 자막을 무료로 가져오기 (자막이 있는 영상만). 신/구 API 모두 지원."""
    video_id = youtube_video_id(url)
    from youtube_transcript_api import YouTubeTranscriptApi

    languages = ["ko", "en", "ja", "zh-Hans", "zh-Hant"]
    if hasattr(YouTubeTranscriptApi, "get_transcript"):  # < 1.0
        snippets = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
    else:  # >= 1.0
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
        snippets = getattr(fetched, "snippets", fetched)
    texts = [s["text"] if isinstance(s, dict) else s.text for s in snippets]
    return " ".join(t.replace("\n", " ").strip() for t in texts if t).strip()


def download_youtube_audio(url: str, out_dir: str, progress=None) -> str:
    """자막이 없는 영상: yt-dlp로 오디오만 저용량 다운로드 → STT로 넘김."""
    import yt_dlp

    if progress:
        progress("유튜브에서 오디오 다운로드 중...")
    opts = {
        "format": "bestaudio[abr<=64]/bestaudio/best",
        "outtmpl": os.path.join(out_dir, "yt_audio.%(ext)s"),
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "ffmpeg_location": os.path.dirname(_ffmpeg()),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    return path

# ---------------------------------------------------------------- 캡션 후처리

def strip_indentation(text: str) -> str:
    """AI가 번호 리스트 등에서 실수로 넣은 줄 앞 공백만 제거 (내용/줄바꿈 위치는 그대로)."""
    return "\n".join(line.lstrip(" \t") for line in text.splitlines())

# ---------------------------------------------------------------- 캡션 프롬프트 (공용)

def _caption_user_text(
    transcript: str, extra_info: str, caption_mode: str, has_frames: bool,
    guideline: str = "", has_guideline_files: bool = False,
) -> str:
    # 대본이 아주 길면 앞뒤 위주로 절약 (비용 + 집중도)
    if len(transcript) > 24000:
        transcript = transcript[:16000] + "\n...(중략)...\n" + transcript[-8000:]

    mode_note = (
        "영상 대본의 핵심 내용을 캡션으로 요약해줘."
        if caption_mode == "영상 내용 요약"
        else "영상 주제를 먼저 파악한 뒤, 같은 맥락에서 시청자가 꼭 알아야 할 "
             "추가 정보와 실전 팁 중심으로 캡션을 구성해줘. "
             "(대본 반복이 아니라 '저장할 가치가 있는 보너스 정보' 느낌으로)"
    )

    text = f"# 작성 방향\n{mode_note}\n\n# 영상 대본\n{transcript}"
    if guideline.strip() or has_guideline_files:
        text += (
            "\n\n# 원고/가이드라인 (최우선 순위 — 절대 누락 금지)\n"
            "아래는 클라이언트나 브랜드에서 받은 실제 원고 또는 가이드라인이다 "
            "(텍스트 그리고/또는 첨부된 이미지·PDF 파일).\n"
            "이 안에 담긴 정보, 문구, 지시사항을 하나도 빠짐없이 전부 파악해서, "
            "트래블디토의 말투와 위 영상 대본의 정보를 더해 하나의 완성된 캡션으로 "
            "재구성해줘. 가이드라인에 있는 내용 중 단 하나라도 누락되면 실패작이다. "
            "가이드라인과 영상 대본 내용이 겹치면 가이드라인 쪽 표현/사실을 우선해라."
        )
        if guideline.strip():
            text += f"\n\n[가이드라인 텍스트]\n{guideline.strip()}"
        if has_guideline_files:
            text += "\n\n[첨부된 가이드라인 파일도 함께 읽고 반영해라 — 아래 이미지/PDF 첨부 참고]"
    if extra_info.strip():
        text += f"\n\n# 추가 정보/요청사항 (반영 필수)\n{extra_info.strip()}"
    if has_frames:
        text += (
            "\n\n# 첨부 이미지 (영상 장면)\n영상에서 뽑은 장면들이야. "
            "화면에 보이는 분위기/비주얼 정보를 캡션에 자연스럽게 반영해줘."
        )
    return text

# ---------------------------------------------------------------- Google Gemini (키 1개로 STT + 캡션)

_TRANSCRIBE_PROMPT = (
    "이 오디오/영상은 여행 브이로그야. 사람이 말하는 내용을 처음부터 끝까지 "
    "빠짐없이, 최대한 정확하게 받아써줘.\n"
    "- 들리는 발화를 있는 그대로 전사해. 의역하거나 요약하거나 문장을 생략하지 마.\n"
    "- 배경 소음, 음악, 잘 안 들리는 구간이 있어도 문맥과 발음을 최대한 유추해서 "
    "자연스러운 한국어 문장으로 채워 넣어. 완전히 불가능한 경우에만 [안 들림]으로 표시해.\n"
    "- 지명, 음식 이름, 숫자(가격/시간 등)는 특히 정확하게 받아써줘.\n"
    "- 타임스탬프, 화자 표시, 설명, 요약 없이 발화 문장만 순서대로 출력해."
)


def _gemini_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)


def _gemini_media_part(client, media_path: str, mime_type: str):
    """19MB 미만이면 인라인, 크면 File API 업로드 후 처리 대기."""
    import time
    from google.genai import types

    if os.path.getsize(media_path) < 19_000_000:
        with open(media_path, "rb") as f:
            return types.Part.from_bytes(data=f.read(), mime_type=mime_type)
    uploaded = client.files.upload(file=media_path, config={"mime_type": mime_type})
    for _ in range(300):
        state = getattr(getattr(uploaded, "state", None), "name", "ACTIVE")
        if state != "PROCESSING":
            break
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)
    return uploaded


_VIDEO_MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".avi": "video/x-msvideo",
}


def gemini_video_content(
    video_path: str, google_api_key: str,
    model: str = "gemini-flash-latest", progress=None,
) -> str:
    """목소리 없는 영상: Gemini가 화면을 직접 보고 자막·화면 텍스트·장면을 정리."""
    from google.genai import types

    client = _gemini_client(google_api_key)
    if progress:
        progress("영상 화면을 Gemini가 직접 분석 중... (파일이 크면 몇 분 걸릴 수 있어요)")
    mime = _VIDEO_MIME.get(os.path.splitext(video_path)[1].lower(), "video/mp4")
    part = _gemini_media_part(client, video_path, mime)
    prompt = (
        "이 영상을 처음부터 끝까지 보고, 인스타그램 캡션 작성에 쓸 수 있도록 "
        "내용을 자세히 정리해줘:\n"
        "1) 화면에 나오는 자막/텍스트/멘트를 순서대로 전부 받아써줘 (가장 중요)\n"
        "2) 음성 멘트가 있다면 그것도 최대한 정확하게 받아써줘. "
        "잘 안 들리는 구간은 문맥으로 유추해서 채우고, 완전히 불가능할 때만 [안 들림]으로 표시해\n"
        "3) 장면별로 어떤 장소/음식/활동을 보여주는지 구체적으로 설명해줘\n"
        "다른 인사말 없이 정리 내용만 출력해."
    )
    config = types.GenerateContentConfig(temperature=0.0)
    try:
        resp = client.models.generate_content(
            model=model, contents=[part, prompt], config=config,
        )
    except UnicodeEncodeError:
        # 검색/응답 처리 중 비ASCII 문자로 인한 오류 발생 시 안전 옵션으로 재시도
        resp = client.models.generate_content(
            model=model, contents=[part, prompt],
        )
    text = _gemini_text(resp)
    if not text:
        raise RuntimeError("영상 내용을 읽어오지 못했습니다.")
    return text


def transcribe_audio_gemini(
    audio_path: str, google_api_key: str,
    model: str = "gemini-flash-latest", progress=None,
) -> str:
    """Gemini로 오디오 전사 — Groq 없이 구글 키 하나로 해결."""
    from google.genai import types

    client = _gemini_client(google_api_key)
    if progress:
        progress("Gemini로 음성 인식 중...")
    part = _gemini_media_part(client, audio_path, "audio/mpeg")
    resp = client.models.generate_content(
        model=model, contents=[part, _TRANSCRIBE_PROMPT],
        config=types.GenerateContentConfig(temperature=0.0),
    )
    text = _gemini_text(resp)
    if not text:
        raise RuntimeError("음성 인식 결과가 비어 있습니다.")
    return text


def gemini_youtube_transcript(
    url: str, google_api_key: str, model: str = "gemini-flash-latest",
) -> str:
    """자막 없는 공개 유튜브 영상: Gemini가 URL을 직접 보고 받아쓰기 (다운로드 불필요)."""
    from google.genai import types

    client = _gemini_client(google_api_key)
    part = types.Part(file_data=types.FileData(file_uri=url))
    resp = client.models.generate_content(
        model=model, contents=[part, _TRANSCRIBE_PROMPT],
    )
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("Gemini가 영상 내용을 읽지 못했습니다.")
    return text


def _gemini_text(resp) -> str:
    """응답에서 텍스트를 최대한 안전하게 회수 (간헐적 빈 text 대응)."""
    try:
        if resp.text:
            return resp.text.strip()
    except Exception:
        pass
    out = []
    for c in (resp.candidates or []):
        if c.content and c.content.parts:
            out.extend(p.text for p in c.content.parts if getattr(p, "text", None))
    return "\n".join(out).strip()


def generate_caption_gemini(
    transcript: str,
    style_prompt: str,
    google_api_key: str,
    model: str = "gemini-flash-latest",
    extra_info: str = "",
    frames: list | None = None,
    caption_mode: str = "영상 내용 요약",
    use_search: bool = True,
    guideline: str = "",
    guideline_files: list | None = None,
) -> str:
    """guideline_files: [(bytes, mime_type), ...] — 원고/가이드라인 이미지·PDF."""
    from google.genai import types

    client = _gemini_client(google_api_key)
    user_text = _caption_user_text(
        transcript, extra_info, caption_mode, bool(frames),
        guideline=guideline, has_guideline_files=bool(guideline_files),
    )
    if use_search:
        user_text += (
            "\n\n# 웹 검색 보강 (필수)\n"
            "캡션을 쓰기 전에 구글 검색으로 이 주제의 실전 정보를 조사해서 반영해줘: "
            "현재 가격대(현지 통화 + 원화 환산), 예약 방법(한국인이 많이 쓰는 앱/플랫폼 이름), "
            "소요 시간, 추천 시간대, 준비물, 주의사항. "
            "대본의 경험담과 검색된 최신 정보를 합쳐서 '저장 각' 실전 가이드를 만들어줘. "
            "검색해도 확실하지 않은 수치는 쓰지 마."
        )

    parts = [
        types.Part.from_bytes(data=data, mime_type=mime)
        for data, mime in (guideline_files or [])
    ]
    parts += [
        types.Part.from_bytes(data=jpg, mime_type="image/jpeg")
        for jpg in (frames or [])
    ]
    parts.append(user_text)

    def make_config(with_search: bool) -> "types.GenerateContentConfig":
        return types.GenerateContentConfig(
            system_instruction=style_prompt,
            max_output_tokens=16000,
            # 생각(thinking) 토큰이 출력 예산을 다 먹어 빈 응답이 나오는 걸 방지
            thinking_config=types.ThinkingConfig(thinking_budget=4096),
            tools=[types.Tool(google_search=types.GoogleSearch())] if with_search else None,
        )

    config = make_config(use_search)
    caption = ""
    for attempt in range(3):  # 간헐적 빈 응답 대비 재시도
        try:
            resp = client.models.generate_content(model=model, contents=parts, config=config)
        except UnicodeEncodeError:
            # 일부 클라우드 환경에서 검색 그라운딩 결과(비ASCII URL 등) 처리 중
            # 인코딩 오류가 나는 경우가 있어, 검색 없이 한 번 더 시도
            if not config.tools:
                raise
            config = make_config(with_search=False)
            resp = client.models.generate_content(model=model, contents=parts, config=config)
        caption = _gemini_text(resp)
        if caption:
            break
    if not caption:
        raise RuntimeError("Gemini가 빈 응답을 반환했습니다. 잠시 후 다시 시도해 주세요.")
    return caption

# ---------------------------------------------------------------- 캡션 생성 (Claude)

def generate_caption(
    transcript: str,
    style_prompt: str,
    anthropic_api_key: str,
    model: str = "claude-sonnet-5",
    extra_info: str = "",
    frames: list | None = None,
    caption_mode: str = "영상 내용 요약",
    guideline: str = "",
    guideline_files: list | None = None,
) -> str:
    """guideline_files: [(bytes, mime_type), ...] — 원고/가이드라인 이미지·PDF."""
    import anthropic

    client = anthropic.Anthropic(api_key=anthropic_api_key)
    user_text = _caption_user_text(
        transcript, extra_info, caption_mode, bool(frames),
        guideline=guideline, has_guideline_files=bool(guideline_files),
    )

    content = []
    for data, mime in (guideline_files or []):
        if mime == "application/pdf":
            content.append({
                "type": "document",
                "source": {
                    "type": "base64", "media_type": mime,
                    "data": base64.standard_b64encode(data).decode(),
                },
            })
        else:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64", "media_type": mime,
                    "data": base64.standard_b64encode(data).decode(),
                },
            })
    for jpg in (frames or []):
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(jpg).decode(),
            },
        })
    content.append({"type": "text", "text": user_text})

    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        system=style_prompt,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()
