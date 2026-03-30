#!/bin/bash
# set -e ❌ 제거 (중간 실패로 전체 중단 방지)

echo "🌀 RunPod 재시작 시 의존성 복구 시작"

############################################
# 📦 코어 파이썬 패키지 (ComfyUI 필수)
############################################
# 💨 빠른 실행을 위한 시스템 패키지 설치 확인 (휘발성 마커 사용)
if [ ! -f "/tmp/.a1_sys_pkg_checked" ]; then
    echo '📦 코어 파이썬 패키지 설치'

    # 🔥 [CRITICAL] Torch 버전 완전 재설치 (버전 불일치 방지)
    # 기존 버전 제거 (찌꺼기 방지)
    pip uninstall -y torch torchvision torchaudio

    # 최신 노드 호환을 위해 Torch 2.4.1 + CUDA 12.1 조합으로 업그레이드
    pip install torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1 --index-url https://download.pytorch.org/whl/cu121 || echo '⚠️ Torch 재설치 실패'

    # 필수 의존성 및 누락 패키지(pydantic-settings) 추가
    pip install torchsde av pydantic-settings || echo '⚠️ 초기 의존성 설치 실패'

    echo '📦 파이썬 패키지 설치'

    pip install --no-cache-dir \
        GitPython onnx onnxruntime opencv-python-headless tqdm requests \
        scikit-image piexif packaging transformers accelerate peft sentencepiece \
        protobuf scipy einops pandas matplotlib imageio[ffmpeg] pyzbar pillow numba \
        gguf diffusers insightface dill taichi pyloudnorm || echo '⚠️ 일부 pip 설치 실패'

    pip install mtcnn==0.1.1 || echo '⚠️ mtcnn 실패'
    pip install facexlib basicsr gfpgan realesrgan || echo '⚠️ facexlib 실패'
    pip install timm || echo '⚠️ timm 실패'
    pip install ultralytics || echo '⚠️ ultralytics 실패'
    pip install ftfy || echo '⚠️ ftfy 실패'
    pip install bitsandbytes xformers || echo '⚠️ bitsandbytes 또는 xformers 설치 실패'
    pip install bitsandbytes xformers || echo '⚠️ bitsandbytes 또는 xformers 설치 실패'
    pip install sageattention || echo '⚠️ sageattention 설치 실패'
    
    # [중요] 모든 필수 패키지 설치 시도가 끝났을 때만 마커 생성
    # (실패 시 마커 안 생김 -> 수동 재실행 시 다시 시도 가능)
    touch "/tmp/.a1_sys_pkg_checked"
else
    echo "⏩ 시스템 패키지 설치 확인됨 (스킵)"
fi

############################################
# 📁 커스텀 노드 설치 (안 깨지게 서브셸로)
############################################
echo '📁 커스텀 노드 및 의존성 설치 시작'

mkdir -p /workspace/ComfyUI/custom_nodes

