#include "../AudioRing.hpp"
#include <cassert>
#include <iostream>
#include <thread>

int main() {
    vrap::AudioRing<8> ring;
    vrap::Frame f;
    assert(!ring.peek(f));
    for (unsigned i = 0; i < 8; i++)
        assert(ring.push({float(i), -float(i), i, i / 4}));
    assert(!ring.push({}));
    // A mode change discards the previous generation without clearing a live ring.
    while (ring.peek(f) && f.generation != 1)
        assert(ring.pop(f));
    assert(ring.size() == 4 && ring.peek(f) && f.captureTicks == 4);
    for (unsigned i = 4; i < 8; i++) {
        assert(ring.peek(f) && f.captureTicks == i);
        assert(ring.pop(f) && f.captureTicks == i && f.generation == 1);
    }
    assert(!ring.pop(f));
    constexpr unsigned total = 100000;
    std::thread producer([&] {
        for (unsigned i = 0; i < total; i++)
            while (!ring.push({float(i), -float(i), i, i / 1000}))
                std::this_thread::yield();
    });
    for (unsigned i = 0; i < total; i++) {
        while (!ring.peek(f))
            std::this_thread::yield();
        assert(f.captureTicks == i && f.generation == i / 1000);
        assert(ring.pop(f) && f.captureTicks == i && f.right == -float(i));
    }
    producer.join();
    assert(ring.size() == 0);
    std::cout << "PASS: generation discard, non-destructive peek, bounded overflow, 100000 "
                 "concurrent frames\n";
}
