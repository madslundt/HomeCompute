import unittest

from average import average


class AverageTest(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(average([2, 4, 6]), 4)

    def test_empty_input_has_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            average([])


if __name__ == "__main__":
    unittest.main()
