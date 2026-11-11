<p align="center">
  <img src="brand/lidar-readme.png" alt="LiDAR" width="900">
</p>

<h1 align="center">LiDAR. Beta</h1>

<p align="center">
  <a href="https://github.com/sinonmiyaki/LiDAR/releases/latest"><img src="https://img.shields.io/github/v/release/sinonmiyaki/LiDAR?label=Release&amp;color=6600CC" alt="최신 릴리스"></a>
  <img src="https://img.shields.io/badge/Windows-x64-151515" alt="Windows x64">
  <img src="https://img.shields.io/badge/SteamVR-VRChat-151515" alt="SteamVR VRChat">
  <a href="https://github.com/RapidAI/RapidOCR"><img src="https://img.shields.io/badge/OCR-RapidOCR-6600CC" alt="RapidOCR"></a>
</p>

<p align="center">
  <strong><a href="https://github.com/sinonmiyaki/LiDAR/releases/latest">Windows 설치판 받기</a></strong>
  · <a href="#사용-방법">사용 방법</a>
  · <a href="#번역-모델">번역 모델</a>
  · <a href="#windows-빌드">소스 빌드</a>
</p>

---

**LiDAR**는 SteamVR에서 손으로 만든 프레임을 따라가는 스캐너입니다. VRChat에서 QR 링크는 PC 브라우저로 보내고, 화면에서 읽은 텍스트는 번역해 VR 안에 보여줍니다.

## 주요 기능

| 기능 | 동작 |
| --- | --- |
| **손 제스처 스캔** | 반투명 정사각형 프레임이 양손의 위치와 간격을 따라 움직입니다. |
| **QR 링크 열기** | 인식한 QR을 잠깐 보여준 뒤 VRChat 화면을 유지하면서 PC 기본 브라우저에 링크를 엽니다. 대시보드의 **최근 스캔**에서도 다시 열 수 있습니다. |
| **텍스트 인식과 번역** | RapidOCR이 PC에서 글자를 읽습니다. 번역은 VR 오버레이에 표시되고, 앱의 **AI 번역** 화면에서 원문과 함께 확인할 수 있습니다. |

## 사용 방법

