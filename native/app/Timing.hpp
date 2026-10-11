#pragma once
#include <array>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>

// Bounded instrumentation: no allocation or locks in audio callbacks.
struct Timing {
    std::array<std::atomic<uint64_t>, 2001> latency{};
    std::atomic<uint64_t> samples{0}, firstReply{0}, firstPlay{0}, firstDepth{0};
    std::atomic<double> firstCompute{0}, firstReplyAge{0}, firstScheduledAge{0};
    void observe(double ms) {
        if (ms < 0 || !std::isfinite(ms))
            return;
        latency[std::min<size_t>(2000, size_t(std::ceil(ms)))].fetch_add(1,
                                                                         std::memory_order_relaxed);
        samples.fetch_add(1, std::memory_order_relaxed);
    }
    double percentile(double fraction) const {
        uint64_t target = uint64_t(std::ceil(samples.load() * fraction)), n = 0;
        if (!target)
            return 0;
        for (size_t i = 0; i < latency.size(); i++)
            if ((n += latency[i].load()) >= target)
                return double(i);
        return 2000;
    }
};
