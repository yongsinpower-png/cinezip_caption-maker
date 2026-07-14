# ✈️ 트래블디토 인스타 캡션 자동 생성기

영상(파일 업로드 or 유튜브 링크)을 넣으면 → 대본을 추출하고 → 트래블디토 말투로
저장·공유를 부르는 인스타그램 캡션을 자동 작성하는 웹 앱입니다.

## 입력 방식 3가지

| 방식 | 언제 쓰나 | 비용 |
|---|---|---|
| 📁 영상 파일 업로드 | 유튜브 업로드 전 오프라인 영상 | Groq STT (무료 티어 / 시간당 약 $0.04) |
| 🔗 유튜브 링크 | 이미 올린 영상. 자막 있으면 그대로 사용 | 자막 있으면 **0원** |
| 📝 대본 직접 입력 | 대본/블로그 글이 이미 있을 때 | 0원 |

## AI 엔진 2가지 (사이드바에서 선택)

| 엔진 | 필요한 키 | 특징 |
|---|---|---|
| **Google Gemini (기본)** | Google API 키 1개 | 음성 인식+캡션을 키 하나로. 무료 사용량이 커서 사실상 무료 |
| Claude + Groq | Anthropic + Groq 키 2개 | 캡션 문장 품질을 더 끌어올리고 싶을 때 |

Gemini 모드는 자막 없는 유튜브 영상도 URL을 직접 분석해서 다운로드 과정 없이 처리합니다.

## 처음 한 번만 하면 되는 준비

1. Python 설치 (3.10 이상)
2. 이 폴더에서 터미널을 열고:
   ```
   pip install -r requirements.txt
   ```
3. API 키 발급 (Gemini 모드는 1번만 있으면 됨):
   - **Google** (음성 인식+캡션, 무료): https://aistudio.google.com/apikey
     (⚠️ 유튜브 Data API 키와는 다릅니다 — AI Studio에서 발급한 키여야 합니다)
   - Claude 모드 사용 시: **Anthropic** https://console.anthropic.com / **Groq** https://console.groq.com
4. (선택) 매번 키 입력이 귀찮으면 `.streamlit/secrets.toml` 파일을 만들어 저장:
   ```toml
   GOOGLE_API_KEY = "AIza..."
   # 아래 둘은 Claude 모드를 쓸 때만
   # ANTHROPIC_API_KEY = "sk-ant-..."
   # GROQ_API_KEY = "gsk_..."
   ```

## 실행

```
streamlit run app.py
```

브라우저가 자동으로 열립니다 (http://localhost:8501).

## 다른 컴퓨터에서도 쓰기 (무료 배포)

Streamlit Community Cloud에 올리면 어느 컴퓨터에서든 브라우저로 접속할 수 있습니다.

1. 이 폴더를 GitHub 저장소로 올림 (**secrets.toml은 절대 올리지 말 것** — .gitignore에 포함됨)
2. https://share.streamlit.io 접속 → GitHub 로그인 → New app → 저장소 선택, Main file: `app.py`
3. 앱 Settings → Secrets에 API 키 붙여넣기:
   ```toml
   GOOGLE_API_KEY = "AIza..."
   ```
4. 발급된 `https://○○○.streamlit.app` 주소를 즐겨찾기 해두면 끝.

⚠️ 공개 URL이므로 사람들에게 오픈하기 전까지는 앱 Settings에서
"Only specific people can view this app"으로 비공개 설정을 권장합니다.

⚠️ 클라우드 배포 시 유튜브 오디오 다운로드(자막 없는 영상)는 유튜브 측 차단으로
실패할 수 있습니다. 그 경우 영상 파일 업로드나 자막 있는 링크를 사용하세요.

## 비용 절감 팁

- 자막이 이미 있는 유튜브 영상 링크가 가장 저렴 (STT 0원)
- Groq 무료 티어만으로 일반적인 사용량은 충분
- 사이드바에서 "절약 (Haiku)" 모델을 고르면 캡션 생성 비용이 약 1/3
- 키프레임 분석은 화면 비주얼 언급이 필요할 때만 켜기

## 나중에: 다른 사람에게 오픈할 때

- `style_prompt.py`의 말투 규칙을 사용자별 프로필로 확장
- 사용자의 기존 인스타 캡션 3~5개를 붙여넣으면 말투를 분석해 프로필을 만드는
  "말투 학습" 탭 추가 예정
