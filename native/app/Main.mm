#import <AppKit/AppKit.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#include "Session.hpp"

static NSTextField *label(NSString *text, CGFloat size, NSColor *color = nil) {
    auto *v = [NSTextField wrappingLabelWithString:text];
    v.font = [NSFont systemFontOfSize:size];
    v.textColor = color ?: NSColor.labelColor;
    return v;
}
static NSDictionary *inventory() {
    NSMutableArray *apps = [NSMutableArray array], *devices = [NSMutableArray array];
    for (NSRunningApplication *app in NSWorkspace.sharedWorkspace.runningApplications) {
        if (app.activationPolicy != NSApplicationActivationPolicyRegular ||
            app.processIdentifier == getpid())
            continue;
        [apps addObject:@{
            @"name" : app.localizedName ?: @"Application",
            @"bundle" : app.bundleIdentifier ?: @"",
            @"pid" : @(app.processIdentifier)
        }];
    }
    for (auto id : objects(kAudioObjectSystemObject, kAudioHardwarePropertyDevices)) {
        try {
            if (objects(id, kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput).empty())
                continue;
            [devices addObject:@{
                @"name" : stringProperty(id, kAudioObjectPropertyName),
                @"uid" : stringProperty(id, kAudioDevicePropertyDeviceUID),
                @"id" : @(id)
            }];
        } catch (...) {
        }
    }
    auto current =
        get<AudioObjectID>(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice);
    return @{@"apps" : apps, @"devices" : devices, @"current_output" : @(current)};
}
@interface AppController : NSObject <NSApplicationDelegate, NSWindowDelegate> {
    NSWindow *_window;
    NSPopUpButton *_sources, *_outputs;
    NSButton *_referenceButton, *_startButton, *_bypassButton, *_refreshButton;
    NSTextField *_referenceLabel, *_status, *_detail;
    NSString *_reference, *_sourceBundle;
    pid_t _sourcePID;
    AudioObjectID _chosenOutput;
    BOOL _working, _running, _testMode;
    NSDictionary *_config, *_test;
    dispatch_queue_t _queue;
    std::shared_ptr<AppSession> _session;
    NSTimer *_timer;
    NSDate *_began;
    NSMutableArray *_testEvents;
    NSArray<NSString *> *_testSwitches;
    NSUInteger _nextTestSwitch;
}
@end
@implementation AppController
- (NSButton *)button:(NSString *)title action:(SEL)action {
    NSButton *b = [NSButton buttonWithTitle:title target:self action:action];
    b.bezelStyle = NSBezelStyleRounded;
    return b;
}
- (void)applicationDidFinishLaunching:(NSNotification *)note {
    (void)note;
    _queue = dispatch_queue_create("local.voicerelay.session", DISPATCH_QUEUE_SERIAL);
    NSData *configData =
        [NSData dataWithContentsOfURL:[NSBundle.mainBundle URLForResource:@"runtime"
                                                            withExtension:@"json"]];
    if (configData)
        _config = [NSJSONSerialization JSONObjectWithData:configData options:0 error:nullptr];
    if (![_config[@"root"] isKindOfClass:NSString.class] ||
        ![_config[@"python"] isKindOfClass:NSString.class] ||
        ![_config[@"worker"] isKindOfClass:NSString.class]) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"VoiceRelay needs rebuilding";
        alert.informativeText =
            @"Run bash scripts/setup.sh, then bash native/build_app.sh from the project folder.";
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }
    _test = NSUserDefaults.standardUserDefaults.dictionaryRepresentation;
    _testMode = _test[@"test-pid"] != nil;
    _testEvents = [NSMutableArray array];
    _testSwitches = [(_test[@"test-switches"] ?: @"5,9") componentsSeparatedByString:@","];
    _window =
        [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 570, 480)
                                    styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                                              NSWindowStyleMaskMiniaturizable
                                      backing:NSBackingStoreBuffered
                                        defer:NO];
    _window.title = @"VoiceRelay";
    _window.delegate = self;
    _window.releasedWhenClosed = NO;
    _window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
    _window.backgroundColor = [NSColor colorWithCalibratedRed:.965 green:.961 blue:.941 alpha:1];
    NSView *content = _window.contentView;
    NSStackView *stack = [[NSStackView alloc] init];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical;
    stack.alignment = NSLayoutAttributeLeading;
    stack.spacing = 12;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [content addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:30],
        [stack.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-30],
        [stack.topAnchor constraintEqualToAnchor:content.topAnchor constant:26]
    ]];
    NSTextField *title = label(@"Your audio. A different voice.", 26);
    title.font = [NSFont systemFontOfSize:26 weight:NSFontWeightSemibold];
    [stack addArrangedSubview:title];
    [stack addArrangedSubview:label(@"Continuous, local voice conversion with X-VC.", 13,
                                    NSColor.secondaryLabelColor)];
    [stack setCustomSpacing:19 afterView:stack.arrangedSubviews.lastObject];
    [stack addArrangedSubview:label(@"Source application", 12, NSColor.secondaryLabelColor)];
    _sources = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
    _sources.target = self;
    _sources.action = @selector(selectionChanged:);
    _sources.accessibilityLabel = @"Source application";
    [stack addArrangedSubview:_sources];
    [_sources.widthAnchor constraintEqualToAnchor:stack.widthAnchor].active = YES;
    [stack addArrangedSubview:label(@"Voice reference", 12, NSColor.secondaryLabelColor)];
    NSStackView *refRow = [NSStackView stackViewWithViews:@[]];
    refRow.spacing = 12;
    refRow.alignment = NSLayoutAttributeCenterY;
    _referenceButton = [self button:@"Choose audio…" action:@selector(chooseReference:)];
    _referenceLabel =
        label(@"Choose a WAV, FLAC or AIFF voice recording.", 12, NSColor.secondaryLabelColor);
    _referenceLabel.maximumNumberOfLines = 2;
    [_referenceLabel
        setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow
                                 forOrientation:NSLayoutConstraintOrientationHorizontal];
    [refRow addArrangedSubview:_referenceButton];
    [refRow addArrangedSubview:_referenceLabel];
    [stack addArrangedSubview:refRow];
    [refRow.widthAnchor constraintEqualToAnchor:stack.widthAnchor].active = YES;
    [stack addArrangedSubview:label(@"Output device", 12, NSColor.secondaryLabelColor)];
    _outputs = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
    _outputs.target = self;
    _outputs.action = @selector(selectionChanged:);
    _outputs.accessibilityLabel = @"Output device";
    [stack addArrangedSubview:_outputs];
    [_outputs.widthAnchor constraintEqualToAnchor:stack.widthAnchor].active = YES;
    NSStackView *buttons = [NSStackView stackViewWithViews:@[]];
    buttons.spacing = 10;
    _startButton = [self button:@"Start" action:@selector(toggleStart:)];
    _startButton.keyEquivalent = @"\r";
    [_startButton.widthAnchor constraintEqualToConstant:105].active = YES;
    _bypassButton = [self button:@"Bypass" action:@selector(toggleBypass:)];
    [_bypassButton setButtonType:NSButtonTypePushOnPushOff];
    _bypassButton.enabled = NO;
    _bypassButton.toolTip = @"Play the original audio through the selected output device.";
    _refreshButton = [self button:@"Refresh" action:@selector(refresh:)];
    [buttons addArrangedSubview:_startButton];
    [buttons addArrangedSubview:_bypassButton];
    [buttons addArrangedSubview:_refreshButton];
    [stack addArrangedSubview:buttons];
    [stack setCustomSpacing:18 afterView:buttons];
    _status = label(@"Ready to connect", 14);
    _status.font = [NSFont systemFontOfSize:14 weight:NSFontWeightMedium];
    [stack addArrangedSubview:_status];
    _detail = label(@"Your microphone stays with the source app. Audio is not recorded.", 12,
                    NSColor.secondaryLabelColor);
    _detail.toolTip = @"Routing delay is measured from the captured sample to scheduled Core Audio "
                      @"output. Bluetooth and acoustic delay are excluded.";
    [stack addArrangedSubview:_detail];
    [_detail.widthAnchor constraintEqualToAnchor:stack.widthAnchor].active = YES;
    NSMenu *menu = [[NSMenu alloc] init];
    NSMenuItem *appItem = [[NSMenuItem alloc] init];
    [menu addItem:appItem];
    NSMenu *appMenu = [[NSMenu alloc] init];
    [appMenu addItemWithTitle:@"Quit VoiceRelay" action:@selector(terminate:) keyEquivalent:@"q"];
    appItem.submenu = appMenu;
    NSApp.mainMenu = menu;
    _reference = [NSUserDefaults.standardUserDefaults stringForKey:@"voiceReference"];
    if (_reference.length && [NSFileManager.defaultManager isReadableFileAtPath:_reference])
        _referenceLabel.stringValue = _reference.lastPathComponent;
    else
        _reference = nil;
    [self refresh:nil];
    [_window center];
    [_window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    _timer = [NSTimer scheduledTimerWithTimeInterval:.5
                                              target:self
                                            selector:@selector(tick:)
                                            userInfo:nil
                                             repeats:YES];
    if (_testMode) {
        [_sources insertItemWithTitle:@"VoiceRelay test player" atIndex:1];
        [_sources itemAtIndex:1].representedObject =
            @{@"bundle" : @"", @"pid" : @([_test[@"test-pid"] intValue])};
        [_sources selectItemAtIndex:1];
        _reference = _test[@"test-reference"];
        _referenceLabel.stringValue = _reference.lastPathComponent;
        for (NSMenuItem *item in _outputs.itemArray)
            if ([item.representedObject isEqual:_test[@"test-output"]])
                [_outputs selectItem:item];
        [self toggleStart:nil];
    }
}
- (void)refresh:(id)sender {
    (void)sender;
    if (_working || _running)
        return;
    NSDictionary *values = nil;
    try {
        values = inventory();
    } catch (const std::exception &e) {
        _status.stringValue = @(e.what());
        return;
    }
    NSString *source = [_sources.selectedItem.representedObject objectForKey:@"bundle"]
                           ?: [NSUserDefaults.standardUserDefaults stringForKey:@"sourceBundle"];
    NSString *output=_outputs.selectedItem.representedObject?:[NSUserDefaults.standardUserDefaults stringForKey:@"outputUID"]?:@"current";
    [_sources removeAllItems];
    [_sources addItemWithTitle:@"Choose a running application…"];
    NSArray *apps = [values[@"apps"] sortedArrayUsingDescriptors:@[
        [NSSortDescriptor sortDescriptorWithKey:@"name"
                                      ascending:YES
                                       selector:@selector(localizedCaseInsensitiveCompare:)]
    ]];
    for (NSDictionary *app in apps) {
        [_sources addItemWithTitle:app[@"name"]];
        _sources.lastItem.representedObject = app;
        if (source.length && [app[@"bundle"] isEqual:source])
            [_sources selectItem:_sources.lastItem];
    }
    [_outputs removeAllItems];
    NSString *current = @"Current output";
    for (NSDictionary *d in values[@"devices"])
        if ([d[@"id"] isEqual:values[@"current_output"]])
            current = [NSString stringWithFormat:@"Current output — %@", d[@"name"]];
    [_outputs addItemWithTitle:current];
    _outputs.lastItem.representedObject = @"current";
    for (NSDictionary *d in values[@"devices"]) {
        [_outputs addItemWithTitle:d[@"name"]];
        _outputs.lastItem.representedObject = d[@"uid"];
        if ([d[@"uid"] isEqual:output])
            [_outputs selectItem:_outputs.lastItem];
    }
    if ([output isEqual:@"current"])
        [_outputs selectItemAtIndex:0];
}
- (void)selectionChanged:(id)sender {
    (void)sender;
    if (_testMode)
        return;
    if (_sources.selectedItem.representedObject)
        [NSUserDefaults.standardUserDefaults
            setObject:_sources.selectedItem.representedObject[@"bundle"]
               forKey:@"sourceBundle"];
    [NSUserDefaults.standardUserDefaults setObject:_outputs.selectedItem.representedObject
                                            forKey:@"outputUID"];
}
- (void)chooseReference:(id)sender {
    (void)sender;
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = NO;
    panel.allowedContentTypes = @[
        [UTType typeWithFilenameExtension:@"wav"], [UTType typeWithFilenameExtension:@"flac"],
        [UTType typeWithFilenameExtension:@"aiff"], [UTType typeWithFilenameExtension:@"aif"]
    ];
    panel.message = @"Choose a 1–120 second voice recording. X-VC uses its first three seconds.";
    [panel beginSheetModalForWindow:_window
                  completionHandler:^(NSModalResponse result) {
                    if (result == NSModalResponseOK) {
                        self->_reference = panel.URL.path;
                        self->_referenceLabel.stringValue = self->_reference.lastPathComponent;
                        [NSUserDefaults.standardUserDefaults setObject:self->_reference
                                                                forKey:@"voiceReference"];
                    }
                  }];
}
- (void)setControls {
    BOOL idle = !_working && !_running;
    _sources.enabled = idle;
    _outputs.enabled = idle;
    _referenceButton.enabled = idle;
    _refreshButton.enabled = idle;
    _startButton.title = idle ? @"Start" : @"Stop";
    _startButton.enabled = YES;
    _bypassButton.enabled = _running;
}
- (void)toggleStart:(id)sender {
    (void)sender;
    if (_session) {
        [self stopWithMessage:@"Stopped — original app playback restored."];
        return;
    }
    NSDictionary *app = _sources.selectedItem.representedObject;
    if (!app) {
        _status.stringValue = @"Choose the source application first.";
        return;
    }
    if (!_reference.length || ![NSFileManager.defaultManager isReadableFileAtPath:_reference]) {
        _status.stringValue = @"Choose a readable voice reference first.";
        return;
    }
    try {
        NSString *uid = _outputs.selectedItem.representedObject;
        _chosenOutput =
            get<AudioObjectID>(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice);
        if (![uid isEqual:@"current"]) {
            _chosenOutput = 0;
            for (NSDictionary *d in inventory()[@"devices"])
                if ([d[@"uid"] isEqual:uid])
                    _chosenOutput = [d[@"id"] unsignedIntValue];
        }
        if (!_chosenOutput)
            throw std::runtime_error("The output device is unavailable. Refresh the device list.");
        _sourcePID = [app[@"pid"] intValue];
        _sourceBundle = app[@"bundle"];
        _session = std::make_shared<AppSession>();
        auto s = _session;
        if (_testMode && _test[@"test-reserve"])
            s->playbackReserveMS = [_test[@"test-reserve"] doubleValue];
        if (_testMode && _test[@"test-prime"])
            s->primeByDeadline = [_test[@"test-prime"] isEqual:@"deadline"];
        if (_testMode && _test[@"test-target"])
            s->targetLatencyMS = [_test[@"test-target"] doubleValue];
        s->bypass.store(_bypassButton.state == NSControlStateValueOn);
        _working = YES;
        [self setControls];
        _status.stringValue = @"Connecting…";
        _detail.stringValue = @"Preparing the local engine. The source app retains its microphone.";
        NSDictionary *config = _config;
        NSString *reference = _reference, *bundle = _sourceBundle;
        pid_t pid = _sourcePID;
        AudioObjectID output = _chosenOutput;
        dispatch_async(_queue, ^{
          @autoreleasepool {
              try {
                  s->start(config, bundle, pid, output, reference, ^(NSString *text) {
                    dispatch_async(dispatch_get_main_queue(), ^{
                      if (self->_session == s && !s->cancelled.load())
                          self->_status.stringValue = text;
                    });
                  });
                  dispatch_async(dispatch_get_main_queue(), ^{
                    if (self->_session == s && !s->cancelled.load()) {
                        self->_working = NO;
                        self->_running = YES;
                        self->_began = NSDate.date;
                        [self setControls];
                        self->_status.stringValue = s->bypass.load()
                                                        ? @"Bypass — playing original audio"
                                                        : @"Converting audio";
                    }
                  });
              } catch (const std::exception &e) {
                  NSString *message = @(e.what());
                  dispatch_async(dispatch_get_main_queue(), ^{
                    if (self->_session == s)
                        [self stopWithMessage:message];
                  });
              }
          }
        });
    } catch (const std::exception &e) {
        _status.stringValue = @(e.what());
    }
}
- (void)toggleBypass:(id)sender {
    (void)sender;
    if (!_session || !_running)
        return;
    _session->setBypass(_bypassButton.state == NSControlStateValueOn);
    _status.stringValue = _bypassButton.state == NSControlStateValueOn
                              ? @"Bypass — playing original audio"
                              : @"Converting audio";
}
- (void)stopWithMessage:(NSString *)message {
    auto s = _session;
    if (!s)
        return;
    s->cancelled.store(true);
    _working = YES;
    _running = NO;
    _startButton.enabled = NO;
    _bypassButton.enabled = NO;
    _status.stringValue = @"Stopping…";
    dispatch_async(_queue, ^{
      s->stop();
      dispatch_async(dispatch_get_main_queue(), ^{
        if (self->_session == s) {
            self->_session.reset();
            self->_working = NO;
            [self setControls];
            self->_status.stringValue = message;
            self->_detail.stringValue =
                @"Your microphone stays with the source app. Audio is not recorded.";
        }
      });
    });
}
- (void)tick:(NSTimer *)timer {
    (void)timer;
    if (!_session)
        return;
    auto s = _session;
    if (s->cancelled.load())
        return;
    if (s->fault.load()) {
        if (_testMode) {
            [_testEvents addObject:@{@"event" : @"fault", @"message" : @(s->failure().c_str())}];
            [self finishTest];
        } else
            [self stopWithMessage:@(s->failure().c_str())];
        return;
    }
    if (!_running)
        return;
    _detail.stringValue =
        [NSString stringWithFormat:@"%@ · %.0f ms routing delay · %.0f ms processing",
                                   s->bypass.load() ? @"Original audio" : @"X-VC · full context",
                                   s->latencyMS.load(), s->computeMS.load()];
    try {
        if ([_outputs.selectedItem.representedObject isEqual:@"current"] &&
            get<AudioObjectID>(kAudioObjectSystemObject,
                               kAudioHardwarePropertyDefaultOutputDevice) != _chosenOutput)
            s->fail("The current output changed. Press Start to use the new output.");
        s->refreshSource(_sourceBundle, _sourcePID);
    } catch (const std::exception &e) {
        s->fail(e.what());
    }
    if (_testMode) {
        double seconds = -[_began timeIntervalSinceNow];
        if (_nextTestSwitch < _testSwitches.count &&
            seconds > [_testSwitches[_nextTestSwitch] doubleValue]) {
            BOOL direct = (_nextTestSwitch++ % 2) == 0;
            _bypassButton.state = direct ? NSControlStateValueOn : NSControlStateValueOff;
            [self toggleBypass:nil];
            [_testEvents addObject:@{
                @"event" : direct ? @"bypass" : @"convert",
                @"at" : @(seconds),
                @"packets" : @(s->processed.load())
            }];
        }
        if (seconds > [_test[@"test-duration"] doubleValue])
            [self finishTest];
    }
}
- (void)finishTest {
    auto s = _session;
    if (!s)
        return;
    [_timer invalidate];
    NSMutableArray *timings = [NSMutableArray array];
    for (size_t i = 0; i < s->timings.size(); i++) {
        auto &t = s->timings[i];
        if (!t.firstReply.load())
            continue;
        [timings addObject:@{
            @"generation" : @(i),
            @"mode" : i % 2 ? @"bypass" : @"convert",
            @"samples" : @(t.samples.load()),
            @"p50_ms" : @(t.percentile(.5)),
            @"p95_ms" : @(t.percentile(.95)),
            @"p99_ms" : @(t.percentile(.99)),
            @"first_compute_ms" : @(t.firstCompute.load()),
            @"first_reply_age_ms" : @(t.firstReplyAge.load()),
            @"first_scheduled_age_ms" : @(t.firstScheduledAge.load()),
            @"first_queue_ms" : @(t.firstDepth.load() / s->outputRate * 1000),
            @"startup_wait_ms" :
                @((double(t.firstPlay.load()) - double(t.firstReply.load())) * s->msPerTick)
        }];
    }
    NSDictionary *report = @{
        @"timings" : timings,
        @"prime_by_deadline" : @(s->primeByDeadline),
        @"target_latency_ms" : @(s->targetLatencyMS),
        @"fixed_startup_wait_ms" : @(s->primeByDeadline ? 0 : s->playbackReserveMS),
        @"events" : _testEvents,
        @"packets" : @(s->processed.load()),
        @"underflows" : @(s->underflows.load()),
        @"overflows" : @(s->overflows.load()),
        @"fault" : @(s->fault.load()),
        @"microphone_opened" : @NO,
        @"generation" : @(s->generation.load()),
        @"max_compute_ms" : @(s->maxComputeMS.load()),
        @"blocks_over_120ms" : @(s->deadlineMisses.load()),
        @"input_peak" : @(s->inputPeak.load()),
        @"output_peak" : @(s->outputPeak.load()),
        @"last_scheduled_latency_ms" : @(s->latencyMS.load())
    };
    NSMutableDictionary *full = [report mutableCopy];
    full[@"diagnostics"] = @{
        @"sent_packets" : @(s->sentPackets.load()),
        @"max_reply_gap_ms" : @(s->maxReplyGapMS.load()),
        @"underrun_reply_gap_ms" : @(s->underrunReplyGapMS.load()),
        @"underrun_input_queue_ms" : @(s->underrunInputQueueMS.load()),
        @"max_capture_gap_ms" : @(s->maxCaptureGapMS.load()),
        @"underrun_capture_gap_ms" : @(s->underrunCaptureGapMS.load())
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:full
                                                   options:NSJSONWritingPrettyPrinted
                                                     error:nullptr];
    [data writeToFile:_test[@"test-report"] atomically:YES];
    s->cancelled.store(true);
    _running = NO;
    dispatch_async(_queue, ^{
      s->stop();
      [self performSelectorOnMainThread:@selector(terminateAfterTest)
                             withObject:nil
                          waitUntilDone:NO];
    });
}
- (void)terminateAfterTest {
    _session.reset();
    [NSApp terminate:nil];
}
- (void)finishTermination {
    _session.reset();
    [NSApp replyToApplicationShouldTerminate:YES];
}
- (BOOL)windowShouldClose:(NSWindow *)sender {
    (void)sender;
    [NSApp terminate:nil];
    return NO;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    if (!_session)
        return NSTerminateNow;
    auto s = _session;
    s->cancelled.store(true);
    [_timer invalidate];
    dispatch_async(_queue, ^{
      s->stop();
      [self performSelectorOnMainThread:@selector(finishTermination)
                             withObject:nil
                          waitUntilDone:NO];
    });
    return NSTerminateLater;
}
@end

int main(int argc, const char **argv) {
    @autoreleasepool {
        signal(SIGPIPE, SIG_IGN);
        if (argc > 1 && strcmp(argv[1], "--inventory") == 0) {
            NSDictionary *d = inventory();
            NSData *j = [NSJSONSerialization dataWithJSONObject:d options:0 error:nullptr];
            fwrite(j.bytes, 1, j.length, stdout);
            return 0;
        }
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        AppController *controller = [[AppController alloc] init];
        app.delegate = controller;
        [app run];
    }
    return 0;
}
