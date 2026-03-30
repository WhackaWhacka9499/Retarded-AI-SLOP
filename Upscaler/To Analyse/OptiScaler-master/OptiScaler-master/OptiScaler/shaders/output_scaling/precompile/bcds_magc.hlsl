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

static int ClampInt(int v, int lo, int hi)
{
    return min(max(v, lo), hi);
}

static float MagicKernel(float x)
{
    float ax = abs(x);
    if (ax >= 1.5f)
        return 0.0f;

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

    int2 g0 = (int2) ceil(gLowerF);
    int2 g1 = (int2) floor(gUpperF);

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
    tileStart.x = ClampInt(tileStart.x, 0, max(_SrcWidth - tileW, 0));
    tileStart.y = ClampInt(tileStart.y, 0, max(_SrcHeight - tileH, 0));

    // ----------------------------
    // 2) Cooperative load into LDS: only load tileW*tileH texels (not always 32x32).
    // ----------------------------
    uint lane = tid.y * 8u + tid.x;
    uint total = (uint) (tileW * tileH);

    for (uint idx = lane; idx < total; idx += 64u)
    {
        int lx = (int) (idx % (uint) tileW);
        int ly = (int) (idx / (uint) tileW);

        int2 srcPos = tileStart + int2(lx, ly);
        // tileStart already chosen in-bounds, so srcPos is in-bounds; clamp is cheap insurance:
        srcPos.x = ClampInt(srcPos.x, 0, _SrcWidth - 1);
        srcPos.y = ClampInt(srcPos.y, 0, _SrcHeight - 1);

        lds_input[ly][lx] = InputTexture.Load(int3(srcPos, 0));
    }

    GroupMemoryBarrierWithGroupSync();

    // ----------------------------
    // 3) Per-thread pixel work
    // ----------------------------
    if (id.x >= (uint) _DstWidth || id.y >= (uint) _DstHeight)
        return;

    float2 o = float2(id.xy) + 0.5f;

    // Compute per-pixel bounds
    float2 lowerF = (o - R) / k - 0.5f;
    float2 upperF = (o + R) / k - 0.5f;

    int2 x0y0 = (int2) ceil(lowerF);
    int2 x1y1 = (int2) floor(upperF);

    // Clamp to source bounds
    x0y0.x = ClampInt(x0y0.x, 0, _SrcWidth - 1);
    x1y1.x = ClampInt(x1y1.x, 0, _SrcWidth - 1);
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
            float u = k.x * ((float) sx + 0.5f) - o.x;
            float w = MagicKernel(u);
            wx[i] = w;
            sumWx += w;
        }
        else
            wx[i] = 0.0f;

        if (i < ny)
        {
            int sy = x0y0.y + i;
            float v = k.y * ((float) sy + 0.5f) - o.y;
            float w = MagicKernel(v);
            wy[i] = w;
            sumWy += w;
        }
        else
            wy[i] = 0.0f;
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