(
cd /workspace/ComfyUI/custom_nodes || exit 0

git clone https://github.com/ltdrdata/ComfyUI-Manager.git && (cd ComfyUI-Manager && git checkout fa009e7) || echo '⚠️ Manager 실패 (1)'
git clone https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git && (cd ComfyUI-Custom-Scripts && git checkout f2838ed) || echo '⚠️ Scripts 실패(2)'
git clone https://github.com/rgthree/rgthree-comfy.git && (cd rgthree-comfy && git checkout 8ff50e4) || echo '⚠️ rgthree 실패(3)'
git clone https://github.com/WASasquatch/was-node-suite-comfyui.git && (cd was-node-suite-comfyui && git checkout ea935d1) || echo '⚠️ WAS 실패(4)'
git clone https://github.com/kijai/ComfyUI-KJNodes.git && (cd ComfyUI-KJNodes && git checkout 7b13271) || echo '⚠️ KJNodes 실패(5)'
git clone https://github.com/cubiq/ComfyUI_essentials.git && (cd ComfyUI_essentials && git checkout 9d9f4be) || echo '⚠️ Essentials 실패(6)'
git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git && (cd ComfyUI_Comfyroll_CustomNodes && git checkout d78b780) || echo '⚠️ Comfyroll 실패(7)'
git clone https://github.com/city96/ComfyUI-GGUF.git && (cd ComfyUI-GGUF && git checkout 795e451) || echo '⚠️ GGUF 실패(8)'
git clone https://github.com/yolain/ComfyUI-Easy-Use.git && (cd ComfyUI-Easy-Use && git checkout 23d9c36) || echo '⚠️ EasyUse 실패(9)'
git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && (cd ComfyUI-VideoHelperSuite && git checkout 3234937) || echo '⚠️ VideoHelper 실패(10)'
git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git && (cd comfyui_controlnet_aux && git checkout 59b027e088c1c8facf7258f6e392d16d204b4d27) || echo '⚠️ controlnet_aux 실패'
git clone https://github.com/chflame163/ComfyUI_LayerStyle.git && (cd ComfyUI_LayerStyle && git checkout 5840264) || echo '⚠️ ComfyUI_LayerStyle 설치 실패(12)'
git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git && (cd ComfyUI-Frame-Interpolation && git checkout a969c01dbccd9e5510641be04eb51fe93f6bfc3d) || echo '⚠️ Frame-Interpolation 실패'
git clone https://github.com/ltdrdata/ComfyUI-Impact-Pack.git && (cd ComfyUI-Impact-Pack && git checkout 51b7dcd) || echo '⚠️ Impact-Pack 실패(13)'



)



############################################
# 📦 모델 다운로드 (Upscale 모델 등)
############################################
echo '📦 모델 다운로드 시작'
mkdir -p /workspace/ComfyUI/models/upscale_models
if [ ! -f "/workspace/ComfyUI/models/upscale_models/4x-UltraSharpV2.safetensors" ]; then
    echo "⬇️ 4x-UltraSharpV2.safetensors 다운로드 중..."
    wget -q --show-progress -O /workspace/ComfyUI/models/upscale_models/4x-UltraSharpV2.safetensors "https://huggingface.co/Kim2091/UltraSharpV2/resolve/main/4x-UltraSharpV2.safetensors" || echo "⚠️ 4x-UltraSharpV2 다운로드 실패"
else
    echo "⏩ 4x-UltraSharpV2.safetensors 이미 존재함"
fi
############################################
# ⬇⬇⬇⬇⬇⬇⬇⬇⬇⬇⬇⬇⬇⬇⬇⬇
# 👉 기존 init 구조 (그대로 유지)
############################################

cd /workspace/ComfyUI/custom_nodes || {
  echo "⚠️ custom_nodes 디렉토리 없음. ComfyUI 설치 전일 수 있음"
  exit 0
}

for d in */; do
  req_file="${d}requirements.txt"
  marker_file="${d}.installed"

  if [ -f "$req_file" ]; then
    if [ -f "$marker_file" ]; then
      echo "⏩ $d 이미 설치됨, 건너뜀"
      continue
    fi

    echo "📦 $d 의존성 설치 중..."
    if pip install -r "$req_file"; then
      touch "$marker_file"
    else
      echo "⚠️ $d 의존성 설치 실패 (무시하고 진행)"
    fi
  fi
done



echo "✅ 모든 커스텀 노드 의존성 복구 완료"
echo "🚀 다음 단계로 넘어갑니다"
echo -e "\n====🎓 AI 교육 & 커뮤니티 안내====\n"
echo -e "1. Youtube : https://www.youtube.com/@A01demort"
echo "2. 교육 문의 : https://a01demort.com"
echo "3. CLASSU 강의 : https://classu.co.kr/me/19375"
echo "4. Stable AI KOREA : https://cafe.naver.com/sdfkorea"
echo "5. 카카오톡 오픈채팅방 : https://open.kakao.com/o/gxvpv2Mf"
echo "6. CIVITAI : https://civitai.com/user/a01demort"
echo -e "\n==================================="

# /workspace/A1/startup_banner.sh -> Dockerfile에서 병렬 실행으로 변경됨
