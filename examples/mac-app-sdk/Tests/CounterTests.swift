// SPDX-License-Identifier: Apache-2.0
import Testing

struct CounterTests {
    @Test func resetStartsANewSequence() {
        var counter = Counter()
        counter.increment(); counter.increment()
        #expect(counter.value == 2)
        counter.reset(); counter.increment()
        #expect(counter.value == 1)
    }
}
