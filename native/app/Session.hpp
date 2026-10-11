#pragma once
#include "AudioSupport.hpp"
#include "AudioRing.hpp"
#include "Timing.hpp"
#include <thread>
#include <fcntl.h>

static NSArray<NSNumber *> *sourceProcesses(NSString *bundle, pid_t pid) {
    NSMutableArray *found = [NSMutableArray array];
    for (auto object : objects(kAudioObjectSystemObject, kAudioHardwarePropertyProcessObjectList)) {
        try {
            auto processPID = get<pid_t>(object, kAudioProcessPropertyPID);
            NSString *identifier = stringProperty(object, kAudioProcessPropertyBundleID);
            bool related =
                bundle.length && ([identifier isEqual:bundle] ||
                                  [identifier hasPrefix:[bundle stringByAppendingString:@"."]]);
            if (processPID != getpid() && (processPID == pid || related))
                [found addObject:@(object)];
        } catch (...) { /* A process can exit during enumeration. */
        }
    }
    return [found sortedArrayUsingSelector:@selector(compare:)];
}

struct AppSession {
    vrap::AudioRing<131072> input, output;
    std::atomic<bool> cancelled{false}, fault{false}, bypass{false};
    std::atomic<uint64_t> generation{0}, firstPacket{0}, firstGeneration{UINT64_MAX};
    std::atomic<uint64_t> underflows{0}, overflows{0}, processed{0};
    std::atomic<double> computeMS{0}, maxComputeMS{0}, inputPeak{0}, outputPeak{0}, latencyMS{0};
    std::atomic<uint64_t> deadlineMisses{0};
    std::atomic<uint64_t> sentPackets{0}, lastReplyTick{0};
    std::atomic<uint64_t> lastCaptureTick{0};
    std::atomic<double> maxReplyGapMS{0}, underrunReplyGapMS{0}, underrunInputQueueMS{0};
    std::atomic<double> maxCaptureGapMS{0}, underrunCaptureGapMS{0};
    double playbackReserveMS = 180;
    bool primeByDeadline = true;
    double targetLatencyMS = 600;
    std::array<Timing, 16> timings;
    double inputRate = 48000, outputRate = 48000, msPerTick = 1, ticksPerInput = 1;
    UInt32 inputChannels = 2, outputChannels = 2;
    AudioObjectID tap = 0, aggregate = 0, outputDevice = 0;
    IOHandle captureIO, outputIO;
    std::unique_ptr<DeviceWatch> watch;
    NSTask *__strong worker = nil;
    NSPipe *__strong requests = nil, *__strong replies = nil;
    int readFD = -1, writeFD = -1;
    std::thread writer, reader;
    std::mutex errorMutex;
    std::string error;
    uint64_t playbackGeneration = UINT64_MAX;
    bool primed = false;

