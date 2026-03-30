#pragma once
#include "SysUtils.h"
#include <d3dcompiler.h>

struct alignas(256) Constants
{
    int32_t srcWidth;
    int32_t srcHeight;
    int32_t destWidth;
    int32_t destHeight;
};

// Lanczos with luminance correction
inline static std::string downsampleCodeLanczos = R"(
#ifdef VK_MODE
cbuffer Params : register(b0, space0)
#else
cbuffer Params : register(b0)
#endif
{
    int _SrcWidth;
    int _SrcHeight;
    int _DstWidth;
    int _DstHeight;
};

#ifdef VK_MODE
[[vk::binding(1, 0)]]
#endif
Texture2D<float4> InputTexture : register(t0);

#ifdef VK_MODE
[[vk::binding(2, 0)]]
#endif
RWTexture2D<float4> OutputTexture : register(u0);

#ifdef VK_MODE
[[vk::binding(3, 0)]]
#endif
SamplerState LinearClampSampler : register(s0);

static int ClampInt(int v, int lo, int hi)
{
    return min(max(v, lo), hi);
}

static float Sinc(float x)
{
    x *= 3.1415926535f;
    if (abs(x) < 1e-5f)
    {
        return 1.0f;
    }
    return sin(x) / x;
}

static float Lanczos(float x, float a)
{
    float ax = abs(x);
    if (ax >= a)
    {
        return 0.0f;
    }
    return Sinc(x) * Sinc(x / a);
}

// Lanczos3: a = 3, radius 3 => 6 taps per axis
static const float A_LANCZOS = 3.0f;
static const int   TAP_COUNT = 6;
static const int   RADIUS    = 3;

[numthreads(8, 8, 1)]
void CSMain(uint3 id : SV_DispatchThreadID)
{
    uint ox = id.x;
    uint oy = id.y;
    if (ox >= (uint)_DstWidth || oy >= (uint)_DstHeight)
    {
        return;
    }

    float2 dst = float2((float)ox + 0.5f, (float)oy + 0.5f);
    float2 scale = float2((float)_SrcWidth / (float)_DstWidth,
                          (float)_SrcHeight / (float)_DstHeight);

    float2 srcPos = dst * scale - 0.5f;

    float2 ip = floor(srcPos);
    float2 f  = srcPos - ip;

    // For radius 3, use taps at {-2,-1,0,1,2,3} around ip
    int2 base = (int2)ip - int2(RADIUS - 1, RADIUS - 1); // ip - 2

    float wx[TAP_COUNT];
    float wy[TAP_COUNT];
    float sumWx = 0.0f;
    float sumWy = 0.0f;

    [unroll]
    for (int i = 0; i < TAP_COUNT; ++i)
    {
        float dx = (float)i - (float)(RADIUS - 1) - f.x; // i - 2 - f.x
        wx[i] = Lanczos(dx, A_LANCZOS);
        sumWx += wx[i];

        float dy = (float)i - (float)(RADIUS - 1) - f.y; // i - 2 - f.y
        wy[i] = Lanczos(dy, A_LANCZOS);
        sumWy += wy[i];
    }

    float invSumWx = (sumWx != 0.0f) ? (1.0f / sumWx) : 0.0f;
    float invSumWy = (sumWy != 0.0f) ? (1.0f / sumWy) : 0.0f;

    [unroll]
    for (int i = 0; i < TAP_COUNT; ++i)
    {
        wx[i] *= invSumWx;
        wy[i] *= invSumWy;
    }

    float2 invSrc = 1.0f / float2((float)_SrcWidth, (float)_SrcHeight);

    float3 acc = 0.0f;

    // Min/max clamp (Lanczos rings more than bicubic)
    float3 mn = 1e30;
    float3 mx = -1e30;

    [unroll]
    for (int j = 0; j < TAP_COUNT; ++j)
    {
        int y = ClampInt(base.y + j, 0, _SrcHeight - 1);
        float wyj = wy[j];

        [unroll]
        for (int i = 0; i < TAP_COUNT; ++i)
        {
            int x = ClampInt(base.x + i, 0, _SrcWidth - 1);
            float w = wx[i] * wyj;

            // Sample at exact texel centers via UV
            float2 uv = (float2((float)x + 0.5f, (float)y + 0.5f)) * invSrc;
            float3 s = InputTexture.SampleLevel(LinearClampSampler, uv, 0.0f).rgb;

            mn = min(mn, s);
            mx = max(mx, s);

            acc += s * w;
        }
    }

    float3 outRgb = clamp(acc, mn, mx);
    OutputTexture[uint2(ox, oy)] = float4(outRgb, 1.0f);
}
)";

