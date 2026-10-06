import unittest

from carbon_sensor import (
    CARBON,
    CLEAR,
    FAULT,
    DebouncedClassifier,
    Thresholds,
    ma_to_raw,
    raw_to_ma,
    volts_to_ma,
)


class ConversionTests(unittest.TestCase):
    def test_510_ohm_nominal_levels(self):
        self.assertAlmostEqual(16.0 * 510.0 / 1000.0, 8.16)
        self.assertAlmostEqual(8.0 * 510.0 / 1000.0, 4.08)

    def test_adc_round_trip(self):
        for current in (3.2, 8.0, 16.0):
            raw = ma_to_raw(current, 510.0)
            self.assertAlmostEqual(raw_to_ma(raw, 510.0), current, delta=0.01)


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.classifier = DebouncedClassifier(Thresholds(), debounce_samples=3)

    def feed(self, current_ma, count=3):
        result = None
        for _ in range(count):
            result = self.classifier.update(current_ma)
        return result

    def test_nominal_states(self):
        self.assertEqual(self.feed(16.0)[1], CLEAR)
        self.assertEqual(self.feed(8.0)[1], CARBON)
        self.assertEqual(self.feed(3.2)[1], FAULT)

    def test_hysteresis_keeps_previous_state(self):
        self.assertEqual(self.feed(16.0)[1], CLEAR)
        self.assertEqual(self.feed(12.0, count=5)[1], CLEAR)
        self.assertEqual(self.feed(8.0)[1], CARBON)
        self.assertEqual(self.feed(12.0, count=5)[1], CARBON)

    def test_single_spike_does_not_change_stable_state(self):
        self.assertEqual(self.feed(16.0)[1], CLEAR)
        self.assertEqual(self.feed(8.0, count=1)[1], CLEAR)


if __name__ == "__main__":
    unittest.main()