    void fail(const std::string &message) {
        {
            std::lock_guard<std::mutex> lock(errorMutex);
            if (error.empty())
                error = message;
        }
        fault.store(true);
    }
    std::string failure() {
        std::lock_guard<std::mutex> lock(errorMutex);
        if (underflows.load())
            return "Playback underrun: the next audio packet missed its deadline. Press Start to "
                   "reconnect.";
        return error.empty()
                   ? "Audio device changed or a buffer could not keep up. Press Start to reconnect."
                   : error;
    }
    bool transfer(int fd, void *data, size_t length, bool writing) {
        auto *bytes = static_cast<uint8_t *>(data);
        uint64_t begin = mach_absolute_time();
        while (length && !cancelled.load() && !fault.load()) {
            pollfd p{fd, short(writing ? POLLOUT : POLLIN), 0};
            int ready = poll(&p, 1, 100);
            if (ready < 0 && errno == EINTR)
                continue;
            if (ready < 0 || (mach_absolute_time() - begin) * msPerTick > 180000)
                return false;
            if (!ready)
                continue;
            ssize_t n = writing ? write(fd, bytes, length) : read(fd, bytes, length);
            if (n < 0 && (errno == EINTR || errno == EAGAIN))
                continue;
            if (n <= 0)
                return false;
            bytes += n;
            length -= size_t(n);
        }
        return length == 0;
    }
    bool send(NSDictionary *header, const std::vector<float> &pcm = {}) {
        NSData *json = [NSJSONSerialization dataWithJSONObject:header options:0 error:nullptr];
        if (!json || json.length > 16383)
            return false;
        std::vector<uint8_t> bytes(json.length + 1 + pcm.size() * 4);
        memcpy(bytes.data(), json.bytes, json.length);
        bytes[json.length] = '\n';
        if (!pcm.empty())
            memcpy(bytes.data() + json.length + 1, pcm.data(), pcm.size() * 4);
        return transfer(writeFD, bytes.data(), bytes.size(), true);
    }
    NSDictionary *receive(std::vector<float> &pcm) {
        std::string line;
        char c = 0;
        while (line.size() < 16384) {
            if (!transfer(readFD, &c, 1, false))
                return nil;
            if (c == '\n')
                break;
            line.push_back(c);
        }
        if (c != '\n')
            return nil;
        NSData *data = [NSData dataWithBytes:line.data() length:line.size()];
        id object = [NSJSONSerialization JSONObjectWithData:data options:0 error:nullptr];
        if (![object isKindOfClass:NSDictionary.class])
            return nil;
        NSDictionary *h = object;
        pcm.clear();
        if ([h[@"type"] isEqual:@"audio"]) {
            NSInteger n = [h[@"frames"] integerValue];
            if (n < 1 || n > outputRate || [h[@"channels"] intValue] != 2 ||
                [h[@"sample_rate"] doubleValue] != outputRate)
                return nil;
            pcm.resize(size_t(n) * 2);
            if (!transfer(readFD, pcm.data(), pcm.size() * 4, false))
                return nil;
        }
        return h;
    }
    void launchWorker(NSDictionary *config, NSString *reference) {
        worker = [[NSTask alloc] init];
        requests = [NSPipe pipe];
        replies = [NSPipe pipe];
        worker.executableURL = [NSURL fileURLWithPath:config[@"python"]];
        NSMutableArray *args = [NSMutableArray arrayWithArray:@[
            config[@"worker"], @"--root", config[@"root"], @"--input-rate",
            [NSString stringWithFormat:@"%.0f", inputRate], @"--output-rate",
            [NSString stringWithFormat:@"%.0f", outputRate], @"--reference", reference
        ]];
        if (bypass.load())
            [args addObject:@"--bypass"];
        worker.arguments = args;
        worker.standardInput = requests;
        worker.standardOutput = replies;
        worker.standardError = NSFileHandle.fileHandleWithStandardError;
        NSError *e = nil;
        if (![worker launchAndReturnError:&e])
            throw std::runtime_error(e.localizedDescription.UTF8String);
        [requests.fileHandleForReading closeFile];
        [replies.fileHandleForWriting closeFile];
        writeFD = requests.fileHandleForWriting.fileDescriptor;
        readFD = replies.fileHandleForReading.fileDescriptor;
        fcntl(readFD, F_SETFL, fcntl(readFD, F_GETFL) | O_NONBLOCK);
        fcntl(writeFD, F_SETFL, fcntl(writeFD, F_GETFL) | O_NONBLOCK);
        std::vector<float> ignored;
        NSDictionary *ready = receive(ignored);
        if (![ready[@"type"] isEqual:@"ready"])
            throw std::runtime_error(ready[@"message"]
                                         ? [ready[@"message"] UTF8String]
                                         : "The X-VC worker could not prepare the voice.");
        reader = std::thread([this] { readLoop(); });
        writer = std::thread([this] { writeLoop(); });
    }
    void writeLoop() {
        const size_t frames = size_t(inputRate / 50);
        uint64_t seq = 0, current = 0;
        std::vector<float> pcm;
        pcm.reserve(frames * 2);
        vrap::Frame f;
        while (!cancelled.load() && !fault.load()) {
            @autoreleasepool {
                uint64_t wanted = generation.load();
                if (wanted != current) {
                    current = wanted;
                    seq = 0;
                    pcm.clear();
                    if (!send(@{
                            @"op" : @"reset",
                            @"generation" : @(current),
                            @"bypass" : @(bypass.load())
                        }))
                        break;
                }
                if (!input.pop(f)) {
                    usleep(1000);
                    continue;
                }
                if (f.generation != current)
                    continue;
                pcm.push_back(f.left);
                pcm.push_back(f.right);
                if (pcm.size() != frames * 2)
                    continue;
                uint64_t end = uint64_t((f.captureTicks * msPerTick + 1000 / inputRate) * 1e6);
                if (!f.captureTicks || !send(
                                           @{
                                               @"op" : @"process",
                                               @"generation" : @(current),
                                               @"seq" : @(seq++),
                                               @"frames" : @(frames),
                                               @"channels" : @2,
                                               @"sample_rate" : @(inputRate),
                                               @"capture_ns" : @(end)
                                           },
                                           pcm))
                    break;
                pcm.clear();
                sentPackets++;
            }
        }
        if (!cancelled.load() && !fault.load())
            fail("The audio worker input pipe closed.");
    }
    void readLoop() {
        while (!cancelled.load() && !fault.load()) {
            @autoreleasepool {
                std::vector<float> pcm;
                NSDictionary *h = receive(pcm);
                if (!h) {
                    if (!cancelled.load() && !fault.load())
                        fail("The X-VC worker stopped unexpectedly.");
                    break;
                }
                if ([h[@"type"] isEqual:@"error"]) {
                    fail([h[@"message"] UTF8String]);
                    break;
                }
                if (![h[@"type"] isEqual:@"audio"])
                    continue;
                uint64_t gen = [h[@"generation"] unsignedLongLongValue];
                if (gen != generation.load())
                    continue;
                uint64_t now = mach_absolute_time(), previous = lastReplyTick.exchange(now);
                if (previous && firstGeneration.load() == gen)
                    maxReplyGapMS.store(
                        std::max(maxReplyGapMS.load(), (now - previous) * msPerTick));
                size_t count = pcm.size() / 2;
                // A warmup/resampler burst can contain multiple output hops.
                // Backpressure here is safe: this is the pipe reader, not an IOProc.
                uint64_t waitBegan = mach_absolute_time();
                while (output.size() + count > size_t(outputRate * .4) &&
                       gen == generation.load() && !cancelled.load() && !fault.load()) {
                    if ((mach_absolute_time() - waitBegan) * msPerTick > 1000) {
                        overflows++;
                        fail("Playback stopped consuming audio. Press Start to reconnect.");
                        break;
                    }
                    usleep(1000);
                }
                if (gen != generation.load())
                    continue;
                if (cancelled.load() || fault.load())
                    break;
                double ns = [h[@"capture_ns"] doubleValue],
                       end = [h[@"capture_end_ns"] doubleValue];
                if (end <= ns) {
                    fail("The worker returned an invalid audio timestamp.");
                    break;
                }
                double ticks = ns / 1e6 / msPerTick, step = (end - ns) / count / 1e6 / msPerTick;
                for (size_t i = 0; i < count; i++) {
                    if (!std::isfinite(pcm[2 * i]) || !std::isfinite(pcm[2 * i + 1]) ||
                        !output.push(
                            {pcm[2 * i], pcm[2 * i + 1], uint64_t(ticks + i * step), gen})) {
                        fail("The output audio buffer is invalid.");
                        break;
                    }
                }
                if (firstGeneration.load() != gen) {
                    if (gen < timings.size()) {
                        auto &t = timings[gen];
                        t.firstReply.store(mach_absolute_time());
                        t.firstCompute.store([h[@"compute_ms"] doubleValue]);
                        t.firstReplyAge.store((double(mach_absolute_time()) - ticks) * msPerTick);
                    }
                    firstPacket.store(mach_absolute_time());
                    firstGeneration.store(gen);
                }
                double elapsed = [h[@"compute_ms"] doubleValue];
                computeMS.store(elapsed);
                maxComputeMS.store(std::max(maxComputeMS.load(), elapsed));
                if (elapsed > 120)
                    deadlineMisses++;
                processed++;
            }
        }
    }
    static OSStatus capture(AudioObjectID, const AudioTimeStamp *, const AudioBufferList *in,
                            const AudioTimeStamp *time, AudioBufferList *, const AudioTimeStamp *,
                            void *ptr) {
        auto &s = *static_cast<AppSession *>(ptr);
        if (s.cancelled.load() || s.fault.load())
            return noErr;
        uint64_t now = mach_absolute_time(), previous = s.lastCaptureTick.exchange(now);
        if (previous)
            s.maxCaptureGapMS.store(
                std::max(s.maxCaptureGapMS.load(), (now - previous) * s.msPerTick));
        UInt32 n = frameCount(in, s.inputChannels);
        if (!n || !time || !(time->mFlags & kAudioTimeStampHostTimeValid)) {
            s.fault.store(true);
            return noErr;
        }
        if (s.input.size() + n > size_t(s.inputRate * .4)) {
            s.overflows++;
            s.fault.store(true);
            return noErr;
        }
        uint64_t gen = s.generation.load();
        for (UInt32 i = 0; i < n; i++) {
            float l = sample(in, 0, i), r = s.inputChannels == 2 ? sample(in, 1, i) : l;
            double peak = std::max(std::abs(l), std::abs(r));
            if (peak > s.inputPeak.load(std::memory_order_relaxed))
                s.inputPeak.store(peak, std::memory_order_relaxed);
            if (!std::isfinite(l) || !std::isfinite(r) ||
                !s.input.push({l, r, time->mHostTime + uint64_t(i * s.ticksPerInput), gen})) {
                s.fault.store(true);
                break;
            }
        }
        return noErr;
    }
    static OSStatus render(AudioObjectID, const AudioTimeStamp *, const AudioBufferList *,
                           const AudioTimeStamp *, AudioBufferList *out,
                           const AudioTimeStamp *outTime, void *ptr) {
        auto &s = *static_cast<AppSession *>(ptr);
        silence(out);
        if (s.cancelled.load() || s.fault.load())
            return noErr;
        auto gen = s.generation.load();
        vrap::Frame f;
        if (s.playbackGeneration != gen) {
            s.playbackGeneration = gen;
            s.primed = false;
        }
        while (s.output.peek(f) && f.generation != gen)
            s.output.pop(f);
        UInt32 n = frameCount(out, s.outputChannels);
        if (!n) {
            s.fault.store(true);
            return noErr;
        }
        if (!s.primed) {
            double hold = s.bypass.load() ? 40 : s.playbackReserveMS;
            if (s.firstGeneration.load() != gen)
                return noErr;
            if (s.primeByDeadline && !s.bypass.load()) {
                if (!outTime || !(outTime->mFlags & kAudioTimeStampHostTimeValid) ||
                    !s.output.peek(f) || s.output.size() < size_t(s.outputRate * .04))
                    return noErr;
                // Count all elapsed capture/model time toward the playout budget.
                // A slow first block must not add a second, fixed wait on top.
                if ((double(outTime->mHostTime) - double(f.captureTicks)) * s.msPerTick <
                    s.targetLatencyMS)
                    return noErr;
            } else if ((mach_absolute_time() - s.firstPacket.load()) * s.msPerTick < hold ||
                       s.output.size() < size_t(s.outputRate * .04))
                return noErr;
            s.primed = true;
            if (gen < s.timings.size()) {
                s.timings[gen].firstPlay.store(mach_absolute_time());
                s.timings[gen].firstDepth.store(s.output.size());
            }
        }
        for (UInt32 i = 0; i < n; i++) {
            if (!s.output.pop(f)) {
                s.underrunReplyGapMS.store((mach_absolute_time() - s.lastReplyTick.load()) *
                                           s.msPerTick);
                s.underrunCaptureGapMS.store((mach_absolute_time() - s.lastCaptureTick.load()) *
                                             s.msPerTick);
                s.underrunInputQueueMS.store(s.input.size() / s.inputRate * 1000);
                s.underflows++;
                s.fault.store(true);
                return noErr;
            }
            if (f.generation != gen || gen != s.generation.load())
                return noErr;
            if (i == 0 && outTime && (outTime->mFlags & kAudioTimeStampHostTimeValid)) {
                double delay = (double(outTime->mHostTime) - double(f.captureTicks)) * s.msPerTick;
                s.latencyMS.store(delay, std::memory_order_relaxed);
                if (gen < s.timings.size()) {
                    auto &t = s.timings[gen];
                    if (!t.samples.load())
                        t.firstScheduledAge.store(delay);
                    t.observe(delay);
                }
            }
            double peak = std::max(std::abs(f.left), std::abs(f.right));
            if (peak > s.outputPeak.load(std::memory_order_relaxed))
                s.outputPeak.store(peak, std::memory_order_relaxed);
            setSample(out, 0, i, s.outputChannels == 1 ? (f.left + f.right) * .5f : f.left);
            if (s.outputChannels == 2)
                setSample(out, 1, i, f.right);
        }
        return noErr;
    }
    void start(NSDictionary *config, NSString *bundle, pid_t pid, AudioObjectID device,
               NSString *reference, void (^status)(NSString *)) {
        outputDevice = device;
        auto format = deviceFormat(device, kAudioObjectPropertyScopeOutput);
        outputRate = format.mSampleRate;
        outputChannels = format.mChannelsPerFrame;
        mach_timebase_info_data_t tb{};
        mach_timebase_info(&tb);
        msPerTick = double(tb.numer) / tb.denom / 1e6;
        NSArray *ids = sourceProcesses(bundle, pid);
        while (!ids.count && !cancelled.load()) {
            status(@"Waiting for the selected app to open its audio stream…");
            usleep(250000);
            ids = sourceProcesses(bundle, pid);
        }
        if (cancelled.load())
            return;
        CATapDescription *desc = [[CATapDescription alloc] initStereoMixdownOfProcesses:ids];
        desc.name = @"VoiceRelay selected application";
        desc.privateTap = YES;
        desc.muteBehavior = CATapMutedWhenTapped;
        check(AudioHardwareCreateProcessTap(desc, &tap), "Create app audio tap");
        NSDictionary *definition = @{
            @kAudioAggregateDeviceNameKey : @"VoiceRelay Private Capture",
            @kAudioAggregateDeviceUIDKey :
                [@"local.voicerelay.capture." stringByAppendingString:NSUUID.UUID.UUIDString],
            @kAudioAggregateDeviceIsPrivateKey : @YES,
            @kAudioAggregateDeviceTapAutoStartKey : @YES,
            @kAudioAggregateDeviceTapListKey : @[ @{
                @kAudioSubTapUIDKey : desc.UUID.UUIDString,
                @kAudioSubTapDriftCompensationKey : @YES
            } ]
        };
        check(AudioHardwareCreateAggregateDevice((__bridge CFDictionaryRef)definition, &aggregate),
              "Create private capture device");
        auto inputFormat = deviceFormat(aggregate, kAudioObjectPropertyScopeInput);
        inputRate = inputFormat.mSampleRate;
        inputChannels = inputFormat.mChannelsPerFrame;
        ticksPerInput = 1000 / inputRate / msPerTick;
        status(@"Preparing X-VC and warming the voice…");
        launchWorker(config, reference);
        if (cancelled.load())
            return;
        captureIO.device = aggregate;
        outputIO.device = device;
        check(AudioDeviceCreateIOProcID(aggregate, capture, this, &captureIO.callback),
              "Create capture callback");
        check(AudioDeviceCreateIOProcID(device, render, this, &outputIO.callback),
              "Create output callback");
        disablePhysicalInputs(device, outputIO.callback);
        watch = std::make_unique<DeviceWatch>();
        watch->start(device, fault);
        check(AudioDeviceStart(aggregate, captureIO.callback),
              "Start app capture (allow system audio recording if prompted)");
        captureIO.started = true;
        check(AudioDeviceStart(device, outputIO.callback), "Start selected output");
        outputIO.started = true;
    }
    void setBypass(bool value) {
        bypass.store(value);
        generation.fetch_add(1);
    }
    void refreshSource(NSString *bundle, pid_t pid) {
        if (!tap || cancelled.load() || fault.load())
            return;
        NSArray *ids = sourceProcesses(bundle, pid);
        if (!ids.count) {
            fail("The selected source app closed. Open it and press Start.");
            return;
        }
        CFTypeRef raw = get<CFTypeRef>(tap, kAudioTapPropertyDescription);
        CATapDescription *description = CFBridgingRelease(raw);
        if (![description.processes isEqual:ids]) {
            description.processes = ids;
            auto a = addr(kAudioTapPropertyDescription);
            CFTypeRef update = (__bridge CFTypeRef)description;
            check(AudioObjectSetPropertyData(tap, &a, 0, nullptr, sizeof(update), &update),
                  "Update selected app audio processes");
        }
    }
    void stop() {
        cancelled.store(true);
        generation.fetch_add(1);
        outputIO.stop();
        captureIO.stop();
        watch.reset();
        if (aggregate) {
            AudioHardwareDestroyAggregateDevice(aggregate);
            aggregate = 0;
        }
        if (tap) {
            AudioHardwareDestroyProcessTap(tap);
            tap = 0;
        }
        if (writer.joinable())
            writer.join();
        if (reader.joinable())
            reader.join();
        @try {
            if (requests)
                [requests.fileHandleForWriting closeFile];
            if (replies)
                [replies.fileHandleForReading closeFile];
        } @catch (NSException *e) {
            (void)e;
        }
        if (worker.running)
            [worker terminate];
        for (int i = 0; i < 20 && worker.running; i++)
            usleep(50000);
        if (worker.running)
            kill(worker.processIdentifier, SIGKILL);
    }
    ~AppSession() {
        stop();
    }
};
