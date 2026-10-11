#include "../Timing.hpp"
#include <cassert>
#include <iostream>
int main() {
    Timing t;
    assert(t.percentile(.95) == 0);
    for (unsigned i = 0; i < 95; i++)
        t.observe(523.73);
    for (unsigned i = 0; i < 5; i++)
        t.observe(676.61);
    t.observe(-1);
    t.observe(NAN);
    assert(t.samples == 100);
    assert(t.percentile(.50) == 524 && t.percentile(.95) == 524 && t.percentile(.99) == 677);
    std::cout << "PASS: bounded timing histogram, percentile ranks, invalid samples\n";
}