1. [최신 릴리스](https://github.com/sinonmiyaki/LiDAR/releases/latest)에서 `LiDAR-Setup-…-x64.exe`를 받아 설치하고, 시작 메뉴에서 **LiDAR**를 실행합니다.
2. SteamVR에서 VRChat을 열고 대시보드를 닫습니다. LiDAR는 SteamVR과 헤드셋이 준비되면 자동으로 연결됩니다. 기본 손가락 바인딩은 **Valve Index 컨트롤러**용입니다.
3. 양쪽 엄지와 검지를 펴서 L자 모양을 만들고 나머지 손가락으로 그립을 잡습니다. 프레임이 밝아지면 QR이나 텍스트를 가운데에 맞춘 뒤 자세를 풉니다.
4. 오른손 트리거를 당기면 진행 중인 스캔을 취소합니다. **QR 스캔**과 **AI 번역**을 모두 켜면 QR을 먼저 확인하고, QR이 없으면 텍스트를 읽습니다.

텍스트를 읽으면 PC 앱의 **AI 번역** 탭에 원문이 먼저 표시됩니다. 번역 중에는 PC 앱과 VR에서 흰색 점 세 개가 움직입니다. 번역이 완료되면 PC 앱과 VR에 결과가 표시됩니다.

대시보드에서 **QR 스캔**과 **AI 번역**을 각각 켜고 끌 수 있습니다. 왼손·오른손·위치의 인식 상태도 앱 창에 표시됩니다. 창의 **X** 버튼을 누르면 스캔을 계속하면서 Windows 알림 영역으로 이동합니다. LiDAR 아이콘을 클릭하면 창이 다시 열리고, 우클릭 메뉴에서 **종료**할 수 있습니다.

## 번역 모델

사이드바의 **Language** 메뉴에서 앱 표시 언어를 한국어, 일본어, 영어, 중국어 간체로 바꿀 수 있습니다. 번역 결과 언어는 별도로 설정합니다.

**설정 → 번역 모델 설정**에서 모델을 선택합니다. 결과 언어는 **설정 → 번역 언어** 또는 대시보드의 **AI 번역** 카드 옆 톱니에서 변경합니다. 기본값은 한국어입니다.

| 모델 | 시작 방법 | 알아둘 점 |
| --- | --- | --- |
| **GPT-6 Luna** | ChatGPT 계정으로 연결 | Codex 사용 한도를 사용합니다. 추론 `none`을 우선 요청하며, 서버가 거절하고 `low`를 제공하면 전환합니다. [사용 한도](https://learn.chatgpt.com/docs/pricing) |
| **DeepL** | 사용자 API 키 입력 | Developer 플랜은 갱신 없이 총 100만 자를 체험할 수 있습니다. Developer 키는 개인정보가 없는 텍스트에만 사용하세요. [요금제](https://support.deepl.com/hc/en-us/articles/360021200939-DeepL-API-plans) · [이용 약관](https://www.deepl.com/en/pro-license_embedded) |
| **Local · M2M-100 418M INT8** | 모델을 한 번 다운로드 | 이후 오프라인으로 번역하며, 기존 모델 파일을 재사용할 수 있습니다. VRChat과 함께 실행하면 성능에 영향을 줄 수 있습니다. |

클라우드 모델에는 OCR로 읽은 텍스트를 보냅니다. 번역 요청이 실패하면 PC 앱의 **AI 번역** 탭에서 원문과 오류를 확인할 수 있습니다. DeepL API 키는 Windows 사용자 계정으로 암호화해 저장합니다.

## AI 어시스턴트

1. **설정 → 어시스턴트 설정**에서 Codex에 연결하고 **Gemini 3.5 Transcribe** API 키를 입력합니다. 같은 메뉴에서 Luna의 추론 수준을 설정할 수 있습니다.
2. 대시보드에서 **AI 어시스턴트**를 켜고, 오른손 트리거를 두 번 누릅니다. 처음에는 PC 앱에서 예문을 읽고 주변 소리를 측정해 마이크 감도를 저장합니다. 이후에는 같은 설정 메뉴에서 수동으로 조정하거나 다시 보정할 수 있습니다.
3. VR에 **AI Ready**가 뜨면 프레임으로 화면을 지정합니다. 효과음이 들리면 질문하세요. 발화 후 1초간 침묵하면 녹음을 Gemini로 전송합니다. 받아쓴 문장이 VR에 표시되며, 질문과 지정한 화면은 Codex의 GPT-6 Luna에 전달됩니다.
4. 질문이나 요청을 취소하려면 트리거를 한 번 누릅니다. 답변은 트리거를 두 번 눌러 어시스턴트를 닫을 때까지 표시됩니다.

마이크 입력 장치와 답변 말투는 **어시스턴트 설정**에서 바꿀 수 있습니다. 마이크를 변경한 뒤에는 새 장치의 감도를 조절하거나 다시 보정할 수 있습니다. Gemini API 키는 Windows 사용자 계정으로 암호화해 저장합니다.
PC 앱의 **AI 어시스턴트** 탭에서 최근 질문과 답변 5개를 볼 수 있습니다. 앱은 질문과 답변 텍스트를 PC에 저장합니다.

## 자동 실행과 업데이트

- 첫 실행에서 **SteamVR 자동 실행**을 선택할 수 있습니다. SteamVR이 꺼져 있으면 두 프로그램이 함께 실행되는 시점에 등록합니다. **설정**에서 자동 실행과 다크 모드를 각각 변경할 수 있습니다.
- SteamVR이 종료되면 알림 영역에 있는 LiDAR도 함께 종료됩니다. SteamVR을 아직 켜지 않았다면 LiDAR는 연결을 기다립니다.
- 새 버전이 있으면 LiDAR 창 안에 알림이 표시됩니다. **지금 업데이트**는 다운로드와 검증 후 설치 프로그램을 열고, **이번 버전 건너뛰기**는 해당 버전의 알림을 숨깁니다. **나중에**를 선택하면 다음 실행 때 다시 확인합니다.

## Windows 빌드

앱 UI에서 표시하는 팝업은 `InAppDialog`를 사용해 LiDAR 메인 창 안에서 엽니다.

Python 3.10 x64와 Inno Setup 6.7을 사용합니다. `updater.py`의 `VERSION`, Inno Setup의 `AppVersion`, GitHub 릴리스 태그 `vX.Y.Z`를 일치시킵니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt pyinstaller==6.20.0
.\.venv\Scripts\python fetch_models.py
.\.venv\Scripts\python -m PyInstaller --noconfirm LiDAR.spec
ISCC.exe /DAppVersion=0.3.0 installer.iss
```

Inno Setup의 `Output` 폴더에 생성된 설치 파일을 같은 버전의 GitHub Release에 첨부합니다.

## 사용 기술

OCR에는 RapidOCR과 PP-OCR, PC 화면과 VR 결과 카드에는 Pretendard를 사용합니다. [RapidOCR](https://github.com/RapidAI/RapidOCR) · [Pretendard](https://github.com/orioncactus/pretendard) · [번들 라이선스](THIRD_PARTY.md)

[카메라 세리머니 참고](https://www.tottenhamhotspur.com/news/988624/sonny-explains-his-celebration-as-he-reaches-his-premier-league-century) · [SteamVR 손가락 추적](https://github.com/ValveSoftware/openvr/wiki/SteamVR-Skeletal-Input)
