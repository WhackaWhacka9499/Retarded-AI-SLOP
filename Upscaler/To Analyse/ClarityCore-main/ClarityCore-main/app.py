import streamlit as st
import streamlit.components.v1 as components

# 1. This must be the first Streamlit command
st.set_page_config(page_title="ClarityCore", page_icon="🖼️")

# 2. Inject the verification tag into the app's frontend
components.html(
    """
    <script>
        var meta = document.createElement('meta');
        meta.name = "google-site-verification";
        meta.content="hPP59Rl7Sok5J4vVLS6lh_aKUsXdp2w1DH8r0VtmorQ";
        document.getElementsByTagName('head')[0].appendChild(meta);
    </script>
    """,
    height=0,
)
import cv2
import numpy as np
import os

# The name of the file Google gave you
FILE_NAME = "googlefdb548e1a93d50a6.html" 

# The content inside that file (usually just 'google-site-verification: google123456789.html')
FILE_CONTENT = "google-site-verification: googlefdb548e1a93d50a6.html"

# This creates the file in the root directory during deployment
if not os.path.exists(FILE_NAME):
    with open(FILE_NAME, "w") as f:
        f.write(FILE_CONTENT)
import requests
import threading
import time

# 1. Setup Page
st.set_page_config(page_title="ClarityCore | Image Upscaler", page_icon="🖼️", layout="wide")

# 2. Prevent crashes with multiple users
if 'lock' not in st.session_state:
    st.session_state.lock = threading.Lock()

if 'processed_image' not in st.session_state:
    st.session_state.processed_image = None

# 3. FAST Model Loader (Checks local file first)
@st.cache_resource(show_spinner=False)
def load_sr_model():
    model_dir = "models"
    model_path = os.path.join(model_dir, "FSRCNN_x4.pb")
    
    if not os.path.exists(model_dir):
        os.makedirs(model_dir)
        
    if not os.path.exists(model_path):
        url = "https://github.com/Saafke/FSRCNN_Tensorflow/raw/master/models/FSRCNN_x4.pb"
        try:
            response = requests.get(url, timeout=15)
            with open(model_path, "wb") as f:
                f.write(response.content)
        except:
            return None

    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(model_path)
    sr.setModel("fsrcnn", 4)
    # CPU specific optimizations for speed
    sr.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    sr.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    return sr

def to_bytes(image, fmt):
    if fmt == 'PNG':
        _, b = cv2.imencode('.png', image, [cv2.IMWRITE_PNG_COMPRESSION, 4])
    else:
        temp = image[:, :, :3] if len(image.shape) > 2 and image.shape[2] == 4 else image
        _, b = cv2.imencode('.jpg', temp, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return b.tobytes()

# 4. User Interface
st.title("🚀 ClarityCore AI Upscaler")
st.markdown("Upload Your Images to Upscale By AI Powered ClarityCore Upscaler.")

file = st.sidebar.file_uploader("Upload Image", type=['jpg', 'png', 'jpeg'])

if file:
    # Decode image once
    file_bytes = np.asarray(bytearray(file.read()), dtype=np.uint8)
    img = cv2.imdecode(file_bytes, cv2.IMREAD_UNCHANGED)
    
    if img is not None:
        target_width = st.sidebar.slider("Target Width", 800, 3840, 1920)
        
        if st.sidebar.button("⚡ Upscale", type="primary", use_container_width=True):
            start_time = time.time()
            with st.session_state.lock:
                with st.spinner("AI Processing..."):
                    sr = load_sr_model()
                    if sr:
                        # Prepare work image
                        h, w = img.shape[:2]
                        work_img = img[:,:,:3] if len(img.shape) > 2 and img.shape[2] == 4 else img
                        
                        # AI Reconstruction
                        upscaled = sr.upsample(work_img)
                        
                        # Fast Polish & Final Scale
                        result = cv2.resize(upscaled, (target_width, int(target_width*(h/w))), interpolation=cv2.INTER_LANCZOS4)
                        
                        if len(img.shape) > 2 and img.shape[2] == 4:
                            alpha = cv2.resize(img[:, :, 3], (result.shape[1], result.shape[0]), interpolation=cv2.INTER_AREA)
                            result = cv2.merge([result, alpha])
                        
                        st.session_state.processed_image = result
                        st.sidebar.success(f"Fixed in {time.time() - start_time:.2f}s")

        # --- RESTORED DIFFERENCE LOGIC (Side-by-Side) ---
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Original Image")
            disp_orig = cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA if len(img.shape)>2 and img.shape[2]==4 else cv2.COLOR_BGR2RGB)
            st.image(disp_orig, use_container_width=True)
            
        with col2:
            st.subheader("Upscaled Image")
            if st.session_state.processed_image is not None:
                res = st.session_state.processed_image
                disp_res = cv2.cvtColor(res, cv2.COLOR_BGRA2RGBA if len(res.shape)>2 and res.shape[2]==4 else cv2.COLOR_BGR2RGB)
                st.image(disp_res, use_container_width=True)
                
                # Optimized Download Buttons
                st.download_button("Download PNG", to_bytes(res, 'PNG'), "clarity_4k.png", "image/png", use_container_width=True)
                st.download_button("Download JPG", to_bytes(res, 'JPG'), "clarity_4k.jpg", "image/jpeg", use_container_width=True)
else:
    st.info("Upload an image to Upscale it.")