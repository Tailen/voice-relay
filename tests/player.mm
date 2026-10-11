// Dedicated, quiet file player for the live route test. Never opens an input.
#import <AVFoundation/AVFoundation.h>
#include "../native/app/AudioSupport.hpp"
#include <iostream>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        try {
            NSString *uid = nil, *path = nil;
            for (int i = 1; i + 1 < argc; i += 2) {
                if (strcmp(argv[i], "--output-uid") == 0)
                    uid = @(argv[i + 1]);
                else if (strcmp(argv[i], "--source-wav") == 0)
                    path = @(argv[i + 1]);
                else
                    throw std::runtime_error("Unknown player argument");
            }
            if (!uid || !path)
                throw std::runtime_error("Supply --output-uid and --source-wav");
            AudioObjectID device = 0;
            for (auto id : objects(kAudioObjectSystemObject, kAudioHardwarePropertyDevices))
                if ([stringProperty(id, kAudioDevicePropertyDeviceUID) isEqual:uid])
                    device = id;
            if (!device)
                throw std::runtime_error("Output device not found");
            NSError *error = nil;
            AVAudioFile *file = [[AVAudioFile alloc] initForReading:[NSURL fileURLWithPath:path]
                                                              error:&error];
            if (!file)
                throw std::runtime_error(error.localizedDescription.UTF8String);
            if (file.length <= 0 || file.length > file.processingFormat.sampleRate * 120)
                throw std::runtime_error("Use a source recording of up to 120 seconds");
            AVAudioPCMBuffer *pcm =
                [[AVAudioPCMBuffer alloc] initWithPCMFormat:file.processingFormat
                                              frameCapacity:AVAudioFrameCount(file.length)];
            if (![file readIntoBuffer:pcm error:&error])
                throw std::runtime_error(error.localizedDescription.UTF8String);
            AVAudioEngine *engine = [[AVAudioEngine alloc] init];
            check(AudioUnitSetProperty(engine.outputNode.audioUnit,
                                       kAudioOutputUnitProperty_CurrentDevice,
                                       kAudioUnitScope_Global, 0, &device, sizeof(device)),
                  "Select test output");
            AVAudioPlayerNode *player = [[AVAudioPlayerNode alloc] init];
            [engine attachNode:player];
            [engine connect:player to:engine.mainMixerNode format:pcm.format];
            player.volume = 0.12;
            [player scheduleBuffer:pcm
                            atTime:nil
                           options:AVAudioPlayerNodeBufferLoops
                 completionHandler:nil];
            if (![engine startAndReturnError:&error])
                throw std::runtime_error(error.localizedDescription.UTF8String);
            std::cout << "{\"type\":\"ready\"}" << std::endl;
            char command;
            while (std::cin.get(command))
                if (command == 'S')
                    [player play];
            [player stop];
            [engine stop];
        } catch (const std::exception &error) {
            std::cerr << error.what() << std::endl;
            return 1;
        }
    }
    return 0;
}
