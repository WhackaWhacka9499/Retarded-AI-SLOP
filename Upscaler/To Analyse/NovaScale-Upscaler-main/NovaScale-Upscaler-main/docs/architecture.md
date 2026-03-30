# NovaScale: Professional Real-Time Upscaling Architecture

## 1. Overview
NovaScale is a high-performance, anti-cheat safe upscaling solution for Windows. It utilizes the **DXGI Desktop Duplication API** for non-invasive frame capture and **DirectX 11 / CUDA** for real-time reconstruction. Unlike traditional upscalers integrated into games, NovaScale operates at a system level, making it compatible with any windowed application.

## 2. Core Components

### A. Capture Engine (`capture_dxgi.c`)
- Uses `IDXGIOutputDuplication` to capture the desktop contents.
- Specifically targets the window of interest or the primary display.
- Implements a low-latency "wait-for-vblank" mechanism to minimize input lag.
- Handles resolution changes and frame size mismatches dynamically.

### B. Upscaling Pipeline (`upscale_spatial.c` & `upscale_cnn.c`)
- **Spatial Mode**: A re-implementation of edge-adaptive upscaling. 
  - **EASU (Edge-Adaptive Spatial Upsampling)**: Detects local gradients to reconstruct high-frequency details.
  - **RCAS (Robust Contrast-Adaptive Sharpening)**: Fine-tunes sharpness without introducing "ringing" artifacts or excessive noise.
- **CNN Mode (Experimental)**: 
  - A 4-6 layer convolutional neural network optimized for NVIDIA Pascal (GTX 10xx) and newer.
  - Utilizes CUDA-D3D11 interoperability to process textures without CPU round-trips.
  - Optimized for 720p to 1080p and 1080p to 1440p transitions.

### C. Presentation Engine (`present_dx11.c`)
- Creates a transparent, borderless fullscreen window that overlays the entire screen.
- Employs a `DXGI_SWAP_EFFECT_FLIP_DISCARD` swap chain for maximum performance.
- Direct Flip support ensures the upscaled content is presented with minimal OS compositor overhead.

### D. Python UI (PySide6)
- Modern, Qt6-based interface.
- Communicates with the C core via a stable C ABI (`ctypes`).
- Provides real-time controls for sharpness, scale factors, and mode switching.

## 3. Anti-Cheat Safety Protocol
NovaScale is designed to be **Zero-Entry**.
1. **No Hooks**: No `SetWindowsHookEx`, `Detours`, or VTable swapping.
2. **No Injection**: No DLLs are injected into third-party processes.
3. **No Memory Access**: The application never reads from or writes to the memory of game processes.
4. **Standard APIs**: Uses standard Windows APIs (DXGI, D3D11) that are whitelist-safe in competitive environments (same as OBS and Discord).

## 4. Performance Goals
| GPU | Mode | Latency | Target |
| :--- | :--- | :--- | :--- |
| GTX 1060 | Spatial | < 3ms | 720p -> 1080p |
| GTX 1060 | CNN | < 8ms | 720p -> 1080p |
| RTX 3060+ | Spatial | < 1ms | 1080p -> 4K |

## 5. Technology Stack
- **Language**: Core (C11), UI (Python 3.10+).
- **Graphics**: HLSL (Compute Shaders), CUDA (Compute 6.1+).
- **UI Framework**: PySide6 (Qt 6.4+).
- **OS**: Windows 10/11 (DXGI 1.2+).