// Catmull-rom with luminance correction
inline static std::string downsampleCodeCatmull = R"(
#ifdef VK_MODE
cbuffer Params : register(b0, space0)
#else
cbuffer Params : register(b0)
#endif
{
    int _SrcWidth;
    int _SrcHeight;
    int _DstWidth;
    int _DstHeight;
};

#ifdef VK_MODE
[[vk::binding(1, 0)]]
#endif
Texture2D<float4> InputTexture : register(t0);

#ifdef VK_MODE
[[vk::binding(2, 0)]]
#endif
RWTexture2D<float4> OutputTexture : register(u0);

#ifdef VK_MODE
[[vk::binding(3, 0)]]
#endif
SamplerState LinearClampSampler : register(s0);

// Catmull–Rom = Keys bicubic with A = -0.5
static const float A = -0.45f;

static float CubicKeys(float x)
{
    x = abs(x);
    float x2 = x * x;
    float x3 = x2 * x;

    if (x < 1.0f)
    {
        return (A + 2.0f) * x3 - (A + 3.0f) * x2 + 1.0f;
    }
    else if (x < 2.0f)
    {
        return A * x3 - 5.0f * A * x2 + 8.0f * A * x - 4.0f * A;
    }
    return 0.0f;
}

// Convert 4 cubic taps into 2 bilinear taps on an axis.
static void BicubicAxis(float t, out float w01, out float w23, out float o01, out float o23)
{
    float w0 = CubicKeys(1.0f + t);
    float w1 = CubicKeys(t);
    float w2 = CubicKeys(1.0f - t);
    float w3 = CubicKeys(2.0f - t);

    w01 = w0 + w1;
    w23 = w2 + w3;

    float invW01 = (w01 != 0.0f) ? (1.0f / w01) : 0.0f;
    float invW23 = (w23 != 0.0f) ? (1.0f / w23) : 0.0f;

    o01 = (-1.0f) + (w1 * invW01);
    o23 = ( 1.0f) + (w3 * invW23);
}

[numthreads(8, 8, 1)]
void CSMain(uint3 id : SV_DispatchThreadID)
{
    uint ox = id.x;
    uint oy = id.y;
    if (ox >= (uint)_DstWidth || oy >= (uint)_DstHeight)
        return;

    float2 dst = float2((float)ox + 0.5f, (float)oy + 0.5f);
    float2 scale = float2((float)_SrcWidth / (float)_DstWidth,
                          (float)_SrcHeight / (float)_DstHeight);

    float2 srcPos = dst * scale - 0.5f;

    float2 ip = floor(srcPos);
    float2 f  = srcPos - ip;

    float2 base = ip - 1.0f;

    float wx01, wx23, ox01, ox23;
    float wy01, wy23, oy01, oy23;
    BicubicAxis(f.x, wx01, wx23, ox01, ox23);
    BicubicAxis(f.y, wy01, wy23, oy01, oy23);

    float2 invSrc = 1.0f / float2((float)_SrcWidth, (float)_SrcHeight);

    float2 uv00 = (base + float2(ox01, oy01) + 0.5f) * invSrc;
    float2 uv10 = (base + float2(ox23, oy01) + 0.5f) * invSrc;
    float2 uv01 = (base + float2(ox01, oy23) + 0.5f) * invSrc;
    float2 uv11 = (base + float2(ox23, oy23) + 0.5f) * invSrc;

    float3 s00 = InputTexture.SampleLevel(LinearClampSampler, uv00, 0.0f).rgb;
    float3 s10 = InputTexture.SampleLevel(LinearClampSampler, uv10, 0.0f).rgb;
    float3 s01 = InputTexture.SampleLevel(LinearClampSampler, uv01, 0.0f).rgb;
    float3 s11 = InputTexture.SampleLevel(LinearClampSampler, uv11, 0.0f).rgb;

    float3 outRgb =
        (s00 * wx01 + s10 * wx23) * wy01 +
        (s01 * wx01 + s11 * wx23) * wy23;

    // Cheap clamp (recommended for HDR stability)
    float3 mn = min(min(s00, s10), min(s01, s11));
    float3 mx = max(max(s00, s10), max(s01, s11));
    outRgb = clamp(outRgb, mn, mx);

    OutputTexture[uint2(ox, oy)] = float4(outRgb, 1.0f);
}
)";

