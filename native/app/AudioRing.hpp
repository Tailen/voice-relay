#pragma once
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace vrap {
struct Frame {
    float left = 0, right = 0;
    uint64_t captureTicks = 0, generation = 0;
};
// One producer, one consumer. No allocation, locks, or producer overwrite.
// Clearing is only legal after both audio callbacks have stopped.
template <size_t Capacity> class AudioRing {
    static_assert(Capacity > 1 && (Capacity & (Capacity - 1)) == 0);
    std::array<Frame, Capacity> data_{};
    alignas(64) std::atomic<uint64_t> read_{0};
    alignas(64) std::atomic<uint64_t> write_{0};

  public:
    static_assert(std::atomic<uint64_t>::is_always_lock_free);
    size_t size() const {
        auto r = read_.load(std::memory_order_acquire);
        return write_.load(std::memory_order_acquire) - r;
    }
    bool push(Frame frame) {
        auto w = write_.load(std::memory_order_relaxed);
        if (w - read_.load(std::memory_order_acquire) == Capacity)
            return false;
        data_[w & (Capacity - 1)] = frame;
        write_.store(w + 1, std::memory_order_release);
        return true;
    }
    bool peek(Frame &frame) const {
        auto r = read_.load(std::memory_order_relaxed);
        if (r == write_.load(std::memory_order_acquire))
            return false;
        frame = data_[r & (Capacity - 1)];
        return true;
    }
    bool pop(Frame &frame) {
        auto r = read_.load(std::memory_order_relaxed);
        if (r == write_.load(std::memory_order_acquire))
            return false;
        frame = data_[r & (Capacity - 1)];
        read_.store(r + 1, std::memory_order_release);
        return true;
    }
};
} // namespace vrap
