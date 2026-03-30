#ifdef VK_MODE
[[vk::binding(0, 0)]]
#endif
RWTexture2D<uint> SourceTexture : register(u0);

#ifdef VK_MODE
[[vk::binding(1, 0)]]
#endif
RWTexture2D<float> DestinationTexture : register(u1);

// Shader to perform the conversion
[numthreads(16, 16, 1)]
void CSMain(uint3 dispatchThreadID : SV_DispatchThreadID)
{
    uint srcColor = SourceTexture[dispatchThreadID.xy];
    DestinationTexture[dispatchThreadID.xy] = float(srcColor) / 131071.0f;
}