// Bicubic
inline static std::string downsampleCodeBC = R"(
#ifdef VK_MODE
cbuffer Params : register(b0, space0)
#else
cbuffer Params : register(b0)
#endif
{
    int _SrcWidth;
    int _SrcHeight;
    int _DstWidth;
    int _DstHeight;
};

#ifdef VK_MODE
[[vk::binding(1, 0)]]
#endif
Texture2D<float4> InputTexture : register(t0);

#ifdef VK_MODE
[[vk::binding(2, 0)]]
#endif
RWTexture2D<float4> OutputTexture : register(u0);

#ifdef VK_MODE
[[vk::binding(3, 0)]]
#endif
SamplerState LinearClampSampler : register(s0);

// Keys bicubic parameter:
//  -0.5 = Catmull-Rom (sharper)
//  -0.75 = smoother (more Mitchell-ish feel, less ringing)
// You can tune this constant. No extra cbuffer needed.
static const float A = -0.6f;

// Keys cubic weight for distance x in [0,2)
static float CubicKeys(float x)
{
    x = abs(x);
    float x2 = x * x;
    float x3 = x2 * x;

    if (x < 1.0f)
    {
        return (A + 2.0f) * x3 - (A + 3.0f) * x2 + 1.0f;
    }
    else if (x < 2.0f)
    {
        return A * x3 - 5.0f * A * x2 + 8.0f * A * x - 4.0f * A;
    }
    return 0.0f;
}

// Compute two bilinear sample positions and their combined weights from 4 cubic taps.
// This is the standard “4 taps via 2 bilinear taps per axis” trick.
static void BicubicAxis(float t, out float w01, out float w23, out float o01, out float o23)
{
    // t is fractional part in [0,1)
    float w0 = CubicKeys(1.0f + t);
    float w1 = CubicKeys(t);
    float w2 = CubicKeys(1.0f - t);
    float w3 = CubicKeys(2.0f - t);

    w01 = w0 + w1;
    w23 = w2 + w3;

    // Avoid division by zero; in practice w01/w23 should be >0 for these kernels.
    float invW01 = (w01 != 0.0f) ? (1.0f / w01) : 0.0f;
    float invW23 = (w23 != 0.0f) ? (1.0f / w23) : 0.0f;

    // Offsets relative to the “base” texel index (floor(pos) - 1)
    // These produce the correct mix of the two texels in each bilinear pair.
    o01 = (-1.0f) + (w1 * invW01); // between base+0 and base+1
    o23 = ( 1.0f) + (w3 * invW23); // between base+2 and base+3
}

