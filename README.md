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

**LiDAR**는 손으로 만든 프레임을 따라 움직이는 SteamVR 스캐너입니다. VRChat에서 QR을 스캔하면 PC 브라우저로 링크를 보냅니다. 화면의 텍스트를 읽어 번역하고 VR 안에 결과를 보여주는 기능도 갖췄습니다.

## 주요 기능

| 기능 | 동작 |
| --- | --- |
| **손 제스처 스캔** | 반투명 정사각형 프레임이 양손의 위치와 간격을 따라 움직입니다. |
| **QR 링크 열기** | 인식한 QR을 잠깐 보여주고 PC 기본 브라우저에 링크를 엽니다. VRChat 화면은 그대로 유지됩니다. 대시보드의 **최근 스캔**에서도 링크를 다시 열 수 있습니다. |
| **텍스트 인식과 번역** | PC에서 RapidOCR로 글자를 읽고 번역을 VR 오버레이에 띄웁니다. 앱의 **AI 번역** 화면에서는 원문과 번역을 함께 확인할 수 있습니다. |

## 사용 방법

### 설치와 연결

1. [최신 릴리스](https://github.com/sinonmiyaki/LiDAR/releases/latest)에서 `LiDAR-Setup-…-x64.exe`를 받아 설치합니다. 시작 메뉴에서 **LiDAR**를 실행합니다.
2. SteamVR에서 VRChat을 연 다음 대시보드를 닫습니다. SteamVR과 헤드셋이 준비되면 LiDAR가 자동으로 연결됩니다.

기본 손가락 바인딩은 **Valve Index 컨트롤러**용입니다.

### 손 제스처로 스캔

1. 양쪽 엄지와 검지를 L자 모양으로 펴고 나머지 손가락으로 그립을 잡습니다.
2. 프레임이 밝아지면 가운데에 QR이나 텍스트를 맞춥니다. 이어서 자세를 풀면 됩니다.

진행 중인 스캔은 오른손 트리거를 당겨 취소합니다. 대시보드에서 **QR 스캔**과 **AI 번역**을 각각 켜고 끌 수 있습니다. 둘 다 켜져 있으면 QR부터 확인하고 QR이 없을 때 텍스트를 읽습니다.

### 결과 확인과 앱 창 관리

텍스트 인식이 끝나면 PC 앱의 **AI 번역** 탭에 원문부터 나타납니다. 번역하는 동안에는 PC 앱과 VR에서 흰색 점 세 개가 움직이며 완료 후 양쪽에 번역 결과가 표시됩니다.

앱 창에서는 왼손·오른손·위치의 인식 상태를 확인합니다. **X** 버튼을 누르면 스캔을 계속한 채 창이 Windows 알림 영역으로 이동합니다. 창을 다시 열려면 LiDAR 아이콘을 클릭합니다. 아이콘의 우클릭 메뉴에서 **종료**할 수 있습니다.

## 번역 모델

번역에 쓸 모델은 **설정 → 번역 모델 설정**에서 선택합니다. 결과 언어의 기본값은 한국어이며 **설정 → 번역 언어** 또는 대시보드의 **AI 번역** 카드 옆 톱니에서 바꿉니다.

앱 표시 언어는 번역 결과 언어와 별도로 설정합니다. 사이드바의 **Language** 메뉴에서 한국어, 일본어, 영어, 중국어 간체 중 하나를 선택합니다.

| 모델 | 시작 방법 | 알아둘 점 |
| --- | --- | --- |
| **GPT-6 Luna** | ChatGPT 계정으로 연결 | Codex 사용 한도에서 차감됩니다. 추론 `none`을 우선 요청하며 서버가 거절하고 `low`를 제공하면 전환합니다. [사용 한도](https://learn.chatgpt.com/docs/pricing) |
| **DeepL** | 사용자 API 키 입력 | Developer 플랜은 갱신 없이 총 100만 자를 체험할 수 있습니다. Developer 키는 개인정보가 없는 텍스트에만 사용하세요. [요금제](https://support.deepl.com/hc/en-us/articles/360021200939-DeepL-API-plans) · [이용 약관](https://www.deepl.com/en/pro-license_embedded) |
| **Local · M2M-100 418M INT8** | 모델을 한 번 다운로드 | 이후 오프라인으로 번역하며 기존 모델 파일을 재사용할 수 있습니다. VRChat과 함께 실행하면 성능에 영향을 줄 수 있습니다. |

클라우드 모델을 선택하면 OCR로 읽은 텍스트를 해당 모델에 보냅니다. 번역 요청이 실패하면 PC 앱의 **AI 번역** 탭에서 원문과 오류를 확인할 수 있습니다. DeepL API 키는 Windows 사용자 계정으로 암호화해 저장합니다.

## AI 어시스턴트

### 연결과 마이크 설정

1. **설정 → 어시스턴트 설정**에서 Codex에 연결한 뒤 **Gemini 3.5 Transcribe** API 키를 입력합니다. Luna의 추론 수준도 이 메뉴에서 설정할 수 있습니다.
2. 대시보드의 **AI 어시스턴트**를 켜고 오른손 트리거를 두 번 누릅니다.
3. 처음 실행할 때는 PC 앱에서 예문을 읽고 주변 소리를 측정합니다. 이 과정에서 마이크 감도가 저장되며 이후 같은 설정 메뉴에서 수동으로 조정하거나 다시 보정할 수 있습니다.

**어시스턴트 설정**에서는 마이크 입력 장치와 답변 말투도 바꿀 수 있습니다. 마이크를 변경한 뒤에는 새 장치에 맞게 감도를 조절하거나 다시 보정할 수 있습니다. Gemini API 키는 Windows 사용자 계정으로 암호화해 저장합니다.

### 질문과 답변

1. VR에 **AI Ready**가 뜨면 프레임으로 화면을 지정합니다.
2. 효과음이 들린 뒤 질문합니다. 발화 후 1초간 침묵하면 녹음을 Gemini로 전송합니다.
3. 받아쓴 문장이 VR에 나타납니다. 질문과 지정한 화면은 Codex의 GPT-6 Luna에 전달됩니다.

질문이나 요청은 트리거를 한 번 눌러 취소합니다. 답변은 어시스턴트를 닫을 때까지 표시됩니다. 닫으려면 트리거를 두 번 누릅니다.

PC 앱의 **AI 어시스턴트** 탭에서 최근 질문과 답변 5개를 볼 수 있습니다. 질문과 답변 텍스트는 PC에 저장합니다.

## 자동 실행과 업데이트

첫 실행에서 **SteamVR 자동 실행**을 선택할 수 있습니다. SteamVR이 꺼진 상태라면 두 프로그램이 함께 실행될 때 등록합니다. 자동 실행과 다크 모드는 **설정**에서 각각 변경할 수 있습니다.

SteamVR을 아직 켜지 않았다면 LiDAR는 연결을 기다립니다. SteamVR이 종료되면 알림 영역에 있는 LiDAR도 함께 종료됩니다.

새 버전이 있으면 LiDAR 창 안에 알림이 뜹니다. 알림에서 업데이트 시점을 선택합니다.

- **지금 업데이트**: 다운로드와 검증을 마친 뒤 설치 프로그램을 엽니다.
- **이번 버전 건너뛰기**: 해당 버전의 알림을 숨깁니다.
- **나중에**: 다음 실행 때 다시 확인합니다.

## 사용 기술

OCR에는 RapidOCR과 PP-OCR, PC 화면과 VR 결과 카드에는 Pretendard를 사용합니다. [RapidOCR](https://github.com/RapidAI/RapidOCR) · [Pretendard](https://github.com/orioncactus/pretendard) · [번들 라이선스](THIRD_PARTY.md)

[카메라 세리머니 참고](https://www.tottenhamhotspur.com/news/988624/sonny-explains-his-celebration-as-he-reaches-his-premier-league-century) · [SteamVR 손가락 추적](https://github.com/ValveSoftware/openvr/wiki/SteamVR-Skeletal-Input)
