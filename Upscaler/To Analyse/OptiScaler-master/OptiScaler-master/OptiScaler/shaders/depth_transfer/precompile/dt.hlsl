#ifdef VK_MODE
[[vk::binding(0, 0)]]
#endif
RWTexture2D<float> SourceTexture : register(u0);

#ifdef VK_MODE
[[vk::binding(1, 0)]]
#endif
RWTexture2D<float> DestinationTexture : register(u1);

// Shader to perform the conversion
[numthreads(16, 16, 1)]
void CSMain(uint3 dispatchThreadID : SV_DispatchThreadID)
{
    float srcColor = SourceTexture[dispatchThreadID.xy];
    DestinationTexture[dispatchThreadID.xy] = srcColor;
}