[numthreads(8, 8, 1)]
void CSMain(uint3 id : SV_DispatchThreadID)
{
    uint ox = id.x;
    uint oy = id.y;
    if (ox >= (uint)_DstWidth || oy >= (uint)_DstHeight)
        return;

    // Map destination pixel center to source pixel space (center-aligned)
    float2 dst = float2((float)ox + 0.5f, (float)oy + 0.5f);
    float2 scale = float2((float)_SrcWidth / (float)_DstWidth,
                          (float)_SrcHeight / (float)_DstHeight);

    // Source position in texel space, with texel centers at i+0.5
    float2 srcPos = dst * scale - 0.5f;

    float2 ip = floor(srcPos);
    float2 f  = srcPos - ip;

    // Bicubic uses 4 taps: base = ip - 1
    float2 base = ip - 1.0f;

    float wx01, wx23, ox01, ox23;
    float wy01, wy23, oy01, oy23;
    BicubicAxis(f.x, wx01, wx23, ox01, ox23);
    BicubicAxis(f.y, wy01, wy23, oy01, oy23);

    // Convert texel-space sample positions to UV (normalized)
    float2 invSrc = 1.0f / float2((float)_SrcWidth, (float)_SrcHeight);

    float2 uv00 = (base + float2(ox01, oy01) + 0.5f) * invSrc;
    float2 uv10 = (base + float2(ox23, oy01) + 0.5f) * invSrc;
    float2 uv01 = (base + float2(ox01, oy23) + 0.5f) * invSrc;
    float2 uv11 = (base + float2(ox23, oy23) + 0.5f) * invSrc;

    // 4 bilinear samples (each bilinear internally mixes a 2x2 quad)
    float3 s00 = InputTexture.SampleLevel(LinearClampSampler, uv00, 0.0f).rgb;
    float3 s10 = InputTexture.SampleLevel(LinearClampSampler, uv10, 0.0f).rgb;
    float3 s01 = InputTexture.SampleLevel(LinearClampSampler, uv01, 0.0f).rgb;
    float3 s11 = InputTexture.SampleLevel(LinearClampSampler, uv11, 0.0f).rgb;

    // Combine separably
    float3 outRgb =
        (s00 * wx01 + s10 * wx23) * wy01 +
        (s01 * wx01 + s11 * wx23) * wy23;

    // Cheap clamp (helps prevent HDR undershoot-looking pinholes)
    float3 mn = min(min(s00, s10), min(s01, s11));
    float3 mx = max(max(s00, s10), max(s01, s11));
    outRgb = clamp(outRgb, mn, mx);

    OutputTexture[uint2(ox, oy)] = float4(outRgb, 1.0f);
}
)";

// Magic kernel
inline static std::string downsampleCodeMAGIC = R"(
#ifdef VK_MODE
cbuffer Params : register(b0, space0)
#else
cbuffer Params : register(b0)
#endif
{
    int _SrcWidth;
    int _SrcHeight;
    int _DstWidth;
    int _DstHeight;
};

#ifdef VK_MODE
[[vk::binding(1, 0)]]
#endif
Texture2D<float4> InputTexture : register(t0);

#ifdef VK_MODE
[[vk::binding(2, 0)]]
#endif
RWTexture2D<float4> OutputTexture : register(u0);

static int ClampInt(int v, int lo, int hi) { return min(max(v, lo), hi); }

static float MagicKernel(float x)
{
    float ax = abs(x);
    if (ax >= 1.5f) return 0.0f;

    if (x <= -0.5f)
    {
        float t = x + 1.5f;
        return 0.5f * t * t;
    }
    else if (x < 0.5f)
    {
        return 0.75f - x * x;
    }
    else
    {
        float t = x - 1.5f;
        return 0.5f * t * t;
    }
}

static const float R = 1.5f;

#define TILE_SIZE 32
#define MAX_TAPS  12

// Use float4 for better LDS alignment; ignore .w
groupshared float4 lds_input[TILE_SIZE][TILE_SIZE];

