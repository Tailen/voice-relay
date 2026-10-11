#pragma once
#import <Foundation/Foundation.h>
#import <CoreAudio/CoreAudio.h>
#import <CoreAudio/AudioHardwareTapping.h>
#import <CoreAudio/CATapDescription.h>
#import <AudioToolbox/AudioToolbox.h>
#include <mach/mach_time.h>
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

static inline void check(OSStatus status, const char *operation) {
    if (status)
        throw std::runtime_error(std::string(operation) + ": OSStatus " + std::to_string(status));
}
static inline AudioObjectPropertyAddress
addr(AudioObjectPropertySelector sel,
     AudioObjectPropertyScope scope = kAudioObjectPropertyScopeGlobal) {
    return {sel, scope, kAudioObjectPropertyElementMain};
}
template <typename T>
static inline T get(AudioObjectID id, AudioObjectPropertySelector sel,
             AudioObjectPropertyScope scope = kAudioObjectPropertyScopeGlobal) {
    T value{};
    UInt32 bytes = sizeof(value);
    auto a = addr(sel, scope);
    check(AudioObjectGetPropertyData(id, &a, 0, nullptr, &bytes, &value), "read property");
    if (bytes != sizeof(value))
        throw std::runtime_error("Unexpected property size");
    return value;
}
static inline std::vector<AudioObjectID>
objects(AudioObjectID id, AudioObjectPropertySelector sel,
        AudioObjectPropertyScope scope = kAudioObjectPropertyScopeGlobal) {
    auto a = addr(sel, scope);
    UInt32 bytes = 0;
    check(AudioObjectGetPropertyDataSize(id, &a, 0, nullptr, &bytes), "property array size");
    if (bytes % sizeof(AudioObjectID))
        throw std::runtime_error("Malformed property array");
    std::vector<AudioObjectID> values(bytes / sizeof(AudioObjectID));
    if (bytes)
        check(AudioObjectGetPropertyData(id, &a, 0, nullptr, &bytes, values.data()),
              "property array");
    return values;
}
static inline NSString *stringProperty(AudioObjectID id, AudioObjectPropertySelector sel) {
    return CFBridgingRelease(get<CFStringRef>(id, sel));
}
static inline AudioStreamBasicDescription deviceFormat(AudioObjectID id, AudioObjectPropertyScope scope) {
    auto streams = objects(id, kAudioDevicePropertyStreams, scope);
    if (streams.size() != 1)
        throw std::runtime_error(
            "This device layout is not supported. Select a mono or stereo output.");
    auto f = get<AudioStreamBasicDescription>(streams[0], kAudioStreamPropertyVirtualFormat);
    if (f.mFormatID != kAudioFormatLinearPCM || !(f.mFormatFlags & kAudioFormatFlagIsFloat) ||
        (f.mFormatFlags & kAudioFormatFlagIsBigEndian) || f.mBitsPerChannel != 32 ||
        f.mChannelsPerFrame < 1 || f.mChannelsPerFrame > 2 || f.mFramesPerPacket != 1 ||
        f.mSampleRate < 8000 || f.mSampleRate > 192000)
        throw std::runtime_error(
            "This device format is not supported. Select a float32 mono or stereo output.");
    const UInt32 frameBytes =
        4 * ((f.mFormatFlags & kAudioFormatFlagIsNonInterleaved) ? 1 : f.mChannelsPerFrame);
    if (f.mBytesPerFrame != frameBytes)
        throw std::runtime_error("Unexpected PCM padding");
    return f;
}
static inline UInt32 frameCount(const AudioBufferList *buffers, UInt32 channels) {
    if (!buffers || buffers->mNumberBuffers < 1 || buffers->mNumberBuffers > 2)
        return 0;
    UInt32 sum = 0, frames = 0;
    for (UInt32 b = 0; b < buffers->mNumberBuffers; ++b) {
        const auto &buffer = buffers->mBuffers[b];
        if (!buffer.mData || buffer.mNumberChannels == 0 || buffer.mNumberChannels > 2 ||
            buffer.mDataByteSize % (4 * buffer.mNumberChannels))
            return 0;
        UInt32 n = buffer.mDataByteSize / (4 * buffer.mNumberChannels);
        if (b && n != frames)
            return 0;
        frames = n;
        sum += buffer.mNumberChannels;
    }
    return sum == channels ? frames : 0;
}
static inline float sample(const AudioBufferList *buffers, UInt32 channel, UInt32 frame) {
    for (UInt32 b = 0; b < buffers->mNumberBuffers; ++b) {
        const auto &buffer = buffers->mBuffers[b];
        if (channel < buffer.mNumberChannels)
            return static_cast<const float *>(
                buffer.mData)[frame * buffer.mNumberChannels + channel];
        channel -= buffer.mNumberChannels;
    }
    return 0;
}
static inline void setSample(AudioBufferList *buffers, UInt32 channel, UInt32 frame, float value) {
    for (UInt32 b = 0; b < buffers->mNumberBuffers; ++b) {
        auto &buffer = buffers->mBuffers[b];
        if (channel < buffer.mNumberChannels) {
            static_cast<float *>(buffer.mData)[frame * buffer.mNumberChannels + channel] = value;
            return;
        }
        channel -= buffer.mNumberChannels;
    }
}
static inline void silence(AudioBufferList *buffers) {
    if (buffers)
        for (UInt32 b = 0; b < buffers->mNumberBuffers; ++b)
            if (buffers->mBuffers[b].mData)
                memset(buffers->mBuffers[b].mData, 0, buffers->mBuffers[b].mDataByteSize);
}
// Disable every input stream on physical output IOProcs before start. A USB
// headset may be duplex; ignoring the callback's input pointer alone is insufficient.
static inline void disablePhysicalInputs(AudioObjectID device, AudioDeviceIOProcID callback) {
    auto streams = objects(device, kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput);
    if (streams.empty())
        return;
    const size_t bytes =
        offsetof(AudioHardwareIOProcStreamUsage, mStreamIsOn) + streams.size() * sizeof(UInt32);
    std::vector<uint8_t> storage(bytes, 0);
    auto *usage = reinterpret_cast<AudioHardwareIOProcStreamUsage *>(storage.data());
    usage->mIOProc = reinterpret_cast<void *>(callback);
    usage->mNumberStreams = UInt32(streams.size());
    auto a = addr(kAudioDevicePropertyIOProcStreamUsage, kAudioObjectPropertyScopeInput);
    check(AudioObjectSetPropertyData(device, &a, 0, nullptr, UInt32(bytes), usage),
          "disable physical input streams");
    UInt32 returned = UInt32(bytes);
    check(AudioObjectGetPropertyData(device, &a, 0, nullptr, &returned, usage),
          "verify input streams disabled");
    if (returned != bytes || usage->mNumberStreams != streams.size())
        throw std::runtime_error("Input stream layout changed during configuration");
    for (UInt32 i = 0; i < usage->mNumberStreams; ++i)
        if (usage->mStreamIsOn[i])
            throw std::runtime_error("Input stream remained enabled; refusing playback");
}
struct DeviceWatch {
    AudioObjectID device = 0;
    std::atomic<bool> *fault = nullptr;
    std::vector<AudioObjectPropertyAddress> watched;
    static OSStatus changed(AudioObjectID, UInt32, const AudioObjectPropertyAddress *,
                            void *opaque) {
        static_cast<DeviceWatch *>(opaque)->fault->store(true);
        return noErr;
    }
    void start(AudioObjectID id, std::atomic<bool> &flag) {
        device = id;
        fault = &flag;
        for (auto a :
             {addr(kAudioDevicePropertyDeviceIsAlive), addr(kAudioDevicePropertyNominalSampleRate),
              addr(kAudioDevicePropertyDataSource, kAudioObjectPropertyScopeOutput),
              addr(kAudioDevicePropertyJackIsConnected, kAudioObjectPropertyScopeOutput),
              addr(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeOutput)}) {
            if (AudioObjectHasProperty(id, &a)) {
                check(AudioObjectAddPropertyListener(id, &a, changed, this), "watch pinned device");
                watched.push_back(a);
            }
        }
    }
    ~DeviceWatch() {
        for (auto &a : watched)
            AudioObjectRemovePropertyListener(device, &a, changed, this);
    }
};
struct IOHandle {
    AudioObjectID device = 0;
    AudioDeviceIOProcID callback = nullptr;
    bool started = false;
    void stop() {
        if (started) {
            AudioDeviceStop(device, callback);
            started = false;
        }
        if (callback) {
            AudioDeviceDestroyIOProcID(device, callback);
            callback = nullptr;
        }
    }
    ~IOHandle() {
        stop();
    }
};