[numthreads(8, 8, 1)]
void CSMain(uint3 id : SV_DispatchThreadID,
            uint3 groupID : SV_GroupID,
            uint3 tid : SV_GroupThreadID)
{
    // Early out if group is completely outside (still safe to return early,
    // but we need LDS load for in-bounds threads; easiest is per-thread after load.)
    float2 srcDim = float2(_SrcWidth, _SrcHeight);
    float2 dstDim = float2(_DstWidth, _DstHeight);
    float2 k = dstDim / srcDim; // k < 1 for downsample

    // ----------------------------
    // 1) Compute the source tile footprint for the *whole* 8x8 output block.
    // Output pixels covered by this group: [base .. base+7] in each axis.
    // We need the min lower bound from (base) and the max upper bound from (base+7).
    // Condition: |k*(x+0.5) - (o+0.5)| < R
    // Lower: x > ((o+0.5)-R)/k - 0.5
    // Upper: x < ((o+0.5)+R)/k - 0.5
    // ----------------------------
    int2 outBase = int2(groupID.xy * 8);

    float2 oMin = float2(outBase) + 0.5f;
    float2 oMax = float2(outBase + int2(7, 7)) + 0.5f;

    float2 gLowerF = (oMin - R) / k - 0.5f;
    float2 gUpperF = (oMax + R) / k - 0.5f;

    int2 g0 = (int2)ceil(gLowerF);
    int2 g1 = (int2)floor(gUpperF);

    // Desired tile extents in source space
    int tileW = g1.x - g0.x + 1;
    int tileH = g1.y - g0.y + 1;

    // Clamp tile size to LDS capacity (should be <= 32 for k>=1/3; if not, we truncate)
    tileW = ClampInt(tileW, 1, TILE_SIZE);
    tileH = ClampInt(tileH, 1, TILE_SIZE);

    // Choose tile start so that it stays in-bounds and still covers as much of [g0..g1] as possible.
    // If g0 is negative, start at 0. If g1 exceeds src-1, shift start left/up.
    int2 tileStart;
    tileStart.x = g0.x;
    tileStart.y = g0.y;

    // Ensure tileStart so that [tileStart .. tileStart+tileW-1] within [0..Src-1]
    tileStart.x = ClampInt(tileStart.x, 0, max(_SrcWidth  - tileW, 0));
    tileStart.y = ClampInt(tileStart.y, 0, max(_SrcHeight - tileH, 0));

    // ----------------------------
    // 2) Cooperative load into LDS: only load tileW*tileH texels (not always 32x32).
    // ----------------------------
    uint lane = tid.y * 8u + tid.x;
    uint total = (uint)(tileW * tileH);

    for (uint idx = lane; idx < total; idx += 64u)
    {
        int lx = (int)(idx % (uint)tileW);
        int ly = (int)(idx / (uint)tileW);

        int2 srcPos = tileStart + int2(lx, ly);
        // tileStart already chosen in-bounds, so srcPos is in-bounds; clamp is cheap insurance:
        srcPos.x = ClampInt(srcPos.x, 0, _SrcWidth  - 1);
        srcPos.y = ClampInt(srcPos.y, 0, _SrcHeight - 1);

        lds_input[ly][lx] = InputTexture.Load(int3(srcPos, 0));
    }

    GroupMemoryBarrierWithGroupSync();

    // ----------------------------
    // 3) Per-thread pixel work
    // ----------------------------
    if (id.x >= (uint)_DstWidth || id.y >= (uint)_DstHeight)
        return;

    float2 o = float2(id.xy) + 0.5f;

    // Compute per-pixel bounds
    float2 lowerF = (o - R) / k - 0.5f;
    float2 upperF = (o + R) / k - 0.5f;

    int2 x0y0 = (int2)ceil(lowerF);
    int2 x1y1 = (int2)floor(upperF);

    // Clamp to source bounds
    x0y0.x = ClampInt(x0y0.x, 0, _SrcWidth  - 1);
    x1y1.x = ClampInt(x1y1.x, 0, _SrcWidth  - 1);
    x0y0.y = ClampInt(x0y0.y, 0, _SrcHeight - 1);
    x1y1.y = ClampInt(x1y1.y, 0, _SrcHeight - 1);

    int nx = x1y1.x - x0y0.x + 1;
    int ny = x1y1.y - x0y0.y + 1;

    // Safety: for k>=1/3, nx,ny should be <= 9 for Magic. We keep MAX_TAPS=12 headroom.
    nx = ClampInt(nx, 1, MAX_TAPS);
    ny = ClampInt(ny, 1, MAX_TAPS);

    float wx[MAX_TAPS];
    float wy[MAX_TAPS];
    float sumWx = 0.0f;
    float sumWy = 0.0f;

    [unroll]
    for (int i = 0; i < MAX_TAPS; ++i)
    {
        if (i < nx)
        {
            int sx = x0y0.x + i;
            float u = k.x * ((float)sx + 0.5f) - o.x;
            float w = MagicKernel(u);
            wx[i] = w;
            sumWx += w;
        }
        else wx[i] = 0.0f;

        if (i < ny)
        {
            int sy = x0y0.y + i;
            float v = k.y * ((float)sy + 0.5f) - o.y;
            float w = MagicKernel(v);
            wy[i] = w;
            sumWy += w;
        }
        else wy[i] = 0.0f;
    }

    float invSumWx = (sumWx > 0.0f) ? (1.0f / sumWx) : 0.0f;
    float invSumWy = (sumWy > 0.0f) ? (1.0f / sumWy) : 0.0f;

    // LDS offsets: since the group tile was built to cover all group pixels' footprints,
    // these should be within [0..tileW/H). Clamp only for safety, but it should rarely hit.
    int baseLX = ClampInt(x0y0.x - tileStart.x, 0, tileW - nx);
    int baseLY = ClampInt(x0y0.y - tileStart.y, 0, tileH - ny);

    float3 acc = 0.0f;

    [loop]
    for (int j = 0; j < ny; ++j)
    {
        float wyj = wy[j] * invSumWy;
        int ly = baseLY + j;

        [unroll]
        for (int i = 0; i < MAX_TAPS; ++i)
        {
            if (i < nx)
            {
                float w = (wx[i] * invSumWx) * wyj;
                int lx = baseLX + i;

                acc += lds_input[ly][lx].rgb * w;
            }
        }
    }

    OutputTexture[id.xy] = float4(acc, 1.0f);
}
)";

inline static std::string upsampleCode = R"(
//
// Copyright (c) Microsoft. All rights reserved.
// This code is licensed under the MIT License (MIT).
// THIS CODE IS PROVIDED *AS IS* WITHOUT WARRANTY OF
// ANY KIND, EITHER EXPRESS OR IMPLIED, INCLUDING ANY
// IMPLIED WARRANTIES OF FITNESS FOR A PARTICULAR
// PURPOSE, MERCHANTABILITY, OR NON-INFRINGEMENT.
//
// Developed by Minigraph
//
// Author(s):  James Stanard
//

Texture2D<float3> Source : register(t0);
RWTexture2D<float3> Dest : register(u0);

cbuffer Params : register(b0)
{
	int _SrcWidth;
	int _SrcHeight;
	int _DstWidth;
	int _DstHeight;
};

#define TILE_DIM_X 16
#define TILE_DIM_Y 16

#define GROUP_COUNT (TILE_DIM_X * TILE_DIM_Y)

#define SAMPLES_X (TILE_DIM_X + 3)
#define SAMPLES_Y (TILE_DIM_Y + 3)

#define TOTAL_SAMPLES (SAMPLES_X * SAMPLES_Y)

// De-interleaved to avoid LDS bank conflicts
groupshared float g_R[TOTAL_SAMPLES];
groupshared float g_G[TOTAL_SAMPLES];
groupshared float g_B[TOTAL_SAMPLES];

float W1(float x, float A)
{
	return x * x * ((A + 2) * x - (A + 3)) + 1.0;
}

float W2(float x, float A)
{
	return A * (x * (x * (x - 5) + 8) - 4);
}

float4 ComputeWeights(float d1, float A)
{
	return float4(W2(1.0 + d1, A), W1(d1, A), W1(1.0 - d1, A), W2(2.0 - d1, A));
}

float4 GetBicubicFilterWeights(float offset, float A)
{
	//return ComputeWeights(offset, A);

	// Precompute weights for 16 discrete offsets
	static const float4 FilterWeights[16] =
	{
		ComputeWeights( 0.5 / 16.0, -0.5),
		ComputeWeights( 1.5 / 16.0, -0.5),
		ComputeWeights( 2.5 / 16.0, -0.5),
		ComputeWeights( 3.5 / 16.0, -0.5),
		ComputeWeights( 4.5 / 16.0, -0.5),
		ComputeWeights( 5.5 / 16.0, -0.5),
		ComputeWeights( 6.5 / 16.0, -0.5),
		ComputeWeights( 7.5 / 16.0, -0.5),
		ComputeWeights( 8.5 / 16.0, -0.5),
		ComputeWeights( 9.5 / 16.0, -0.5),
		ComputeWeights(10.5 / 16.0, -0.5),
		ComputeWeights(11.5 / 16.0, -0.5),
		ComputeWeights(12.5 / 16.0, -0.5),
		ComputeWeights(13.5 / 16.0, -0.5),
		ComputeWeights(14.5 / 16.0, -0.5),
		ComputeWeights(15.5 / 16.0, -0.5)
	};

	return FilterWeights[(uint)(offset * 16.0)];
}

// Store pixel to LDS (local data store)
void StoreLDS(uint LdsIdx, float3 rgb)
{
	g_R[LdsIdx] = rgb.r;
	g_G[LdsIdx] = rgb.g;
	g_B[LdsIdx] = rgb.b;
}

// Load four pixel samples from LDS.  Stride determines horizontal or vertical groups.
float3x4 LoadSamples(uint idx, uint Stride)
{
	uint i0 = idx, i1 = idx+Stride, i2 = idx+2*Stride, i3=idx+3*Stride;
	return float3x4(
		g_R[i0], g_R[i1], g_R[i2], g_R[i3],
		g_G[i0], g_G[i1], g_G[i2], g_G[i3],
		g_B[i0], g_B[i1], g_B[i2], g_B[i3]);
}

[numthreads(TILE_DIM_X, TILE_DIM_Y, 1)]
void CSMain(uint3 DTid : SV_DispatchThreadID, uint3 GTid : SV_GroupThreadID, uint3 Gid : SV_GroupID, uint GI : SV_GroupIndex)
{
	float scaleX = (float)_SrcWidth / (float)_DstWidth;
	float scaleY = (float)_SrcHeight / (float)_DstHeight;
	const float2 kRcpScale = float2(scaleX, scaleY);
	const float kA = 0.3f;
	
	// Number of samples needed from the source buffer to generate the output tile dimensions.
	const uint2 SampleSpace = ceil(float2(TILE_DIM_X, TILE_DIM_Y) * kRcpScale + 3.0);
	
	// Pre-Load source pixels
	int2 UpperLeft = floor((Gid.xy * uint2(TILE_DIM_X, TILE_DIM_Y) + 0.5) * kRcpScale - 1.5);

	for (uint i = GI; i < TOTAL_SAMPLES; i += GROUP_COUNT)
		StoreLDS(i, Source[UpperLeft + int2(i % SAMPLES_X, i / SAMPLES_Y)]);

	GroupMemoryBarrierWithGroupSync();

	// The coordinate of the top-left sample from the 4x4 kernel (offset by -0.5
	// so that whole numbers land on a pixel center.)  This is in source texture space.
	float2 TopLeftSample = (DTid.xy + 0.5) * kRcpScale - 1.5;

	// Position of samples relative to pixels used to evaluate the Sinc function.
	float2 Phase = frac(TopLeftSample);

	// LDS tile coordinate for the top-left sample (for this thread)
	uint2 TileST = int2(floor(TopLeftSample)) - UpperLeft;

	// Convolution weights, one per sample (in each dimension)
	float4 xWeights = GetBicubicFilterWeights(Phase.x, kA);
	float4 yWeights = GetBicubicFilterWeights(Phase.y, kA);

	// Horizontally convolve the first N rows
	uint ReadIdx = TileST.x + GTid.y * SAMPLES_X;

	uint WriteIdx = GTid.x + GTid.y * SAMPLES_X;
	StoreLDS(WriteIdx, mul(LoadSamples(ReadIdx, 1), xWeights));

	// If the source tile plus border is larger than the destination tile, we
	// have to convolve a few more rows.
	if (GI + GROUP_COUNT < SampleSpace.y * TILE_DIM_X)
	{
		ReadIdx += TILE_DIM_Y * SAMPLES_X;
		WriteIdx += TILE_DIM_Y * SAMPLES_X;
		StoreLDS(WriteIdx, mul(LoadSamples(ReadIdx, 1), xWeights));
	}

	GroupMemoryBarrierWithGroupSync();

	// Convolve vertically N columns
	ReadIdx = GTid.x + TileST.y * SAMPLES_X;
	float3 Result = mul(LoadSamples(ReadIdx, SAMPLES_X), yWeights);

	// Transform to display settings
	// Result = RemoveDisplayProfile(Result, LDR_COLOR_FORMAT);
	// Dest[DTid.xy] = ApplyDisplayProfile(Result, DISPLAY_PLANE_FORMAT);
	Dest[DTid.xy] = Result;
}
)";

inline static ID3DBlob* OS_CompileShader(const char* shaderCode, const char* entryPoint, const char* target)
{
    ID3DBlob* shaderBlob = nullptr;
    ID3DBlob* errorBlob = nullptr;

    HRESULT hr = D3DCompile(shaderCode, strlen(shaderCode), nullptr, nullptr, nullptr, entryPoint, target,
                            D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &shaderBlob, &errorBlob);

    if (FAILED(hr))
    {
        LOG_ERROR("error while compiling shader");

        if (errorBlob)
        {
            LOG_ERROR("error while compiling shader : {0}", (char*) errorBlob->GetBufferPointer());
            errorBlob->Release();
        }

        if (shaderBlob)
            shaderBlob->Release();

        return nullptr;
    }

    if (errorBlob)
        errorBlob->Release();

    return shaderBlob;
}
